import numpy as np
import pandas as pd
import pytest

from us_equity_alpha.alpha_filter_diagnostics import (
    diagnose_factor,
    forward_open_returns,
    weekly_signal_sessions,
    weekly_top10_turnover,
)


def _config() -> dict:
    return {
        "quantiles": 5,
        "benchmark_security_id": "SPY",
        "full_year_min_signal_sessions": 26,
        "partial_final_year_min_signal_sessions": 13,
    }


def _predictive_panels() -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = pd.date_range("2025-01-03", periods=30, freq="7D")
    columns = [f"S{i:02d}" for i in range(20)]
    base = np.arange(20, dtype=float)
    ranks = pd.DataFrame(np.tile(base, (len(dates), 1)), index=dates, columns=columns)
    returns = pd.DataFrame(
        np.tile(base / 1000, (len(dates), 1)), index=dates, columns=columns
    )
    return ranks, returns


def test_weekly_signal_sessions_are_session_before_first_session_of_new_week():
    sessions = pd.DatetimeIndex(
        pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-12"])
    )
    assert weekly_signal_sessions(sessions) == [sessions[0], sessions[2]]


def test_forward_label_uses_t1_open_and_t6_open():
    sessions = pd.bdate_range("2026-01-02", periods=8)
    opens = pd.DataFrame({"A": range(100, 108)}, index=sessions)
    result = forward_open_returns(opens, [sessions[0]], horizon=5)
    assert result.loc[sessions[0], "A"] == pytest.approx(106 / 101 - 1)


def test_forward_label_preserves_missing_entry_or_exit_price():
    sessions = pd.bdate_range("2026-01-02", periods=8)
    opens = pd.DataFrame({"A": range(100, 108)}, index=sessions, dtype=float)
    opens.loc[sessions[1], "A"] = np.nan
    result = forward_open_returns(opens, [sessions[0]], horizon=5)
    assert pd.isna(result.loc[sessions[0], "A"])


def test_spy_is_excluded_before_cross_sectional_metrics():
    ranks, returns = _predictive_panels()
    ranks["SPY"] = 0.5
    returns["SPY"] = 0.1
    with pytest.raises(ValueError, match="BENCHMARK_IN_FACTOR_CROSS_SECTION"):
        diagnose_factor(ranks, returns, _config())


def test_diagnostics_report_weekly_ic_years_quantiles_and_turnover():
    ranks, returns = _predictive_panels()
    metrics, yearly = diagnose_factor(ranks, returns, _config())
    assert metrics["valid_signal_sessions"] == 30
    assert metrics["median_cross_section_coverage"] == 1.0
    assert metrics["mean_rank_ic_5d"] == pytest.approx(1.0)
    assert metrics["icir_5d"] is None
    assert metrics["positive_year_ratio"] == 1.0
    assert metrics["q5_minus_q1_5d"] > 0
    assert metrics["weekly_top10_turnover"] == 0.0
    assert set(yearly.columns) == {
        "year",
        "valid_signal_sessions",
        "mean_rank_ic_5d",
        "year_status",
    }
    assert yearly.iloc[0].year_status == "FULL_YEAR"


def test_partial_final_year_requires_thirteen_valid_sessions():
    ranks, returns = _predictive_panels()
    metrics, yearly = diagnose_factor(ranks.iloc[:13], returns.iloc[:13], _config())
    assert metrics["positive_year_ratio"] == 1.0
    assert yearly.iloc[0].year_status == "PARTIAL_YEAR"


def test_weekly_top10_turnover_uses_prior_top_set_as_denominator():
    dates = pd.to_datetime(["2026-01-02", "2026-01-09"])
    columns = [f"S{i}" for i in range(10)]
    ranks = pd.DataFrame(
        [range(10), [9, *range(9)]], index=dates, columns=columns, dtype=float
    )
    assert weekly_top10_turnover(ranks) == 1.0


def test_zero_valid_ic_sessions_returns_missing_metrics_instead_of_crashing():
    dates = pd.date_range("2025-01-03", periods=30, freq="7D")
    ranks = pd.DataFrame(1.0, index=dates, columns=["A", "B"])
    returns = pd.DataFrame(0.0, index=dates, columns=["A", "B"])
    metrics, yearly = diagnose_factor(ranks, returns, _config())
    assert metrics["valid_signal_sessions"] == 0
    assert metrics["mean_rank_ic_5d"] is None
    assert metrics["positive_year_ratio"] is None
    assert yearly.empty
    assert list(yearly.columns) == [
        "year", "valid_signal_sessions", "mean_rank_ic_5d", "year_status"
    ]
