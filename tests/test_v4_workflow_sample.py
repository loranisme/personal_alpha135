import pandas as pd
import pytest

from us_equity_alpha.sample_workflow import (
    alphalens_detail_tables,
    cash_funded_target_shares,
    combine_factor_panels,
    freeze_sample_contract,
    vectorbt_order_records,
    weekly_rebalance_dates,
    write_latest_selection_csvs,
)


FACTOR_IDS = [
    "local_reconstruction__005de510c57320d1",
    "local_reconstruction__0224b8f97afcd6f8",
    "local_reconstruction__02f04fec00966282",
]


def _library():
    return {
        "factors": [
            {
                "local_factor_id": factor_id,
                "build_status": "COMPUTE_VERIFIED",
                "usage_tier": "DIAGNOSTIC_ONLY",
                "expression": f"expression_{index}",
                "source_alpha_ids": [f"source_{index}"],
            }
            for index, factor_id in enumerate(FACTOR_IDS)
        ]
    }


def test_sample_contract_freezes_exact_factors_before_evaluation():
    contract = freeze_sample_contract(_library(), FACTOR_IDS)
    assert [row["local_factor_id"] for row in contract["factors"]] == FACTOR_IDS
    assert contract["selection_basis"] == "STABLE_ID_FIELD_DIVERSITY_NO_RETURN_SELECTION"
    assert contract["formal_release_allowed"] is False
    assert contract["portfolio"]["top_fraction"] == pytest.approx(0.10)
    assert contract["portfolio"]["minimum_cash"] == pytest.approx(0.05)


def test_sample_contract_rejects_non_verified_or_reordered_selection():
    library = _library()
    library["factors"][1]["build_status"] = "COMPILED"
    with pytest.raises(ValueError, match="FACTOR_NOT_COMPUTE_VERIFIED"):
        freeze_sample_contract(library, FACTOR_IDS)
    with pytest.raises(ValueError, match="FROZEN_FACTOR_ORDER"):
        freeze_sample_contract(_library(), list(reversed(FACTOR_IDS)))


def test_composite_is_equal_weight_cross_sectional_rank_and_excludes_spy():
    dates = pd.to_datetime(["2025-01-02", "2025-01-03"], utc=True)
    panels = {
        FACTOR_IDS[0]: pd.DataFrame({"A": [1, 3], "B": [2, 1], "SPY": [99, 99]}, index=dates),
        FACTOR_IDS[1]: pd.DataFrame({"A": [2, 3], "B": [1, 1], "SPY": [99, 99]}, index=dates),
        FACTOR_IDS[2]: pd.DataFrame({"A": [2, 3], "B": [1, None], "SPY": [99, 99]}, index=dates),
    }
    composite, ranked = combine_factor_panels(panels, FACTOR_IDS)
    assert list(composite.columns) == ["A", "B"]
    assert composite.loc[dates[0], "A"] == pytest.approx((0.5 + 1 + 1) / 3)
    assert composite.loc[dates[0], "B"] == pytest.approx((1 + 0.5 + 0.5) / 3)
    assert pd.isna(composite.loc[dates[1], "B"])
    assert set(ranked) == set(FACTOR_IDS)


def test_weekly_rebalance_uses_first_session_and_previous_session_signal():
    sessions = pd.to_datetime(
        ["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07", "2025-01-13"],
        utc=True,
    )
    rows = weekly_rebalance_dates(sessions)
    assert rows == [
        (sessions[2], sessions[1]),
        (sessions[4], sessions[3]),
    ]


def test_weekly_rebalance_accepts_exchange_calendar_naive_session_labels():
    sessions = pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06"])
    assert weekly_rebalance_dates(sessions) == [(sessions[2], sessions[1])]


def test_alphalens_detail_tables_expose_native_framework_outputs():
    rows = []
    cutoff = pd.Timestamp("2025-01-02 21:00", tz="UTC")
    for index in range(10):
        rows.append(
            {
                "signal_cutoff": cutoff,
                "security_id": f"S{index:02d}",
                "score": float(index),
                "forward_return": float(index) / 100,
            }
        )
    result = alphalens_detail_tables(pd.DataFrame(rows), horizon=5, quantiles=5)
    assert result["summary"]["engine"] == "alphalens-reloaded"
    assert result["summary"]["mean_rank_ic"] == pytest.approx(1.0)
    assert list(result["ic_by_date"].columns) == ["5D"]
    assert set(result["factor_data"]["factor_quantile"]) == {1, 2, 3, 4, 5}
    assert "5D" in result["mean_return_by_quantile"]


def test_cash_funded_target_shares_preserves_minimum_cash_after_fees():
    shares, audit = cash_funded_target_shares(
        ["A", "B"],
        {"A": 60.0, "B": 70.0},
        equity=1000.0,
        minimum_cash=0.05,
        fee_rate=0.001,
    )
    assert shares == {"A": 7, "B": 6}
    assert audit["ending_cash"] >= 50.0
    assert audit["fee"] == pytest.approx(audit["notional"] * 0.001)


def test_vectorbt_order_records_accept_dataframe_records():
    grid = pd.to_datetime(["2025-01-02 14:30", "2025-01-02 21:00"], utc=True)
    raw = pd.DataFrame(
        [{"id": 0, "col": 1, "idx": 0, "size": 3.0, "price": 20.0, "fees": 0.06, "side": 0}]
    )
    result = vectorbt_order_records(raw, ["A", "B"], grid)
    assert result.to_dict("records") == [
        {
            "order_id": 0,
            "execution_at": grid[0],
            "security_id": "B",
            "side": "BUY",
            "quantity": 3.0,
            "price": 20.0,
            "fee": 0.06,
        }
    ]


def test_latest_selection_writer_creates_its_delivery_directory(tmp_path):
    frame = pd.DataFrame([{"security_id": "A", "value": 1}])
    result = write_latest_selection_csvs(tmp_path, ranking=frame, targets=frame, orders=frame, checks=frame)
    assert set(result) == {"ranking", "target_portfolio", "rebalance_orders", "checks"}
    assert all(path.is_file() for path in result.values())
