import numpy as np
import pandas as pd

from us_equity_alpha.operators import (
    cs_rank,
    group_neutralize,
    group_rank,
    linear_decay,
    price_delta,
    ts_delay,
    ts_arg_max,
    ts_arg_min,
    ts_corr,
    ts_covariance,
    ts_mean,
    ts_rank,
    ts_scale,
    ts_sum,
    ts_std_dev,
    days_from_last_change,
)


def test_price_difference_is_not_percentage_return():
    prices = pd.DataFrame({"A": [100.0, 110.0], "B": [5.0, 6.0]})
    delta = price_delta(prices, window=1).iloc[-1]
    returns = prices.iloc[-1] / prices.iloc[0] - 1
    assert delta["A"] == 10.0 and delta["B"] == 1.0
    assert returns["A"] < returns["B"]


def test_core_time_series_operators_have_locked_window_semantics():
    values = pd.DataFrame({"A": [1.0, 2.0, 2.0, 4.0]})
    assert ts_delay(values, 1).iloc[-1, 0] == 2.0
    assert ts_mean(values, 2).iloc[-1, 0] == 3.0
    assert ts_std_dev(values, 2).iloc[-1, 0] == np.std([2.0, 4.0], ddof=0)
    assert ts_rank(values.iloc[:3], 3).iloc[-1, 0] == 2.5 / 3.0


def test_cross_section_and_group_operators_preserve_missing_values():
    values = pd.DataFrame([[1.0, 3.0, np.nan, 2.0]], columns=list("ABCD"))
    groups = pd.Series({"A": "X", "B": "X", "C": "Y", "D": "Y"})
    ranked = cs_rank(values)
    assert ranked.loc[0, "A"] == 1 / 3 and ranked.loc[0, "B"] == 1.0
    assert np.isnan(ranked.loc[0, "C"])
    grouped = group_rank(values, groups)
    assert grouped.loc[0, "A"] == 0.5 and grouped.loc[0, "B"] == 1.0
    assert np.isnan(grouped.loc[0, "C"])
    neutral = group_neutralize(values, groups)
    assert neutral.loc[0, "A"] == -1.0 and neutral.loc[0, "B"] == 1.0
    assert np.isnan(neutral.loc[0, "C"])


def test_linear_decay_weights_recent_values_more_heavily():
    values = pd.DataFrame({"A": [1.0, 2.0]})
    assert linear_decay(values, 2).iloc[-1, 0] == 5.0 / 3.0


def test_sum_correlation_and_population_covariance_use_full_windows():
    left = pd.DataFrame({"A": [1.0, 2.0, 3.0], "B": [1.0, np.nan, 3.0]})
    right = pd.DataFrame({"A": [2.0, 4.0, 6.0], "B": [1.0, 2.0, 3.0]})
    assert ts_sum(left, 3).iloc[-1, 0] == 6.0
    assert np.isclose(ts_corr(left, right, 3).iloc[-1, 0], 1.0)
    assert np.isclose(ts_covariance(left, right, 3).iloc[-1, 0], 4.0 / 3.0)
    assert np.isnan(ts_corr(left, right, 3).iloc[-1, 1])


def test_arg_extrema_count_sessions_back_from_current_row():
    values = pd.DataFrame({"A": [5.0, 1.0, 3.0, 9.0]})
    assert ts_arg_min(values, 4).iloc[-1, 0] == 2.0
    assert ts_arg_max(values, 4).iloc[-1, 0] == 0.0


def test_time_series_scale_and_days_since_change_have_explicit_semantics():
    values = pd.DataFrame({"A": [2.0, 2.0, 4.0, 4.0], "B": [1.0, 1.0, 1.0, 1.0]})
    scaled = ts_scale(values, 3)
    assert scaled.iloc[-1, 0] == 1.0
    assert np.isnan(scaled.iloc[-1, 1])
    changed = days_from_last_change(values)
    assert changed["A"].tolist() == [0.0, 1.0, 0.0, 1.0]
    assert changed["B"].tolist() == [0.0, 1.0, 2.0, 3.0]
