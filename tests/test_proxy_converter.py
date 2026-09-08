import copy
import json

import pandas as pd

from us_equity_alpha.proxy_converter import (
    build_source_view,
    convert_library,
    default_market_capabilities,
    verify_factors,
    write_conversion_artifacts,
)


CAPABILITIES = {
    "close": {"status": "VERIFIED_SAMPLE", "binding": "close"},
    "volume": {"status": "VERIFIED_SAMPLE", "binding": "volume"},
    "returns": {
        "status": "DERIVED_REQUIRES_QA",
        "binding": "close / ts_delay(close, 1) - 1",
    },
    "historical_volatility_20": {
        "status": "DERIVED_REQUIRES_QA",
        "binding": "ts_std_dev(close / ts_delay(close, 1) - 1, 20)",
    },
}


def record(alpha_id, expression, fields, operators, neutralization="NONE", description=None):
    return {
        "alpha_id": alpha_id,
        "definition_hash": f"hash-{alpha_id}",
        "settings": {"delay": 1, "decay": 0, "neutralization": neutralization,
                     "universe": "TOP3000", "truncation": 0.02},
        "dependencies": {
            "safe": True,
            "fields": [{"identifier": f, "type": "MATRIX"} for f in fields],
            "operators": operators,
            "unknown_identifiers": [],
            "unsupported_syntax": [],
        },
        "raw": {
            "regular": {"code": expression, "description": description},
            "is": {"sharpe": 9.9},
            "train": {"returns": 1.0},
            "checks": [{"name": "LOW_SHARPE", "result": "PASS"}],
        },
        "provenance": {"source_path": "private/source.json", "record_index": 0},
    }


def mapping(*rows):
    return {row[0]: {"field_id": row[0], "dataset": row[1], "description": row[2]}
            for row in rows}


def test_source_view_excludes_platform_performance():
    source = build_source_view([record("A", "rank(-returns)", ["returns"], ["rank"])])
    serialized = repr(source)
    assert "sharpe" not in serialized
    assert "returns': 1.0" not in serialized
    assert "LOW_SHARPE" not in serialized
    assert source[0]["expression"] == "rank(-returns)"


def test_fundamental_only_source_is_deferred_instead_of_relabelled_as_momentum():
    item = record("F", "rank(cashflow_op/cap)", ["cashflow_op", "cap"], ["rank"])
    result = convert_library(
        build_source_view([item]),
        mapping(("cashflow_op", "fundamental2", "Operating cash flow"),
                ("cap", "pv1", "Market capitalization")),
        CAPABILITIES,
    )
    assert result["decisions"][0]["decision"] == "DEFERRED"
    assert result["decisions"][0]["semantic_status"] == "INSUFFICIENT_LINK"
    assert "fundamental_quality" in result["cards"][0]["mechanisms"]
    assert result["factors"] == []
    assert result["summary"]["deferred_blocker_counts"]["CAPABILITY_NOT_VERIFIED"] == 2
    assert result["summary"]["deferred_mechanism_counts"]["fundamental_quality"] == 1


def test_implied_volatility_spread_is_not_collapsed_to_realized_volatility():
    item = record(
        "V",
        "rank(implied_volatility_mean_30-historical_volatility_20)",
        ["implied_volatility_mean_30", "historical_volatility_20"],
        ["rank"],
    )
    result = convert_library(
        build_source_view([item]),
        mapping(("implied_volatility_mean_30", "option8", "Mean implied volatility 30 days"),
                ("historical_volatility_20", "option8", "Historical volatility 20 days")),
        CAPABILITIES,
    )
    decision = result["decisions"][0]
    assert decision["decision"] == "DEFERRED"
    assert "FORWARD_LOOKING_OPTION_INFORMATION_LOST" in decision["blockers"]


def test_same_local_definition_has_one_factor_and_two_source_links():
    records = [
        record("A", "rank(-returns)", ["returns"], ["rank"]),
        record("B", "rank(-returns)", ["returns"], ["rank"]),
    ]
    result = convert_library(
        build_source_view(records),
        mapping(("returns", "pv1", "Daily return")),
        CAPABILITIES,
    )
    assert len(result["factors"]) == 1
    assert result["factors"][0]["source_alpha_ids"] == ["A", "B"]
    assert result["summary"]["source_count"] == 2
    assert result["summary"]["unique_factor_count"] == 1
    assert result["summary"]["direct_source_count"] == 0
    assert result["summary"]["proxy_source_count"] == 2
    assert result["summary"]["family_count"] == 1


def test_different_price_volume_formula_shapes_are_different_families():
    records = [
        record("R", "rank(-returns)", ["returns"], ["rank"]),
        record("V", "rank(volume)", ["volume"], ["rank"]),
    ]
    result = convert_library(
        build_source_view(records),
        mapping(("returns", "pv1", "Daily return"), ("volume", "pv1", "Volume")),
        CAPABILITIES,
    )
    assert result["summary"]["family_count"] == 2
    assert len({factor["family_id"] for factor in result["factors"]}) == 2


def test_settings_neutralization_without_classification_is_deferred_not_rewritten():
    item = record("N", "rank(-returns)", ["returns"], ["rank"], "SUBINDUSTRY")
    result = convert_library(
        build_source_view([item]), mapping(("returns", "pv1", "Daily return")), CAPABILITIES
    )
    assert result["factors"] == []
    decision = result["decisions"][0]
    assert decision["decision"] == "DEFERRED"
    assert "SETTINGS_NEUTRALIZATION_UNSUPPORTED:SUBINDUSTRY" in decision["blockers"]
    assert decision["settings_audit"]["neutralization"] == "UNSUPPORTED_BLOCKING"


def test_all_source_settings_are_preserved_and_audited():
    item = record("S", "rank(close)", ["close"], ["rank"])
    result = convert_library(
        build_source_view([item]), mapping(("close", "pv1", "Close")), CAPABILITIES
    )
    factor = result["factors"][0]
    lineage = factor["source_lineage"][0]
    assert lineage["source_settings"] == item["settings"]
    assert set(lineage["settings_audit"]) == set(item["settings"])
    assert lineage["settings_audit"]["delay"] == "PRESERVED"
    assert lineage["settings_audit"]["universe"] == "REPLACED_BY_PROJECT_CALC_UNIVERSE"
    assert lineage["settings_audit"]["truncation"] == "NOT_IMPLEMENTED_LOCAL"
    assert result["decisions"][0]["settings_audit"] == lineage["settings_audit"]
    assert list(factor["settings_audit"]) == ["delay", "decay", "neutralization"]


def test_distinct_calculation_settings_are_not_deduplicated():
    left = record("A", "rank(close)", ["close"], ["rank"])
    right = record("B", "rank(close)", ["close"], ["rank"])
    right["settings"]["truncation"] = 0.08
    result = convert_library(
        build_source_view([left, right]), mapping(("close", "pv1", "Close")), CAPABILITIES
    )
    assert len(result["factors"]) == 2


def test_group_operator_without_classification_is_deferred():
    item = record("G", "group_rank(-returns,subindustry)", ["returns", "subindustry"],
                  ["group_rank"], "SUBINDUSTRY")
    result = convert_library(
        build_source_view([item]),
        mapping(("returns", "pv1", "Daily return"),
                ("subindustry", "pv1", "Subindustry classification")),
        CAPABILITIES,
    )
    assert result["decisions"][0]["decision"] == "DEFERRED"
    assert "GROUP_RELATION_IS_ESSENTIAL" in result["decisions"][0]["blockers"]


def test_description_formula_conflict_remains_unresolved():
    item = record("C", "rank(-returns)", ["returns"], ["rank"],
                  description="High operating cash flow quality should outperform")
    result = convert_library(
        build_source_view([item]), mapping(("returns", "pv1", "Daily return")), CAPABILITIES
    )
    assert result["cards"][0]["description_formula_conflict"] is True
    assert result["decisions"][0]["decision"] == "DEFERRED"


def test_unverified_capability_cannot_be_compute_verified():
    item = record("U", "rank(-returns)", ["returns"], ["rank"])
    capabilities = copy.deepcopy(CAPABILITIES)
    capabilities["returns"]["status"] = "UNVERIFIED"
    result = convert_library(
        build_source_view([item]), mapping(("returns", "pv1", "Daily return")), capabilities
    )
    assert result["decisions"][0]["decision"] == "DEFERRED"
    assert result["summary"]["compute_verified_count"] == 0


def test_unsafe_source_is_not_executed_but_keeps_a_card():
    item = record("X", "danger.system(close)", ["close"], [], "NONE")
    item["dependencies"]["safe"] = False
    result = convert_library(
        build_source_view([item]), mapping(("close", "pv1", "Close")), CAPABILITIES
    )
    assert len(result["cards"]) == 1
    assert result["decisions"][0]["decision"] == "DEFERRED"
    assert "SOURCE_EXPRESSION_UNSAFE" in result["decisions"][0]["blockers"]


def test_platform_metrics_do_not_flow_into_proxy_factor():
    item = record("P", "rank(-returns)", ["returns"], ["rank"])
    result = convert_library(
        build_source_view([item]), mapping(("returns", "pv1", "Daily return")), CAPABILITIES
    )
    assert result["factors"][0]["brain_value_parity"] == "NOT_CLAIMED"
    assert "sharpe" not in repr(result)


def test_default_capabilities_only_claim_observed_and_explicit_derivations():
    capabilities = default_market_capabilities()
    assert capabilities["close"]["status"] == "VERIFIED_SAMPLE"
    assert capabilities["vwap"]["providers"] == ["alpaca_sip"]
    assert capabilities["returns"]["binding"] == "close / ts_delay(close, 1) - 1"
    assert capabilities["historical_volatility_30"]["status"] == "DERIVED_REQUIRES_QA"
    assert "cap" not in capabilities
    assert "implied_volatility_mean_30" not in capabilities


def test_verifier_promotes_only_factors_that_compute_and_pass_prefix_check():
    good = record("A", "rank(-returns)", ["returns"], ["rank"])
    bad = record("B", "rank(log(-abs(close)))", ["close"], ["rank", "log", "abs"])
    converted = convert_library(
        build_source_view([good, bad]), mapping(("returns", "pv1", "Daily return"),
                                                  ("close", "pv1", "Close")), CAPABILITIES
    )
    index = pd.date_range("2024-01-02", periods=8, tz="UTC")
    market = {
        "close": pd.DataFrame({"AAPL": range(100, 108), "MSFT": range(50, 58)}, index=index),
        "volume": pd.DataFrame({"AAPL": range(10, 18), "MSFT": range(20, 28)}, index=index),
    }
    verified, matrices = verify_factors(converted, {"fixture": market})
    by_source = {factor["source_alpha_ids"][0]: factor for factor in verified["factors"]}
    assert by_source["A"]["build_status"] == "COMPUTE_VERIFIED"
    assert by_source["A"]["checks"]["fixture"]["prefix_invariant"] is True
    assert by_source["B"]["build_status"] == "ENGINEERING_FAILED"
    assert by_source["B"]["usage_tier"] == "DIAGNOSTIC_ONLY"
    assert set(matrices) == {by_source["A"]["local_factor_id"]}


def test_verifier_keeps_long_window_factor_compiled_when_sample_is_too_short():
    item = record("L", "rank(close / ts_delay(close, 20) - 1)", ["close"],
                  ["rank", "ts_delay"])
    converted = convert_library(
        build_source_view([item]), mapping(("close", "pv1", "Close")), CAPABILITIES
    )
    index = pd.date_range("2024-01-02", periods=8, tz="UTC")
    close = pd.DataFrame({"AAPL": range(100, 108), "MSFT": range(50, 58)}, index=index)
    verified, matrices = verify_factors(converted, {"fixture": {"close": close}})
    factor = verified["factors"][0]
    assert factor["build_status"] == "COMPILED"
    assert factor["checks"]["fixture"]["status"] == "INSUFFICIENT_SAMPLE"
    assert matrices == {}
    assert verified["summary"]["insufficient_sample_count"] == 1


def test_nested_windows_and_decay_are_included_in_required_history():
    item = record("L", "ts_mean(ts_delay(close,5),5)", ["close"],
                  ["ts_mean", "ts_delay"])
    item["settings"]["decay"] = 2
    converted = convert_library(
        build_source_view([item]), mapping(("close", "pv1", "Close")), CAPABILITIES
    )
    index = pd.date_range("2024-01-02", periods=8, tz="UTC")
    close = pd.DataFrame({"AAPL": range(100, 108), "MSFT": range(50, 58)}, index=index)
    verified, matrices = verify_factors(converted, {"fixture": {"close": close}})
    factor = verified["factors"][0]
    assert factor["build_status"] == "COMPILED"
    assert factor["checks"]["fixture"] == {
        "status": "INSUFFICIENT_SAMPLE", "rows": 8, "required_history": 11,
    }
    assert matrices == {}


def test_ts_delta_needs_the_full_shift_window_of_history():
    item = record("D", "ts_delta(close,7)", ["close"], ["ts_delta"])
    item["settings"]["delay"] = 0
    converted = convert_library(
        build_source_view([item]), mapping(("close", "pv1", "Close")), CAPABILITIES
    )
    index = pd.date_range("2024-01-02", periods=7, tz="UTC")
    close = pd.DataFrame({"AAPL": range(100, 107), "MSFT": range(50, 57)}, index=index)
    verified, _ = verify_factors(converted, {"fixture": {"close": close}})
    factor = verified["factors"][0]
    assert factor["build_status"] == "COMPILED"
    assert factor["checks"]["fixture"]["required_history"] == 7


def test_assignment_chain_is_not_treated_as_provider_inputs_and_accumulates_warmup():
    item = record("A", "a=ts_mean(close,5); b=ts_mean(a,5); b", ["close"],
                  ["ts_mean"])
    item["settings"]["delay"] = 0
    converted = convert_library(
        build_source_view([item]), mapping(("close", "pv1", "Close")), CAPABILITIES
    )
    index = pd.date_range("2024-01-02", periods=8, tz="UTC")
    close = pd.DataFrame({"AAPL": range(100, 108), "MSFT": range(50, 58)}, index=index)
    verified, _ = verify_factors(converted, {"fixture": {"close": close}})
    factor = verified["factors"][0]
    assert factor["build_status"] == "COMPILED"
    assert factor["checks"]["fixture"] == {
        "status": "INSUFFICIENT_SAMPLE", "rows": 8, "required_history": 8,
    }


def test_writer_preserves_all_cards_decisions_and_manifest(tmp_path):
    converted = convert_library(
        build_source_view([record("A", "rank(-returns)", ["returns"], ["rank"])]),
        mapping(("returns", "pv1", "Daily return")), CAPABILITIES,
    )
    write_conversion_artifacts(converted, {}, default_market_capabilities(), tmp_path)
    assert len((tmp_path / "hypothesis_cards.jsonl").read_text().splitlines()) == 1
    assert len((tmp_path / "proxy_decisions.jsonl").read_text().splitlines()) == 1
    library = json.loads((tmp_path / "project_factor_library.json").read_text())
    assert library["factors"][0]["source_alpha_ids"] == ["A"]
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert "project_factor_library.json" in manifest
    assert "conversion_policy.json" in manifest
    assert "proxy_templates.json" in manifest


def test_verifier_accepts_one_supported_provider_and_marks_other_not_applicable():
    item = record("W", "rank(vwap-close)", ["vwap", "close"], ["rank"])
    capabilities = dict(CAPABILITIES)
    capabilities["vwap"] = {"status": "VERIFIED_SAMPLE", "binding": "vwap"}
    converted = convert_library(
        build_source_view([item]),
        mapping(("vwap", "pv1", "VWAP"), ("close", "pv1", "Close")),
        capabilities,
    )
    index = pd.date_range("2024-01-02", periods=8, tz="UTC")
    close = pd.DataFrame({"AAPL": range(100, 108), "MSFT": range(50, 58)}, index=index)
    verified, matrices = verify_factors(converted, {
        "alpaca": {"close": close, "vwap": close + 0.25},
        "tiingo": {"close": close},
    })
    factor = verified["factors"][0]
    assert factor["build_status"] == "COMPUTE_VERIFIED"
    assert factor["checks"]["tiingo"]["status"] == "NOT_APPLICABLE"
    assert set(matrices[factor["local_factor_id"]]) == {"alpaca"}


def test_if_else_comparison_is_safe_and_computable():
    item = record("I", "rank(ts_mean(if_else(returns > 0, 1, 0), 3))",
                  ["returns"], ["rank", "ts_mean", "if_else"])
    converted = convert_library(
        build_source_view([item]), mapping(("returns", "pv1", "Daily return")), CAPABILITIES
    )
    index = pd.date_range("2024-01-02", periods=8, tz="UTC")
    close = pd.DataFrame({"AAPL": [10, 11, 10, 12, 13, 12, 14, 15],
                          "MSFT": [10, 9, 10, 8, 7, 8, 6, 5]}, index=index)
    verified, _ = verify_factors(converted, {"fixture": {"close": close}})
    assert verified["factors"][0]["build_status"] == "COMPUTE_VERIFIED"
