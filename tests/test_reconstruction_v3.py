import numpy as np
import pandas as pd

from us_equity_alpha.derived_capabilities import add_local_market_capabilities
from us_equity_alpha.proxy_converter import default_market_capabilities
from us_equity_alpha.reconstruction import reconstruct_library


def source(alpha_id, expression, description=None):
    return {
        "alpha_id": alpha_id,
        "expression": expression,
        "description": description,
        "settings": {"neutralization": "SUBINDUSTRY"},
    }


def test_functional_price_arithmetic_is_a_substantive_observable():
    result = reconstruct_library(
        [source("A", "rank(ts_mean(divide(subtract(close,vwap),add(vwap,0.0001)),5))")],
        default_market_capabilities(),
    )
    assert result["summary"]["reconstructed_source_count"] == 1
    assert result["factors"][0]["usage_tier"] == "DIAGNOSTIC_ONLY"


def test_filtered_variadic_add_uses_local_missing_policy_and_records_loss():
    result = reconstruct_library(
        [source("A", "add(rank(-returns),rank(-volume),rank(close),filter=true)")],
        default_market_capabilities(),
    )
    factor = result["factors"][0]
    assert "+" in factor["expression"]
    assert "SOURCE_FILTER_SEMANTICS_REDEFINED_MISSING_PROPAGATES" in factor["lost"]


def test_safe_outer_wrapper_can_release_only_explicit_additive_ranked_component():
    result = reconstruct_library(
        [source("A", "hump(0.5*rank(-returns)+0.5*rank(cashflow_op/cap))")],
        default_market_capabilities(),
    )
    factor = result["factors"][0]
    assert factor["expression"] == "rank(-(close / ts_delay(close, 1) - 1))"
    assert "SOURCE_WRAPPER_NOT_RETAINED:hump" in factor["lost"]
    assert result["decisions"][0]["semantic_status"] == "PARTIAL_INTENT"


def test_description_formula_conflict_keeps_description_and_related_formula_tracks():
    result = reconstruct_library(
        [source("A", "rank(-(1-open/close))", "High operating income versus debt")],
        default_market_capabilities(),
    )
    assert result["summary"]["reconstructed_source_count"] == 1
    assert result["decisions"][0]["semantic_status"] == "RELATED_NEW_HYPOTHESIS"
    tracks = result["cards"][0]["hypothesis_tracks"]
    assert tracks[0]["status"] == "DEFERRED_DESCRIPTION_HYPOTHESIS"
    assert tracks[1]["status"] == "RELATED_FORMULA_HYPOTHESIS"


def test_unverified_regression_semantics_stay_deferred():
    result = reconstruct_library(
        [source("A", "rank(ts_regression(close,ts_step(1),20,lag=0,rettype=2))")],
        default_market_capabilities(),
    )
    assert not result["factors"]
    assert result["decisions"][0]["blockers"] == [
        "OPERATOR_SEMANTICS_UNVERIFIED:ts_regression"
    ]


def test_authorized_derived_field_is_translated_but_never_claims_value_parity():
    close = pd.DataFrame(
        np.exp(np.random.default_rng(4).normal(0, 0.01, (500, 3)).cumsum(0)),
        columns=["A", "B", "SPY"],
    )
    inputs = {
        "tiingo_eod": {
            "close": close,
            "high": close + 0.02,
            "low": close - 0.02,
            "volume": close * 1000,
        }
    }
    _, caps = add_local_market_capabilities(
        inputs,
        default_market_capabilities(
            {"tiingo_eod": {"close", "high", "low", "volume"}}
        ),
    )
    result = reconstruct_library(
        [source("A", "group_rank(ts_rank(-correlation_last_90_days_spy,63),industry)")],
        caps,
    )
    factor = result["factors"][0]
    assert "ts_corr" in factor["expression"]
    assert "SOURCE_FIELD_PARITY_NOT_CLAIMED:correlation_last_90_days_spy" in factor["lost"]
    assert factor["usage_tier"] == "DIAGNOSTIC_ONLY"
