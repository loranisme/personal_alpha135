"""Fail-closed contract tests for the detached SEC expansion release."""

import json
import hashlib
from pathlib import Path

import pandas as pd
import pytest

import us_equity_alpha.sec_alpha_expansion_workflow as workflow
from us_equity_alpha.sec_alpha_expansion_workflow import run_sec_alpha_expansion


def _paths(tmp_path: Path) -> dict:
    names = (
        "registry", "brain_field_catalog", "v4_library", "v4_manifest",
        "v4_source_index", "sec_manifest", "field_bindings", "proxy_rules",
    )
    result = {name + "_path": tmp_path / (name + ".json") for name in names}
    for path in result.values():
        path.write_text("{}", encoding="utf-8")
    return result


def test_rejects_nonempty_output_before_reading_inputs(tmp_path):
    output = tmp_path / "delivery"
    output.mkdir()
    (output / "preserved.txt").write_text("keep")
    with pytest.raises(FileExistsError, match="OUTPUT_DIRECTORY_NOT_EMPTY"):
        run_sec_alpha_expansion(**_paths(tmp_path), output_dir=output)
    assert (output / "preserved.txt").read_text() == "keep"


def test_rejects_unapproved_release_before_source_records(tmp_path):
    paths = _paths(tmp_path)
    paths["sec_manifest_path"].write_text(json.dumps({"validation_status": "FAIL"}))
    with pytest.raises(ValueError, match="SEC_RELEASE_NOT_APPROVED"):
        run_sec_alpha_expansion(**paths, output_dir=tmp_path / "delivery")
    assert not (tmp_path / "delivery").exists()


def test_rejects_latest_or_symlink_input_paths(tmp_path):
    paths = _paths(tmp_path)
    paths["registry_path"] = tmp_path / "latest.json"
    paths["registry_path"].write_text("{}")
    with pytest.raises(ValueError, match="UNPINNED_INPUT_PATH"):
        run_sec_alpha_expansion(**paths, output_dir=tmp_path / "delivery")


def test_exact_delivery_is_detached_and_hash_bound(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    v4_dir = tmp_path / "v4"
    v4_dir.mkdir()
    paths["v4_library_path"] = v4_dir / "project_factor_library.json"
    paths["v4_source_index_path"] = v4_dir / "source_reconstruction_index.csv"
    paths["v4_manifest_path"] = v4_dir / "manifest.json"
    paths["v4_library_path"].write_text(json.dumps({
        "schema_version": "V4", "factors": [
            {"local_factor_id": f"f{i}", "expression": f"rank(x + {i})",
             "field_bindings": {"x": "close/DAILY"},
             "settings": {"delay": 1, "decay": 0, "neutralization": "NONE"}}
            for i in range(135)]}))
    ids = [f"s{i:03d}" for i in range(886)]
    pd.DataFrame({"source_alpha_id": ids,
                  "decision": ["DEFERRED"] * 569 + ["MAPPED"] * 317,
                  "missing_fields": [""] * 886}).to_csv(paths["v4_source_index_path"], index=False)
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (paths["v4_library_path"], paths["v4_source_index_path"])}
    paths["v4_manifest_path"].write_text(json.dumps(hashes))
    paths["registry_path"].write_text(json.dumps({"records": [{"id": sid} for sid in ids]}))
    paths["brain_field_catalog_path"].write_text("[]")
    paths["field_bindings_path"].write_text("[]")
    paths["proxy_rules_path"].write_text(json.dumps({"schema_version": "SEC_PROXY_HYPOTHESES_V1", "rules": {}}))
    paths["sec_manifest_path"].write_text(json.dumps({"validation_status": "PASS"}))

    class PinnedProvider:
        def __init__(self, manifest_path):
            assert Path(manifest_path) == paths["sec_manifest_path"]
            self._catalog = pd.DataFrame(columns=["canonical_field", "supported_basis",
                                                  "identity_usage", "semantic_grade"])
            self.evidence_hashes = {"dataset_version": "SEC_PIT_FUNDAMENTALS_V1",
                                    "processed_manifest_sha256": "a" * 64,
                                    "canonical_mapping_hash": "b" * 64}

    def audit(*_args):
        demand_class = (["FUNDAMENTAL_ONLY"] * 86 + ["MIXED_DOMAIN"] * 203
                        + ["NO_FUNDAMENTAL"] * 280)
        return pd.DataFrame({"source_alpha_id": ids[:569], "demand_class": demand_class,
                             "candidate_track": ["OUT_OF_SCOPE"] * 569,
                             "original_expression_hash": ["a" * 64] * 569,
                             "blocking_reasons": [[] for _ in range(569)]})

    monkeypatch.setattr(workflow, "SecFundamentalProvider", PinnedProvider)
    monkeypatch.setattr(workflow, "audit_fundamental_deferred_sources", audit)
    monkeypatch.setattr(workflow, "build_sec_source_linked_candidates", lambda *_: ([], []))
    output = tmp_path / "delivery"
    result = run_sec_alpha_expansion(**paths, output_dir=output)
    assert result["base_factor_count"] == 135
    assert result["final_unique_factor_count"] == 135
    assert result["filter_executed"] is False
    assert result["current_library_pointer_updated"] is False
    assert result["live_orders_submitted"] == 0
    assert {p.name for p in output.iterdir()} == {
        "expanded_alpha_library.json", "fundamental_deferred_source_map.csv",
        "sec_source_linked_factors.json", "sec_hypothesis_proxy_factors.json",
        "source_reconstruction_index.csv", "deduplication_report.csv",
        "conversion_report.json", "validation_report.md", "manifest.json", "factor_values"}
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["input_hashes"]["v4_library"] == hashes["project_factor_library.json"]
    assert all(hashlib.sha256((output / name).read_bytes()).hexdigest() == digest
               for name, digest in manifest["output_hashes"].items())
    assert len(pd.read_csv(output / "source_reconstruction_index.csv")) == 886


def test_source_index_names_duplicate_relationship_reason():
    v4 = pd.DataFrame([{"source_alpha_id": f"s{i}", "decision": "DEFERRED"}
                       for i in range(1, 887)])
    demand = pd.DataFrame([{"source_alpha_id": "s1", "demand_class": "FUNDAMENTAL_ONLY",
                            "original_expression_hash": "a" * 64}])
    relationships = [{"source_alpha_id": "s1", "local_factor_id": "v4_existing",
                      "provenance_kind": "SEC_SOURCE_LINKED"}]
    dedup = pd.DataFrame([{"source_alpha_ids": "s1", "dedup_reason": "DUPLICATE_V4"}])
    result = workflow._relationship_index(v4, demand, [], [], relationships, dedup)
    assert result.iloc[0].expansion_status == "DUPLICATE_V4"
    assert result.iloc[0].local_factor_ids == "v4_existing"


def test_source_index_retains_proxy_and_verification_blockers():
    v4 = pd.DataFrame([{"source_alpha_id": f"s{i}", "decision": "DEFERRED"}
                       for i in range(1, 887)])
    demand = pd.DataFrame([{"source_alpha_id": "s1", "demand_class": "MIXED_DOMAIN",
                            "original_expression_hash": "a" * 64}])
    strict = [{"source_alpha_id": "s1", "blocking_reasons": ["PRELIMINARY_TRACK_NOT_SOURCE_LINKED"]}]
    proxy = [{"source_alpha_id": "s1", "blocking_reasons": [], "build_status": "COMPILED"}]
    verified = [{"source_alpha_ids": ["s1"], "build_status": "COMPUTE_FAILED",
                 "verification_failures": ["NO_VALID_CROSS_SECTIONAL_OBSERVATIONS"]}]
    result = workflow._relationship_index(v4, demand, strict, proxy, [], pd.DataFrame(), verified)
    assert result.iloc[0].expansion_status == "DEFERRED"
    assert result.iloc[0].blocking_reasons == "NO_VALID_CROSS_SECTIONAL_OBSERVATIONS"
