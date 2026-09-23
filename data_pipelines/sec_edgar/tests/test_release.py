import json
from pathlib import Path

import pandas as pd
import pytest

from sec_edgar_pipeline.release import build_sec_release, sha256_path


def _inputs(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    versions = pd.DataFrame([{
        "issuer_cik": "0000000001", "security_id": "sec-1",
        "canonical_field": "revenue", "historical_available_at": "2020-02-01T21:00:00Z",
        "first_seen_at": "2020-02-01T21:00:00Z",
        "available_at": "2020-02-01T21:00:00Z", "mapping_status": "MAPPED",
        "reported_basis": "TTM",
        "source_fact_id": "fact-1", "raw_source_hash": "a" * 64,
        "accession_number": "0001", "value": 1.0, "unit": "USD",
    }])
    panel = pd.DataFrame([{
        "session": "2020-02-03", "security_id": "sec-1",
        "canonical_field": "revenue", "reported_basis": "TTM", "value": 1.0,
        "source_fact_id": "fact-1", "signal_cutoff": "2020-02-03T21:00:00Z",
        "selected_available_at": "2020-02-01T21:00:00Z",
        "availability_mode": "HISTORICAL_DIAGNOSTIC", "accession_number": "0001",
        "unit": "USD", "issuer_cik": "0000000001",
    }])
    versions.to_parquet(source / "fundamental_versions.parquet", index=False)
    panel.to_parquet(source / "daily_pit_fundamental_panel.parquet", index=False)
    pd.DataFrame([{"canonical_field": "revenue", "supported_basis": "TTM"}]).to_csv(source / "canonical_field_catalog.csv", index=False)
    (source / "derived_field_definitions.json").write_text("{}\n")
    pd.DataFrame([{"security_id": "sec-1", "issuer_cik": "0000000001", "mapping_status": "VERIFIED_SINGLE_CLASS"}]).to_csv(source / "cik_security_map.csv", index=False)
    (source / "validation_report.md").write_text("# PASS\n")
    pd.DataFrame([{"canonical_field": "revenue", "finite_fraction": 1.0}]).to_csv(source / "coverage_report.csv", index=False)
    (source / "PIT_DATASET_README.md").write_text("# SEC PIT\n")
    raw_manifest = source / "raw_manifest.json"
    raw_manifest.write_text("{}\n")
    universe = source / "universe.csv"
    pd.DataFrame([{"security_id": "sec-1"}]).to_csv(universe, index=False)
    validation_json = source / "validation_report.json"
    validation_json.write_text(json.dumps({
        "validation_status": "PASS",
        "fixture_status": "PASS",
        "manual_status": "PASS",
        "raw_status": "PASS",
        "identity_status": "PASS",
        "processing_status": "PASS",
        "manual_identity": {"status": "PASS"},
        "versions_sha256": sha256_path(source / "fundamental_versions.parquet"),
        "panel_sha256": sha256_path(source / "daily_pit_fundamental_panel.parquet"),
        "universe_snapshot_sha256": sha256_path(universe),
        "raw_manifest_sha256": sha256_path(raw_manifest),
        "identity_map_sha256": sha256_path(source / "cik_security_map.csv"),
    }), encoding="utf-8")
    return {
        "fixture_status": "PASS", "manual_status": "PASS",
        "dataset_version": "SEC_PIT_FUNDAMENTALS_V1",
        "fundamental_versions": source / "fundamental_versions.parquet",
        "daily_pit_fundamental_panel": source / "daily_pit_fundamental_panel.parquet",
        "canonical_field_catalog": source / "canonical_field_catalog.csv",
        "derived_field_definitions": source / "derived_field_definitions.json",
        "cik_security_map": source / "cik_security_map.csv",
        "validation_report": source / "validation_report.md",
        "coverage_report": source / "coverage_report.csv",
        "dataset_readme": source / "PIT_DATASET_README.md",
        "raw_manifest": raw_manifest, "universe_snapshot": universe,
        "validation_report_json": validation_json,
        "pit_contract_hash": "f" * 64,
        "created_at": "2026-09-18T00:00:00Z",
    }


def _scale_inputs(tmp_path: Path):
    inputs = _inputs(tmp_path)
    identities = pd.DataFrame([
        {
            "security_id": f"sec-{number}",
            "issuer_cik": str(number).zfill(10),
            "mapping_status": (
                "VERIFIED_SINGLE_CLASS" if number <= 440 else "DEFERRED_ISSUER_SCOPE"
            ),
        }
        for number in range(1, 501)
    ])
    identities.to_csv(inputs["cik_security_map"], index=False)
    identities[["security_id"]].to_csv(inputs["universe_snapshot"], index=False)
    inputs.update({
        "scale_release": True,
        "requested_security_count": 500,
        "completed_security_count": 500,
        "requested_issuer_count": 499,
        "completed_issuer_count": 499,
        "eligible_security_count": 440,
        "excluded_security_count": 60,
        "processed_security_count": 1,
        "processing_mapping_hash": "a" * 64,
        "processing_derived_hash": "b" * 64,
        "reviewed_share_facts_hash": "d" * 64,
    })
    reviewed = inputs["raw_manifest"].parent / "reviewed_share_facts.json"
    reviewed.write_text('{"schema_version":1,"reviews":[]}\n')
    inputs["reviewed_share_facts"] = reviewed
    inputs["reviewed_share_facts_hash"] = sha256_path(reviewed)
    process_summary = inputs["raw_manifest"].parent / "process_summary.json"
    process_summary.write_text(json.dumps({
        "canonical_mapping_hash": inputs["processing_mapping_hash"],
        "derived_definitions_hash": inputs["processing_derived_hash"],
        "reviewed_share_facts_hash": inputs["reviewed_share_facts_hash"],
        "succession_review_hash": None,
        "succession_filing_sha256": None,
        "succession_predecessor_ledger_hash": None,
        "succession_filing_ledger_hash": None,
    }))
    inputs["process_summary"] = process_summary
    inputs["process_summary_hash"] = sha256_path(process_summary)
    validation = json.loads(inputs["validation_report_json"].read_text())
    validation["universe_snapshot_sha256"] = sha256_path(inputs["universe_snapshot"])
    validation["identity_map_sha256"] = sha256_path(inputs["cik_security_map"])
    validation["process_summary_sha256"] = inputs["process_summary_hash"]
    inputs["validation_report_json"].write_text(json.dumps(validation))
    return inputs


def test_scale_build_requires_fixture_and_manual_pass(tmp_path):
    with pytest.raises(ValueError, match="SEC_SCALE_GATE_NOT_PASSED"):
        build_sec_release(
            {"fixture_status": "PASS", "manual_status": "FAIL"},
            tmp_path / "out", "abc123"
        )


def test_release_contains_exact_required_files(tmp_path):
    manifest = build_sec_release(_inputs(tmp_path), tmp_path / "out", "abc123")
    assert set(manifest["output_files"]) == {
        "fundamental_versions.parquet", "daily_pit_fundamental_panel.parquet",
        "canonical_field_catalog.csv", "derived_field_definitions.json",
        "cik_security_map.csv", "validation_report.md", "coverage_report.csv",
        "PIT_DATASET_README.md",
    }
    assert manifest["historical_pit_universe_verified"] is False
    assert manifest["validation_status"] == "PASS"
    assert manifest["survivorship_bias"] is True
    stored = json.loads((tmp_path / "out" / "processed_manifest.json").read_text())
    assert stored == manifest
    assert {path.name for path in (tmp_path / "out").iterdir()} == {
        *manifest["output_files"],
        "processed_manifest.json",
    }


def test_release_rejects_nonempty_output(tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    (output / "existing").write_text("x")
    with pytest.raises(FileExistsError, match="OUTPUT_DIRECTORY_NOT_EMPTY"):
        build_sec_release(_inputs(tmp_path), output, "abc123")


def test_release_atomically_replaces_preexisting_empty_output(tmp_path):
    output = tmp_path / "out"
    output.mkdir()

    manifest = build_sec_release(_inputs(tmp_path), output, "abc123")

    assert (output / "processed_manifest.json").is_file()
    assert manifest["validation_status"] == "PASS"


def test_partitioned_parquet_directory_hash_is_deterministic(tmp_path):
    inputs = _inputs(tmp_path)
    original = inputs["daily_pit_fundamental_panel"]
    panel = pd.read_parquet(original)
    partitioned = tmp_path / "partitioned"
    panel.to_parquet(
        partitioned,
        index=False,
        partition_cols=["availability_mode", "canonical_field"],
    )
    inputs["daily_pit_fundamental_panel"] = partitioned
    manifest = build_sec_release(inputs, tmp_path / "out", "abc123")
    released = tmp_path / "out" / "daily_pit_fundamental_panel.parquet"
    assert released.is_dir()
    assert manifest["output_hashes"][released.name] == sha256_path(released)
    assert "processed_manifest.json" not in manifest["output_hashes"]


def test_scale_release_requires_exactly_500_complete_downloads(tmp_path):
    inputs = _inputs(tmp_path)
    inputs.update({
        "scale_release": True,
        "requested_security_count": 500,
        "completed_security_count": 499,
    })
    with pytest.raises(ValueError, match="SEC_SCALE_SECURITY_COVERAGE_INCOMPLETE"):
        build_sec_release(inputs, tmp_path / "out", "abc123")


def test_scale_release_allows_500_securities_with_fewer_unique_issuers(tmp_path):
    inputs = _scale_inputs(tmp_path)
    manifest = build_sec_release(inputs, tmp_path / "out", "abc123")
    assert manifest["validation_status"] == "PASS"
    assert manifest["requested_security_count"] == 500
    assert manifest["eligible_security_count"] == 440
    assert manifest["excluded_security_count"] == 60
    assert manifest["processed_security_count"] == 1
    assert manifest["security_count"] == 1


def test_scale_release_rejects_excluded_rows_counted_as_eligible(tmp_path):
    inputs = _scale_inputs(tmp_path)
    inputs["excluded_security_count"] = 59
    with pytest.raises(ValueError, match="SEC_SCALE_IDENTITY_PARTITION_INVALID"):
        build_sec_release(inputs, tmp_path / "out", "abc123")


def test_scale_release_rejects_processed_count_above_eligible(tmp_path):
    inputs = _scale_inputs(tmp_path)
    inputs["processed_security_count"] = 441
    with pytest.raises(ValueError, match="SEC_SCALE_PROCESSED_COVERAGE_INVALID"):
        build_sec_release(inputs, tmp_path / "out", "abc123")


def test_scale_release_rejects_self_attested_pass_when_raw_gate_failed(tmp_path):
    inputs = _scale_inputs(tmp_path)
    validation = json.loads(inputs["validation_report_json"].read_text())
    validation["raw_status"] = "FAIL"
    inputs["validation_report_json"].write_text(json.dumps(validation))

    with pytest.raises(ValueError, match="RELEASE_VALIDATION_STATUS_NOT_PASS"):
        build_sec_release(inputs, tmp_path / "out", "abc123")


def test_scale_release_rejects_processing_gate_failure(tmp_path):
    inputs = _scale_inputs(tmp_path)
    validation = json.loads(inputs["validation_report_json"].read_text())
    validation["processing_status"] = "FAIL"
    inputs["validation_report_json"].write_text(json.dumps(validation))
    with pytest.raises(ValueError, match="RELEASE_VALIDATION_STATUS_NOT_PASS"):
        build_sec_release(inputs, tmp_path / "out", "abc123")


def test_scale_release_binds_succession_assertions_to_process_summary(tmp_path):
    inputs = _scale_inputs(tmp_path)
    inputs["succession_review_hash"] = "e" * 64
    with pytest.raises(
        ValueError, match="RELEASE_PROCESS_SUMMARY_EVIDENCE_MISMATCH:succession_review_hash"
    ):
        build_sec_release(inputs, tmp_path / "out", "abc123")


def test_scale_release_rejects_validation_from_different_versions(tmp_path):
    inputs = _scale_inputs(tmp_path)
    validation = json.loads(inputs["validation_report_json"].read_text())
    validation["versions_sha256"] = "0" * 64
    inputs["validation_report_json"].write_text(json.dumps(validation))

    with pytest.raises(ValueError, match="RELEASE_VALIDATION_EVIDENCE_MISMATCH:versions_sha256"):
        build_sec_release(inputs, tmp_path / "out", "abc123")


def test_scale_release_rejects_validation_from_different_identity_map(tmp_path):
    inputs = _scale_inputs(tmp_path)
    validation = json.loads(inputs["validation_report_json"].read_text())
    validation["identity_map_sha256"] = "0" * 64
    inputs["validation_report_json"].write_text(json.dumps(validation))

    with pytest.raises(ValueError, match="RELEASE_VALIDATION_EVIDENCE_MISMATCH:identity_map_sha256"):
        build_sec_release(inputs, tmp_path / "out", "abc123")


def test_release_rejects_secret_like_metadata_keys(tmp_path):
    inputs = _inputs(tmp_path)
    inputs["api_key"] = "must-not-enter-release"
    with pytest.raises(ValueError, match="RELEASE_SECRET_LIKE_KEY"):
        build_sec_release(inputs, tmp_path / "out", "abc123")


def test_release_rejects_secret_values_embedded_in_text_artifacts(tmp_path):
    inputs = _inputs(tmp_path)
    inputs["validation_report"].write_text(
        "# Validation\n\napi_key = leaked-credential-value\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="RELEASE_SECRET_LIKE_CONTENT"):
        build_sec_release(inputs, tmp_path / "out", "abc123")
    assert not (tmp_path / "out").exists()


def test_release_rejects_secret_values_embedded_in_parquet_metadata(tmp_path):
    inputs = _inputs(tmp_path)
    versions = pd.read_parquet(inputs["fundamental_versions"])
    versions["notes"] = "api_key = leaked-credential-value"
    versions.to_parquet(inputs["fundamental_versions"], index=False)

    with pytest.raises(ValueError, match="RELEASE_SECRET_LIKE_PARQUET_CONTENT"):
        build_sec_release(inputs, tmp_path / "out", "abc123")


def test_failed_release_preserves_preexisting_empty_output_directory(tmp_path):
    inputs = _inputs(tmp_path)
    inputs["fundamental_versions"].write_text("not parquet", encoding="utf-8")
    output = tmp_path / "out"
    output.mkdir()

    with pytest.raises(ValueError, match="RELEASE_PARQUET_INVALID"):
        build_sec_release(inputs, output, "abc123")

    assert output.is_dir()
    assert not any(output.iterdir())
