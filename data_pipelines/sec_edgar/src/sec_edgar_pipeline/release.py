"""Immutable release assembly for the SEC PIT fundamentals data product."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from .validation import validate_fixture_contracts


OUTPUT_MAP = {
    "fundamental_versions": "fundamental_versions.parquet",
    "daily_pit_fundamental_panel": "daily_pit_fundamental_panel.parquet",
    "canonical_field_catalog": "canonical_field_catalog.csv",
    "derived_field_definitions": "derived_field_definitions.json",
    "cik_security_map": "cik_security_map.csv",
    "validation_report": "validation_report.md",
    "coverage_report": "coverage_report.csv",
    "dataset_readme": "PIT_DATASET_README.md",
}

_SHA256 = re.compile(r"[0-9a-f]{64}")
_SECRET_KEY = re.compile(
    r"(^|_)(api_?key|secret|password|passwd|authorization|bearer|token)($|_)",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(
    r"(?i)(?:api[_-]?key|secret|password|passwd|authorization|bearer|token)"
    r"\s*[:=]\s*[\"']?[^\s,\"']{8,}"
)
_TEXT_OUTPUT_SUFFIXES = {".csv", ".json", ".jsonl", ".md", ".txt"}
_ELIGIBLE_IDENTITY_STATUSES = {"VERIFIED_SINGLE_CLASS", "VERIFIED_ENTITY_LEVEL_ONLY"}


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _directory_entries(path: Path) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for member in sorted(path.rglob("*"), key=lambda item: item.relative_to(path).as_posix()):
        relative = member.relative_to(path).as_posix()
        if member.is_symlink():
            raise ValueError(f"RELEASE_DATASET_SYMLINK:{relative}")
        if member.is_dir():
            continue
        if not member.is_file():
            raise ValueError(f"RELEASE_DATASET_MEMBER_UNSAFE:{relative}")
        entries.append({"path": relative, "sha256": _sha_file(member)})
    if not entries:
        raise ValueError(f"RELEASE_DATASET_EMPTY:{path.name}")
    return entries


def sha256_path(path: Path | str) -> str:
    """Hash a file or partitioned dataset using the consumer tree contract."""

    source = Path(path)
    if source.is_symlink() or (not source.is_file() and not source.is_dir()):
        raise ValueError(f"RELEASE_INPUT_NOT_REGULAR:{source}")
    if source.is_file():
        return _sha_file(source)
    encoded = json.dumps(
        _directory_entries(source),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _copy_path(source: Path, destination: Path) -> None:
    if source.is_symlink() or (not source.is_file() and not source.is_dir()):
        raise ValueError(f"RELEASE_INPUT_NOT_REGULAR:{source}")
    if source.is_file():
        shutil.copyfile(source, destination)
        return
    _directory_entries(source)
    shutil.copytree(source, destination, symlinks=False)


def _read_parquet(path: Path) -> pd.DataFrame:
    try:
        return pd.read_parquet(path, engine="pyarrow")
    except Exception as exc:
        raise ValueError(f"RELEASE_PARQUET_INVALID:{path.name}") from exc


def _validate_evidence_hash(name: str, value: Any) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"RELEASE_EVIDENCE_HASH_INVALID:{name}")
    return value


def _reject_secret_keys(value: Any, path: str = "inputs") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            text = str(key)
            if _SECRET_KEY.search(text):
                raise ValueError(f"RELEASE_SECRET_LIKE_KEY:{path}.{text}")
            _reject_secret_keys(nested, f"{path}.{text}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _reject_secret_keys(nested, f"{path}[{index}]")


def _reject_secret_content(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _TEXT_OUTPUT_SUFFIXES:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"RELEASE_TEXT_OUTPUT_NOT_UTF8:{path.name}") from exc
        if _SECRET_VALUE.search(content):
            raise ValueError(f"RELEASE_SECRET_LIKE_CONTENT:{path.name}")


def _reject_secret_frame(frame: pd.DataFrame, frame_name: str) -> None:
    for column in frame.select_dtypes(include=["object", "string"]).columns:
        values = frame[column].dropna().astype(str)
        if values.str.contains(_SECRET_VALUE, regex=True).any():
            raise ValueError(f"RELEASE_SECRET_LIKE_PARQUET_CONTENT:{frame_name}:{column}")


def _iso_created_at(value: Any) -> str:
    timestamp = pd.Timestamp(value) if value is not None else pd.Timestamp.now(tz="UTC")
    if timestamp.tzinfo is None:
        raise ValueError("RELEASE_CREATED_AT_TIMEZONE_REQUIRED")
    return timestamp.tz_convert("UTC").isoformat()


def _validate_scale_counts(inputs: Mapping[str, Any]) -> None:
    if not bool(inputs.get("scale_release", False)):
        return
    requested = inputs.get("requested_security_count")
    if requested != 500:
        raise ValueError(f"SEC_SCALE_UNIVERSE_COUNT_INVALID:{requested}")
    completed_securities = inputs.get("completed_security_count")
    if completed_securities != requested:
        raise ValueError(
            f"SEC_SCALE_SECURITY_COVERAGE_INCOMPLETE:{completed_securities}:{requested}"
        )
    eligible = inputs.get("eligible_security_count")
    excluded = inputs.get("excluded_security_count")
    processed = inputs.get("processed_security_count")
    if (
        not isinstance(eligible, int)
        or not isinstance(excluded, int)
        or eligible < 1
        or excluded < 0
        or eligible + excluded != requested
    ):
        raise ValueError(f"SEC_SCALE_IDENTITY_PARTITION_INVALID:{eligible}:{excluded}:{requested}")
    if not isinstance(processed, int) or not 1 <= processed <= eligible:
        raise ValueError(f"SEC_SCALE_PROCESSED_COVERAGE_INVALID:{processed}:{eligible}")
    if "requested_issuer_count" in inputs or "completed_issuer_count" in inputs:
        requested_issuers = inputs.get("requested_issuer_count")
        completed_issuers = inputs.get("completed_issuer_count")
        if not isinstance(requested_issuers, int) or requested_issuers < 1:
            raise ValueError(f"SEC_SCALE_ISSUER_COUNT_INVALID:{requested_issuers}")
        if completed_issuers != requested_issuers:
            raise ValueError(
                f"SEC_SCALE_DOWNLOAD_INCOMPLETE:{completed_issuers}:{requested_issuers}"
            )
    else:
        completed = inputs.get("completed_security_count")
        if completed != requested:
            raise ValueError(f"SEC_SCALE_DOWNLOAD_INCOMPLETE:{completed}:{requested}")


def build_sec_release(
    inputs: Mapping[str, Any], output_dir: Path | str, pipeline_commit: str
) -> dict[str, Any]:
    """Copy a frozen staging product into a hash-pinned immutable release."""

    if inputs.get("fixture_status") != "PASS" or inputs.get("manual_status") != "PASS":
        raise ValueError("SEC_SCALE_GATE_NOT_PASSED")
    _validate_scale_counts(inputs)
    if inputs.get("scale_release", False):
        validation_json = inputs.get("validation_report_json")
        if validation_json is None or not Path(validation_json).is_file():
            raise ValueError("RELEASE_VALIDATION_JSON_REQUIRED")
        validation = json.loads(Path(validation_json).read_text(encoding="utf-8"))
        if not isinstance(validation, dict) or any(
            validation.get(name) != "PASS"
            for name in ("validation_status", "fixture_status", "manual_status", "raw_status", "processing_status", "identity_status")
        ) or validation.get("manual_identity", {}).get("status") != "PASS":
            raise ValueError("RELEASE_VALIDATION_STATUS_NOT_PASS")
    if not isinstance(pipeline_commit, str) or not pipeline_commit.strip():
        raise ValueError("PIPELINE_COMMIT_REQUIRED")
    _reject_secret_keys(inputs)

    output = Path(output_dir)
    if output.is_symlink() or (output.exists() and not output.is_dir()):
        raise ValueError("OUTPUT_PATH_NOT_SAFE_DIRECTORY")
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("OUTPUT_DIRECTORY_NOT_EMPTY")
    missing = sorted(set(OUTPUT_MAP) - set(inputs))
    if missing:
        raise ValueError("RELEASE_INPUTS_MISSING:" + ",".join(missing))
    for required in ("raw_manifest", "universe_snapshot", "pit_contract_hash"):
        if required not in inputs:
            raise ValueError(f"RELEASE_INPUTS_MISSING:{required}")

    output.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent)
    )
    staged_output = staging_root / output.name
    staged_output.mkdir()
    try:
        for key, name in OUTPUT_MAP.items():
            _copy_path(Path(inputs[key]), staged_output / name)
        if inputs.get("scale_release", False):
            reviewed_path = inputs.get("reviewed_share_facts")
            if reviewed_path is None:
                raise ValueError("RELEASE_REVIEWED_SHARE_FACTS_REQUIRED")
            if sha256_path(reviewed_path) != inputs.get("reviewed_share_facts_hash"):
                raise ValueError("RELEASE_REVIEWED_SHARE_FACTS_HASH_MISMATCH")
            _copy_path(Path(reviewed_path), staged_output / "reviewed_share_facts.json")
            process_summary_path = inputs.get("process_summary")
            if process_summary_path is None:
                raise ValueError("RELEASE_PROCESS_SUMMARY_REQUIRED")
            if sha256_path(process_summary_path) != inputs.get("process_summary_hash"):
                raise ValueError("RELEASE_PROCESS_SUMMARY_HASH_MISMATCH")
            process_summary = json.loads(Path(process_summary_path).read_text(encoding="utf-8"))
            evidence_bindings = {
                "processing_mapping_hash": "canonical_mapping_hash",
                "processing_derived_hash": "derived_definitions_hash",
                "reviewed_share_facts_hash": "reviewed_share_facts_hash",
                "succession_review_hash": "succession_review_hash",
                "succession_filing_sha256": "succession_filing_sha256",
                "succession_predecessor_ledger_hash": "succession_predecessor_ledger_hash",
                "succession_filing_ledger_hash": "succession_filing_ledger_hash",
            }
            for input_name, summary_name in evidence_bindings.items():
                if inputs.get(input_name) != process_summary.get(summary_name):
                    raise ValueError("RELEASE_PROCESS_SUMMARY_EVIDENCE_MISMATCH:" + input_name)
            _copy_path(Path(process_summary_path), staged_output / "processing_summary.json")

        versions = _read_parquet(staged_output / "fundamental_versions.parquet")
        panel = _read_parquet(staged_output / "daily_pit_fundamental_panel.parquet")
        identity = pd.read_csv(staged_output / "cik_security_map.csv", dtype=str)
        universe = pd.read_csv(inputs["universe_snapshot"], dtype=str)
        if inputs.get("scale_release", False):
            if not {"security_id", "mapping_status"}.issubset(identity.columns) or "security_id" not in universe:
                raise ValueError("SEC_SCALE_IDENTITY_SCHEMA_INVALID")
            requested_ids = universe.security_id.dropna().astype(str)
            identity_ids = identity.security_id.dropna().astype(str)
            if (
                len(requested_ids) != 500
                or requested_ids.nunique() != 500
                or len(identity_ids) != 500
                or identity_ids.nunique() != 500
                or set(requested_ids) != set(identity_ids)
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
            if len(eligible_ids) != inputs["eligible_security_count"]:
                raise ValueError("SEC_SCALE_IDENTITY_PARTITION_INVALID:ACTUAL_ELIGIBLE_COUNT")
            if 500 - len(eligible_ids) != inputs["excluded_security_count"]:
                raise ValueError("SEC_SCALE_IDENTITY_PARTITION_INVALID:ACTUAL_EXCLUDED_COUNT")
            if (
                not panel_ids
                or panel_ids != version_ids
                or not panel_ids.issubset(eligible_ids)
                or len(panel_ids) != inputs["processed_security_count"]
            ):
                raise ValueError("SEC_SCALE_PROCESSED_COVERAGE_INVALID:ACTUAL_IDS")
        for frame_name, frame in (("versions", versions), ("panel", panel)):
            secret_columns = [
                str(column)
                for column in frame.columns
                if _SECRET_KEY.search(str(column))
            ]
            if secret_columns:
                raise ValueError(
                    f"RELEASE_SECRET_LIKE_COLUMN:{frame_name}:"
                    + ",".join(sorted(secret_columns))
                )
            _reject_secret_frame(frame, frame_name)
        fixture_result = validate_fixture_contracts(versions, panel)
        if fixture_result["status"] != "PASS":
            failed = sorted(name for name, passed in fixture_result["checks"].items() if not passed)
            raise ValueError("RELEASE_CAUSAL_CONTRACT_FAILED:" + ",".join(failed))

        _reject_secret_content(staged_output)
        published_files = list(OUTPUT_MAP.values())
        if inputs.get("scale_release", False):
            published_files.extend(["reviewed_share_facts.json", "processing_summary.json"])
        output_hashes = {
            name: sha256_path(staged_output / name) for name in published_files
        }
        raw_manifest_hash = sha256_path(Path(inputs["raw_manifest"]))
        universe_hash = sha256_path(Path(inputs["universe_snapshot"]))
        pit_contract_hash = _validate_evidence_hash(
            "pit_contract_hash", inputs["pit_contract_hash"]
        )
        if inputs.get("scale_release", False):
            for name in ("processing_mapping_hash", "processing_derived_hash", "process_summary_hash", "reviewed_share_facts_hash"):
                _validate_evidence_hash(name, inputs.get(name))
            if inputs.get("succession_review_hash") is not None:
                for name in (
                    "succession_review_hash", "succession_filing_sha256",
                    "succession_predecessor_ledger_hash", "succession_filing_ledger_hash",
                ):
                    _validate_evidence_hash(name, inputs.get(name))
            if validation.get("process_summary_sha256") != inputs["process_summary_hash"]:
                raise ValueError("RELEASE_PROCESS_SUMMARY_HASH_MISMATCH")
            for key, actual in {
                "versions_sha256": sha256_path(Path(inputs["fundamental_versions"])),
                "panel_sha256": sha256_path(Path(inputs["daily_pit_fundamental_panel"])),
                "universe_snapshot_sha256": universe_hash,
                "raw_manifest_sha256": raw_manifest_hash,
                "identity_map_sha256": sha256_path(Path(inputs["cik_security_map"])),
            }.items():
                if validation.get(key) != actual:
                    raise ValueError(f"RELEASE_VALIDATION_EVIDENCE_MISMATCH:{key}")

        sessions = pd.to_datetime(panel.get("session", pd.Series(dtype="datetime64[ns]")))
        manifest = {
            "dataset_version": str(inputs.get("dataset_version", "SEC_PIT_FUNDAMENTALS_V1")),
            "pipeline_commit": pipeline_commit.strip(),
            "created_at": _iso_created_at(inputs.get("created_at")),
            "universe_snapshot_hash": universe_hash,
            "raw_manifest_hash": raw_manifest_hash,
            "canonical_mapping_hash": output_hashes["canonical_field_catalog.csv"],
            # The legacy canonical_mapping_hash names the published field
            # catalog. These additional hashes pin the rules used to compute it.
            "processing_mapping_hash": inputs.get("processing_mapping_hash"),
            "processing_derived_hash": inputs.get("processing_derived_hash"),
            "process_summary_hash": inputs.get("process_summary_hash"),
            "reviewed_share_facts_hash": inputs.get("reviewed_share_facts_hash"),
            "succession_review_hash": inputs.get("succession_review_hash"),
            "succession_filing_sha256": inputs.get("succession_filing_sha256"),
            "succession_predecessor_ledger_hash": inputs.get("succession_predecessor_ledger_hash"),
            "succession_filing_ledger_hash": inputs.get("succession_filing_ledger_hash"),
            "derived_field_catalog_hash": output_hashes["derived_field_definitions.json"],
            "security_mapping_hash": output_hashes["cik_security_map.csv"],
            "pit_contract_hash": pit_contract_hash,
            "date_min": None if sessions.empty else str(sessions.min().date()),
            "date_max": None if sessions.empty else str(sessions.max().date()),
            "security_count": int(panel["security_id"].nunique()) if "security_id" in panel else 0,
            "requested_security_count": (
                int(inputs["requested_security_count"])
                if inputs.get("scale_release", False) else int(len(universe))
            ),
            "eligible_security_count": (
                int(inputs["eligible_security_count"])
                if inputs.get("scale_release", False) else None
            ),
            "excluded_security_count": (
                int(inputs["excluded_security_count"])
                if inputs.get("scale_release", False) else None
            ),
            "processed_security_count": (
                int(inputs["processed_security_count"])
                if inputs.get("scale_release", False) else int(panel["security_id"].nunique())
            ),
            "field_count": int(panel["canonical_field"].nunique()) if "canonical_field" in panel else 0,
            "record_count": int(len(panel)),
            "fact_version_count": int(len(versions)),
            "provider": "SEC_EDGAR_COMPANYFACTS",
            "historical_pit_universe_verified": False,
            "survivorship_bias": True,
            "selection_evidence": "DEVELOPMENT_EXPOSED",
            "validation_status": "PASS",
            "output_files": published_files,
            "output_hashes": output_hashes,
        }
        if not manifest["dataset_version"] or manifest["dataset_version"].lower() == "latest":
            raise ValueError("RELEASE_DATASET_VERSION_NOT_PINNED")
        _reject_secret_keys(manifest, "manifest")
        with (staged_output / "processed_manifest.json").open(
            "x", encoding="utf-8", newline="\n"
        ) as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
        os.replace(staged_output, output)
        return manifest
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
