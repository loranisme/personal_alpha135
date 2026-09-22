import hashlib
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

from sec_edgar_pipeline.release import sha256_path


SCRIPT = Path("data_pipelines/sec_edgar/scripts/run_sec_build.py")
SPEC = importlib.util.spec_from_file_location("run_sec_build", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_market_proxy_request_fails_closed_before_release(tmp_path):
    prices = tmp_path / "prices.parquet"
    prices.write_bytes(b"snapshot")
    with pytest.raises(ValueError, match="MARKET_PROXY_MATERIALIZATION_NOT_IMPLEMENTED"):
        MODULE.main([
            "--raw-root", str(tmp_path / "raw"),
            "--universe-csv", str(tmp_path / "universe.csv"),
            "--validation-report", str(tmp_path / "validation.json"),
            "--policy", "data_pipelines/sec_edgar/config/sec_pipeline.json",
            "--mapping", "data_pipelines/sec_edgar/mappings/canonical_fields.csv",
            "--derived-definitions", "data_pipelines/sec_edgar/config/derived_fields.json",
            "--output", str(tmp_path / "release"),
            "--pipeline-commit", "test-commit",
            "--include-market-proxies", "--price-snapshot", str(prices),
        ])


def test_scale_cli_passes_completed_security_count_to_release(tmp_path, monkeypatch):
    raw_root = tmp_path / "raw"
    processed = raw_root / "processed"
    processed.mkdir(parents=True)
    ids = [f"sec-{index:03d}" for index in range(500)]
    universe = tmp_path / "universe.csv"
    pd.DataFrame({"security_id": ids}).to_csv(universe, index=False)
    pd.DataFrame(
        {
            "security_id": ids,
            "canonical_field": ["total_assets"] * 500,
            "reported_basis": ["INSTANT"] * 500,
        }
    ).to_parquet(processed / "fundamental_versions.parquet", index=False)
    pd.DataFrame(
        {
            "security_id": ids,
            "canonical_field": ["total_assets"] * 500,
            "reported_basis": ["INSTANT"] * 500,
            "availability_mode": ["HISTORICAL_DIAGNOSTIC"] * 500,
            "session": [pd.Timestamp("2026-09-18")] * 500,
            "value": [1.0] * 500,
        }
    ).to_parquet(processed / "daily_pit_fundamental_panel.parquet", index=False)
    pd.DataFrame(
        {
            "security_id": ids,
            "issuer_cik": [f"{index + 1:010d}" for index in range(500)],
            "mapping_status": ["VERIFIED_SINGLE_CLASS"] * 500,
        }
    ).to_csv(processed / "cik_security_map.csv", index=False)
    (raw_root / "resolved_universe_ciks.csv").write_text("security_id,issuer_cik\n", encoding="utf-8")
    raw_manifest = raw_root / "download_ledger.jsonl"
    raw_manifest.write_text("\n", encoding="utf-8")
    raw_hash = hashlib.sha256(raw_manifest.read_bytes()).hexdigest()
    (raw_root / "download_summary.json").write_text(
        json.dumps({
            "requested_cik_count": 500,
            "completed_cik_count": 500,
            "raw_manifest_hash": raw_hash,
        }),
        encoding="utf-8",
    )
    process_summary = processed / "process_summary.json"
    process_summary.write_text(json.dumps({
        "canonical_mapping_hash": sha256_path(Path("data_pipelines/sec_edgar/mappings/canonical_fields.csv")),
        "derived_definitions_hash": sha256_path(Path("data_pipelines/sec_edgar/config/derived_fields.json")),
        "raw_manifest_hash": sha256_path(raw_manifest),
        "resolved_universe_hash": sha256_path(raw_root / "resolved_universe_ciks.csv"),
        "output_hashes": {
            name: sha256_path(processed / name) for name in (
                "fundamental_versions.parquet", "daily_pit_fundamental_panel.parquet",
                "cik_security_map.csv")},
    }), encoding="utf-8")
    validation = tmp_path / "validation_report.json"
    validation.write_text(
        json.dumps({
            "validation_status": "PASS",
            "fixture_status": "PASS",
            "manual_status": "PASS",
            "raw_status": "PASS",
            "processing_status": "PASS",
            "identity_status": "PASS",
            "manual_identity": {"status": "PASS"},
            "versions_sha256": sha256_path(processed / "fundamental_versions.parquet"),
            "panel_sha256": sha256_path(processed / "daily_pit_fundamental_panel.parquet"),
            "universe_snapshot_sha256": sha256_path(universe),
            "raw_manifest_sha256": sha256_path(raw_manifest),
            "identity_map_sha256": sha256_path(processed / "cik_security_map.csv"),
            "process_summary_sha256": sha256_path(process_summary),
        }),
        encoding="utf-8",
    )
    validation.with_suffix(".md").write_text("# PASS\n", encoding="utf-8")
    captured = {}

    def record_release(inputs, output, commit):
        captured.update(inputs)
        return {"validation_status": "PASS"}

    monkeypatch.setattr(MODULE, "build_sec_release", record_release)
    assert MODULE.main([
        "--raw-root", str(raw_root),
        "--universe-csv", str(universe),
        "--validation-report", str(validation),
        "--policy", "data_pipelines/sec_edgar/config/sec_pipeline.json",
        "--mapping", "data_pipelines/sec_edgar/mappings/canonical_fields.csv",
        "--derived-definitions", "data_pipelines/sec_edgar/config/derived_fields.json",
        "--output", str(tmp_path / "release"),
        "--pipeline-commit", "test-commit",
    ]) == 0

    assert captured["scale_release"] is True
    assert captured["requested_security_count"] == 500
    assert captured["completed_security_count"] == 500
    assert captured["eligible_security_count"] == 500
    assert captured["excluded_security_count"] == 0
    assert captured["processed_security_count"] == 500
    assert captured["requested_issuer_count"] == 500
    assert captured["completed_issuer_count"] == 500
    assert captured["validation_report_json"] == validation


def test_build_rejects_mapping_rules_changed_after_processing(tmp_path):
    raw = tmp_path / "raw"
    processed = raw / "processed"
    processed.mkdir(parents=True)
    for name in ("fundamental_versions.parquet", "daily_pit_fundamental_panel.parquet",
                 "cik_security_map.csv"):
        (processed / name).write_bytes(name.encode())
    (raw / "download_ledger.jsonl").write_text("ledger\n")
    (raw / "resolved_universe_ciks.csv").write_text("security_id,issuer_cik\n")
    mapping = tmp_path / "mapping.csv"
    mapping.write_text("original\n")
    derived = tmp_path / "derived.json"
    derived.write_text("{}")
    summary = processed / "process_summary.json"
    summary.write_text(json.dumps({
        "canonical_mapping_hash": sha256_path(mapping),
        "derived_definitions_hash": sha256_path(derived),
        "raw_manifest_hash": sha256_path(raw / "download_ledger.jsonl"),
        "resolved_universe_hash": sha256_path(raw / "resolved_universe_ciks.csv"),
        "output_hashes": {name: sha256_path(processed / name) for name in (
            "fundamental_versions.parquet", "daily_pit_fundamental_panel.parquet",
            "cik_security_map.csv")},
    }))
    validation = {"process_summary_sha256": sha256_path(summary)}
    mapping.write_text("changed\n")
    with pytest.raises(ValueError, match="PROCESS_MAPPING_HASH_MISMATCH"):
        MODULE._verify_processing_evidence(
            raw, mapping, derived, processed / "fundamental_versions.parquet",
            processed / "daily_pit_fundamental_panel.parquet",
            processed / "cik_security_map.csv", validation,
        )


def test_identity_coverage_keeps_requested_and_usable_counts_separate():
    universe = pd.DataFrame({"security_id": ["A", "B", "C", "D"]})
    identity = pd.DataFrame({
        "security_id": ["A", "B", "C", "D"],
        "mapping_status": [
            "VERIFIED_SINGLE_CLASS", "VERIFIED_ENTITY_LEVEL_ONLY",
            "AMBIGUOUS_MULTI_CLASS", "DEFERRED_ISSUER_SCOPE",
        ],
    })
    versions = pd.DataFrame({"security_id": ["A"]})
    panel = pd.DataFrame({"security_id": ["A"]})

    counts = MODULE._identity_coverage(universe, identity, versions, panel)

    assert counts == {
        "requested_security_count": 4,
        "completed_security_count": 4,
        "eligible_security_count": 2,
        "excluded_security_count": 2,
        "processed_security_count": 1,
    }


def test_identity_coverage_rejects_excluded_security_in_panel():
    universe = pd.DataFrame({"security_id": ["A", "B"]})
    identity = pd.DataFrame({
        "security_id": ["A", "B"],
        "mapping_status": ["VERIFIED_SINGLE_CLASS", "AMBIGUOUS_MULTI_CLASS"],
    })
    versions = pd.DataFrame({"security_id": ["A", "B"]})
    panel = pd.DataFrame({"security_id": ["A", "B"]})

    import pytest

    with pytest.raises(ValueError, match="SEC_SCALE_PROCESSED_IDENTITY_INELIGIBLE"):
        MODULE._identity_coverage(universe, identity, versions, panel)
