import copy
import hashlib
import json

import pandas as pd

from us_equity_alpha.sec_field_mapping import SecFieldBinding
from us_equity_alpha.sec_source_reconstruction import (
    build_sec_source_linked_candidates,
    reconstruct_sec_source_linked,
)


def binding(name, field, basis="INSTANT", *, track="SEC_SOURCE_LINKED",
            grade="SOURCE_LINKED_DIRECT"):
    return SecFieldBinding(name, field, basis, grade, "AS_REPORTED", track, "reviewed")


def fixtures(expression="rank(assets/equity)", *, fields=("assets", "equity")):
    bindings = {
        "assets": binding("assets", "total_assets"),
        "equity": binding("equity", "stockholders_equity"),
    }
    source = {"id": "A1", "regular": {"code": expression},
              "settings": {"delay": 1, "decay": 0, "neutralization": "NONE"},
              "economic_hypothesis": "Asset backing relative to equity"}
    demand = {
        "source_alpha_id": "A1",
        "candidate_track": "SEC_SOURCE_LINKED",
        "demand_class": "FUNDAMENTAL_ONLY",
        "fundamental_fields": list(fields),
        "mapped_fields": list(fields),
        "unmapped_fundamental_fields": [],
        "other_domain_fields": [],
        "identity_status": "VERIFIED_SINGLE_CLASS",
        "sec_catalog": [
            {"canonical_field": "total_assets", "supported_basis": "INSTANT",
             "semantic_grade": "SOURCE_LINKED_DIRECT", "identity_usage": "ENTITY_LEVEL"},
            {"canonical_field": "stockholders_equity", "supported_basis": "INSTANT",
             "semantic_grade": "SOURCE_LINKED_DIRECT", "identity_usage": "ENTITY_LEVEL"},
        ],
        "sec_evidence_hashes": {
            key: "a" * 64 for key in (
                "processed_manifest_sha256", "canonical_mapping_hash",
                "derived_field_catalog_hash", "security_mapping_hash", "pit_contract_hash",
                "universe_snapshot_hash", "raw_manifest_hash",
            )
        },
    }
    demand["sec_evidence_hashes"]["dataset_version"] = "SEC_PIT_FUNDAMENTALS_V1"
    demand["original_expression_hash"] = hashlib.sha256(
        json.dumps(expression, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    return source, demand, bindings


def test_complete_source_linked_formula_preserves_ast_and_provenance():
    source, demand, bindings = fixtures()
    result = reconstruct_sec_source_linked(source, demand, bindings, {"rank", "divide"})
    assert result["expression"] == "rank(sec__total_assets__INSTANT / sec__stockholders_equity__INSTANT)"
    assert result["semantic_status"] == "SOURCE_LINKED"
    assert result["provenance_kind"] == "SEC_SOURCE_LINKED"
    assert result["build_status"] == "COMPILED"
    assert result["coverage_status"] == "NOT_MEASURED"
    assert result["usage_tier"] == "DIAGNOSTIC_ONLY"
    assert result["source_alpha_ids"] == ["A1"]
    assert result["settings"] == source["settings"]
    assert result["required_fields"] == ["sec__stockholders_equity__INSTANT", "sec__total_assets__INSTANT"]
    assert result["field_bindings"]["sec__total_assets__INSTANT"] == "total_assets/INSTANT"
    assert result["sec_evidence_hashes"] == demand["sec_evidence_hashes"]


def test_proxy_cap_and_ambiguous_identity_block_strict():
    source, demand, bindings = fixtures("rank(assets/cap)", fields=("assets", "cap"))
    bindings["cap"] = binding("cap", "market_cap_sec_proxy", "DAILY_PROXY",
                              track="SEC_HYPOTHESIS_PROXY", grade="HYPOTHESIS_PROXY")
    demand["identity_status"] = "AMBIGUOUS_SHARE_CLASS"
    result = reconstruct_sec_source_linked(source, demand, bindings, {"rank", "divide"})
    assert result["build_status"] == "DEFERRED_DATA"
    assert "PROXY_FIELD_NOT_ALLOWED_IN_SOURCE_LINKED:cap" in result["blocking_reasons"]
    assert "AMBIGUOUS_IDENTITY:AMBIGUOUS_SHARE_CLASS" in result["blocking_reasons"]
    assert "expression" not in result


def test_unsupported_group_operation_and_setting_are_never_silently_dropped():
    source, demand, bindings = fixtures("group_rank(assets/equity, sector)")
    source["settings"]["neutralization"] = "SECTOR"
    result = reconstruct_sec_source_linked(source, demand, bindings, {"rank", "divide"})
    assert "UNSUPPORTED_OPERATOR:group_rank" in result["blocking_reasons"]
    assert "UNVERIFIED_GROUP_SETTING:SECTOR" in result["blocking_reasons"]


def test_missing_field_basis_and_evidence_all_block():
    source, demand, bindings = fixtures()
    bindings.pop("equity")
    demand["sec_catalog"] = demand["sec_catalog"][:1]
    demand["sec_evidence_hashes"].pop("processed_manifest_sha256")
    result = reconstruct_sec_source_linked(source, demand, bindings, {"rank", "divide"})
    assert "UNMAPPED_FIELD:equity" in result["blocking_reasons"]
    assert "SEC_EVIDENCE_MISSING:processed_manifest_sha256" in result["blocking_reasons"]


def test_basis_mismatch_and_per_share_entity_identity_block():
    source, demand, bindings = fixtures("rank(assets)", fields=("assets",))
    bindings["assets"] = binding("assets", "total_assets", "TTM")
    result = reconstruct_sec_source_linked(source, demand, bindings, {"rank"})
    assert "SEC_BASIS_NOT_IN_CATALOG:assets:TTM" in result["blocking_reasons"]
    source, demand, bindings = fixtures("rank(assets)", fields=("assets",))
    demand["identity_status"] = "VERIFIED_ENTITY_LEVEL_ONLY"
    demand["sec_catalog"][0]["identity_usage"] = "PER_SHARE"
    result = reconstruct_sec_source_linked(source, demand, bindings, {"rank"})
    assert "SHARE_CLASS_IDENTITY_REQUIRED:assets" in result["blocking_reasons"]


def test_unknown_identifier_mixed_domain_and_unsafe_syntax_block():
    source, demand, bindings = fixtures("rank(assets/analyst_score)", fields=("assets",))
    demand["other_domain_fields"] = ["analyst_score"]
    result = reconstruct_sec_source_linked(source, demand, bindings, {"rank", "divide"})
    assert "UNMAPPED_FIELD:analyst_score" in result["blocking_reasons"]
    assert "OTHER_DOMAIN_FIELD:analyst_score" in result["blocking_reasons"]
    source, demand, bindings = fixtures("rank(assets.__class__)", fields=("assets",))
    result = reconstruct_sec_source_linked(source, demand, bindings, {"rank"})
    assert "UNSUPPORTED_SYNTAX" in result["blocking_reasons"]


def test_supported_operator_must_exist_in_evaluator_and_no_keyword_semantics():
    source, demand, bindings = fixtures("rank(assets, pct=True)", fields=("assets",))
    result = reconstruct_sec_source_linked(source, demand, bindings, {"rank"})
    assert "UNSUPPORTED_SYNTAX" in result["blocking_reasons"]
    source, demand, bindings = fixtures("group_rank(assets, sector)", fields=("assets",))
    result = reconstruct_sec_source_linked(source, demand, bindings,
                                            {"rank", "group_rank"})
    assert "UNSUPPORTED_OPERATOR:group_rank" in result["blocking_reasons"]


def test_settings_and_original_hash_are_fail_closed():
    source, demand, bindings = fixtures("rank(assets)", fields=("assets",))
    source["settings"]["truncation"] = 0.05
    demand["original_expression_hash"] = "f" * 64
    result = reconstruct_sec_source_linked(source, demand, bindings, {"rank"})
    assert "UNSUPPORTED_SOURCE_SETTING:truncation" in result["blocking_reasons"]
    assert "ORIGINAL_EXPRESSION_HASH_MISMATCH" in result["blocking_reasons"]


def test_constant_only_and_nonfinite_formula_cannot_enter_strict_track():
    source, demand, bindings = fixtures("rank(1)", fields=())
    result = reconstruct_sec_source_linked(source, demand, bindings, {"rank"})
    assert "SEC_FIELD_DEPENDENCY_MISSING" in result["blocking_reasons"]
    source, demand, bindings = fixtures("rank(assets/1e309)", fields=("assets",))
    result = reconstruct_sec_source_linked(source, demand, bindings, {"rank", "divide"})
    assert "NONFINITE_NUMERIC_CONSTANT" in result["blocking_reasons"]


def test_builder_emits_one_ledger_row_per_source_and_ignores_performance():
    source, demand, bindings = fixtures()
    blocked = copy.deepcopy(source)
    blocked["id"] = "B1"
    blocked["regular"]["code"] = "rank(assets/cap)"
    blocked_demand = copy.deepcopy(demand)
    blocked_demand["source_alpha_id"] = "B1"
    blocked_demand["candidate_track"] = "SEC_HYPOTHESIS_PROXY"
    blocked_demand["original_expression_hash"] = hashlib.sha256(
        json.dumps(blocked["regular"]["code"], separators=(",", ":")).encode()
    ).hexdigest()
    registry = {"records": [blocked, source]}
    rows = pd.DataFrame([blocked_demand, demand])
    candidates, ledger = build_sec_source_linked_candidates(registry, rows, bindings,
                                                              {"rank", "divide"})
    assert [row["source_alpha_ids"] for row in candidates] == [["A1"]]
    assert [row["source_alpha_id"] for row in ledger] == ["A1", "B1"]
    assert ledger[1]["build_status"] == "DEFERRED_DATA"
    mutated = copy.deepcopy(registry)
    for row in mutated["records"]:
        row["is"] = {"sharpe": 999}
    assert build_sec_source_linked_candidates(mutated, rows, bindings, {"rank", "divide"}) == (candidates, ledger)
