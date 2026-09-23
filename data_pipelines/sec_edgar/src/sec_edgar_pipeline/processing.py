"""Finite offline orchestration from immutable SEC objects to PIT datasets."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

import exchange_calendars as xcals
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .derived import materialize_derived_fields_with_diagnostics
from .identity import build_cik_security_map
from .mapping import apply_canonical_mapping, load_mapping_catalog
from .normalize import normalize_companyfacts
from .periods import materialize_period_bases
from .pit import MODES, build_daily_pit_panel
from .issuer_seed import normalize_cik
from .raw_store import RawArtifact, read_raw_artifact
from .reviewed_share_facts import load_reviewed_share_facts, retain_reviewed_share_facts
from .submissions import build_accession_index
from .succession import verify_xom_successor_bridge


PIPELINE_VERSION = "SEC_PIT_FUNDAMENTALS_V1"
_ENTITY_LEVEL_STATUSES = {"VERIFIED_SINGLE_CLASS", "VERIFIED_ENTITY_LEVEL_ONLY"}
_US_STATE_OR_DC = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split()
)
PROCESS_OUTPUTS = (
    "fundamental_versions.parquet",
    "daily_pit_fundamental_panel.parquet",
    "cik_security_map.csv",
    "process_summary.json",
)
_IDENTITY_REVIEW_BLOCKS = Path(__file__).resolve().parents[2] / "config" / "identity_review_blocks.json"
_REVIEWED_SHARE_FACTS = Path(__file__).resolve().parents[2] / "config" / "reviewed_share_facts.json"
_VERSION_COLUMNS = (
    "issuer_cik", "security_id", "ticker_at_source", "xbrl_namespace", "xbrl_tag",
    "canonical_field", "mapping_version", "mapping_status", "value", "unit",
    "decimals", "period_start", "period_end", "fact_type", "duration_days",
    "fiscal_year", "fiscal_period", "reported_basis", "form_type",
    "accession_number", "filed_date", "acceptance_datetime", "acceptance_status",
    "historical_available_at", "first_seen_at", "available_at",
    "availability_precision", "amendment_flag", "frame", "dimensions_signature",
    "source_fact_id", "raw_source_hash", "pipeline_version", "mapping_id",
    "mapping_priority", "required_basis", "semantic_grade", "sign_convention",
    "derivation_rule", "input_source_fact_ids", "input_transformations",
    "definition_hash", "input_shares", "input_raw_close", "price_snapshot_hash",
    "price_event_at", "price_provider", "price_field", "price_adjustment",
    "price_timestamp_semantics", "accounting_period_end",
)
_PANEL_COLUMNS = (
    "session", "signal_cutoff", "security_id", "issuer_cik", "canonical_field",
    "reported_basis", "value", "unit", "selected_available_at",
    "historical_available_at", "first_seen_at", "available_at",
    "accession_number", "source_fact_id", "semantic_grade", "mapping_version",
    "definition_hash", "availability_mode",
)
_TIMESTAMP_COLUMNS = {
    "historical_available_at", "first_seen_at", "available_at", "price_event_at",
    "signal_cutoff", "selected_available_at",
}
_FLOAT_COLUMNS = {"value", "duration_days", "mapping_priority", "input_shares", "input_raw_close"}


def _schema_for(columns: tuple[str, ...]) -> pa.Schema:
    fields: list[pa.Field] = []
    for name in columns:
        if name == "session":
            dtype = pa.timestamp("ns")
        elif name in _TIMESTAMP_COLUMNS:
            dtype = pa.timestamp("ns", tz="UTC")
        elif name in _FLOAT_COLUMNS:
            dtype = pa.float64()
        elif name == "amendment_flag":
            dtype = pa.bool_()
        else:
            dtype = pa.string()
        fields.append(pa.field(name, dtype, nullable=True))
    return pa.schema(fields)


_VERSION_SCHEMA = _schema_for(_VERSION_COLUMNS)
_PANEL_SCHEMA = _schema_for(_PANEL_COLUMNS)


def _arrow_table(frame: pd.DataFrame, schema: pa.Schema) -> pa.Table:
    selected = pd.DataFrame(index=frame.index)
    for field in schema:
        name = field.name
        series = frame[name] if name in frame else pd.Series(pd.NA, index=frame.index)
        if pa.types.is_timestamp(field.type):
            values = pd.to_datetime(series, utc=name != "session", errors="coerce")
            if name == "session" and getattr(values.dt, "tz", None) is not None:
                values = values.dt.tz_localize(None)
            selected[name] = values
        elif pa.types.is_floating(field.type):
            selected[name] = pd.to_numeric(series, errors="coerce").astype(float)
        elif pa.types.is_boolean(field.type):
            selected[name] = series.astype("boolean")
        else:
            selected[name] = series.astype("string")
    return pa.Table.from_pandas(
        selected, schema=schema, preserve_index=False, safe=True
    )


def _write_panel_partitions(panel: pd.DataFrame, destination: Path, security_id: str) -> None:
    fingerprint = hashlib.sha256(security_id.encode("utf-8")).hexdigest()[:20]
    for (mode, field), group in panel.groupby(
        ["availability_mode", "canonical_field"], sort=True, dropna=False
    ):
        if mode not in MODES or not str(field).replace("_", "").isalnum():
            raise ValueError(f"PROCESS_PANEL_PARTITION_INVALID:{mode}:{field}")
        directory = destination / f"availability_mode={mode}" / f"canonical_field={field}"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"part-{fingerprint}.parquet"
        if path.exists():
            raise ValueError(f"PROCESS_PANEL_PARTITION_COLLISION:{security_id}:{field}:{mode}")
        table = _arrow_table(group, _PANEL_SCHEMA).drop(
            ["availability_mode", "canonical_field"]
        )
        pq.write_table(table, path, compression="zstd")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_path(path: Path) -> str:
    if path.is_file():
        return _sha256_file(path)
    entries = [
        {
            "path": member.relative_to(path).as_posix(),
            "sha256": _sha256_file(member),
        }
        for member in sorted(path.rglob("*"), key=lambda item: item.relative_to(path).as_posix())
        if member.is_file()
    ]
    if not entries:
        raise ValueError(f"PROCESS_OUTPUT_DIRECTORY_EMPTY:{path.name}")
    return hashlib.sha256(
        json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _as_utc(value: Any, error: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        raise ValueError(error)
    return timestamp.tz_convert("UTC")


def _load_ledger(raw_root: Path, as_of: pd.Timestamp) -> list[RawArtifact]:
    ledger = raw_root / "download_ledger.jsonl"
    if not ledger.is_file() or ledger.is_symlink():
        raise ValueError("PROCESS_RAW_LEDGER_MISSING_OR_UNSAFE")
    artifacts: list[RawArtifact] = []
    for line_number, line in enumerate(ledger.read_text(encoding="utf-8").splitlines(), 1):
        try:
            artifact = RawArtifact(**json.loads(line))
            requested_at = _as_utc(
                artifact.requested_at, f"PROCESS_LEDGER_TIME_INVALID:{line_number}"
            )
            first_seen_at = _as_utc(
                artifact.first_seen_at, f"PROCESS_FIRST_SEEN_INVALID:{line_number}"
            )
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValueError(f"PROCESS_RAW_LEDGER_INVALID_ROW:{line_number}") from exc
        if artifact.http_status != 200:
            raise ValueError(
                f"PROCESS_RAW_HTTP_STATUS_NOT_OK:{artifact.source_type}:{artifact.cik}:{artifact.http_status}"
            )
        # A verified response is first observable when its body is received.
        # The request timestamp may be earlier, and a content-addressed repeat
        # may retain an even earlier first-seen timestamp.
        if requested_at <= as_of and first_seen_at <= as_of:
            read_raw_artifact(raw_root, artifact)
            artifacts.append(artifact)
    if not artifacts:
        raise ValueError("PROCESS_NO_RAW_ARTIFACTS_ASOF")
    return artifacts


def _latest_artifacts(artifacts: list[RawArtifact]) -> list[RawArtifact]:
    latest: dict[tuple[str, str, str], RawArtifact] = {}
    for artifact in artifacts:
        key = (artifact.cik, artifact.source_type, artifact.source_url)
        previous = latest.get(key)
        if previous is None or (
            artifact.requested_at,
            artifact.sha256,
        ) > (previous.requested_at, previous.sha256):
            latest[key] = artifact
    return [latest[key] for key in sorted(latest)]


def _payload(raw_root: Path, artifact: RawArtifact) -> dict[str, Any]:
    try:
        value = json.loads(read_raw_artifact(raw_root, artifact))
    except json.JSONDecodeError as exc:
        raise ValueError(f"PROCESS_RAW_JSON_INVALID:{artifact.sha256}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"PROCESS_RAW_JSON_NOT_OBJECT:{artifact.sha256}")
    return value


def _artifact_inventory(
    raw_root: Path, artifacts: list[RawArtifact], required_ciks: set[str]
) -> tuple[
    dict[str, RawArtifact],
    dict[str, tuple[RawArtifact, dict[str, Any]]],
    dict[str, list[RawArtifact]],
]:
    companyfacts: dict[str, RawArtifact] = {}
    submissions: dict[str, tuple[RawArtifact, dict[str, Any]]] = {}
    older: dict[str, list[RawArtifact]] = defaultdict(list)
    for artifact in artifacts:
        if artifact.cik not in required_ciks:
            continue
        if artifact.source_type == "companyfacts":
            if artifact.cik in companyfacts:
                raise ValueError(f"PROCESS_MULTIPLE_PRIMARY_COMPANYFACTS:{artifact.cik}")
            companyfacts[artifact.cik] = artifact
        elif artifact.source_type == "submissions":
            if artifact.cik in submissions:
                raise ValueError(f"PROCESS_MULTIPLE_PRIMARY_SUBMISSIONS:{artifact.cik}")
            parsed = _payload(raw_root, artifact)
            payload_cik = parsed.get("cik")
            if payload_cik is None or normalize_cik(payload_cik) != artifact.cik:
                raise ValueError(
                    f"PROCESS_RAW_CIK_MISMATCH:{artifact.source_type}:{artifact.cik}:{payload_cik}"
                )
            parsed["_raw_source_hash"] = artifact.sha256
            submissions[artifact.cik] = (artifact, parsed)
        elif artifact.source_type == "submissions_file":
            older[artifact.cik].append(artifact)
    missing = sorted(
        cik
        for cik in required_ciks
        if cik not in companyfacts or cik not in submissions
    )
    if missing:
        raise ValueError("PROCESS_REQUIRED_CIK_ARTIFACTS_MISSING:" + ",".join(missing))
    for cik, (_, payload) in submissions.items():
        referenced = payload.get("filings", {}).get("files", [])
        if not isinstance(referenced, list):
            raise ValueError(f"PROCESS_SUBMISSIONS_HISTORY_INVALID:{cik}")
        required_names = {
            str(row.get("name"))
            for row in referenced
            if isinstance(row, dict) and row.get("name")
        }
        available_names = {
            artifact.source_url.rsplit("/", 1)[-1]
            for artifact in older.get(cik, [])
        }
        missing_files = sorted(required_names - available_names)
        if missing_files:
            raise ValueError(
                f"PROCESS_SUBMISSIONS_HISTORY_MISSING:{cik}:" + ",".join(missing_files)
            )
        older[cik] = [
            artifact
            for artifact in older.get(cik, [])
            if artifact.source_url.rsplit("/", 1)[-1] in required_names
        ]
    return companyfacts, submissions, older


def _load_definitions(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    definitions = payload.get("definitions") if isinstance(payload, dict) else None
    if not isinstance(definitions, dict) or not definitions:
        raise ValueError("PROCESS_DERIVED_DEFINITIONS_INVALID")
    return definitions


def _apply_identity_review_blocks(
    identity: pd.DataFrame, block_path: Path = _IDENTITY_REVIEW_BLOCKS,
    reviewed_succession: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    payload = json.loads(block_path.read_text(encoding="utf-8"))
    blocks = payload.get("blocks") if isinstance(payload, dict) else None
    if payload.get("schema_version") != 1 or not isinstance(blocks, list):
        raise ValueError("PROCESS_IDENTITY_REVIEW_BLOCKS_INVALID")
    frame = identity.copy()
    frame["review_block_reason"] = ""
    frame["review_block_evidence_url"] = ""
    frame["review_block_effective_date"] = ""
    frame["review_block_predecessor_cik"] = ""
    frame["succession_review_hash"] = ""
    frame["succession_filing_sha256"] = ""
    for block in blocks:
        required = {
            "ticker", "issuer_cik", "predecessor_cik", "effective_date",
            "reason", "evidence_url",
        }
        if not isinstance(block, dict) or set(block) != required:
            raise ValueError("PROCESS_IDENTITY_REVIEW_BLOCK_SCHEMA_INVALID")
        if not str(block["evidence_url"]).startswith("https://www.sec.gov/"):
            raise ValueError("PROCESS_IDENTITY_REVIEW_BLOCK_SOURCE_INVALID")
        pd.Timestamp(block["effective_date"])
        cik_column = "resolved_issuer_cik" if "resolved_issuer_cik" in frame else "issuer_cik"
        mask = frame.ticker.astype(str).str.upper().eq(str(block["ticker"]).upper()) & frame[cik_column].astype(str).str.zfill(10).eq(str(block["issuer_cik"]).zfill(10))
        if reviewed_succession is not None and str(block["ticker"]).upper() == "XOM":
            if (str(block["issuer_cik"]).zfill(10), str(block["predecessor_cik"]).zfill(10),
                str(block["effective_date"]), str(block["evidence_url"])) != (
                reviewed_succession["successor_cik"], reviewed_succession["predecessor_cik"],
                reviewed_succession["effective_at"].date().isoformat(),
                reviewed_succession["sec_filing_url"],
            ):
                raise ValueError("PROCESS_SUCCESSION_BLOCK_REVIEW_CONFLICT")
            if mask.sum() != 1 or frame.loc[mask, "mapping_status"].iloc[0] != "VERIFIED_SINGLE_CLASS":
                raise ValueError("PROCESS_SUCCESSION_SUCCESSOR_IDENTITY_NOT_UNIQUE")
            current_start = pd.Timestamp(frame.loc[mask, "effective_from"].iloc[0])
            if current_start.tzinfo is None:
                raise ValueError("PROCESS_SUCCESSION_IDENTITY_WINDOW_TIMEZONE_REQUIRED")
            frame.loc[mask, "effective_from"] = max(
                current_start.tz_convert("UTC"), reviewed_succession["effective_at"],
                reviewed_succession["bridge_first_seen_at"],
            )
            frame.loc[mask, "mapping_source"] = (
                "SEC_SUBMISSIONS_TICKER_EXCHANGE+REVIEWED_8K_SUCCESSION"
            )
            frame.loc[mask, "succession_review_hash"] = reviewed_succession["review_hash"]
            frame.loc[mask, "succession_filing_sha256"] = reviewed_succession["sec_filing_sha256"]
            frame.loc[mask, "review_block_evidence_url"] = reviewed_succession["sec_filing_url"]
            frame.loc[mask, "review_block_effective_date"] = str(block["effective_date"])
            frame.loc[mask, "review_block_predecessor_cik"] = reviewed_succession["predecessor_cik"]
            continue
        frame.loc[mask, "mapping_status"] = "UNRESOLVED"
        frame.loc[mask, "blocking_reasons"] = frame.loc[mask, "blocking_reasons"].astype(str).map(
            lambda current: "|".join(filter(None, [current, str(block["reason"])]))
        )
        frame.loc[mask, "review_block_reason"] = str(block["reason"])
        frame.loc[mask, "review_block_evidence_url"] = str(block["evidence_url"])
        frame.loc[mask, "review_block_effective_date"] = str(block["effective_date"])
        frame.loc[mask, "review_block_predecessor_cik"] = str(block["predecessor_cik"])
    return frame


def _enrich_domicile_from_submissions(
    resolved: pd.DataFrame,
    submissions: Mapping[str, tuple[RawArtifact, dict[str, Any]]],
) -> pd.DataFrame:
    """Classify current legal domicile using exact, hashed SEC issuer evidence."""

    rows: list[dict[str, Any]] = []
    for source in resolved.to_dict("records"):
        row = dict(source)
        cik = str(row["issuer_cik"]).zfill(10)
        artifact, payload = submissions[cik]
        code = str(payload.get("stateOfIncorporation") or "").strip().upper()
        existing = str(row.get("domicile") or "").strip().upper()
        if code in _US_STATE_OR_DC:
            classification = "US_STATE_OR_DC_INCORPORATED"
            if existing and existing not in {"US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"}:
                raise ValueError(
                    f"PROCESS_DOMICILE_SOURCE_CONFLICT:{row['security_id']}:{existing}:{code}"
                )
            row["domicile"] = "US"
        elif code:
            classification = "NON_US_OR_UNKNOWN_INCORPORATION_CODE"
            if existing in {"US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"}:
                raise ValueError(
                    f"PROCESS_DOMICILE_SOURCE_CONFLICT:{row['security_id']}:{existing}:{code}"
                )
            row["domicile"] = existing
        else:
            classification = "MISSING_INCORPORATION_CODE"
            row["domicile"] = existing
        row["domicile_evidence_field"] = "stateOfIncorporation"
        row["domicile_evidence_raw_hash"] = artifact.sha256
        row["incorporation_code"] = code
        row["domicile_classification"] = classification
        rows.append(row)
    return pd.DataFrame(rows)


def _diagnostic_counts(frame: pd.DataFrame) -> dict[str, int]:
    if frame.empty:
        return {}
    return {
        str(key): int(value)
        for key, value in frame.reason_code.value_counts().sort_index().items()
    }


class _ProcessedWriter:
    """Bounded, atomic writer with one row group per processed security."""

    def __init__(self, destination: Path):
        self.destination = destination
        if destination.exists():
            raise FileExistsError("PROCESS_OUTPUT_ALREADY_EXISTS")
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.temporary = Path(
            tempfile.mkdtemp(prefix=".processed-", dir=destination.parent)
        )
        self._versions_writer: pq.ParquetWriter | None = None
        self.version_count = 0
        self.panel_count = 0

    def append(self, versions: pd.DataFrame, panel: pd.DataFrame, security_id: str) -> None:
        if not versions.empty:
            if self._versions_writer is None:
                self._versions_writer = pq.ParquetWriter(
                    self.temporary / "fundamental_versions.parquet",
                    _VERSION_SCHEMA,
                    compression="zstd",
                )
            self._versions_writer.write_table(_arrow_table(versions, _VERSION_SCHEMA))
            self.version_count += len(versions)
        if not panel.empty:
            _write_panel_partitions(
                panel, self.temporary / "daily_pit_fundamental_panel.parquet", security_id
            )
            self.panel_count += len(panel)

    def finalize(self, identity: pd.DataFrame, evidence: dict[str, Any]) -> dict[str, Any]:
        self.close_writer()
        if self.version_count == 0 or self.panel_count == 0:
            raise ValueError("PROCESS_OUTPUT_EMPTY")
        identity_path = self.temporary / "cik_security_map.csv"
        identity.to_csv(identity_path, index=False)
        evidence["output_hashes"] = {
            "fundamental_versions.parquet": _sha256_path(
                self.temporary / "fundamental_versions.parquet"
            ),
            "daily_pit_fundamental_panel.parquet": _sha256_path(
                self.temporary / "daily_pit_fundamental_panel.parquet"
            ),
            "cik_security_map.csv": _sha256_path(identity_path),
        }
        (self.temporary / "process_summary.json").write_text(
            json.dumps(evidence, sort_keys=True, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        os.replace(self.temporary, self.destination)
        return evidence

    def close_writer(self) -> None:
        if self._versions_writer is not None:
            self._versions_writer.close()
            self._versions_writer = None

    def abort(self) -> None:
        self.close_writer()
        shutil.rmtree(self.temporary, ignore_errors=True)


def _process_one_security(
    *,
    row: Any,
    raw_root: Path,
    company_artifact: RawArtifact,
    submission_payload: Mapping[str, Any],
    older_artifacts: list[RawArtifact],
    catalog: pd.DataFrame,
    definitions: Mapping[str, Any],
    sessions: pd.DatetimeIndex,
    reviewed_share_facts: tuple[dict[str, Any], ...],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    cik = str(row.issuer_cik).zfill(10)
    company_payload = _payload(raw_root, company_artifact)
    payload_cik = company_payload.get("cik")
    if payload_cik is None or normalize_cik(payload_cik) != cik:
        raise ValueError(f"PROCESS_RAW_CIK_MISMATCH:companyfacts:{cik}:{payload_cik}")
    older_payloads = [_payload(raw_root, artifact) for artifact in older_artifacts]
    accession_index = build_accession_index(submission_payload, older_payloads)
    normalized = normalize_companyfacts(
        company_payload,
        accession_index,
        row._asdict(),
        company_artifact.sha256,
        pd.Timestamp(company_artifact.first_seen_at),
        PIPELINE_VERSION,
    )
    conflicts = normalized.acceptance_status.eq("UNRESOLVED_ACCEPTANCE_CONFLICT")
    if conflicts.any():
        facts = sorted(normalized.loc[conflicts, "source_fact_id"].astype(str))
        raise ValueError(
            f"PROCESS_ACCEPTANCE_DIAGNOSTICS_UNRESOLVED:{row.security_id}:"
            + ",".join(facts[:10])
        )
    if normalized.empty:
        return pd.DataFrame(), pd.DataFrame(), {
            "normalized_fact_count": 0,
            "mapped_fact_count": 0,
            "deferred_fact_count": 0,
            "period_version_count": 0,
            "derived_value_count": 0,
            "share_class_facts_excluded": 0,
            "deferred_mapping_status_counts": {},
            "derived_diagnostic_counts": {},
        }
    mapped, deferred = apply_canonical_mapping(normalized, catalog)
    mapped_count = len(mapped)
    share_class_blocked_count = 0
    if not mapped.empty and row.mapping_status == "VERIFIED_ENTITY_LEVEL_ONLY":
        mapped, share_class_blocked_count = retain_reviewed_share_facts(
            mapped, row._asdict(), reviewed_share_facts
        )
    if mapped.empty:
        return pd.DataFrame(), pd.DataFrame(), {
            "normalized_fact_count": int(len(normalized)),
            "mapped_fact_count": mapped_count,
            "deferred_fact_count": int(len(deferred)),
            "period_version_count": 0,
            "derived_value_count": 0,
            "share_class_facts_excluded": share_class_blocked_count,
            "deferred_mapping_status_counts": (
                dict(Counter(deferred.mapping_status.astype(str))) if not deferred.empty else {}
            ),
            "derived_diagnostic_counts": {},
        }
    period_versions = materialize_period_bases(mapped)
    derived_result = materialize_derived_fields_with_diagnostics(
        period_versions, None, definitions
    )
    # A rejected derived observation is field-level missingness. The derived
    # engine already omits that value and records the reason; valid direct
    # statement facts from the same issuer remain usable.
    versions = pd.concat(
        [period_versions, derived_result.values], ignore_index=True, sort=False
    ).sort_values(
        ["security_id", "canonical_field", "reported_basis", "period_end", "historical_available_at", "source_fact_id"],
        kind="stable",
        na_position="first",
    ).reset_index(drop=True)
    panels = [
        build_daily_pit_panel(versions, sessions, mode) for mode in sorted(MODES)
    ]
    panel = pd.concat(panels, ignore_index=True, sort=False)
    if not panel.empty:
        effective_from = pd.Timestamp(row.effective_from).tz_convert("UTC")
        effective_to = pd.Timestamp(row.effective_to).tz_convert("UTC") if pd.notna(row.effective_to) else None
        in_window = panel.signal_cutoff.ge(effective_from)
        if effective_to is not None:
            in_window &= panel.signal_cutoff.lt(effective_to)
        panel = panel.loc[in_window].sort_values(
            ["availability_mode", "canonical_field", "reported_basis", "session", "security_id"],
            kind="stable",
        ).reset_index(drop=True)
    counts = {
        "normalized_fact_count": int(len(normalized)),
        "mapped_fact_count": mapped_count,
        "deferred_fact_count": int(len(deferred)),
        "period_version_count": int(len(period_versions)),
        "derived_value_count": int(len(derived_result.values)),
        "share_class_facts_excluded": share_class_blocked_count,
        "deferred_mapping_status_counts": (
            dict(Counter(deferred.mapping_status.astype(str))) if not deferred.empty else {}
        ),
        "derived_diagnostic_counts": _diagnostic_counts(derived_result.diagnostics),
    }
    return versions, panel, counts


def process_sec_raw(
    raw_root: Path | str,
    mapping_path: Path | str,
    derived_definitions_path: Path | str,
    output_dir: Path | str,
    as_of: Any,
    start_date: str = "2019-01-01",
    end_date: str | None = None,
    succession_predecessor_root: Path | str | None = None,
    succession_filing_root: Path | str | None = None,
) -> dict[str, Any]:
    """Build immutable processed artifacts without any network access."""

    root = Path(raw_root)
    cutoff = _as_utc(as_of, "PROCESS_ASOF_TIMEZONE_REQUIRED")
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date) if end_date is not None else cutoff.tz_localize(None).normalize()
    if start.tzinfo is not None or end.tzinfo is not None:
        raise ValueError("PROCESS_SESSION_DATES_MUST_BE_DATE_ONLY")
    if start > end or end > cutoff.tz_localize(None).normalize():
        raise ValueError("PROCESS_DATE_RANGE_INVALID")
    resolved_path = root / "resolved_universe_ciks.csv"
    unresolved_path = root / "unresolved_universe_ciks.csv"
    if not resolved_path.is_file():
        raise ValueError("PROCESS_RESOLVED_UNIVERSE_MISSING")
    resolved = pd.read_csv(resolved_path, dtype=str, keep_default_na=False)
    if resolved.empty:
        raise ValueError("PROCESS_RESOLVED_UNIVERSE_EMPTY")
    if unresolved_path.exists():
        unresolved = pd.read_csv(unresolved_path, dtype=str, keep_default_na=False)
        if not unresolved.empty:
            raise ValueError(f"PROCESS_UNRESOLVED_UNIVERSE:{len(unresolved)}")
    if "issuer_cik" not in resolved.columns:
        raise ValueError("PROCESS_RESOLVED_UNIVERSE_CIK_MISSING")
    resolved["issuer_cik"] = resolved.issuer_cik.astype(str).str.zfill(10)
    required_ciks = set(resolved.issuer_cik)

    ledger_artifacts = _load_ledger(root, cutoff)
    artifacts = _latest_artifacts(ledger_artifacts)
    companyfacts, submissions, older = _artifact_inventory(
        root, artifacts, required_ciks
    )
    resolved = _enrich_domicile_from_submissions(resolved, submissions)
    submission_payloads = {cik: payload for cik, (_, payload) in submissions.items()}
    identity = build_cik_security_map(resolved, submission_payloads, cutoff)
    evidence_columns = [
        "security_id", "domicile_evidence_field", "domicile_evidence_raw_hash",
        "incorporation_code", "domicile_classification",
    ]
    identity = identity.merge(
        resolved[evidence_columns], on="security_id", how="left", validate="one_to_one"
    )
    identity = identity.merge(
        resolved[["security_id", "issuer_cik"]].rename(columns={"issuer_cik": "resolved_issuer_cik"}),
        on="security_id", how="left", validate="one_to_one"
    )
    mismatched = identity[
        identity.issuer_cik.notna()
        & identity.issuer_cik.astype(str).str.zfill(10).ne(
            identity.resolved_issuer_cik.astype(str).str.zfill(10)
        )
    ]
    if not mismatched.empty:
        raise ValueError(
            "PROCESS_ISSUER_RESOLUTION_CONFLICT:"
            + ",".join(sorted(mismatched.security_id.astype(str)))
        )
    if (succession_predecessor_root is None) != (succession_filing_root is None):
        raise ValueError("PROCESS_SUCCESSION_EVIDENCE_ROOTS_INCOMPLETE")
    reviewed_succession = None
    if succession_predecessor_root is not None:
        reviewed_succession = verify_xom_successor_bridge(
            predecessor_root=Path(succession_predecessor_root),
            successor_root=root,
            filing_root=Path(succession_filing_root),
            as_of=cutoff,
        )
    identity = _apply_identity_review_blocks(
        identity, reviewed_succession=reviewed_succession
    )
    eligible = identity[identity.mapping_status.isin(_ENTITY_LEVEL_STATUSES)]
    if eligible.empty:
        status_counts = identity.mapping_status.value_counts().to_dict()
        raise ValueError(
            "PROCESS_NO_USABLE_IDENTITY:" + json.dumps(status_counts, sort_keys=True)
        )

    catalog = load_mapping_catalog(mapping_path)
    definitions = _load_definitions(Path(derived_definitions_path))
    reviewed_share_facts = load_reviewed_share_facts(_REVIEWED_SHARE_FACTS)
    calendar = xcals.get_calendar("XNYS")
    earliest_identity_date = pd.to_datetime(
        eligible.effective_from, utc=True, errors="raise"
    ).min().tz_localize(None).normalize()
    bounded_start = max(start, earliest_identity_date)
    if bounded_start > end:
        raise ValueError("PROCESS_NO_SECURITY_IDENTITY_WINDOW_IN_RANGE")
    sessions = calendar.sessions_in_range(bounded_start, end)
    writer = _ProcessedWriter(Path(output_dir))
    totals: Counter[str] = Counter()
    mapping_status_counts: Counter[str] = Counter()
    derived_diagnostic_counts: Counter[str] = Counter()
    observed_modes: set[str] = set()
    panel_min: pd.Timestamp | None = None
    panel_max: pd.Timestamp | None = None
    processed_security_count = 0
    try:
        for row in eligible.sort_values("security_id", kind="stable").itertuples(index=False):
            cik = str(row.issuer_cik).zfill(10)
            versions, panel, counts = _process_one_security(
                row=row,
                raw_root=root,
                company_artifact=companyfacts[cik],
                submission_payload=submissions[cik][1],
                older_artifacts=older.get(cik, []),
                catalog=catalog,
                definitions=definitions,
                sessions=sessions,
                reviewed_share_facts=reviewed_share_facts,
            )
            for key, value in counts.items():
                if key == "deferred_mapping_status_counts":
                    mapping_status_counts.update(value)
                elif key == "derived_diagnostic_counts":
                    derived_diagnostic_counts.update(value)
                else:
                    totals[key] += int(value)
            if versions.empty or panel.empty:
                continue
            writer.append(versions, panel, str(row.security_id))
            processed_security_count += 1
            observed_modes.update(panel.availability_mode.astype(str))
            first = pd.Timestamp(panel.session.min())
            last = pd.Timestamp(panel.session.max())
            panel_min = first if panel_min is None else min(panel_min, first)
            panel_max = last if panel_max is None else max(panel_max, last)

        if processed_security_count == 0:
            raise ValueError("PROCESS_DAILY_PIT_PANEL_EMPTY")
        evidence = {
            "dataset_version": PIPELINE_VERSION,
            "as_of": cutoff.isoformat(),
            "start_date": start.date().isoformat(),
            "end_date": end.date().isoformat(),
            "raw_manifest_hash": _sha256_file(root / "download_ledger.jsonl"),
            "resolved_universe_hash": _sha256_file(resolved_path),
            "canonical_mapping_hash": _sha256_file(Path(mapping_path)),
            "derived_definitions_hash": _sha256_file(Path(derived_definitions_path)),
            "identity_review_blocks_hash": _sha256_file(_IDENTITY_REVIEW_BLOCKS),
            "reviewed_share_facts_hash": _sha256_file(_REVIEWED_SHARE_FACTS),
            "succession_review_hash": (
                reviewed_succession["review_hash"] if reviewed_succession else None
            ),
            "succession_filing_sha256": (
                reviewed_succession["sec_filing_sha256"] if reviewed_succession else None
            ),
            "succession_predecessor_ledger_hash": (
                reviewed_succession["predecessor_ledger_hash"] if reviewed_succession else None
            ),
            "succession_filing_ledger_hash": (
                reviewed_succession["filing_ledger_hash"] if reviewed_succession else None
            ),
            "selected_raw_artifacts": [
                {
                    "cik": artifact.cik,
                    "source_type": artifact.source_type,
                    "source_url": artifact.source_url,
                    "requested_at": artifact.requested_at,
                    "first_seen_at": artifact.first_seen_at,
                    "sha256": artifact.sha256,
                }
                for artifact in artifacts
                if artifact.cik in required_ciks
            ],
            "requested_security_count": int(len(resolved)),
            "security_count": int(eligible.security_id.nunique()),
            "issuer_count": int(eligible.issuer_cik.nunique()),
            "excluded_security_count": int(len(identity) - len(eligible)),
            "identity_status_counts": {
                str(key): int(value)
                for key, value in identity.mapping_status.value_counts().sort_index().items()
            },
            "domicile_classification_counts": {
                str(key): int(value)
                for key, value in resolved.domicile_classification.value_counts().sort_index().items()
            },
            "excluded_identity_rows": [
                {
                    "security_id": str(row.security_id),
                    "issuer_cik": "" if pd.isna(row.issuer_cik) else str(row.issuer_cik),
                    "mapping_status": str(row.mapping_status),
                    "blocking_reasons": str(row.blocking_reasons),
                }
                for row in identity.loc[
                    ~identity.mapping_status.isin(_ENTITY_LEVEL_STATUSES)
                ].itertuples(index=False)
            ],
            "processed_security_count": processed_security_count,
            "share_class_facts_excluded": totals["share_class_facts_excluded"],
            "normalized_fact_count": totals["normalized_fact_count"],
            "mapped_fact_count": totals["mapped_fact_count"],
            "deferred_fact_count": totals["deferred_fact_count"],
            "deferred_mapping_status_counts": dict(sorted(mapping_status_counts.items())),
            "period_version_count": totals["period_version_count"],
            "derived_value_count": totals["derived_value_count"],
            "derived_diagnostic_counts": dict(sorted(derived_diagnostic_counts.items())),
            "fundamental_version_count": writer.version_count,
            "daily_panel_record_count": writer.panel_count,
            "availability_modes": sorted(observed_modes),
            "historical_pit_universe_verified": False,
            "survivorship_bias": True,
            "selection_evidence": "DEVELOPMENT_EXPOSED",
            "identity_binding_mode": "EFFECTIVE_WINDOW_ONLY",
            "historical_security_identity_available_before_effective_from": False,
            "panel_date_min": str(panel_min.date()) if panel_min is not None else None,
            "panel_date_max": str(panel_max.date()) if panel_max is not None else None,
        }
        result = writer.finalize(identity, evidence)
        return {"status": "PASS", **result}
    except BaseException:
        writer.abort()
        raise


def process_raw_to_processed(
    raw_root: Path | str,
    mapping_path: Path | str,
    derived_definitions_path: Path | str,
    as_of: Any,
    start_date: str = "2019-01-01",
    end_date: str | None = None,
) -> dict[str, Any]:
    """Compatibility entry point that writes to the raw root's processed child."""

    return process_sec_raw(
        raw_root=raw_root,
        mapping_path=mapping_path,
        derived_definitions_path=derived_definitions_path,
        output_dir=Path(raw_root) / "processed",
        as_of=as_of,
        start_date=start_date,
        end_date=end_date,
    )
