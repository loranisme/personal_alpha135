#!/usr/bin/env python3
"""Run finite offline validation gates over prepared SEC PIT artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from sec_edgar_pipeline.validation import (
    REQUIRED_MANUAL_CIKS,
    validate_fixture_contracts,
    validate_manual_issuers,
    identity_coverage,
)
from sec_edgar_pipeline.release import sha256_path
from sec_edgar_pipeline.succession import (
    DEFAULT_REVIEW, build_xom_predecessor_manual_versions,
    verify_xom_successor_bridge,
)
from sec_edgar_pipeline.processing import _REVIEWED_SHARE_FACTS


_FROZEN_MANUAL_TICKERS = {
    "AAPL": "0000320193",
    "MSFT": "0000789019",
    "JPM": "0000019617",
    "XOM": "0000034088",
    "WMT": "0000104169",
}
_XOM_SUCCESSION_EVIDENCE = {
    "predecessor_cik": "0000034088",
    "successor_cik": "0002115436",
    "effective_date": "2026-07-01",
    "sec_filing_url": "https://www.sec.gov/Archives/edgar/data/2115436/000119312526291990/d71068d8k12b.htm",
    "review_status": "REQUIRES_EFFECTIVE_DATED_IDENTITY_REVIEW",
}


def _manual_identity_evidence(
    raw_root: Path, reviewed_succession: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = raw_root / "resolved_universe_ciks.csv"
    if not path.is_file():
        return {"status": "BLOCKED", "reason": "RESOLVED_UNIVERSE_CIKS_MISSING", "transitions": []}
    resolved = pd.read_csv(path, dtype=str, keep_default_na=False)
    if not {"ticker", "issuer_cik"}.issubset(resolved.columns):
        return {"status": "BLOCKED", "reason": "RESOLVED_UNIVERSE_CIK_SCHEMA_INVALID", "transitions": []}
    transitions = []
    reviewed_transitions = []
    for ticker, frozen_cik in _FROZEN_MANUAL_TICKERS.items():
        found = sorted({
            str(value).zfill(10)
            for value in resolved.loc[
                resolved.ticker.astype(str).str.upper().eq(ticker), "issuer_cik"
            ]
            if str(value).strip()
        })
        if found != [frozen_cik]:
            if (ticker == "XOM" and reviewed_succession is not None
                    and frozen_cik == reviewed_succession["predecessor_cik"]
                    and found == [reviewed_succession["successor_cik"]]):
                reviewed_transitions.append({
                    "ticker": ticker, "predecessor_cik": frozen_cik,
                    "successor_cik": found[0],
                    "effective_date": reviewed_succession["effective_at"].date().isoformat(),
                    "review_hash": reviewed_succession["review_hash"],
                })
                continue
            transitions.append({
                "ticker": ticker,
                "frozen_manual_cik": frozen_cik,
                "resolved_current_ciks": found,
                "reason": "FROZEN_MANUAL_CIK_NOT_CURRENTLY_RESOLVED",
                "succession_evidence": (
                    _XOM_SUCCESSION_EVIDENCE
                    if ticker == "XOM"
                    and frozen_cik == _XOM_SUCCESSION_EVIDENCE["predecessor_cik"]
                    and found == [_XOM_SUCCESSION_EVIDENCE["successor_cik"]]
                    else None
                ),
            })
    return {
        "status": "PASS" if not transitions else "BLOCKED",
        "reason": "MATCH" if not transitions else "IDENTITY_TRANSITION_REQUIRES_REVIEW",
        "transitions": transitions,
        "reviewed_transitions": reviewed_transitions,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve(default_root: Path, explicit: Path | None, name: str) -> Path:
    path = explicit if explicit is not None else default_root / "processed" / name
    if not path.exists():
        raise ValueError(f"VALIDATION_INPUT_MISSING:{path}")
    return path


def _process_gate(
    raw_root: Path, succession_predecessor_root: Path | None = None,
    succession_filing_root: Path | None = None,
) -> dict[str, Any]:
    summary_path = raw_root / "processed" / "process_summary.json"
    if not summary_path.is_file() or summary_path.is_symlink():
        return {"status": "FAIL", "reason": "PROCESS_SUMMARY_MISSING"}
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        output_hashes = summary["output_hashes"]
        expected_outputs = {
            name: sha256_path(raw_root / "processed" / name)
            for name in ("fundamental_versions.parquet", "daily_pit_fundamental_panel.parquet",
                         "cik_security_map.csv")
        }
        checks = {
            "processed_outputs_match_summary": all(
                output_hashes.get(name) == digest for name, digest in expected_outputs.items()
            ),
            "raw_manifest_matches_processing": summary.get("raw_manifest_hash") == sha256_path(
                raw_root / "download_ledger.jsonl"
            ),
            "resolved_universe_matches_processing": summary.get("resolved_universe_hash") == sha256_path(
                raw_root / "resolved_universe_ciks.csv"
            ),
            "mapping_rule_hash_recorded": isinstance(summary.get("canonical_mapping_hash"), str)
                and len(summary["canonical_mapping_hash"]) == 64,
            "derived_rule_hash_recorded": isinstance(summary.get("derived_definitions_hash"), str)
                and len(summary["derived_definitions_hash"]) == 64,
            "reviewed_share_facts_hash_matches": summary.get("reviewed_share_facts_hash") == _sha256(
                _REVIEWED_SHARE_FACTS
            ),
            "succession_rule_hash_matches": (
                summary.get("succession_review_hash") is None
                or summary.get("succession_review_hash") == _sha256(DEFAULT_REVIEW)
            ),
            "succession_evidence_hashes_recorded": (
                summary.get("succession_review_hash") is None
                or all(isinstance(summary.get(name), str) and len(summary[name]) == 64
                       for name in ("succession_filing_sha256", "succession_predecessor_ledger_hash",
                                    "succession_filing_ledger_hash"))
            ),
        }
        if summary.get("succession_review_hash") is not None:
            if succession_predecessor_root is None or succession_filing_root is None:
                checks["succession_raw_evidence_reverified"] = False
            else:
                reviewed = verify_xom_successor_bridge(
                    predecessor_root=succession_predecessor_root,
                    successor_root=raw_root,
                    filing_root=succession_filing_root,
                    as_of=pd.Timestamp(summary["as_of"]),
                )
                checks["succession_raw_evidence_reverified"] = all(
                    summary.get(name) == reviewed[review_name]
                    for name, review_name in (
                        ("succession_review_hash", "review_hash"),
                        ("succession_filing_sha256", "sec_filing_sha256"),
                        ("succession_predecessor_ledger_hash", "predecessor_ledger_hash"),
                        ("succession_filing_ledger_hash", "filing_ledger_hash"),
                    )
                )
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
        return {"status": "FAIL", "reason": "PROCESS_EVIDENCE_INVALID:" + type(exc).__name__}
    return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks,
            "process_summary_sha256": _sha256(summary_path)}


def _raw_gate(raw_root: Path, universe_csv: Path) -> dict[str, Any]:
    ledger = raw_root / "download_ledger.jsonl"
    summary_path = raw_root / "download_summary.json"
    if not ledger.is_file() or not summary_path.is_file():
        return {"status": "FAIL", "reason": "RAW_DOWNLOAD_EVIDENCE_MISSING"}
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    universe = pd.read_csv(universe_csv, dtype=str)
    if "security_id" not in universe.columns:
        return {"status": "FAIL", "reason": "UNIVERSE_SECURITY_ID_MISSING"}
    resolved_path = raw_root / "resolved_universe_ciks.csv"
    unresolved_path = raw_root / "unresolved_universe_ciks.csv"
    resolved = (
        pd.read_csv(resolved_path, dtype=str)
        if resolved_path.is_file()
        else pd.DataFrame()
    )
    unresolved = (
        pd.read_csv(unresolved_path, dtype=str)
        if unresolved_path.is_file() and unresolved_path.stat().st_size > 0
        else pd.DataFrame()
    )
    requested = int(summary.get("requested_cik_count", -1))
    completed = int(summary.get("completed_cik_count", -1))
    declared_hash = str(summary.get("raw_manifest_hash", ""))
    actual_hash = _sha256(ledger)
    object_integrity = True
    receipt_times_verified = True
    ledger_ciks: set[str] = set()
    ledger_types_by_cik: dict[str, set[str]] = {}
    try:
        for line in ledger.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("receipt_time_verified") is not True:
                receipt_times_verified = False
            digest = str(row["sha256"])
            relative = Path(str(row["object_path"]))
            if relative.is_absolute() or ".." in relative.parts:
                object_integrity = False
                continue
            object_path = raw_root / relative
            if (
                not object_path.is_file()
                or object_path.is_symlink()
                or _sha256(object_path) != digest
                or object_path.stat().st_size != int(row["size_bytes"])
            ):
                object_integrity = False
            if row.get("source_type") == "companyfacts":
                ledger_ciks.add(str(row.get("cik", "")).zfill(10))
            cik = str(row.get("cik", "")).zfill(10)
            ledger_types_by_cik.setdefault(cik, set()).add(
                str(row.get("source_type", ""))
            )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        object_integrity = False
        receipt_times_verified = False
    resolved_security_ids = (
        set(resolved.security_id.astype(str))
        if "security_id" in resolved.columns
        else set()
    )
    universe_security_ids = set(universe.security_id.astype(str))
    resolved_ciks = (
        set(resolved.issuer_cik.astype(str).str.zfill(10))
        if "issuer_cik" in resolved.columns
        else set()
    )
    checks = {
        "universe_has_500_rows": len(universe) == 500,
        "universe_has_500_unique_securities": (
            universe.security_id.notna().all()
            and universe.security_id.astype(str).str.strip().ne("").all()
            and universe.security_id.nunique() == 500
        ),
        "all_universe_securities_resolved": (
            resolved_security_ids == universe_security_ids and unresolved.empty
        ),
        "requested_issuers_are_bounded": 0 < requested <= len(universe),
        "download_complete": completed == requested,
        "downloaded_ciks_match_resolved_ciks": ledger_ciks == resolved_ciks,
        "each_cik_has_companyfacts_and_submissions": all(
            {"companyfacts", "submissions"}.issubset(
                ledger_types_by_cik.get(cik, set())
            )
            for cik in resolved_ciks
        ),
        "raw_manifest_hash_matches": declared_hash == actual_hash,
        "raw_objects_verify": object_integrity,
        "each_response_has_verified_receipt_time": receipt_times_verified,
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "universe_security_count": int(len(universe)),
        "requested_issuer_count": requested,
        "completed_issuer_count": completed,
        "raw_manifest_hash": actual_hash,
    }


def run_validation(
    *,
    raw_root: Path,
    universe_csv: Path,
    output: Path,
    versions_path: Path | None = None,
    panel_path: Path | None = None,
    manual_expected: Path,
    succession_predecessor_root: Path | None = None,
    succession_filing_root: Path | None = None,
) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("VALIDATION_OUTPUT_DIRECTORY_NOT_EMPTY")
    versions_path = _resolve(raw_root, versions_path, "fundamental_versions.parquet")
    panel_path = _resolve(raw_root, panel_path, "daily_pit_fundamental_panel.parquet")
    versions = pd.read_parquet(versions_path)
    panel = pd.read_parquet(panel_path)
    fixture = validate_fixture_contracts(versions, panel)
    reviewed_succession = None
    supplemental_versions = None
    if succession_predecessor_root is not None and succession_filing_root is not None:
        reviewed_succession = verify_xom_successor_bridge(
            predecessor_root=succession_predecessor_root,
            successor_root=raw_root,
            filing_root=succession_filing_root,
            as_of=pd.Timestamp(json.loads(
                (raw_root / "processed" / "process_summary.json").read_text(encoding="utf-8")
            )["as_of"]),
        )
        supplemental_versions = build_xom_predecessor_manual_versions(
            predecessor_root=succession_predecessor_root,
            successor_root=raw_root,
            filing_root=succession_filing_root,
            mapping_path=Path(__file__).resolve().parents[1] / "mappings" / "canonical_fields.csv",
            as_of=pd.Timestamp(json.loads(
                (raw_root / "processed" / "process_summary.json").read_text(encoding="utf-8")
            )["as_of"]),
        )
    try:
        manual, manual_markdown = validate_manual_issuers(
            {
                "fundamental_versions": versions_path,
                "manual_expected": manual_expected,
                "raw_manifest": raw_root / "download_ledger.jsonl",
                "raw_root": raw_root,
                "supplemental_fundamental_versions": supplemental_versions,
                "supplemental_raw_root": succession_predecessor_root,
            },
            REQUIRED_MANUAL_CIKS,
        )
    except (OSError, ValueError, KeyError) as exc:
        manual = pd.DataFrame([{
            "issuer_cik": "",
            "canonical_field": "",
            "status": "FAIL",
            "reason": "MANUAL_RAW_RECONCILIATION_ERROR",
            "detail": str(exc),
        }])
        manual_markdown = (
            "# SEC Manual Issuer Validation\n\n"
            f"Status: **FAIL**\n\nRaw reconciliation failed: {exc}\n"
        )
    manual_identity = _manual_identity_evidence(raw_root, reviewed_succession)
    if not manual.empty and manual.status.eq("PASS").all() and manual_identity["status"] == "PASS":
        manual_status = "PASS"
    elif manual.status.eq("FAIL").any():
        manual_status = "FAIL"
    else:
        manual_status = "BLOCKED"
    raw = _raw_gate(raw_root, universe_csv)
    processing = _process_gate(raw_root, succession_predecessor_root, succession_filing_root)
    identity_path = raw_root / "processed" / "cik_security_map.csv"
    try:
        identity = pd.read_csv(identity_path, dtype=str)
        universe = pd.read_csv(universe_csv, dtype=str)
        identity_counts = identity_coverage(universe, identity, versions, panel)
        identity_gate = {"status": "PASS", **identity_counts}
    except (OSError, ValueError, KeyError) as exc:
        identity_gate = {"status": "FAIL", "reason": str(exc)}
    if fixture["status"] == manual_status == raw["status"] == processing["status"] == identity_gate["status"] == "PASS":
        overall = "PASS"
    elif fixture["status"] == raw["status"] == processing["status"] == identity_gate["status"] == "PASS" and manual_status == "BLOCKED":
        overall = "BLOCKED"
    else:
        overall = "FAIL"
    report = {
        "validation_status": overall,
        "fixture_status": fixture["status"],
        "manual_status": manual_status,
        "manual_identity": manual_identity,
        "raw_status": raw["status"],
        "processing_status": processing["status"],
        "processing": processing,
        "identity_status": identity_gate["status"],
        "identity_coverage": identity_gate,
        "fixture": fixture,
        "raw": raw,
        "versions_sha256": sha256_path(versions_path),
        "panel_sha256": sha256_path(panel_path),
        "universe_snapshot_sha256": sha256_path(universe_csv),
        "raw_manifest_sha256": raw.get("raw_manifest_hash"),
        "manual_expected_sha256": _sha256(manual_expected),
        "identity_map_sha256": sha256_path(identity_path) if identity_path.is_file() else None,
        "process_summary_sha256": processing.get("process_summary_sha256"),
        "historical_pit_universe_verified": False,
        "survivorship_bias": True,
        "selection_evidence": "DEVELOPMENT_EXPOSED",
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "validation_report.json").write_text(
        json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    manual.to_csv(output / "manual_reconciliation.csv", index=False)
    markdown = "# SEC PIT Dataset Validation\n\n"
    markdown += f"Status: **{overall}**\n\n"
    markdown += "This is a current-universe development dataset. Historical PIT universe is not verified; survivorship bias remains. No Alpha-effectiveness claim is made.\n\n"
    markdown += "## Deterministic fixture and artifact checks\n\n"
    for name, passed in fixture["checks"].items():
        markdown += f"- {'PASS' if passed else 'FAIL'}: `{name}`\n"
    markdown += "\n## Raw scale gate\n\n"
    for name, passed in raw.get("checks", {}).items():
        markdown += f"- {'PASS' if passed else 'FAIL'}: `{name}`\n"
    markdown += "\n## Processing provenance\n\n"
    for name, passed in processing.get("checks", {}).items():
        markdown += f"- {'PASS' if passed else 'FAIL'}: `{name}`\n"
    markdown += "\n" + manual_markdown + "\n"
    markdown += "\n## Identity coverage\n\n"
    markdown += f"Status: **{identity_gate['status']}**\n\n"
    markdown += f"{json.dumps(identity_gate, sort_keys=True)}\n"
    markdown += "\n## Frozen manual issuer identity\n\n"
    markdown += f"Status: **{manual_identity['status']}**; {manual_identity['reason']}\n\n"
    for transition in manual_identity.get("reviewed_transitions", []):
        markdown += (
            f"- {transition['ticker']}: reviewed CIK transition "
            f"{transition['predecessor_cik']} -> {transition['successor_cik']} "
            f"effective {transition['effective_date']}; review hash "
            f"`{transition['review_hash']}`\n"
        )
    for transition in manual_identity["transitions"]:
        markdown += (
            f"- {transition['ticker']}: frozen CIK {transition['frozen_manual_cik']}, "
            f"currently resolved {transition['resolved_current_ciks']}; "
            f"{transition['reason']}\n"
        )
        if transition["succession_evidence"] is not None:
            evidence = transition["succession_evidence"]
            markdown += (
                f"  SEC succession filing: {evidence['sec_filing_url']}; "
                f"effective date {evidence['effective_date']}; "
                f"{evidence['review_status']}\n"
            )
    (output / "validation_report.md").write_text(markdown, encoding="utf-8")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--universe-csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--versions", type=Path)
    parser.add_argument("--panel", type=Path)
    parser.add_argument("--succession-predecessor-root", type=Path)
    parser.add_argument("--succession-filing-root", type=Path)
    parser.add_argument(
        "--manual-expected",
        type=Path,
        default=Path(__file__).parents[1]
        / "tests"
        / "fixtures"
        / "sec"
        / "manual_reconciliation_expected.csv",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_validation(
        raw_root=args.raw_root,
        universe_csv=args.universe_csv,
        output=args.output,
        versions_path=args.versions,
        panel_path=args.panel,
        manual_expected=args.manual_expected,
        succession_predecessor_root=args.succession_predecessor_root,
        succession_filing_root=args.succession_filing_root,
    )
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["validation_status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
