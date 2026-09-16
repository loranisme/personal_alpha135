import copy

import numpy as np
import pandas as pd
import pytest

from us_equity_alpha.alpha_filter_workflow import (
    compare_composites,
    flat_equal_weight_composite,
)


def _three_rank_panels_with_one_gap() -> dict[str, pd.DataFrame]:
    index = pd.to_datetime(["2026-01-02"])
    return {
        "F1": pd.DataFrame({"A": [0.2], "B": [0.3]}, index=index),
        "F2": pd.DataFrame({"A": [0.4], "B": [np.nan]}, index=index),
        "F3": pd.DataFrame({"A": [0.6], "B": [0.7]}, index=index),
    }


def _scores(multiplier: float = 1.0) -> pd.DataFrame:
    dates = pd.date_range("2025-01-03", periods=30, freq="7D")
    columns = [f"S{i:02d}" for i in range(20)]
    base = np.arange(20, dtype=float) * multiplier
    return pd.DataFrame(np.tile(base, (30, 1)), index=dates, columns=columns)


def _labels() -> pd.DataFrame:
    return _scores(0.001)


def _runner(scores: pd.DataFrame) -> dict:
    return {
        "total_return": float(scores.mean(axis=1).mean() / 100),
        "sharpe": 0.5,
        "max_drawdown": -0.1,
        "modeled_fees": 12.5,
    }


def _metadata() -> dict:
    return {
        "new_provider": "tiingo_eod",
        "baseline_provider": "tiingo_eod",
        "new_universe_hash": "a",
        "baseline_universe_hash": "a",
        "new_signal_sessions_hash": "b",
        "baseline_signal_sessions_hash": "b",
        "new_forward_label": "T_CLOSE_T1_OPEN_T6_OPEN",
        "baseline_forward_label": "T_CLOSE_T1_OPEN_T6_OPEN",
        "filter_decisions_frozen": True,
        "diagnostic_config": {
            "quantiles": 5,
            "benchmark_security_id": "SPY",
            "full_year_min_signal_sessions": 26,
            "partial_final_year_min_signal_sessions": 13,
        },
    }


def test_flat_composite_uses_equal_weights_and_complete_rows_only():
    result = flat_equal_weight_composite(_three_rank_panels_with_one_gap())
    assert result.loc[pd.Timestamp("2026-01-02"), "A"] == pytest.approx(0.4)
    assert pd.isna(result.loc[pd.Timestamp("2026-01-02"), "B"])


def test_comparison_rejects_mismatched_contracts():
    with pytest.raises(ValueError, match="BASELINE_CONTRACT_MISMATCH"):
        compare_composites(
            _scores(),
            _scores(),
            _labels(),
            _runner,
            _metadata() | {"new_universe_hash": "a", "baseline_universe_hash": "b"},
        )


def test_comparison_requires_frozen_filter_decisions():
    with pytest.raises(ValueError, match="FILTER_DECISIONS_NOT_FROZEN"):
        compare_composites(
            _scores(), _scores(), _labels(), _runner,
            _metadata() | {"filter_decisions_frozen": False},
        )


def test_comparison_reports_identical_metric_contract_for_old_and_new():
    result = compare_composites(
        _scores(), _scores(0.5), _labels(), _runner, _metadata()
    ).set_index("composite_id")
    assert set(result.index) == {"V5_1_PROPOSED", "BASELINE_V5_8_FACTOR"}
    assert result.loc["V5_1_PROPOSED", "mean_rank_ic_5d"] == pytest.approx(1.0)
    assert result.loc["BASELINE_V5_8_FACTOR", "mean_rank_ic_5d"] == pytest.approx(1.0)
    assert result.modeled_fees.tolist() == [12.5, 12.5]


def test_portfolio_return_never_changes_factor_decisions():
    decisions = pd.DataFrame(
        [{"factor_id": "F1", "filter_status": "SELECTED"}]
    )
    before = copy.deepcopy(decisions)
    metadata = _metadata() | {"filter_decisions": decisions}
    compare_composites(_scores(), _scores(), _labels(), _runner, metadata)
    pd.testing.assert_frame_equal(before, decisions)

