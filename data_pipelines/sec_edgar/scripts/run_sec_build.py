#!/usr/bin/env python3
"""Assemble a validated 500-security SEC PIT release from prepared artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from sec_edgar_pipeline.contracts import load_policy
from sec_edgar_pipeline.release import build_sec_release, sha256_path
from sec_edgar_pipeline.validation import identity_coverage as _identity_coverage
from sec_edgar_pipeline.succession import DEFAULT_REVIEW, verify_xom_successor_bridge
from sec_edgar_pipeline.processing import _REVIEWED_SHARE_FACTS


def _resolve(raw_root: Path, explicit: Path | None, name: str) -> Path:
    path = explicit if explicit is not None else raw_root / "processed" / name
    if not path.exists():
        raise ValueError(f"BUILD_INPUT_MISSING:{path}")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_processing_evidence(
    raw_root: Path, mapping: Path, derived_definitions: Path,
    versions: Path, panel: Path, identity: Path, validation: dict,
    succession_predecessor_root: Path | None = None,
    succession_filing_root: Path | None = None,
) -> dict:
    """Bind processed rows to the exact rules and raw snapshot used to create them."""
    summary_path = raw_root / "processed" / "process_summary.json"
    if not summary_path.is_file() or summary_path.is_symlink():
        raise ValueError("PROCESS_SUMMARY_MISSING_OR_UNSAFE")
    if validation.get("process_summary_sha256") != _sha256(summary_path):
        raise ValueError("PROCESS_SUMMARY_VALIDATION_HASH_MISMATCH")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("canonical_mapping_hash") != _sha256(mapping):
        raise ValueError("PROCESS_MAPPING_HASH_MISMATCH")
    if summary.get("derived_definitions_hash") != _sha256(derived_definitions):
        raise ValueError("PROCESS_DERIVED_DEFINITIONS_HASH_MISMATCH")
    if summary.get("reviewed_share_facts_hash") != _sha256(_REVIEWED_SHARE_FACTS):
        raise ValueError("PROCESS_REVIEWED_SHARE_FACTS_HASH_MISMATCH")
    if summary.get("succession_review_hash") is not None and summary["succession_review_hash"] != _sha256(DEFAULT_REVIEW):
        raise ValueError("PROCESS_SUCCESSION_RULE_HASH_MISMATCH")
    if summary.get("succession_review_hash") is not None:
        if succession_predecessor_root is None or succession_filing_root is None:
            raise ValueError("PROCESS_SUCCESSION_RAW_EVIDENCE_REQUIRED")
        reviewed = verify_xom_successor_bridge(
            predecessor_root=succession_predecessor_root, successor_root=raw_root,
            filing_root=succession_filing_root, as_of=pd.Timestamp(summary["as_of"]),
        )
        for key, reviewed_key in (
            ("succession_review_hash", "review_hash"),
            ("succession_filing_sha256", "sec_filing_sha256"),
            ("succession_predecessor_ledger_hash", "predecessor_ledger_hash"),
            ("succession_filing_ledger_hash", "filing_ledger_hash"),
        ):
            if summary.get(key) != reviewed[reviewed_key]:
                raise ValueError("PROCESS_SUCCESSION_EVIDENCE_HASH_MISMATCH:" + key)
    for key, path in (
        ("raw_manifest_hash", raw_root / "download_ledger.jsonl"),
        ("resolved_universe_hash", raw_root / "resolved_universe_ciks.csv"),
    ):
        if summary.get(key) != _sha256(path):
            raise ValueError("PROCESS_INPUT_HASH_MISMATCH:" + key)
    output_hashes = summary.get("output_hashes")
    if not isinstance(output_hashes, dict):
        raise ValueError("PROCESS_OUTPUT_HASHES_MISSING")
    for name, path in (
        ("fundamental_versions.parquet", versions),
        ("daily_pit_fundamental_panel.parquet", panel),
        ("cik_security_map.csv", identity),
    ):
        if output_hashes.get(name) != sha256_path(path):
            raise ValueError("PROCESS_OUTPUT_HASH_MISMATCH:" + name)
    return summary


def _field_catalog(versions: pd.DataFrame, mapping_path: Path) -> pd.DataFrame:
    mapping = pd.read_csv(mapping_path, dtype=str, keep_default_na=False)
    pairs = versions[["canonical_field", "reported_basis"]].dropna().drop_duplicates()
    if pairs.empty:
        raise ValueError("BUILD_CANONICAL_FIELD_CATALOG_EMPTY")
    share_fields = {"shares_outstanding_sec", "book_value_per_share", "market_cap_sec_proxy", "enterprise_value_sec_proxy"}
    pairs = pairs.rename(columns={"reported_basis": "supported_basis"})
    pairs["identity_usage"] = np.where(
        pairs.canonical_field.isin(share_fields), "PER_SHARE", "ENTITY"
    )
    pairs["semantic_grade"] = pairs.canonical_field.map(
        mapping.groupby("canonical_field").semantic_grade.first()
    ).fillna("SOURCE_LINKED_DERIVED")
    return pairs.sort_values(
        ["canonical_field", "supported_basis"], kind="stable"
    ).reset_index(drop=True)


def _coverage(panel: pd.DataFrame) -> pd.DataFrame:
    required = {"availability_mode", "canonical_field", "reported_basis", "security_id", "session", "value"}
    if not required.issubset(panel.columns):
        raise ValueError("BUILD_PANEL_COVERAGE_COLUMNS_MISSING")
    rows = []
    for keys, group in panel.groupby(
        ["availability_mode", "canonical_field", "reported_basis"],
        sort=True,
        dropna=False,
    ):
        values = pd.to_numeric(group.value, errors="coerce")
        rows.append({
            "availability_mode": keys[0],
            "canonical_field": keys[1],
            "reported_basis": keys[2],
            "record_count": int(len(group)),
            "security_count": int(group.security_id.nunique()),
            "session_count": int(group.session.nunique()),
            "finite_fraction": float(np.isfinite(values).mean()),
        })
    return pd.DataFrame(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--universe-csv", type=Path, required=True)
    parser.add_argument("--validation-report", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--derived-definitions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pipeline-commit", required=True)
    parser.add_argument("--versions", type=Path)
    parser.add_argument("--panel", type=Path)
    parser.add_argument("--identity-map", type=Path)
    parser.add_argument("--succession-predecessor-root", type=Path)
    parser.add_argument("--succession-filing-root", type=Path)
    parser.add_argument("--price-snapshot", type=Path)
    parser.add_argument("--include-market-proxies", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    policy = load_policy(args.policy)
    if args.include_market_proxies and args.price_snapshot is None:
        raise ValueError("RAW_CLOSE_SNAPSHOT_REQUIRED_FOR_MARKET_PROXIES")
    if args.price_snapshot is not None and not args.price_snapshot.is_file():
        raise ValueError("PRICE_SNAPSHOT_MISSING")
    if args.include_market_proxies:
        raise ValueError("MARKET_PROXY_MATERIALIZATION_NOT_IMPLEMENTED")
    if args.price_snapshot is not None:
        raise ValueError("UNUSED_PRICE_SNAPSHOT_WITHOUT_MARKET_PROXIES")

    validation = json.loads(args.validation_report.read_text(encoding="utf-8"))
    if any(
        validation.get(name) != "PASS"
        for name in ("validation_status", "fixture_status", "manual_status", "raw_status", "processing_status", "identity_status")
    ) or validation.get("manual_identity", {}).get("status") != "PASS":
        raise ValueError("SEC_SCALE_GATE_NOT_PASSED")
    universe = pd.read_csv(args.universe_csv, dtype=str)
    if len(universe) != 500:
        raise ValueError(f"SEC_SCALE_UNIVERSE_COUNT_INVALID:{len(universe)}")
    if (
        "security_id" not in universe.columns
        or universe.security_id.isna().any()
        or universe.security_id.astype(str).str.strip().eq("").any()
        or universe.security_id.nunique() != 500
    ):
        raise ValueError("SEC_SCALE_UNIVERSE_SECURITY_IDS_INVALID")
    versions_path = _resolve(args.raw_root, args.versions, "fundamental_versions.parquet")
    panel_path = _resolve(args.raw_root, args.panel, "daily_pit_fundamental_panel.parquet")
    identity_path = _resolve(args.raw_root, args.identity_map, "cik_security_map.csv")
    process_summary = _verify_processing_evidence(
        args.raw_root, args.mapping, args.derived_definitions,
        versions_path, panel_path, identity_path, validation,
        args.succession_predecessor_root, args.succession_filing_root,
    )
    versions = pd.read_parquet(versions_path)
    panel = pd.read_parquet(panel_path)
    identity = pd.read_csv(identity_path, dtype=str)
    coverage_counts = _identity_coverage(universe, identity, versions, panel)
    summary = json.loads((args.raw_root / "download_summary.json").read_text(encoding="utf-8"))
    raw_manifest = args.raw_root / "download_ledger.jsonl"
    if _sha256(raw_manifest) != summary.get("raw_manifest_hash"):
        raise ValueError("RAW_MANIFEST_HASH_MISMATCH")
    evidence_bindings = {
        "versions_sha256": sha256_path(versions_path),
        "panel_sha256": sha256_path(panel_path),
        "universe_snapshot_sha256": sha256_path(args.universe_csv),
        "raw_manifest_sha256": sha256_path(raw_manifest),
        "identity_map_sha256": sha256_path(identity_path),
    }
    for name, actual in evidence_bindings.items():
        if validation.get(name) != actual:
            raise ValueError(f"VALIDATION_EVIDENCE_MISMATCH:{name}")

    staging_parent = args.output.parent
    staging_parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".sec-release-staging-", dir=staging_parent))
    try:
        catalog_path = temporary / "canonical_field_catalog.csv"
        _field_catalog(versions, args.mapping).to_csv(catalog_path, index=False)
        coverage_path = temporary / "coverage_report.csv"
        _coverage(panel).to_csv(coverage_path, index=False)
        validation_markdown = args.validation_report.with_suffix(".md")
        if not validation_markdown.is_file():
            raise ValueError("VALIDATION_MARKDOWN_MISSING")
        readme = temporary / "PIT_DATASET_README.md"
        readme.write_text(
            "# SEC_PIT_FUNDAMENTALS_V1\n\n"
            "Official SEC Company Facts and Submissions for the supplied current 500-security development universe.\n\n"
            f"Requested securities: {coverage_counts['requested_security_count']}; "
            f"eligible identity: {coverage_counts['eligible_security_count']}; "
            f"excluded identity: {coverage_counts['excluded_security_count']}; "
            f"processed with mapped PIT panel: {coverage_counts['processed_security_count']}. "
            "Excluded securities are not represented as usable facts.\n\n"
            "`historical_pit_universe_verified=false`, `survivorship_bias=true`, and `selection_evidence=DEVELOPMENT_EXPOSED`. This release makes no Alpha-effectiveness claim. Consumers must pin `processed_manifest.json` and verify all hashes.\n",
            encoding="utf-8",
        )
        inputs = {
            "fixture_status": validation["fixture_status"],
            "manual_status": validation["manual_status"],
            "validation_report_json": args.validation_report,
            "scale_release": True,
            **coverage_counts,
            "requested_issuer_count": int(summary["requested_cik_count"]),
            "completed_issuer_count": int(summary["completed_cik_count"]),
            "dataset_version": "SEC_PIT_FUNDAMENTALS_V1",
            "fundamental_versions": versions_path,
            "daily_pit_fundamental_panel": panel_path,
            "canonical_field_catalog": catalog_path,
            "derived_field_definitions": args.derived_definitions,
            "cik_security_map": identity_path,
            "validation_report": validation_markdown,
            "coverage_report": coverage_path,
            "dataset_readme": readme,
            "raw_manifest": raw_manifest,
            "universe_snapshot": args.universe_csv,
            "pit_contract_hash": _sha256(args.policy),
            "processing_mapping_hash": process_summary["canonical_mapping_hash"],
            "processing_derived_hash": process_summary["derived_definitions_hash"],
            "reviewed_share_facts_hash": process_summary["reviewed_share_facts_hash"],
            "reviewed_share_facts": _REVIEWED_SHARE_FACTS,
            "process_summary_hash": _sha256(args.raw_root / "processed" / "process_summary.json"),
            "process_summary": args.raw_root / "processed" / "process_summary.json",
            "succession_review_hash": process_summary.get("succession_review_hash"),
            "succession_filing_sha256": process_summary.get("succession_filing_sha256"),
            "succession_predecessor_ledger_hash": process_summary.get("succession_predecessor_ledger_hash"),
            "succession_filing_ledger_hash": process_summary.get("succession_filing_ledger_hash"),
        }
        manifest = build_sec_release(inputs, args.output, args.pipeline_commit)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    print(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
