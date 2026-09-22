import importlib.util
import json
from pathlib import Path

import pandas as pd

from sec_edgar_pipeline.validation import REQUIRED_MANUAL_CIKS, REQUIRED_MANUAL_FIELDS


SCRIPT = Path("data_pipelines/sec_edgar/scripts/run_sec_manual_preflight.py")
SPEC = importlib.util.spec_from_file_location("run_sec_manual_preflight", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_raw_only_preflight_never_claims_release_pass(tmp_path, monkeypatch):
    raw_root = tmp_path / "raw"
    raw_root.mkdir()
    (raw_root / "download_ledger.jsonl").write_text("{}\n", encoding="utf-8")
    expected = tmp_path / "expected.csv"
    expected.write_text("manual expectation fixture\n", encoding="utf-8")
    rows = pd.DataFrame([
        {
            "issuer_cik": cik,
            "canonical_field": field,
            "status": (
                "UNMAPPED" if (cik, field) == ("0000019617", "revenue")
                else "BLOCKED" if cik == "0000034088" or (cik, field) == (
                    "0000019617", "shares_outstanding_sec"
                ) else "BLOCKED"
            ),
            "reason": "EXPECTATION_PENDING_REVIEW",
            "review_status": (
                "UNMAPPED" if (cik, field) == ("0000019617", "revenue")
                else "BLOCKED" if cik == "0000034088" or (cik, field) == (
                    "0000019617", "shares_outstanding_sec"
                ) else "PENDING"
            ),
            "review_evidence_hash": "f" * 64 if (cik, field) == (
                "0000019617", "shares_outstanding_sec"
            ) else "",
        }
        for cik in REQUIRED_MANUAL_CIKS
        for field in REQUIRED_MANUAL_FIELDS
    ])
    monkeypatch.setattr(MODULE, "validate_manual_issuers", lambda *args, **kwargs: (rows, ""))
    monkeypatch.setattr(MODULE, "_jpm_submissions_evidence", lambda root, digest: {
        "source_field": "tickers", "raw_source_hash": digest,
        "reported_tickers": ["JPM", "JPM-PC"],
    })

    output = tmp_path / "preflight.json"
    report = MODULE.run_preflight(raw_root, expected, output)

    assert report["status"] == "BLOCKED"
    assert report["scope"] == "RAW_ONLY_PREFLIGHT"
    assert report["release_ready"] is False
    assert report["processed_reconciliation_performed"] is False
    assert report["requested_manual_check_count"] == 25
    assert report["review_status_counts"] == {
        "BLOCKED": 6, "PENDING": 18, "UNMAPPED": 1,
    }
    assert json.loads(output.read_text(encoding="utf-8")) == report
