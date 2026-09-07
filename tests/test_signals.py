import numpy as np
import pandas as pd
import pytest

from us_equity_alpha.factors import UnsupportedExpression, evaluate_factor
from us_equity_alpha.signals import combine_equal_weight, select_top_n


def test_factor_evaluation_applies_setting_delay_once_before_formula():
    dates = pd.date_range("2025-01-01", periods=4, tz="UTC")
    close = pd.DataFrame({"A": [1.0, 2.0, 4.0, 8.0], "B": [1.0, 3.0, 4.0, 5.0]}, index=dates)
    result = evaluate_factor(
        "rank(ts_delta(close, 1))",
        {"close": close},
        {"delay": 1, "decay": 0, "neutralization": "NONE"},
    )
    assert np.isnan(result.iloc[1]).all()
    assert result.iloc[2].to_dict() == {"A": 0.5, "B": 1.0}


def test_unsupported_operator_and_neutralization_are_blocked():
    values = pd.DataFrame({"A": [1.0]})
    with pytest.raises(UnsupportedExpression):
        evaluate_factor("mystery(close)", {"close": values}, {"delay": 1, "decay": 0, "neutralization": "NONE"})
    with pytest.raises(UnsupportedExpression):
        evaluate_factor("rank(close)", {"close": values}, {"delay": 1, "decay": 0, "neutralization": "INDUSTRY"})


def test_prefix_invariance_and_fixed_factor_coverage():
    dates = pd.date_range("2025-01-01", periods=5, tz="UTC")
    close = pd.DataFrame({"A": [1, 2, 4, 3, 5], "B": [1, 3, 2, 5, 4]}, index=dates, dtype=float)
    settings = {"delay": 1, "decay": 0, "neutralization": "NONE"}
    short = evaluate_factor("rank(ts_delta(close, 1))", {"close": close.iloc[:4]}, settings)
    long = evaluate_factor("rank(ts_delta(close, 1))", {"close": close}, settings)
    pd.testing.assert_frame_equal(short, long.iloc[:4])

    composite = combine_equal_weight({"f1": short, "f2": short * -1}, expected_factor_ids=["f1", "f2"])
    assert composite.notna().equals(short.notna())
    with pytest.raises(ValueError, match="MISSING_FACTOR"):
        combine_equal_weight({"f1": short}, expected_factor_ids=["f1", "f2"])


def test_top_n_has_deterministic_tie_break_and_equal_weights():
    scores = pd.Series({"MSFT": 0.8, "AAPL": 0.8, "NVDA": 0.7})
    result = select_top_n(scores, n=2, cash_buffer=0.05)
    assert result["ticker"].tolist() == ["AAPL", "MSFT", "NVDA"]
    assert result["status"].tolist() == ["BUY", "BUY", "OUT"]
    assert result.loc[result.status == "BUY", "target_weight"].tolist() == [0.475, 0.475]
