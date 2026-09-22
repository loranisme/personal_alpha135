import copy
import hashlib
import json
from pathlib import Path

import pytest

from us_equity_alpha.sec_field_mapping import SecFieldBinding
from us_equity_alpha.sec_proxy_reconstruction import (
    extract_sec_only_subtrees,
    load_proxy_hypotheses,
    reconstruct_sec_hypothesis_proxy,
)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _binding(name, canonical, basis="TTM", grade="HYPOTHESIS_PROXY"):
    return SecFieldBinding(name, canonical, basis, grade, "AS_REPORTED",
                           "SEC_HYPOTHESIS_PROXY", "Reviewed SEC concept")


def _fixtures(expression="0.25 * rank(cashflow_op / cap) + 0.75 * rank(option_skew_30 - option_skew_180)"):
    source = {"id": "A1", "regular": {"code": expression},
              "settings": {"delay": 1, "decay": 0, "neutralization": "NONE"},
              "economic_hypothesis": "Cash flow yield and option skew jointly predict returns"}
    demand = {"source_alpha_id": "A1", "original_expression_hash": _hash(expression),
              "candidate_track": "SEC_HYPOTHESIS_PROXY", "demand_class": "MIXED_DOMAIN",
              "fundamental_fields": ["cashflow_op", "cap"],
              "other_domain_fields": ["option_skew_30", "option_skew_180"],
              "blocking_reasons": ["OTHER_DOMAIN_FIELD:option_skew_30:options",
                                   "OTHER_DOMAIN_FIELD:option_skew_180:options"],
              "identity_status": "VERIFIED_SINGLE_CLASS",
              "sec_catalog": [
                  {"canonical_field": "operating_cash_flow", "supported_basis": "TTM",
                   "semantic_grade": "HYPOTHESIS_PROXY", "identity_usage": "ENTITY_LEVEL"},
                  {"canonical_field": "market_cap_sec_proxy", "supported_basis": "DAILY_PROXY",
                   "semantic_grade": "HYPOTHESIS_PROXY", "identity_usage": "MARKET_CAP"},
              ],
              "sec_evidence_hashes": {"dataset_version": "SEC_PIT_FUNDAMENTALS_V1", **{
                  key: "a" * 64 for key in (
                      "processed_manifest_sha256", "canonical_mapping_hash",
                      "derived_field_catalog_hash", "security_mapping_hash",
                      "pit_contract_hash", "universe_snapshot_hash", "raw_manifest_hash")}},
              }
    bindings = {"cashflow_op": _binding("cashflow_op", "operating_cash_flow"),
                "cap": _binding("cap", "market_cap_sec_proxy", "DAILY_PROXY")}
    return source, demand, bindings


def test_extracts_maximal_sec_only_component_from_weighted_mixed_formula():
    expression = "0.25 * rank(cashflow_op / cap) + 0.75 * rank(option_skew_30 - option_skew_180)"
    assert extract_sec_only_subtrees(expression, {"cashflow_op", "cap"},
                                     {"option_skew_30", "option_skew_180"}) == ["rank(cashflow_op / cap)"]


def test_entangled_mixed_expression_requires_frozen_rule():
    source, demand, bindings = _fixtures("rank(cashflow_op * option_skew_30)")
    demand["fundamental_fields"] = ["cashflow_op"]
    result = reconstruct_sec_hypothesis_proxy(source, demand, bindings, {})
    assert result["build_status"] == "DEFERRED_DATA"
    assert "MIXED_EXPRESSION_NOT_SEPARABLE_AND_NO_FROZEN_RULE" in result["blocking_reasons"]


def test_proxy_records_losses_and_evidence_without_claiming_parity():
    source, demand, bindings = _fixtures()
    result = reconstruct_sec_hypothesis_proxy(source, demand, bindings, {})
    assert result["build_status"] == "COMPILED"
    assert result["semantic_status"] == "HYPOTHESIS_PROXY"
    assert result["usage_tier"] == "DIAGNOSTIC_ONLY"
    assert result["discarded_information_domains"] == ["OPTIONS"]
    assert "SEC_CAP_IS_NOT_BRAIN_DAILY_CAP" in result["semantic_losses"]
    assert "MIXED_SOURCE_RELATION_DROPPED" in result["semantic_losses"]
    assert result["original_expression_hash"] == demand["original_expression_hash"]
    assert result["proxy_expression"] == "rank(sec__operating_cash_flow__TTM / sec__market_cap_sec_proxy__DAILY_PROXY)"
    assert result["required_fields"] == ["sec__market_cap_sec_proxy__DAILY_PROXY", "sec__operating_cash_flow__TTM"]
    assert result["coverage_status"] == "NOT_MEASURED"
    assert result["sec_evidence_hashes"] == demand["sec_evidence_hashes"]


def test_proxy_definition_is_invariant_to_performance_fields():
    source, demand, bindings = _fixtures()
    changed = copy.deepcopy(source)
    changed["raw"] = {"is": {"sharpe": 1000.0, "returns": -1.0}}
    left = reconstruct_sec_hypothesis_proxy(source, demand, bindings, {})
    right = reconstruct_sec_hypothesis_proxy(changed, demand, bindings, {})
    assert left == right


def test_multiple_separable_components_retain_source_coefficients():
    source, demand, bindings = _fixtures("2 * rank(cashflow_op) - 3 * rank(cap) + rank(option_skew_30)")
    result = reconstruct_sec_hypothesis_proxy(source, demand, bindings, {})
    assert result["build_status"] == "COMPILED"
    assert result["proxy_expression"] == "2 * rank(sec__operating_cash_flow__TTM) - 3 * rank(sec__market_cap_sec_proxy__DAILY_PROXY)"


def test_entangled_components_cannot_be_salvaged_from_nested_ratio():
    expression = "rank(cashflow_op / option_skew_30) + rank(option_skew_180)"
    assert extract_sec_only_subtrees(expression, {"cashflow_op"},
                                     {"option_skew_30", "option_skew_180"}) == []


def test_wrong_hash_missing_catalog_and_identity_block():
    source, demand, bindings = _fixtures()
    demand["original_expression_hash"] = "f" * 64
    demand["sec_catalog"] = []
    demand["identity_status"] = "AMBIGUOUS_SHARE_CLASS"
    result = reconstruct_sec_hypothesis_proxy(source, demand, bindings, {})
    assert "ORIGINAL_EXPRESSION_HASH_MISMATCH" in result["blocking_reasons"]
    assert "SEC_BASIS_NOT_IN_CATALOG:cashflow_op:TTM" in result["blocking_reasons"]
    assert "AMBIGUOUS_IDENTITY:AMBIGUOUS_SHARE_CLASS" in result["blocking_reasons"]


def test_exact_hash_rule_requires_reviewed_metadata_and_only_sec_fields(tmp_path):
    source, demand, bindings = _fixtures("rank(cashflow_op * option_skew_30)")
    demand["fundamental_fields"] = ["cashflow_op"]
    rule = {"parent_source_alpha_ids": ["A1"],
            "proxy_expression": "rank(cashflow_op)",
            "retained_economic_hypothesis": "Operating cash-flow level is related to returns",
            "required_bindings": ["cashflow_op"],
            "discarded_information_domains": ["OPTIONS"],
            "semantic_losses": ["MIXED_SOURCE_RELATION_DROPPED"],
            "review_author": "researcher", "reviewed_at": "2026-09-17T00:00:00Z",
            "rule_version": "1"}
    path = tmp_path / "rules.json"
    path.write_text(json.dumps({"schema_version": "SEC_PROXY_HYPOTHESES_V1",
                                "rules": {demand["original_expression_hash"]: rule}}))
    rules = load_proxy_hypotheses(path, bindings)
    result = reconstruct_sec_hypothesis_proxy(source, demand, bindings, rules)
    assert result["build_status"] == "COMPILED"
    assert result["reconstruction_method"] == "EXPLICIT_HASH_RULE"
    assert result["retained_economic_hypothesis"] == rule["retained_economic_hypothesis"]
    rule["proxy_expression"] = "rank(option_skew_30)"
    path.write_text(json.dumps({"schema_version": "SEC_PROXY_HYPOTHESES_V1",
                                "rules": {demand["original_expression_hash"]: rule}}))
    with pytest.raises(ValueError, match="PROXY_RULE_UNBOUND_FIELD"):
        load_proxy_hypotheses(path, bindings)


def test_reject_wildcard_rule_and_missing_review(tmp_path):
    _, _, bindings = _fixtures()
    path = tmp_path / "rules.json"
    path.write_text(json.dumps({"schema_version": "SEC_PROXY_HYPOTHESES_V1", "rules": {"*": {}}}))
    with pytest.raises(ValueError, match="PROXY_RULE_HASH_INVALID"):
        load_proxy_hypotheses(path, bindings)


def test_constructor_rejects_unreviewed_in_memory_rule():
    source, demand, bindings = _fixtures("rank(cashflow_op * option_skew_30)")
    demand["fundamental_fields"] = ["cashflow_op"]
    result = reconstruct_sec_hypothesis_proxy(source, demand, bindings, {
        demand["original_expression_hash"]: {"proxy_expression": "rank(cashflow_op)"}
    })
    assert result["build_status"] == "DEFERRED_DATA"
    assert "PROXY_RULE_NOT_REVIEWED" in result["blocking_reasons"]


def test_unclassified_discarded_field_and_source_setting_fail_closed():
    source, demand, bindings = _fixtures("rank(cashflow_op) + rank(unclassified_score)")
    demand["fundamental_fields"] = ["cashflow_op"]
    demand["other_domain_fields"] = ["unclassified_score"]
    demand["blocking_reasons"] = []
    source["settings"]["neutralization"] = "SUBINDUSTRY"
    result = reconstruct_sec_hypothesis_proxy(source, demand, bindings, {})
    assert "DISCARDED_DOMAIN_UNCLASSIFIED:unclassified_score" in result["blocking_reasons"]


def test_pinned_real_brain_settings_translate_only_in_proxy_track():
    source, demand, bindings = _fixtures()
    raw = json.loads((Path(__file__).parents[1] /
                      "private/runs/brain-sync-20260907-live/alpha_export.json").read_text())
    source["settings"] = raw["records"][0]["settings"]
    result = reconstruct_sec_hypothesis_proxy(source, demand, bindings, {})
    assert result["build_status"] == "COMPILED"
    assert result["settings"] == {"delay": 1, "decay": source["settings"]["decay"],
                                   "neutralization": "NONE"}
    assert result["source_settings"] == source["settings"]
    assert result["source_settings_sha256"] == _hash(source["settings"])
    assert f"BRAIN_NEUTRALIZATION_NOT_REPRODUCED:{source['settings']['neutralization']}" in result["semantic_losses"]
    assert f"BRAIN_UNIVERSE_NOT_REPRODUCED:{source['settings']['universe']}" in result["semantic_losses"]
    assert any(loss.startswith("BRAIN_TRUNCATION_NOT_REPRODUCED:") for loss in result["semantic_losses"])


def test_proxy_group_neutralization_is_explicit_semantic_loss():
    source, demand, bindings = _fixtures()
    source["settings"]["neutralization"] = "SUBINDUSTRY"
    result = reconstruct_sec_hypothesis_proxy(source, demand, bindings, {})
    assert result["build_status"] == "COMPILED"
    assert result["settings"]["neutralization"] == "NONE"
    assert "BRAIN_NEUTRALIZATION_NOT_REPRODUCED:SUBINDUSTRY" in result["semantic_losses"]


@pytest.mark.parametrize("mutate,expected", [
    (lambda settings: settings.update(mystery="ON"), "UNSUPPORTED_SOURCE_SETTING:mystery"),
    (lambda settings: settings.update(nanHandling="MAYBE"), "UNREVIEWED_SOURCE_SETTING_VALUE:nanHandling"),
    (lambda settings: settings.update(nanHandling=["ON"]), "UNREVIEWED_SOURCE_SETTING_VALUE:nanHandling"),
    (lambda settings: settings.update(truncation=0.99), "UNREVIEWED_SOURCE_SETTING_VALUE:truncation"),
])
def test_proxy_settings_unknown_key_or_value_fails_closed(mutate, expected):
    source, demand, bindings = _fixtures()
    mutate(source["settings"])
    result = reconstruct_sec_hypothesis_proxy(source, demand, bindings, {})
    assert result["build_status"] == "DEFERRED_DATA"
    assert expected in result["blocking_reasons"]
