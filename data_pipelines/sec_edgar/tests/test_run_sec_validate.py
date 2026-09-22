import importlib.util
import json
import hashlib
from pathlib import Path

import pandas as pd

from sec_edgar_pipeline.release import sha256_path
from sec_edgar_pipeline.raw_store import RawArtifact, store_raw_response


SCRIPT = Path("data_pipelines/sec_edgar/scripts/run_sec_validate.py")
SPEC = importlib.util.spec_from_file_location("run_sec_validate", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_process_gate_rejects_tampered_output(tmp_path):
    processed = tmp_path / "processed"
    processed.mkdir()
    for name in ("fundamental_versions.parquet", "daily_pit_fundamental_panel.parquet",
                 "cik_security_map.csv"):
        (processed / name).write_bytes(name.encode())
    (tmp_path / "download_ledger.jsonl").write_text("raw\n")
    (tmp_path / "resolved_universe_ciks.csv").write_text("security_id,issuer_cik\n")
    summary = {
        "canonical_mapping_hash": "a" * 64,
        "derived_definitions_hash": "b" * 64,
        "raw_manifest_hash": sha256_path(tmp_path / "download_ledger.jsonl"),
        "resolved_universe_hash": sha256_path(tmp_path / "resolved_universe_ciks.csv"),
        "output_hashes": {name: sha256_path(processed / name) for name in (
            "fundamental_versions.parquet", "daily_pit_fundamental_panel.parquet",
            "cik_security_map.csv")},
    }
    (processed / "process_summary.json").write_text(json.dumps(summary))
    result = MODULE._process_gate(tmp_path)
    assert result["status"] == "PASS"
    (processed / "fundamental_versions.parquet").write_bytes(b"tampered")
    result = MODULE._process_gate(tmp_path)
    assert result["status"] == "FAIL"
    assert result["checks"]["processed_outputs_match_summary"] is False


def test_legacy_batch_timestamp_is_not_release_eligible(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    body = b"{}"
    store_raw_response(raw, RawArtifact(
        source_type="companyfacts", cik="0000320193",
        source_url="https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
        requested_at="2026-09-18T00:00:00Z", first_seen_at="2026-09-18T00:00:00Z",
        http_status=200, size_bytes=len(body), sha256=hashlib.sha256(body).hexdigest(),
        object_path="", pipeline_version="1",
    ), body)
    ledger = raw / "download_ledger.jsonl"
    (raw / "download_summary.json").write_text(json.dumps({
        "requested_cik_count": 1, "completed_cik_count": 1,
        "raw_manifest_hash": sha256_path(ledger),
    }))
    pd.DataFrame([{"security_id": "AAPL", "issuer_cik": "0000320193"}]).to_csv(
        raw / "resolved_universe_ciks.csv", index=False)
    pd.DataFrame(columns=["security_id", "issuer_cik"]).to_csv(
        raw / "unresolved_universe_ciks.csv", index=False)
    universe = tmp_path / "universe.csv"
    pd.DataFrame([{"security_id": "AAPL"}]).to_csv(universe, index=False)
    result = MODULE._raw_gate(raw, universe)
    assert result["checks"]["each_response_has_verified_receipt_time"] is False
    assert result["status"] == "FAIL"


def test_frozen_xom_cik_transition_blocks_manual_gate_with_evidence(tmp_path):
    pd.DataFrame(
        [
            {"ticker": "AAPL", "issuer_cik": "0000320193"},
            {"ticker": "MSFT", "issuer_cik": "0000789019"},
            {"ticker": "JPM", "issuer_cik": "0000019617"},
            {"ticker": "XOM", "issuer_cik": "0002115436"},
            {"ticker": "WMT", "issuer_cik": "0000104169"},
        ]
    ).to_csv(tmp_path / "resolved_universe_ciks.csv", index=False)

    result = MODULE._manual_identity_evidence(tmp_path)

    assert result["status"] == "BLOCKED"
    assert len(result["transitions"]) == 1
    transition = result["transitions"][0]
    assert transition["ticker"] == "XOM"
    assert transition["frozen_manual_cik"] == "0000034088"
    assert transition["resolved_current_ciks"] == ["0002115436"]
    assert transition["reason"] == "FROZEN_MANUAL_CIK_NOT_CURRENTLY_RESOLVED"
    assert transition["succession_evidence"]["effective_date"] == "2026-07-01"
    assert transition["succession_evidence"]["review_status"] == (
        "REQUIRES_EFFECTIVE_DATED_IDENTITY_REVIEW"
    )


def test_missing_frozen_cik_does_not_pass_identity_check(tmp_path):
    pd.DataFrame(
        [{"ticker": "AAPL", "issuer_cik": "0000320193"}]
    ).to_csv(tmp_path / "resolved_universe_ciks.csv", index=False)

    result = MODULE._manual_identity_evidence(tmp_path)

    assert result["status"] == "BLOCKED"
    assert {entry["ticker"] for entry in result["transitions"]} == {
        "MSFT", "JPM", "XOM", "WMT"
    }
