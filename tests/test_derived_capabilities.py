import numpy as np
import pandas as pd

from us_equity_alpha.derived_capabilities import add_local_market_capabilities
from us_equity_alpha.proxy_converter import default_market_capabilities


def panels(include_spy=True):
    columns = ["A", "SPY"] if include_spy else ["A"]
    close = pd.DataFrame(
        np.arange(40 * len(columns), dtype=float).reshape(40, len(columns)) + 100,
        columns=columns,
    )
    return {
        "close": close,
        "high": close + 2,
        "low": close - 2,
        "volume": close * 1000,
    }


def test_adds_price_proxies_with_explicit_local_bindings():
    inputs = {"tiingo_eod": panels()}
    caps = default_market_capabilities(
        {"tiingo_eod": {"close", "high", "low", "volume"}}
    )
    augmented, observed = add_local_market_capabilities(inputs, caps)
    assert observed["news_close_vol"]["binding"] == "volume"
    assert "ts_mean" in observed["news_atr14"]["binding"]
    assert "20" in observed["news_atr_ratio"]["binding"]
    assert observed["news_atr_ratio"]["semantic_status"] == "PARTIAL_INTENT"
    assert observed["news_atr_ratio"]["local_formula_authorized"] is True
    assert "benchmark_returns" in augmented["tiingo_eod"]
    assert list(augmented["tiingo_eod"]["close"].columns) == ["A"]
    assert list(augmented["tiingo_eod"]["benchmark_returns"].columns) == ["A"]


def test_adds_registered_spy_risk_bindings_only_when_spy_is_observed():
    inputs = {"with_spy": panels(True), "without_spy": panels(False)}
    caps = default_market_capabilities(
        {
            "with_spy": {"close", "high", "low", "volume"},
            "without_spy": {"close", "high", "low", "volume"},
        }
    )
    _, observed = add_local_market_capabilities(inputs, caps)
    assert observed["beta_last_90_days_spy"]["providers"] == ["with_spy"]
    assert observed["correlation_last_360_days_spy"]["providers"] == ["with_spy"]
    assert observed["systematic_risk_last_60_days"]["providers"] == ["with_spy"]
    assert observed["unsystematic_risk_last_90_days"]["providers"] == ["with_spy"]
    assert "without_spy" in observed["news_atr14"]["providers"]
    assert observed["systematic_risk_last_60_days"]["historical_pit_verified"] is False
