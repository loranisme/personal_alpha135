import copy

import numpy as np
import pandas as pd
import pytest

from us_equity_alpha.alpha_filter import (
    apply_filter_gates,
    build_proposed_pool,
    compute_redundancy_conflicts,
    greedy_nonredundant_selection,
)


def _policy() -> dict:
    return {
        "min_median_coverage": 0.95,
        "min_valid_signal_sessions": 100,
        "require_positive_mean_rank_ic": True,
        "require_positive_q5_q1": True,
        "min_positive_year_ratio": 0.60,
        "max_weekly_top10_turnover": 0.80,
        "score_corr_conflict": 0.80,
        "top10_overlap_conflict": 0.70,
        "active_pool_min": 5,
        "active_pool_max": 12,
    }


def _diagnostics(**overrides) -> pd.DataFrame:
    row = {
        "factor_id": "F1",
        "factor_version": 1,
        "family_id": "family-1",
        "mechanism_tag": "momentum",
        "semantic_status": "FULL_INTENT",
        "warmup_sessions": 10,
        "direction": 1,
        "valid_signal_sessions": 120,
        "median_cross_section_coverage": 0.99,
        "mean_rank_ic_5d": 0.02,
        "icir_5d": 0.20,
        "positive_year_ratio": 0.75,
        "q1_mean_5d": -0.01,
        "q5_mean_5d": 0.01,
        "q5_minus_q1_5d": 0.02,
        "weekly_top10_turnover": 0.50,
    }
    return pd.DataFrame([row | overrides])


def _passing_candidates(count: int, *, same_mechanism: bool = False) -> pd.DataFrame:
    rows = []
    for index in range(count):
        row = _diagnostics().iloc[0].to_dict()
        row.update(
            factor_id=f"F{index + 1}",
            factor_version=1,
            family_id=f"family-{index + 1}",
            mechanism_tag="momentum" if same_mechanism else f"mechanism-{index + 1}",
            positive_year_ratio=0.9 - index / 100,
            icir_5d=0.4 - index / 100,
            mean_rank_ic_5d=0.04 - index / 1000,
            filter_status="PASS_GATES",
            filter_reason="PASS_ALL_GATES",
        )
        rows.append(row)
    return pd.DataFrame(rows)


def _no_conflicts() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "left_factor_id", "left_factor_version", "right_factor_id",
            "right_factor_version", "hard_conflict", "conflict_reason",
            "same_mechanism",
        ]
    )


def test_gates_do_not_flip_negative_factor():
    result = apply_filter_gates(
        _diagnostics(mean_rank_ic_5d=-0.01, q5_minus_q1_5d=-0.02), _policy()
    )
    assert result.iloc[0].filter_status == "REJECT_SIGNAL"
    assert result.iloc[0].direction == 1


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"median_cross_section_coverage": 0.90}, "REJECT_DATA"),
        ({"positive_year_ratio": 0.50}, "WATCHLIST_UNSTABLE"),
        ({"weekly_top10_turnover": 0.90}, "WATCHLIST_HIGH_TURNOVER"),
        ({}, "PASS_GATES"),
    ],
)
def test_gates_are_applied_in_frozen_sequence(overrides, expected):
    assert apply_filter_gates(_diagnostics(**overrides), _policy()).iloc[0].filter_status == expected


def test_filter_does_not_pad_to_minimum():
    decisions, messages = greedy_nonredundant_selection(
        _passing_candidates(4), _no_conflicts(), _policy()
    )
    assert decisions.filter_status.eq("SELECTED").sum() == 4
    assert messages == ["COMPOSITE_BLOCKED_INSUFFICIENT_ALPHA"]


def test_same_mechanism_is_warning_not_conflict():
    decisions, messages = greedy_nonredundant_selection(
        _passing_candidates(5, same_mechanism=True), _no_conflicts(), _policy()
    )
    assert decisions.filter_status.eq("SELECTED").sum() == 5
    assert "SAME_MECHANISM_INCLUDED" in messages


def test_conflicts_reject_lower_priority_factor_deterministically():
    conflicts = pd.DataFrame(
        [
            {
                "left_factor_id": "F1",
                "left_factor_version": 1,
                "right_factor_id": "F2",
                "right_factor_version": 1,
                "hard_conflict": True,
                "conflict_reason": "SCORE_CORRELATION",
                "same_mechanism": False,
            }
        ]
    )
    decisions, _ = greedy_nonredundant_selection(
        _passing_candidates(5), conflicts, _policy()
    )
    row = decisions.set_index("factor_id").loc["F2"]
    assert row.filter_status == "REJECT_REDUNDANT"
    assert row.filter_reason == "REJECT_REDUNDANT_WITH_F1"


def test_pairwise_conflicts_measure_family_score_and_top10_overlap():
    candidates = _passing_candidates(2)
    candidates.loc[1, "family_id"] = candidates.loc[0, "family_id"]
    dates = pd.date_range("2026-01-02", periods=4, freq="7D")
    columns = [f"S{i}" for i in range(10)]
    panel = pd.DataFrame(np.tile(np.arange(10), (4, 1)), index=dates, columns=columns)
    conflicts = compute_redundancy_conflicts(
        candidates, {("F1", 1): panel, ("F2", 1): panel.copy()}, _policy()
    )
    row = conflicts.iloc[0]
    assert row.hard_conflict
    assert row.median_abs_score_correlation == pytest.approx(1.0)
    assert row.mean_top10_jaccard == pytest.approx(1.0)
    assert set(row.conflict_reason.split("|")) == {
        "SAME_FAMILY", "SCORE_CORRELATION", "TOP10_OVERLAP"
    }


def test_identical_inputs_produce_identical_proposed_pool_hash():
    selected, _ = greedy_nonredundant_selection(
        _passing_candidates(5), _no_conflicts(), _policy()
    )
    metadata = {
        "filter_run_id": "run-1",
        "selection_config_hash": "a" * 64,
        "v4_manifest_hash": "b" * 64,
        "expansion_registry_snapshot_hash": "c" * 64,
        "alpha_catalog_hash": "d" * 64,
        "provider": "tiingo_eod",
        "universe_hash": "e" * 64,
        "sample_start": "2020-01-01",
        "sample_end": "2025-12-31",
    }
    first = build_proposed_pool(selected, metadata, _policy())
    second = build_proposed_pool(copy.deepcopy(selected), copy.deepcopy(metadata), _policy())
    assert first == second
    assert first["live_orders_submitted"] == 0

