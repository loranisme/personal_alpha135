import copy
import numpy as np
import pandas as pd
import pytest
import copy

from us_equity_alpha.reconstruction import reconstruct_library
from us_equity_alpha.proxy_converter import default_market_capabilities, verify_factors
from us_equity_alpha.factor_library import build_registry_view


def source(id, expression, **extra):
    return {"alpha_id": id, "expression": expression, "settings": {"neutralization": "SUBINDUSTRY"},
            "description": None, "dependencies": {}, **extra}


def build(rows, caps=None):
    return reconstruct_library(rows, caps or default_market_capabilities())


def test_local_policy_removes_parity_blocker_but_records_lost_group_relation():
    result = build([source("A", "group_rank(-ts_mean(returns,3),subindustry)")])
    f = result["factors"][0]
    assert f["provenance_kind"] == "LOCAL_RECONSTRUCTION"
    assert f["settings"]["neutralization"] == "NONE"
    assert f["semantic_status"] == "PARTIAL_INTENT"
    assert "SOURCE_GROUP_RELATION_REDEFINED" in f["lost"]
    assert f["usage_tier"] == "DIAGNOSTIC_ONLY"
    assert result["summary"]["research_eligible_count"] == 0


def test_close_denominator_and_implied_realized_spread_are_not_price_hypotheses():
    rows = [source("A", "rank(cashflow_op/close)"),
            source("B", "rank(-(implied_volatility_mean_30-historical_volatility_30))")]
    r = build(rows)
    assert not r["factors"]
    assert r["summary"]["deferred_source_count"] == 2
    assert all(x["missing_fields"] for x in r["cards"])


def test_separable_component_is_partial_and_source_coverage_is_not_formula_count():
    r = build([source("A", "0.25*rank(cashflow_op/cap)+0.75*rank(-ts_mean(returns,3))"),
               source("B", "rank(-ts_mean(returns,3))")])
    assert len(r["factors"]) == 1
    assert r["factors"][0]["source_alpha_ids"] == ["A", "B"]
    a = next(x for x in r["decisions"] if x["source_alpha_id"] == "A")
    assert a["semantic_status"] == "PARTIAL_INTENT"
    assert r["summary"]["reconstructed_source_count"] == 2
    assert r["summary"]["unique_factor_count"] == 1


def test_performance_and_source_order_cannot_change_local_definitions():
    rows = [source("B", "rank(-returns)"), source("A", "rank(-returns)")]
    x = build(rows)
    y = copy.deepcopy(rows[::-1])
    y[0]["Sharpe"] = 99
    y[1]["returns"] = [100, 200]
    assert x == build(y)


def test_unknown_source_and_unsafe_code_never_executed():
    r = build([source("A", "__import__('os').system('echo bad')"), source("B", "rank(model_private)")])
    assert not r["factors"]
    assert r["summary"]["card_count"] == 2


def test_targets_are_diagnostic_t3_compatible_and_prefix_invariant():
    r = build([source("A", "rank(-ts_mean(returns,3))")])
    rng = np.random.default_rng(19)
    close = pd.DataFrame(100*np.exp(np.cumsum(rng.normal(0,.01,(40,4)),axis=0)))
    verified, matrices = verify_factors(r, {"tiingo_eod": {"close": close}})
    f = verified["factors"][0]
    assert f["build_status"] == "COMPUTE_VERIFIED"
    assert f["checks"]["tiingo_eod"]["prefix_invariant"]
    assert matrices[f["local_factor_id"]]
    assert build_registry_view(verified, [f["local_factor_id"]], usage="diagnostic")
    with pytest.raises(ValueError, match="FACTOR_NOT_RESEARCH_ELIGIBLE"):
        build_registry_view(verified, [f["local_factor_id"]], usage="research")


def test_unverified_field_cannot_be_admitted():
    caps = default_market_capabilities()
    caps["vwap"]["status"] = "UNAVAILABLE_OR_UNVERIFIED"
    r = build([source("A", "rank((vwap-close)/vwap)")],caps)
    assert not r["factors"]


def test_local_factor_identity_ignores_verification_sample_coverage():
    first_caps = default_market_capabilities({"tiingo_eod": {"close"}})
    second_caps = copy.deepcopy(first_caps)
    first_caps["close"]["coverage_scope"] = {
        "tiingo_eod": {"symbols": 4, "sessions": 124}
    }
    second_caps["close"]["coverage_scope"] = {
        "tiingo_eod": {"symbols": 50, "sessions": 1572}
    }
    first = build([source("A", "rank(-returns)")], first_caps)
    second = build([source("A", "rank(-returns)")], second_caps)
    assert first["factors"][0]["local_factor_id"] == second["factors"][0][
        "local_factor_id"
    ]


def test_bad_arithmetic_does_not_abort_other_sources():
    r=build([source('bad','rank(returns + 1/0)'),source('good','rank(-returns)')])
    close=pd.DataFrame([[100+i,90+2*i] for i in range(30)],dtype=float)
    result,_=verify_factors(r,{'tiingo_eod':{'close':close}})
    assert result['summary']['engineering_failed_count']==1
    assert result['summary']['compute_verified_count']==1


def test_t3_recomputes_beta_from_ordinary_bar_inputs_and_checks_provider():
    from us_equity_alpha.factor_library import compute_registry_factors
    from us_equity_alpha.reconstruction_pipeline import add_benchmark_capabilities
    rng=np.random.default_rng(7)
    close=pd.DataFrame(np.exp(rng.normal(0,.01,(400,3)).cumsum(0)),columns=['A','B','SPY'])
    raw={'tiingo_eod':{'close':close}}
    augmented,caps=add_benchmark_capabilities(raw,default_market_capabilities({'tiingo_eod':{'close'}}))
    r=reconstruct_library([source('B','rank(-(beta_last_30_days_spy-beta_last_360_days_spy))')],caps)
    verified,matrices=verify_factors(r,augmented)
    fid=verified['factors'][0]['local_factor_id']
    observed=compute_registry_factors(verified,[fid],'tiingo_eod',raw['tiingo_eod'],usage='diagnostic')
    assert list(observed[fid].columns) == ['A', 'B']
    pd.testing.assert_frame_equal(observed[fid],matrices[fid]['tiingo_eod'])
    with pytest.raises(ValueError,match='PROVIDER_NOT_BOUND'):
        compute_registry_factors(verified,[fid],'invented',raw['tiingo_eod'],usage='diagnostic')
    with pytest.raises(ValueError,match='BENCHMARK_SPY_REQUIRED'):
        compute_registry_factors(verified,[fid],'tiingo_eod',{'close':close.drop(columns='SPY')},usage='diagnostic')
