"""Fail-closed validation gates for the SEC PIT data product."""

from __future__ import annotations

import json
import hashlib
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .availability import historical_available_at
from .mapping import load_mapping_catalog
from .submissions import build_accession_index


REQUIRED_MANUAL_CIKS = (
    "0000320193",  # AAPL
    "0000789019",  # MSFT
    "0000019617",  # JPM
    "0000034088",  # XOM
    "0000104169",  # WMT
)
REQUIRED_MANUAL_FIELDS = (
    "total_assets",
    "revenue",
    "net_income",
    "operating_cash_flow",
    "shares_outstanding_sec",
)
_KNOWN_AVAILABILITY_MODES = {"HISTORICAL_DIAGNOSTIC", "FORWARD_OBSERVED"}
_SHA256 = re.compile(r"[0-9a-f]{64}")
_ELIGIBLE_IDENTITY_STATUSES = {"VERIFIED_SINGLE_CLASS", "VERIFIED_ENTITY_LEVEL_ONLY"}


def identity_coverage(
    universe: pd.DataFrame,
    identity: pd.DataFrame,
    versions: pd.DataFrame,
    panel: pd.DataFrame,
) -> dict[str, int]:
    """Count requested, eligible, excluded and observed identities separately."""

    if "security_id" not in universe or not {"security_id", "mapping_status"}.issubset(identity.columns):
        raise ValueError("SEC_IDENTITY_SECURITY_ID_OR_STATUS_MISSING")
    requested_ids = universe.security_id.dropna().astype(str)
    identity_ids = identity.security_id.dropna().astype(str)
    if (
        len(requested_ids) != len(universe)
        or requested_ids.nunique() != len(universe)
        or len(identity_ids) != len(identity)
        or identity_ids.nunique() != len(identity)
        or set(identity_ids) != set(requested_ids)
        or identity.mapping_status.fillna("").astype(str).str.strip().eq("").any()
    ):
        raise ValueError("SEC_SCALE_IDENTITY_UNIVERSE_MISMATCH")
    eligible_ids = set(
        identity.loc[
            identity.mapping_status.isin(_ELIGIBLE_IDENTITY_STATUSES), "security_id"
        ].astype(str)
    )
    version_ids = set(versions.security_id.dropna().astype(str))
    panel_ids = set(panel.security_id.dropna().astype(str))
    if not panel_ids or panel_ids != version_ids or not panel_ids.issubset(eligible_ids):
        raise ValueError("SEC_SCALE_PROCESSED_IDENTITY_INELIGIBLE")
    return {
        "requested_security_count": int(len(requested_ids)),
        "completed_security_count": int(len(identity_ids)),
        "eligible_security_count": int(len(eligible_ids)),
        "excluded_security_count": int(len(identity_ids) - len(eligible_ids)),
        "processed_security_count": int(len(panel_ids)),
    }


def validate_fixture_contracts(
    versions: pd.DataFrame, panel: pd.DataFrame
) -> dict[str, Any]:
    """Check cross-artifact causality and lineage before release assembly."""

    version_required = {
        "source_fact_id",
        "raw_source_hash",
        "historical_available_at",
        "first_seen_at",
        "available_at",
        "mapping_status",
        "canonical_field",
        "reported_basis",
        "issuer_cik",
        "security_id",
        "accession_number",
        "value",
        "unit",
    }
    panel_required = {
        "source_fact_id",
        "signal_cutoff",
        "selected_available_at",
        "availability_mode",
        "canonical_field",
        "reported_basis",
        "issuer_cik",
        "security_id",
        "value",
        "unit",
        "accession_number",
    }
    if not version_required.issubset(versions.columns):
        raise ValueError("VALIDATION_VERSION_COLUMNS_MISSING")
    if not panel_required.issubset(panel.columns):
        raise ValueError("VALIDATION_PANEL_COLUMNS_MISSING")

    source = versions.copy()
    visible = panel.copy()
    for column in ("historical_available_at", "first_seen_at", "available_at"):
        source[column] = pd.to_datetime(source[column], utc=True, errors="coerce")
    visible["signal_cutoff"] = pd.to_datetime(
        visible.signal_cutoff, utc=True, errors="coerce"
    )
    visible["selected_available_at"] = pd.to_datetime(
        visible.selected_available_at, utc=True, errors="coerce"
    )
    version_ids = set(source.source_fact_id.dropna().astype(str))
    panel_ids = set(visible.source_fact_id.dropna().astype(str))
    mapped = source.mapping_status.eq("MAPPED")
    numeric_values = pd.to_numeric(visible.value, errors="coerce")
    source_numeric_values = pd.to_numeric(source.value, errors="coerce")
    lineage_keys = [
        "source_fact_id",
        "security_id",
        "canonical_field",
        "reported_basis",
    ]
    source_lineage_keys_unique = bool(
        source.source_fact_id.notna().all()
        and source.source_fact_id.astype(str).str.strip().ne("").all()
        and not source.duplicated(lineage_keys).any()
    )
    version_metadata = [
        "historical_available_at",
        "available_at",
        "issuer_cik",
        "accession_number",
        "value",
        "unit",
    ]
    source_for_join = source[[*lineage_keys, *version_metadata]].rename(
        columns={column: f"{column}_version" for column in version_metadata}
    )
    joined = visible.merge(source_for_join, on=lineage_keys, how="left")
    lineage_resolves = bool(
        source_lineage_keys_unique
        and len(joined) == len(visible)
        and joined.historical_available_at_version.notna().all()
    )
    if lineage_resolves:
        metadata_equal = pd.Series(True, index=joined.index)
        for column in (
            "issuer_cik",
            "accession_number",
            "unit",
        ):
            left = joined[column].astype("string").fillna("")
            right = joined[f"{column}_version"].astype("string").fillna("")
            if column == "issuer_cik":
                left = left.str.zfill(10)
                right = right.str.zfill(10)
            metadata_equal &= left.eq(right)
        panel_numbers = pd.to_numeric(joined.value, errors="coerce")
        version_numbers = pd.to_numeric(joined.value_version, errors="coerce")
        values_equal = pd.Series(
            np.isclose(panel_numbers, version_numbers, rtol=0, atol=1e-12),
            index=joined.index,
        )
        expected_selected = pd.Series(pd.NaT, index=joined.index, dtype="datetime64[ns, UTC]")
        historical_rows = joined.availability_mode.eq("HISTORICAL_DIAGNOSTIC")
        forward_rows = joined.availability_mode.eq("FORWARD_OBSERVED")
        expected_selected.loc[historical_rows] = joined.loc[
            historical_rows, "historical_available_at_version"
        ]
        expected_selected.loc[forward_rows] = joined.loc[
            forward_rows, "available_at_version"
        ]
        selected_matches_source = joined.selected_available_at.eq(expected_selected)
    else:
        metadata_equal = pd.Series(False, index=joined.index)
        values_equal = pd.Series(False, index=joined.index)
        selected_matches_source = pd.Series(False, index=joined.index)

    checks = {
        "versions_nonempty": not source.empty,
        "panel_nonempty": not visible.empty,
        "version_times_parse": bool(
            source.historical_available_at.notna().all()
            and source.first_seen_at.notna().all()
            and source.available_at.notna().all()
        ),
        "panel_times_parse": bool(
            visible.signal_cutoff.notna().all()
            and visible.selected_available_at.notna().all()
        ),
        "no_future_values": bool(
            (visible.selected_available_at <= visible.signal_cutoff).all()
        ),
        "forward_never_precedes_historical": bool(
            (
                source.available_at.ge(source.historical_available_at)
                & source.available_at.ge(source.first_seen_at)
            ).all()
        ),
        "version_lineage_keys_unique": source_lineage_keys_unique,
        "panel_lineage_resolves": panel_ids <= version_ids and lineage_resolves,
        "panel_lineage_metadata_matches": bool(metadata_equal.all()),
        "panel_values_match_versions": bool(values_equal.all()),
        "panel_selected_availability_matches_source": bool(
            selected_matches_source.all()
        ),
        "panel_lineage_complete": bool(
            visible.source_fact_id.notna().all()
            and visible.accession_number.fillna("").astype(str).str.strip().ne("").all()
        ),
        "version_lineage_complete": bool(
            source.source_fact_id.notna().all()
            and source.accession_number.fillna("").astype(str).str.strip().ne("").all()
        ),
        "raw_hashes_present": bool(
            source.raw_source_hash.fillna("").astype(str).str.fullmatch(r"[0-9a-f]{64}").all()
        ),
        "mapped_fields_named": bool(
            source.loc[mapped, "canonical_field"].notna().all()
            and source.loc[mapped, "canonical_field"].astype(str).str.strip().ne("").all()
        ),
        "availability_modes_known": set(visible.availability_mode) <= _KNOWN_AVAILABILITY_MODES,
        "values_are_finite": bool(np.isfinite(numeric_values).all()),
        "version_values_are_finite": bool(np.isfinite(source_numeric_values).all()),
        "units_present": bool(visible.unit.fillna("").astype(str).str.strip().ne("").all()),
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "version_count": int(len(source)),
        "panel_record_count": int(len(visible)),
    }


def _same_number(left: Any, right: Any) -> bool:
    try:
        return bool(np.isclose(float(left), float(right), rtol=0, atol=1e-9))
    except (TypeError, ValueError):
        return False


def _markdown_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "No reconciliation rows."
    columns = [str(column) for column in frame.columns]

    def cell(value: Any) -> str:
        if value is None or pd.isna(value):
            return ""
        return str(value).replace("|", "\\|").replace("\n", " ")

    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    lines.extend(
        "| " + " | ".join(cell(row[column]) for column in frame.columns) + " |"
        for _, row in frame.iterrows()
    )
    return "\n".join(lines)


def _raw_inventory(
    raw_root: Path, ledger: Path, required_ciks: set[str]
) -> tuple[
    dict[tuple[str, str], dict],
    dict[str, pd.DataFrame],
    dict[tuple[str, str, str], dict],
]:
    """Read only hash-verified SEC objects; a ledger line alone is not evidence."""

    if not raw_root.is_dir() or not ledger.is_file():
        raise ValueError("MANUAL_RAW_SOURCE_MISSING")
    companyfacts: dict[tuple[str, str], dict] = {}
    submissions: dict[str, dict[str, Any]] = {}
    verified_sources: dict[tuple[str, str, str], dict] = {}
    for line_number, line in enumerate(ledger.read_text(encoding="utf-8").splitlines(), 1):
        try:
            artifact = json.loads(line)
            cik = str(artifact["cik"]).zfill(10)
            if cik not in required_ciks:
                continue
            digest = artifact["sha256"]
            relative = Path(artifact["object_path"])
            if (
                not isinstance(digest, str)
                or not _SHA256.fullmatch(digest)
                or relative.is_absolute()
                or ".." in relative.parts
            ):
                raise ValueError("invalid ledger path or digest")
            object_path = raw_root / relative
            if object_path.is_symlink() or not object_path.is_file():
                raise ValueError("raw object missing")
            contents = object_path.read_bytes()
            if (
                hashlib.sha256(contents).hexdigest() != digest
                or len(contents) != int(artifact["size_bytes"])
            ):
                raise ValueError("raw object hash or size mismatch")
            payload = json.loads(contents)
            source_type = str(artifact["source_type"])
            source_url = str(artifact["source_url"])
            if int(artifact["http_status"]) != 200:
                raise ValueError("raw SEC request was not successful")
            if source_type == "companyfacts" and source_url != (
                f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
            ):
                raise ValueError("raw CompanyFacts URL is not official")
            if source_type == "submissions" and source_url != (
                f"https://data.sec.gov/submissions/CIK{cik}.json"
            ):
                raise ValueError("raw Submissions URL is not official")
            if source_type in {"companyfacts", "submissions"} and str(
                payload.get("cik", "")
            ).zfill(10) != cik:
                raise ValueError("raw object issuer mismatch")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"MANUAL_RAW_OBJECT_INVALID:{line_number}") from exc
        verified_sources[(cik, source_url, digest)] = payload
        if source_type == "companyfacts":
            companyfacts[(cik, digest)] = payload
        elif source_type == "submissions":
            submissions.setdefault(cik, {})["main"] = payload
        elif source_type == "submissions_file":
            submissions.setdefault(cik, {}).setdefault("older", []).append(payload)

    accession_indexes: dict[str, pd.DataFrame] = {}
    for cik, pages in submissions.items():
        if "main" not in pages:
            raise ValueError(f"MANUAL_RAW_SUBMISSIONS_MAIN_MISSING:{cik}")
        accession_indexes[cik] = build_accession_index(
            pages["main"], pages.get("older", [])
        )
    return companyfacts, accession_indexes, verified_sources


def _unmapped_field_reason(
    *,
    expected: Mapping[str, str],
    versions: pd.DataFrame,
    companyfacts: Mapping[tuple[str, str], dict],
    mapping_catalog: pd.DataFrame,
) -> str:
    cik = expected["issuer_cik"]
    field = expected["canonical_field"]
    digest = expected["expected_raw_source_hash"]
    payload = companyfacts.get((cik, digest))
    if payload is None:
        return "UNMAPPED_RAW_OBJECT_NOT_VERIFIED"
    if not versions.loc[
        versions.issuer_cik.astype(str).str.zfill(10).eq(cik)
        & versions.canonical_field.eq(field)
    ].empty:
        return "UNMAPPED_CONTRADICTED_BY_PROCESSED_FIELD"
    bindings = mapping_catalog[mapping_catalog.canonical_field.eq(field)]
    if bindings.empty:
        return "UNMAPPED_FIELD_NOT_IN_CANONICAL_CATALOG"
    present = [
        f"{row.taxonomy}:{row.tag}"
        for row in bindings.itertuples(index=False)
        if row.tag in payload.get("facts", {}).get(row.taxonomy, {})
    ]
    if present:
        return "UNMAPPED_APPROVED_TAG_PRESENT:" + ",".join(sorted(present))
    return "NO_APPROVED_CANONICAL_TAG_IN_VERIFIED_COMPANYFACTS"


def _raw_fact_match(
    *,
    expected: Mapping[str, str],
    processed: pd.Series,
    companyfacts: Mapping[tuple[str, str], dict],
    accession_indexes: Mapping[str, pd.DataFrame],
) -> tuple[bool, str]:
    cik = expected["issuer_cik"]
    payload = companyfacts.get((cik, expected["expected_raw_source_hash"]))
    if payload is None:
        return False, "RAW_COMPANYFACTS_OBJECT_NOT_VERIFIED"
    namespace = str(processed.get("xbrl_namespace") or "")
    tag = expected["xbrl_tag"]
    observations = (
        payload.get("facts", {})
        .get(namespace, {})
        .get(tag, {})
        .get("units", {})
        .get(expected["unit"], [])
    )
    matched_observations = [
        (index, observation)
        for index, observation in enumerate(observations)
        if str(observation.get("accn", "")) == expected["accession_number"]
        and str(observation.get("end", "")) == expected["period_end"]
        and _same_number(observation.get("val"), expected["expected_value"])
    ]
    if not matched_observations:
        return False, "RAW_FACT_ACCESSION_TAG_UNIT_PERIOD_VALUE_MISMATCH"

    source_id = expected["expected_source_fact_id"]
    source_identity_matches = False
    basis_matches = False
    for source_index, observation in matched_observations:
        dimensions = observation.get("dimensions")
        dimensions_signature = (
            hashlib.sha256(
                json.dumps(dimensions, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
            ).hexdigest()
            if dimensions else None
        )
        source_identity = {
            "namespace": namespace,
            "tag": tag,
            "unit": expected["unit"],
            "accession": expected["accession_number"],
            "start": observation.get("start"),
            "end": observation.get("end"),
            "value": observation.get("val"),
            "frame": observation.get("frame"),
            "dimensions": dimensions_signature,
            "source_index": source_index,
        }
        computed_id = hashlib.sha256(
            json.dumps(source_identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
        ).hexdigest()
        if computed_id == source_id:
            source_identity_matches = True
            basis = expected["expected_reported_basis"]
            start = observation.get("start")
            if not start:
                basis_matches = basis in {"RAW_REPORTED", "INSTANT"}
            else:
                duration = (
                    pd.Timestamp(observation["end"]) - pd.Timestamp(start)
                ).days + 1
                basis_matches = (
                    basis == "RAW_REPORTED"
                    or basis == "ANNUAL"
                    and observation.get("fp") == "FY"
                    and 300 <= duration <= 400
                    or basis == "QUARTERLY_DISCRETE"
                    and 45 <= duration <= 120
                )
            break
    if not source_identity_matches:
        return False, "RAW_SOURCE_FACT_ID_MISMATCH"
    if not basis_matches:
        return False, "RAW_FACT_BASIS_NOT_DIRECTLY_SUPPORTED"

    index = accession_indexes.get(cik)
    if index is None:
        return False, "RAW_SUBMISSIONS_ACCESSION_MISSING"
    accession = index[index.accession_number.eq(expected["accession_number"])]
    if len(accession) != 1:
        return False, "RAW_SUBMISSIONS_ACCESSION_NOT_UNIQUE"
    metadata = accession.iloc[0]
    if metadata.acceptance_status == "UNRESOLVED_ACCEPTANCE_CONFLICT":
        return False, "RAW_ACCEPTANCE_CONFLICT"
    filed = str(metadata.filed_date or matched_observations[0][1].get("filed") or "")
    if not filed:
        return False, "RAW_FILED_DATE_MISSING"
    actual_time, _ = historical_available_at(
        metadata.acceptance_datetime, filed, "XNYS", "16:00"
    )
    if actual_time != pd.Timestamp(expected["expected_historical_available_at"]):
        return False, "RAW_ACCEPTANCE_PLUS_BUFFER_MISMATCH"
    return True, "MATCH"


def validate_manual_issuers(
    release_inputs: Mapping[str, Path],
    issuer_ciks: Sequence[str] = REQUIRED_MANUAL_CIKS,
) -> tuple[pd.DataFrame, str]:
    """Reconcile reviewed observations to verified raw SEC facts and timestamps."""

    versions = pd.read_parquet(release_inputs["fundamental_versions"])
    supplemental_versions = release_inputs.get("supplemental_fundamental_versions")
    if supplemental_versions is not None:
        supplemental = (
            supplemental_versions.copy()
            if isinstance(supplemental_versions, pd.DataFrame)
            else pd.read_parquet(supplemental_versions)
        )
        versions = pd.concat([versions, supplemental], ignore_index=True, sort=False)
    expected = pd.read_csv(
        release_inputs["manual_expected"], dtype=str, keep_default_na=False
    )
    required = {
        "issuer_cik",
        "canonical_field",
        "accession_number",
        "xbrl_tag",
        "unit",
        "expected_value",
        "period_end",
        "expected_historical_available_at",
        "expected_reported_basis",
        "expected_source_fact_id",
        "expected_raw_source_hash",
    }
    if not required.issubset(expected.columns):
        raise ValueError("MANUAL_EXPECTED_SCHEMA_INVALID")

    normalized_ciks = tuple(str(cik).zfill(10) for cik in issuer_ciks)
    expected = expected.copy()
    expected["issuer_cik"] = expected.issuer_cik.astype(str).str.zfill(10)
    if "review_status" not in expected:
        expected["review_status"] = "REVIEWED"
    if "review_reason" not in expected:
        expected["review_reason"] = ""
    if "review_evidence_url" not in expected:
        expected["review_evidence_url"] = ""
    if "review_evidence_hash" not in expected:
        expected["review_evidence_hash"] = ""
    allowed_review_statuses = {"REVIEWED", "PENDING", "UNMAPPED", "BLOCKED"}
    unknown_statuses = sorted(set(expected.review_status) - allowed_review_statuses)
    if unknown_statuses:
        raise ValueError("MANUAL_REVIEW_STATUS_INVALID:" + ",".join(unknown_statuses))
    raw_root = release_inputs.get("raw_root")
    raw_manifest = release_inputs.get("raw_manifest")
    if raw_root is None or raw_manifest is None:
        raise ValueError("MANUAL_RAW_SOURCE_REQUIRED")
    evidence_ciks = set(
        expected.loc[
            expected.review_status.isin({"REVIEWED", "UNMAPPED"})
            | (
                expected.review_status.eq("BLOCKED")
                & expected.review_evidence_url.str.startswith("https://data.sec.gov/")
            ), "issuer_cik"
        ]
    )
    companyfacts, accession_indexes, verified_sources = _raw_inventory(
        Path(raw_root), Path(raw_manifest), evidence_ciks
    )
    supplemental_root = release_inputs.get("supplemental_raw_root")
    if supplemental_root is not None:
        extra_companyfacts, extra_accessions, extra_sources = _raw_inventory(
            Path(supplemental_root), Path(supplemental_root) / "download_ledger.jsonl",
            evidence_ciks,
        )
        companyfacts.update(extra_companyfacts)
        accession_indexes.update(extra_accessions)
        verified_sources.update(extra_sources)
    mapping_catalog = load_mapping_catalog(
        release_inputs.get(
            "mapping_catalog",
            Path(__file__).resolve().parents[2] / "mappings" / "canonical_fields.csv",
        )
    )
    ledger_hashes = {digest for _, digest in companyfacts}
    rows: list[dict[str, Any]] = []
    identity_columns = [
        "accession_number",
        "xbrl_tag",
        "unit",
        "expected_value",
        "period_end",
        "expected_historical_available_at",
        "expected_reported_basis",
        "expected_source_fact_id",
        "expected_raw_source_hash",
    ]
    for expected_row in expected.to_dict("records"):
        cik = expected_row["issuer_cik"]
        review_status = expected_row["review_status"]
        if review_status == "PENDING":
            rows.append({
                **expected_row, "status": "BLOCKED", "reason": "EXPECTATION_PENDING_REVIEW"
            })
            continue
        if review_status == "BLOCKED":
            reason = str(expected_row["review_reason"]).strip()
            evidence_url = str(expected_row["review_evidence_url"]).strip()
            evidence_hash = str(expected_row["review_evidence_hash"]).strip()
            if not reason or not evidence_url.startswith(("https://www.sec.gov/", "https://data.sec.gov/")):
                rows.append({
                    **expected_row, "status": "FAIL", "reason": "BLOCK_EVIDENCE_INCOMPLETE"
                })
            elif evidence_url.startswith("https://data.sec.gov/"):
                payload = verified_sources.get((cik, evidence_url, evidence_hash))
                if not _SHA256.fullmatch(evidence_hash) or payload is None:
                    rows.append({
                        **expected_row, "status": "FAIL", "reason": "BLOCK_RAW_EVIDENCE_NOT_VERIFIED"
                    })
                elif reason == "SHARE_CLASS_IDENTITY_REQUIRED" and (
                    evidence_url != f"https://data.sec.gov/submissions/CIK{cik}.json"
                    or not isinstance(payload.get("tickers"), list)
                    or len(set(payload["tickers"])) < 2
                ):
                    rows.append({
                        **expected_row, "status": "FAIL", "reason": "BLOCK_SHARE_CLASS_EVIDENCE_MISMATCH"
                    })
                else:
                    rows.append({
                        **expected_row, "status": "BLOCKED", "reason": reason
                    })
            else:
                if reason == "SHARE_CLASS_IDENTITY_REQUIRED":
                    rows.append({
                        **expected_row, "status": "FAIL", "reason": "BLOCK_SHARE_CLASS_EVIDENCE_MISMATCH"
                    })
                    continue
                rows.append({
                    **expected_row, "status": "BLOCKED", "reason": reason
                })
            continue
        if review_status == "UNMAPPED":
            digest = expected_row["expected_raw_source_hash"]
            if not _SHA256.fullmatch(digest):
                rows.append({
                    **expected_row, "status": "FAIL", "reason": "UNMAPPED_RAW_HASH_MISSING"
                })
            else:
                reason = _unmapped_field_reason(
                    expected=expected_row,
                    versions=versions,
                    companyfacts=companyfacts,
                    mapping_catalog=mapping_catalog,
                )
                rows.append({
                    **expected_row,
                    "status": "UNMAPPED" if reason == "NO_APPROVED_CANONICAL_TAG_IN_VERIFIED_COMPANYFACTS" else "FAIL",
                    "reason": reason,
                })
            continue
        if any(not str(expected_row[name]).strip() for name in identity_columns):
            rows.append({**expected_row, "status": "FAIL", "reason": "EXPECTATION_INCOMPLETE"})
            continue
        if expected_row["expected_raw_source_hash"] not in ledger_hashes:
            rows.append(
                {
                    **expected_row,
                    "status": "FAIL",
                    "reason": "EXPECTED_RAW_HASH_NOT_IN_VERIFIED_LEDGER",
                }
            )
            continue
        matches = versions[
            versions.issuer_cik.astype(str).str.zfill(10).eq(cik)
            & versions.canonical_field.eq(expected_row["canonical_field"])
            & versions.accession_number.eq(expected_row["accession_number"])
            & versions.xbrl_tag.eq(expected_row["xbrl_tag"])
            & versions.unit.eq(expected_row["unit"])
            & versions.period_end.astype(str).eq(expected_row["period_end"])
            & versions.reported_basis.eq(expected_row["expected_reported_basis"])
        ]
        status = "FAIL"
        reason = "FACT_NOT_FOUND"
        if len(matches) == 1:
            actual = matches.iloc[0]
            value_ok = _same_number(actual.value, expected_row["expected_value"])
            time_ok = pd.Timestamp(actual.historical_available_at) == pd.Timestamp(
                expected_row["expected_historical_available_at"]
            )
            lineage_ok = (
                str(actual.source_fact_id) == expected_row["expected_source_fact_id"]
                and str(actual.raw_source_hash) == expected_row["expected_raw_source_hash"]
            )
            if value_ok and time_ok and lineage_ok:
                raw_ok, reason = _raw_fact_match(
                    expected=expected_row,
                    processed=actual,
                    companyfacts=companyfacts,
                    accession_indexes=accession_indexes,
                )
                status = "PASS" if raw_ok else "FAIL"
            else:
                reason = "VALUE_TIME_OR_LINEAGE_MISMATCH"
        elif len(matches) > 1:
            reason = "FACT_MATCH_NOT_UNIQUE"
        rows.append({**expected_row, "status": status, "reason": reason})

    report = pd.DataFrame(rows)
    present = set(expected.issuer_cik)
    missing_issuers = sorted(set(normalized_ciks) - present)
    missing_bindings = sorted(
        f"{cik}:{field}"
        for cik in normalized_ciks
        for field in REQUIRED_MANUAL_FIELDS
        if expected[
            expected.issuer_cik.eq(cik) & expected.canonical_field.eq(field)
        ].empty
    )
    missing_rows = [
        {
            "issuer_cik": cik,
            "canonical_field": "",
            "status": "FAIL",
            "reason": "REQUIRED_ISSUER_MISSING",
        }
        for cik in missing_issuers
    ]
    missing_rows.extend(
        {
            "issuer_cik": binding.split(":", 1)[0],
            "canonical_field": binding.split(":", 1)[1],
            "status": "FAIL",
            "reason": "REQUIRED_FIELD_CHECK_MISSING",
        }
        for binding in missing_bindings
    )
    if missing_rows:
        report = pd.concat([report, pd.DataFrame(missing_rows)], ignore_index=True, sort=False)
    if (
        not missing_issuers
        and not missing_bindings
        and not report.empty
        and report.status.eq("PASS").all()
    ):
        overall = "PASS"
    elif report.status.isin({"BLOCKED", "UNMAPPED"}).any() and not report.status.eq("FAIL").any():
        overall = "BLOCKED"
    else:
        overall = "FAIL"
    markdown = "# SEC Manual Issuer Validation\n\n"
    markdown += f"Status: **{overall}**\n\n"
    if missing_issuers:
        markdown += "Missing required issuers: " + ", ".join(missing_issuers) + "\n\n"
    if missing_bindings:
        markdown += "Missing required field checks: " + ", ".join(missing_bindings) + "\n\n"
    markdown += _markdown_table(report)
    return report, markdown
