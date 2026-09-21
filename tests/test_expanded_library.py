from __future__ import annotations

import copy
import hashlib
import json

import pytest

from us_equity_alpha.expanded_library import (
    assemble_expanded_library,
    canonical_factor_identity,
    deduplicate_sec_candidates,
)


def _binding(basis: str = "TTM") -> list[dict[str, str]]:
    return [{"alias": "x", "field": "revenue", "basis": basis}]


def _settings() -> dict[str, object]:
    return {"delay": 1, "decay": 0, "neutralization": "NONE"}


def _candidate(source_id: str, *, track: str = "SEC_SOURCE_LINKED") -> dict:
    return {
        "source_alpha_ids": [source_id],
        "parent_source_alpha_ids": [source_id],
        "expression": "rank(x)",
        "field_bindings": _binding(),
        "settings": _settings(),
        "build_status": "COMPUTE_VERIFIED",
        "semantic_status": "SOURCE_LINKED" if track == "SEC_SOURCE_LINKED" else "HYPOTHESIS_PROXY",
        "provenance_kind": track,
        "usage_tier": "DIAGNOSTIC_ONLY",
        "reconstruction_rule_hash": "a" * 64 if track == "SEC_SOURCE_LINKED" else "b" * 64,
    }


def test_canonical_identity_ignores_whitespace_and_redundant_parentheses():
    left = canonical_factor_identity("rank((x))", _binding(), _settings())
    right = canonical_factor_identity(" rank(x) ", _binding(), _settings())
    assert left == right


def test_canonical_identity_preserves_field_basis_and_factor_settings():
    ttm = canonical_factor_identity("rank(x)", _binding("TTM"), _settings())
    annual = canonical_factor_identity("rank(x)", _binding("ANNUAL"), _settings())
    changed_delay = canonical_factor_identity("rank(x)", _binding(), {**_settings(), "delay": 0})
    assert ttm != annual
    assert ttm != changed_delay


def test_duplicate_against_v4_keeps_v4_id_and_preserves_input():
    candidate = _candidate("S1")
    v4 = {"schema_version": "V4", "factors": [{
        "local_factor_id": "local_v4_a", "expression": "rank((x))",
        "field_bindings": _binding(), "settings": _settings(),
        "usage_tier": "DIAGNOSTIC_ONLY",
    }]}
    before = hashlib.sha256(json.dumps(v4, sort_keys=True).encode()).hexdigest()
    unique, report, relationships = deduplicate_sec_candidates(v4, [candidate])
    assert unique == []
    assert report.iloc[0].dedup_reason == "DUPLICATE_V4"
    assert relationships[0]["local_factor_id"] == "local_v4_a"
    assert hashlib.sha256(json.dumps(v4, sort_keys=True).encode()).hexdigest() == before


def test_cross_track_duplicate_keeps_one_formula_and_both_source_relationships():
    strict = _candidate("S1")
    proxy = _candidate("S2", track="SEC_HYPOTHESIS_PROXY")
    unique, report, relationships = deduplicate_sec_candidates(
        {"schema_version": "V4", "factors": []}, [proxy, strict]
    )
    assert len(unique) == 1
    assert unique[0]["provenance_kind"] == "SEC_SOURCE_LINKED"
    assert unique[0]["source_alpha_ids"] == ["S1"]
    assert set(report.dedup_reason) == {"NEW_UNIQUE", "DUPLICATE_CROSS_TRACK"}
    assert {row["semantic_status"] for row in relationships} == {
        "SOURCE_LINKED", "HYPOTHESIS_PROXY"
    }


def test_assembly_deep_copies_v4_and_does_not_promote_usage():
    v4 = {"schema_version": "V4", "factors": [{
        "local_factor_id": "local_v4_a", "expression": "rank(y)",
        "field_bindings": [{"alias": "y", "field": "close", "basis": "DAILY"}],
        "settings": _settings(), "usage_tier": "DIAGNOSTIC_ONLY",
    }]}
    original = copy.deepcopy(v4)
    unique, _, relationships = deduplicate_sec_candidates(v4, [_candidate("S1")])
    assembled = assemble_expanded_library(
        v4, unique, relationships,
        base_library_manifest_sha256="a" * 64,
        sec_evidence_hashes={
            "dataset_version": "SEC_PIT_FUNDAMENTALS_V1",
            "processed_manifest_sha256": "b" * 64,
            "canonical_mapping_hash": "c" * 64,
        },
    )
    assert assembled["base_factor_count"] == 1
    assert assembled["new_unique_factor_count"] == 1
    assert assembled["final_unique_factor_count"] == 2
    assert assembled["factors"][0] == v4["factors"][0]
    assert assembled["factors"][1]["usage_tier"] == "DIAGNOSTIC_ONLY"
    assembled["factors"][0]["expression"] = "changed"
    assert v4 == original


def test_dedup_rejects_cross_track_semantic_label_mismatch():
    candidate = _candidate("S1")
    candidate["semantic_status"] = "HYPOTHESIS_PROXY"
    with pytest.raises(ValueError, match="SEC_CANDIDATE_TRACK_SEMANTICS_INVALID"):
        deduplicate_sec_candidates({"factors": []}, [candidate])


def test_assembly_requires_v4_and_sec_release_hash_evidence():
    with pytest.raises(ValueError, match="BASE_LIBRARY_MANIFEST_HASH_REQUIRED"):
        assemble_expanded_library({"factors": []}, [], [])
