#!/usr/bin/env python3
"""Record a raw-only five-issuer manual gate preflight without claiming release PASS."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

import pandas as pd

from sec_edgar_pipeline.validation import (
    REQUIRED_MANUAL_CIKS,
    REQUIRED_MANUAL_FIELDS,
    validate_manual_issuers,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _jpm_submissions_evidence(raw_root: Path, digest: str) -> dict:
    url = "https://data.sec.gov/submissions/CIK0000019617.json"
    for line in (raw_root / "download_ledger.jsonl").read_text(encoding="utf-8").splitlines():
        artifact = json.loads(line)
        if artifact.get("source_url") != url or artifact.get("sha256") != digest:
            continue
        relative = Path(str(artifact["object_path"]))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("PREFLIGHT_RAW_PATH_INVALID")
        source = raw_root / relative
        if not source.is_file() or source.is_symlink() or _sha256(source) != digest:
            raise ValueError("PREFLIGHT_JPM_SUBMISSIONS_HASH_MISMATCH")
        payload = json.loads(source.read_text(encoding="utf-8"))
        return {
            "source_field": "tickers",
            "source_url": url,
            "raw_source_hash": digest,
            "reported_tickers": payload.get("tickers", []),
        }
    raise ValueError("PREFLIGHT_JPM_SUBMISSIONS_EVIDENCE_MISSING")


def run_preflight(raw_root: Path, manual_expected: Path, output: Path) -> dict:
    """Inspect immutable raw evidence; processed-value review is intentionally absent."""

    with tempfile.TemporaryDirectory(prefix="sec-manual-preflight-") as temporary:
        versions_path = Path(temporary) / "empty_versions.parquet"
        pd.DataFrame({
            "issuer_cik": pd.Series(dtype=str),
            "canonical_field": pd.Series(dtype=str),
        }).to_parquet(versions_path, index=False)
        rows, _ = validate_manual_issuers(
            {
                "fundamental_versions": versions_path,
                "manual_expected": manual_expected,
                "raw_root": raw_root,
                "raw_manifest": raw_root / "download_ledger.jsonl",
            },
            REQUIRED_MANUAL_CIKS,
        )
    if len(rows) != len(REQUIRED_MANUAL_CIKS) * len(REQUIRED_MANUAL_FIELDS):
        raise ValueError("PREFLIGHT_MANUAL_CHECK_COUNT_INVALID")
    jpm_shares = rows.loc[
        rows.issuer_cik.eq("0000019617")
        & rows.canonical_field.eq("shares_outstanding_sec")
    ]
    if len(jpm_shares) != 1:
        raise ValueError("PREFLIGHT_JPM_SHARES_CHECK_MISSING")
    share_row = jpm_shares.iloc[0]
    jpm_evidence = _jpm_submissions_evidence(
        raw_root, str(share_row.get("review_evidence_hash", ""))
    )
    counts = {str(key): int(value) for key, value in rows.status.value_counts().sort_index().items()}
    review_counts = {
        str(key): int(value)
        for key, value in rows.review_status.value_counts().sort_index().items()
    }
    result = {
        "status": "FAIL" if counts.get("FAIL", 0) else "BLOCKED",
        "scope": "RAW_ONLY_PREFLIGHT",
        "release_ready": False,
        "processed_reconciliation_performed": False,
        "requested_manual_issuer_count": len(REQUIRED_MANUAL_CIKS),
        "requested_manual_field_count": len(REQUIRED_MANUAL_FIELDS),
        "requested_manual_check_count": len(REQUIRED_MANUAL_CIKS) * len(REQUIRED_MANUAL_FIELDS),
        "observed_status_counts": counts,
        "review_status_counts": review_counts,
        "raw_manifest_sha256": _sha256(raw_root / "download_ledger.jsonl"),
        "manual_expectations_sha256": _sha256(manual_expected),
        "jpm_share_class_evidence": jpm_evidence,
        "rows": rows.fillna("").to_dict("records"),
        "notes": [
            "PENDING fields require individual accession, value, period and availability review.",
            "JPM revenue is UNMAPPED against the approved canonical tags in the verified CompanyFacts snapshot.",
            "JPM shares_outstanding_sec is BLOCKED because the verified Submissions snapshot lists multiple security tickers; security-class shares need separate identity evidence.",
            "XOM predecessor/successor CIK succession still requires an effective-dated identity bridge.",
            "No raw-only preflight is a release validation PASS.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=output.parent, prefix=f".{output.name}.",
        suffix=".tmp", delete=False,
    ) as handle:
        temporary_output = Path(handle.name)
        json.dump(result, handle, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    os.replace(temporary_output, output)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument(
        "--manual-expected", type=Path,
        default=Path(__file__).parents[1] / "tests" / "fixtures" / "sec"
        / "manual_reconciliation_expected.csv",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = run_preflight(args.raw_root, args.manual_expected, args.output)
    print(json.dumps({
        "status": result["status"], "scope": result["scope"],
        "observed_status_counts": result["observed_status_counts"],
        "review_status_counts": result["review_status_counts"],
        "output": str(args.output),
    }, sort_keys=True))
    return 0 if result["status"] == "BLOCKED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
