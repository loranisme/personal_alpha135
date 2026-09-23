"""Read-only adapter for a pinned SEC PIT fundamentals release."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import exchange_calendars as xcals
import pandas as pd


_HASH_PATTERN = re.compile(r"[0-9a-f]{64}")
_REQUIRED_OUTPUTS = {
    "daily_pit_fundamental_panel.parquet",
    "canonical_field_catalog.csv",
    "derived_field_definitions.json",
    "cik_security_map.csv",
}
_EVIDENCE_OUTPUT_BINDINGS = {
    "canonical_mapping_hash": "canonical_field_catalog.csv",
    "derived_field_catalog_hash": "derived_field_definitions.json",
    "security_mapping_hash": "cik_security_map.csv",
}
_REQUIRED_EVIDENCE_HASHES = {
    *_EVIDENCE_OUTPUT_BINDINGS,
    "pit_contract_hash",
    "universe_snapshot_hash",
    "raw_manifest_hash",
}
_EXTERNAL_EVIDENCE_HASHES = {
    "pit_contract_hash",
    "universe_snapshot_hash",
    "raw_manifest_hash",
}
_KNOWN_MAPPING_STATUSES = {
    "VERIFIED_SINGLE_CLASS",
    "VERIFIED_ENTITY_LEVEL_ONLY",
    "AMBIGUOUS_MULTI_CLASS",
    "UNRESOLVED",
    "DEFERRED_ISSUER_SCOPE",
}
_ENTITY_USAGES = {"ENTITY", "ENTITY_LEVEL", "STATEMENT"}
_SHARE_CLASS_USAGES = {
    "PER_SHARE",
    "SHARES",
    "SHARE_COUNT",
    "SHARES_OUTSTANDING",
    "MARKET_CAP",
    "ENTERPRISE_VALUE",
}
_PANEL_COLUMNS = [
    "session",
    "signal_cutoff",
    "security_id",
    "issuer_cik",
    "canonical_field",
    "reported_basis",
    "value",
    "unit",
    "selected_available_at",
    "historical_available_at",
    "first_seen_at",
    "available_at",
    "accession_number",
    "source_fact_id",
    "semantic_grade",
    "mapping_version",
    "definition_hash",
    "availability_mode",
]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_release_path(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if (
        candidate.is_absolute()
        or not candidate.parts
        or candidate == Path(".")
        or ".." in candidate.parts
    ):
        raise ValueError(f"OUTPUT_PATH_UNSAFE:{relative}")
    current = root
    for part in candidate.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"OUTPUT_PATH_SYMLINK:{relative}")
    path = root / candidate
    if not path.exists() or (not path.is_file() and not path.is_dir()):
        raise ValueError(f"OUTPUT_PATH_MISSING_OR_UNSAFE:{relative}")
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    if resolved_root != resolved_path and resolved_root not in resolved_path.parents:
        raise ValueError(f"OUTPUT_PATH_ESCAPES_ROOT:{relative}")
    return path


def _sha256_directory(path: Path) -> str:
    entries: list[dict[str, str]] = []
    for member in sorted(path.rglob("*"), key=lambda item: item.relative_to(path).as_posix()):
        relative = member.relative_to(path).as_posix()
        if member.is_symlink():
            raise ValueError(f"OUTPUT_DATASET_SYMLINK:{relative}")
        if member.is_dir():
            continue
        if not member.is_file():
            raise ValueError(f"OUTPUT_DATASET_MEMBER_UNSAFE:{relative}")
        entries.append({"path": relative, "sha256": _sha256_file(member)})
    if not entries:
        raise ValueError(f"OUTPUT_DATASET_EMPTY:{path.name}")
    encoded = json.dumps(
        entries,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_path(path: Path) -> str:
    return _sha256_file(path) if path.is_file() else _sha256_directory(path)


def _as_utc_series(values: pd.Series, error: str) -> pd.Series:
    try:
        return pd.to_datetime(values, utc=True, errors="raise")
    except (TypeError, ValueError) as exc:
        raise ValueError(error) from exc


class SecFundamentalProvider:
    """Verify a release once, then expose only requested field/basis panels."""

    def __init__(self, manifest_path: Path | str):
        self._manifest_path = Path(manifest_path)
        if self._manifest_path.is_symlink() or not self._manifest_path.is_file():
            raise ValueError("SEC_MANIFEST_MISSING_OR_UNSAFE")
        self._root = self._manifest_path.parent.resolve()
        manifest_bytes = self._manifest_path.read_bytes()
        self._manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        payload = json.loads(manifest_bytes.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("SEC_MANIFEST_NOT_OBJECT")
        if payload.get("validation_status") != "PASS":
            raise ValueError("SEC_RELEASE_NOT_APPROVED")
        version = payload.get("dataset_version")
        if not isinstance(version, str) or not version or version.lower() == "latest":
            raise ValueError("SEC_DATASET_VERSION_NOT_PINNED")

        hashes = payload.get("output_hashes")
        if not isinstance(hashes, dict) or not hashes:
            raise ValueError("SEC_OUTPUT_HASHES_MISSING")
        missing_outputs = sorted(_REQUIRED_OUTPUTS.difference(hashes))
        if missing_outputs:
            raise ValueError("SEC_REQUIRED_OUTPUT_NOT_HASHED:" + ",".join(missing_outputs))
        verified_outputs: dict[str, str] = {}
        for relative, expected in hashes.items():
            relative_text = str(relative)
            if not isinstance(expected, str) or not _HASH_PATTERN.fullmatch(expected):
                raise ValueError(f"OUTPUT_HASH_INVALID:{relative_text}")
            path = _safe_release_path(self._root, relative_text)
            actual = _sha256_path(path)
            if actual != expected:
                raise ValueError(f"OUTPUT_HASH_MISMATCH:{relative_text}")
            verified_outputs[relative_text] = actual

        for name in sorted(_REQUIRED_EVIDENCE_HASHES):
            value = payload.get(name)
            if not isinstance(value, str) or not _HASH_PATTERN.fullmatch(value):
                raise ValueError(f"SEC_EVIDENCE_HASH_INVALID:{name}")
        for evidence_name, output_name in _EVIDENCE_OUTPUT_BINDINGS.items():
            if payload[evidence_name] != verified_outputs[output_name]:
                raise ValueError(
                    f"SEC_EVIDENCE_OUTPUT_HASH_MISMATCH:{evidence_name}:{output_name}"
                )

        self._reviewed_share_facts: dict[str, dict[str, Any]] = {}
        reviewed_hash = payload.get("reviewed_share_facts_hash")
        if reviewed_hash is not None:
            if (not isinstance(reviewed_hash, str) or not _HASH_PATTERN.fullmatch(reviewed_hash)
                    or verified_outputs.get("reviewed_share_facts.json") != reviewed_hash):
                raise ValueError("SEC_REVIEWED_SHARE_FACTS_HASH_INVALID")
            reviewed_payload = json.loads((_safe_release_path(
                self._root, "reviewed_share_facts.json"
            )).read_text(encoding="utf-8"))
            if (not isinstance(reviewed_payload, dict)
                    or reviewed_payload.get("schema_version") != 1
                    or not isinstance(reviewed_payload.get("reviews"), list)):
                raise ValueError("SEC_REVIEWED_SHARE_FACTS_SCHEMA_INVALID")
            for review in reviewed_payload["reviews"]:
                if (not isinstance(review, dict)
                        or review.get("canonical_field") != "shares_outstanding_sec"
                        or review.get("semantic_grade") != "HYPOTHESIS_PROXY"
                        or not isinstance(review.get("source_fact_id"), str)
                        or not _HASH_PATTERN.fullmatch(review["source_fact_id"])):
                    raise ValueError("SEC_REVIEWED_SHARE_FACTS_SCHEMA_INVALID")
                source_id = review["source_fact_id"]
                if source_id in self._reviewed_share_facts:
                    raise ValueError("SEC_REVIEWED_SHARE_FACT_DUPLICATE")
                self._reviewed_share_facts[source_id] = review

        evidence_files = payload.get("evidence_files", {})
        if not isinstance(evidence_files, dict):
            raise ValueError("SEC_EVIDENCE_FILES_NOT_OBJECT")
        unknown_evidence_files = sorted(
            set(evidence_files).difference(_EXTERNAL_EVIDENCE_HASHES)
        )
        if unknown_evidence_files:
            raise ValueError(
                "SEC_EVIDENCE_FILE_UNKNOWN:" + ",".join(unknown_evidence_files)
            )
        verified_evidence_files: dict[str, str] = {}
        for evidence_name, relative in evidence_files.items():
            if not isinstance(relative, str) or not relative:
                raise ValueError(f"SEC_EVIDENCE_FILE_INVALID:{evidence_name}")
            path = _safe_release_path(self._root, relative)
            actual = _sha256_path(path)
            if actual != payload[evidence_name]:
                raise ValueError(f"SEC_EVIDENCE_FILE_HASH_MISMATCH:{evidence_name}")
            verified_evidence_files[evidence_name] = relative

        self._manifest = payload
        self._verified_outputs = verified_outputs
        self._verified_evidence_files = verified_evidence_files
        self._panel_path = _safe_release_path(
            self._root, "daily_pit_fundamental_panel.parquet"
        )
        self._catalog = pd.read_csv(
            _safe_release_path(self._root, "canonical_field_catalog.csv"),
            dtype=str,
            keep_default_na=False,
        )
        required_catalog = {"canonical_field", "supported_basis", "identity_usage"}
        if not required_catalog.issubset(self._catalog.columns):
            raise ValueError("SEC_FIELD_CATALOG_SCHEMA_INVALID")
        self._binding_usage: dict[tuple[str, str], str] = {}
        allowed_usages = _ENTITY_USAGES | _SHARE_CLASS_USAGES
        for (field, basis), group in self._catalog.groupby(
            ["canonical_field", "supported_basis"], sort=False, dropna=False
        ):
            usages = {str(value).strip().upper() for value in group.identity_usage}
            if len(usages) != 1 or not usages.issubset(allowed_usages):
                raise ValueError(f"SEC_FIELD_USAGE_INVALID:{field}:{basis}")
            self._binding_usage[(str(field), str(basis))] = usages.pop()

        self._identity = pd.read_csv(
            _safe_release_path(self._root, "cik_security_map.csv"),
            dtype=str,
            keep_default_na=False,
        )
        identity_required = {
            "security_id",
            "issuer_cik",
            "mapping_status",
            "effective_from",
            "effective_to",
        }
        if not identity_required.issubset(self._identity.columns):
            raise ValueError("SEC_IDENTITY_MAP_SCHEMA_INVALID")
        unknown_statuses = set(self._identity.mapping_status).difference(
            _KNOWN_MAPPING_STATUSES
        )
        if unknown_statuses:
            raise ValueError(
                "SEC_IDENTITY_STATUS_UNKNOWN:" + ",".join(sorted(unknown_statuses))
            )
        self._identity["effective_from"] = _as_utc_series(
            self._identity.effective_from, "SEC_IDENTITY_EFFECTIVE_FROM_INVALID"
        )
        end = self._identity.effective_to.replace("", pd.NA)
        self._identity["effective_to"] = pd.to_datetime(
            end, utc=True, errors="coerce"
        )
        invalid_end = end.notna() & self._identity.effective_to.isna()
        if invalid_end.any():
            raise ValueError("SEC_IDENTITY_EFFECTIVE_TO_INVALID")
        closed = self._identity.effective_to.notna()
        if (
            self._identity.loc[closed, "effective_to"]
            <= self._identity.loc[closed, "effective_from"]
        ).any():
            raise ValueError("SEC_IDENTITY_EFFECTIVE_INTERVAL_INVALID")

    @property
    def evidence_hashes(self) -> Mapping[str, str]:
        evidence = {
            "dataset_version": self._manifest["dataset_version"],
            "processed_manifest_sha256": self._manifest_sha256,
            **{
                name: self._manifest[name]
                for name in sorted(_REQUIRED_EVIDENCE_HASHES)
            },
        }
        evidence.update(
            {f"output:{name}": digest for name, digest in sorted(self._verified_outputs.items())}
        )
        evidence.update(
            {
                f"evidence_file:{name}": relative
                for name, relative in sorted(self._verified_evidence_files.items())
            }
        )
        return evidence

    def _verify_pinned_release_unchanged(self) -> None:
        if self._manifest_path.is_symlink() or self._panel_path.is_symlink():
            raise ValueError("SEC_RELEASE_PATH_CHANGED_AFTER_PROVIDER_INIT")
        if _sha256_file(self._manifest_path) != self._manifest_sha256:
            raise ValueError("SEC_MANIFEST_CHANGED_AFTER_PROVIDER_INIT")
        expected = self._verified_outputs[
            "daily_pit_fundamental_panel.parquet"
        ]
        if _sha256_path(self._panel_path) != expected:
            raise ValueError(
                "OUTPUT_HASH_MISMATCH:daily_pit_fundamental_panel.parquet"
            )

    def _validate_identity_rows(self, frame: pd.DataFrame) -> None:
        for row in frame.itertuples(index=False):
            security_id = str(row.security_id)
            cutoff = pd.Timestamp(row.signal_cutoff)
            if cutoff.tzinfo is None:
                cutoff = cutoff.tz_localize("UTC")
            else:
                cutoff = cutoff.tz_convert("UTC")
            candidates = self._identity[
                self._identity.security_id.eq(security_id)
                & self._identity.effective_from.le(cutoff)
                & (
                    self._identity.effective_to.isna()
                    | self._identity.effective_to.gt(cutoff)
                )
            ]
            if len(candidates) != 1:
                raise ValueError(
                    f"SEC_PANEL_IDENTITY_INTERVAL_COUNT:{security_id}:{cutoff.isoformat()}:{len(candidates)}"
                )
            identity = candidates.iloc[0]
            if str(identity.issuer_cik).zfill(10) != str(row.issuer_cik).zfill(10):
                raise ValueError(f"SEC_PANEL_IDENTITY_ISSUER_MISMATCH:{security_id}")
            usage = self._binding_usage[
                (str(row.canonical_field), str(row.reported_basis))
            ]
            status = str(identity.mapping_status)
            allowed = (
                {"VERIFIED_SINGLE_CLASS", "VERIFIED_ENTITY_LEVEL_ONLY"}
                if usage in _ENTITY_USAGES
                else {"VERIFIED_SINGLE_CLASS"}
            )
            reviewed_share = self._reviewed_share_facts.get(str(row.source_fact_id))
            narrow_proxy_exception = bool(
                status == "VERIFIED_ENTITY_LEVEL_ONLY"
                and usage in _SHARE_CLASS_USAGES
                and reviewed_share is not None
                and str(row.issuer_cik).zfill(10) == reviewed_share.get("issuer_cik")
                and str(identity.get("ticker", "")).upper() == reviewed_share.get("ticker")
                and str(identity.get("exchange", "")).upper() == reviewed_share.get("exchange")
                and str(row.canonical_field) == reviewed_share.get("canonical_field")
                and str(row.accession_number) == reviewed_share.get("accession_number")
                and str(row.semantic_grade) == "HYPOTHESIS_PROXY"
                and float(row.value) == float(reviewed_share.get("value"))
            )
            if status not in allowed and not narrow_proxy_exception:
                raise ValueError(
                    f"SEC_PANEL_IDENTITY_NOT_USABLE:{usage}:{security_id}:{status}"
                )

    def load_panels(
        self,
        bindings: Sequence[tuple[str, str]],
        start: str,
        end: str,
        mode: str = "HISTORICAL_DIAGNOSTIC",
        *,
        proxy_bindings: Sequence[tuple[str, str]] = (),
    ) -> dict[str, pd.DataFrame]:
        if mode not in {"HISTORICAL_DIAGNOSTIC", "FORWARD_OBSERVED"}:
            raise ValueError(f"SEC_AVAILABILITY_MODE_INVALID:{mode}")
        requested = list(
            dict.fromkeys((str(field), str(basis)) for field, basis in bindings)
        )
        proxy_pairs = {(str(field), str(basis)) for field, basis in proxy_bindings}
        if not proxy_pairs.issubset(requested):
            raise ValueError("SEC_PROXY_BINDING_NOT_REQUESTED")
        for field_basis in requested:
            if field_basis not in self._binding_usage:
                raise ValueError(
                    f"SEC_FIELD_BASIS_UNKNOWN:{field_basis[0]}:{field_basis[1]}"
                )
        if not requested:
            return {}
        self._verify_pinned_release_unchanged()
        start_time, end_time = pd.Timestamp(start), pd.Timestamp(end)
        if start_time.tzinfo is not None:
            start_time = start_time.tz_convert("UTC").tz_localize(None)
        if end_time.tzinfo is not None:
            end_time = end_time.tz_convert("UTC").tz_localize(None)
        start_time = start_time.normalize()
        end_time = end_time.normalize()
        if end_time < start_time:
            raise ValueError("SEC_PANEL_DATE_RANGE_INVALID")
        calendar = xcals.get_calendar("XNYS")
        session_index = calendar.sessions_in_range(start_time, end_time)
        if session_index.tz is not None:
            session_index = session_index.tz_localize(None)
        session_index = pd.DatetimeIndex(session_index, name="session")
        fields = sorted({field for field, _ in requested})
        bases = sorted({basis for _, basis in requested})
        filters: list[tuple[str, str, Any]] = [
            ("availability_mode", "==", mode),
            ("canonical_field", "in", fields),
            ("reported_basis", "in", bases),
            ("session", ">=", start_time),
            ("session", "<=", end_time),
        ]
        try:
            frame = pd.read_parquet(
                self._panel_path,
                engine="pyarrow",
                columns=_PANEL_COLUMNS,
                filters=filters,
            )
        except (KeyError, ValueError) as exc:
            raise ValueError("SEC_DAILY_PANEL_SCHEMA_INVALID") from exc
        required = set(_PANEL_COLUMNS)
        if not required.issubset(frame.columns):
            raise ValueError("SEC_DAILY_PANEL_SCHEMA_INVALID")
        frame["session"] = pd.to_datetime(frame.session, errors="raise")
        frame["signal_cutoff"] = pd.to_datetime(
            frame.signal_cutoff, utc=True, errors="raise"
        )
        for column in (
            "selected_available_at",
            "historical_available_at",
            "first_seen_at",
            "available_at",
        ):
            frame[column] = _as_utc_series(
                frame[column], f"SEC_DAILY_PANEL_TIME_INVALID:{column}"
            )
        if frame[_PANEL_COLUMNS].isna().drop(
            columns=["definition_hash"], errors="ignore"
        ).any().any():
            raise ValueError("SEC_DAILY_PANEL_REQUIRED_VALUE_MISSING")
        for column in (
            "security_id",
            "issuer_cik",
            "canonical_field",
            "reported_basis",
            "unit",
            "accession_number",
            "source_fact_id",
            "semantic_grade",
            "mapping_version",
        ):
            if frame[column].astype(str).str.strip().eq("").any():
                raise ValueError(f"SEC_DAILY_PANEL_REQUIRED_VALUE_MISSING:{column}")
        if (
            frame.available_at.lt(frame.historical_available_at)
            | frame.available_at.lt(frame.first_seen_at)
        ).any():
            raise ValueError("SEC_DAILY_PANEL_AVAILABLE_AT_INVARIANT")
        expected_availability = (
            frame.historical_available_at
            if mode == "HISTORICAL_DIAGNOSTIC"
            else frame.available_at
        )
        if not frame.selected_available_at.eq(expected_availability).all():
            raise ValueError("SEC_DAILY_PANEL_SELECTED_AVAILABILITY_MISMATCH")
        if frame.selected_available_at.gt(frame.signal_cutoff).any():
            raise ValueError("SEC_DAILY_PANEL_LOOKAHEAD")
        if not frame.session.eq(frame.session.dt.normalize()).all():
            raise ValueError("SEC_DAILY_PANEL_SESSION_NOT_NORMALIZED")
        try:
            frame["value"] = pd.to_numeric(frame.value, errors="raise")
        except (TypeError, ValueError) as exc:
            raise ValueError("SEC_DAILY_PANEL_VALUE_NOT_NUMERIC") from exc
        frame = frame[
            frame.availability_mode.eq(mode)
            & frame.session.between(start_time, end_time)
        ]
        requested_set = set(requested)
        frame = frame[
            [
                (str(field), str(basis)) in requested_set
                for field, basis in zip(
                    frame.canonical_field, frame.reported_basis, strict=True
                )
            ]
        ]
        approved_grades = {"SOURCE_LINKED_DIRECT", "SOURCE_LINKED_DERIVED", "HYPOTHESIS_PROXY"}
        unknown_grades = set(frame.semantic_grade).difference(approved_grades)
        if unknown_grades:
            raise ValueError("SEC_PANEL_SEMANTIC_GRADE_UNKNOWN:" + ",".join(sorted(unknown_grades)))
        frame = frame.loc[
            frame.semantic_grade.ne("HYPOTHESIS_PROXY")
            | pd.Series(
                [(str(field), str(basis)) in proxy_pairs
                 for field, basis in zip(frame.canonical_field, frame.reported_basis, strict=True)],
                index=frame.index,
            )
        ]
        self._validate_identity_rows(frame)

        output: dict[str, pd.DataFrame] = {}
        for field, basis in requested:
            selected = frame[
                frame.canonical_field.eq(field) & frame.reported_basis.eq(basis)
            ]
            if selected.duplicated(["session", "security_id"]).any():
                raise ValueError(f"SEC_PANEL_DUPLICATE_VALUE:{field}:{basis}")
            matrix = selected.pivot(
                index="session", columns="security_id", values="value"
            ).sort_index().reindex(session_index)
            matrix.index.name = "session"
            matrix.columns.name = "security_id"
            output[f"{field}__{basis}"] = matrix
        return output
