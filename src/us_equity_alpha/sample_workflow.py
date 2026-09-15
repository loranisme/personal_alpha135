"""Frozen helpers for the V4 three-alpha engineering acceptance sample."""

from __future__ import annotations

import importlib.metadata
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd


V4_SAMPLE_FACTOR_IDS = (
    "local_reconstruction__005de510c57320d1",
    "local_reconstruction__0224b8f97afcd6f8",
    "local_reconstruction__02f04fec00966282",
)


def freeze_sample_contract(
    library: Mapping[str, Any], factor_ids: Sequence[str]
) -> dict[str, Any]:
    """Return the pre-performance contract for the approved acceptance sample."""
    if tuple(factor_ids) != V4_SAMPLE_FACTOR_IDS:
        raise ValueError("FROZEN_FACTOR_ORDER")
    indexed = {row.get("local_factor_id"): row for row in library.get("factors", [])}
    factors = []
    for factor_id in factor_ids:
        row = indexed.get(factor_id)
        if row is None:
            raise ValueError(f"FROZEN_FACTOR_ID_MISSING:{factor_id}")
        if row.get("build_status") != "COMPUTE_VERIFIED":
            raise ValueError(f"FACTOR_NOT_COMPUTE_VERIFIED:{factor_id}")
        factors.append(dict(row))
    return {
        "contract_version": "v4-three-alpha-workflow-sample-20260914-v1",
        "selection_basis": "STABLE_ID_FIELD_DIVERSITY_NO_RETURN_SELECTION",
        "factors": factors,
        "factor_weights": {factor_id: 1 / 3 for factor_id in factor_ids},
        "data": {
            "provider": "tiingo_eod",
            "warmup_start": "2019-10-01",
            "evaluation_start": "2020-01-01",
            "end": "2025-12-31",
            "security_count": 50,
            "benchmark": "SPY",
            "historical_pit_verified": False,
            "survivorship_bias": True,
        },
        "portfolio": {
            "kind": "ENGINEERING_DEMO_ONLY",
            "top_fraction": 0.10,
            "minimum_cash": 0.05,
            "weighting": "EQUAL_WEIGHT",
            "integer_shares": True,
            "rebalance": "WEEKLY_FIRST_SESSION",
            "signal_timing": "PREVIOUS_SESSION_CLOSE",
            "execution_reference": "NEXT_SESSION_OPEN",
            "one_way_cost_rate": 0.001,
            "initial_cash": 100000.0,
        },
        "diagnostic_horizon_sessions": 5,
        "formal_release_allowed": False,
        "live_orders_submitted": 0,
    }


def combine_factor_panels(
    panels: Mapping[str, pd.DataFrame], factor_ids: Sequence[str]
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """Rank each factor cross-sectionally and require all factors in the composite."""
    if tuple(factor_ids) != V4_SAMPLE_FACTOR_IDS:
        raise ValueError("FROZEN_FACTOR_ORDER")
    ranked: dict[str, pd.DataFrame] = {}
    common_index = None
    common_columns = None
    for factor_id in factor_ids:
        if factor_id not in panels:
            raise ValueError(f"FACTOR_PANEL_MISSING:{factor_id}")
        panel = panels[factor_id].drop(columns=["SPY"], errors="ignore").copy()
        common_index = panel.index if common_index is None else common_index.intersection(panel.index)
        common_columns = (
            panel.columns if common_columns is None else common_columns.intersection(panel.columns)
        )
        ranked[factor_id] = panel.rank(axis=1, pct=True, method="average")
    common_columns = pd.Index(common_columns)
    common_index = pd.DatetimeIndex(common_index)
    stack = pd.concat(
        [ranked[factor_id].loc[common_index, common_columns] for factor_id in factor_ids],
        keys=factor_ids,
    )
    composite = stack.groupby(level=1).mean().loc[common_index, common_columns]
    complete = pd.concat(
        [ranked[factor_id].loc[common_index, common_columns].notna() for factor_id in factor_ids],
        keys=factor_ids,
    ).groupby(level=1).all()
    composite = composite.where(complete)
    return composite, ranked


def weekly_rebalance_dates(
    sessions: pd.DatetimeIndex,
) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Pair each new ISO week's first session with the prior session's signal."""
    if not isinstance(sessions, pd.DatetimeIndex):
        raise ValueError("SESSION_INDEX_REQUIRED")
    if not sessions.is_monotonic_increasing or sessions.has_duplicates:
        raise ValueError("SESSION_INDEX_INVALID")
    pairs: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    previous_week = None
    for index, session in enumerate(sessions):
        week = (int(session.isocalendar().year), int(session.isocalendar().week))
        if index and week != previous_week:
            pairs.append((session, sessions[index - 1]))
        previous_week = week
    return pairs


def alphalens_detail_tables(
    labels: pd.DataFrame, *, horizon: int, quantiles: int
) -> dict[str, Any]:
    """Return the native Alphalens tables used by the acceptance package."""
    from alphalens import performance, utils

    data = labels.copy()
    data = data[
        np.isfinite(pd.to_numeric(data["score"], errors="coerce"))
        & np.isfinite(pd.to_numeric(data["forward_return"], errors="coerce"))
    ]
    if data.empty:
        raise ValueError("NO_VALID_LABELS")
    data["date"] = pd.to_datetime(data["signal_cutoff"], utc=True)
    data["asset"] = data["security_id"].astype(str)
    if data.duplicated(["date", "asset"]).any():
        raise ValueError("DUPLICATE_LABEL")
    column = f"{horizon}D"
    keep = ["date", "asset", "score", "forward_return"]
    factor_data = data[keep].set_index(["date", "asset"]).rename(
        columns={"score": "factor", "forward_return": column}
    )
    factor_data["factor_quantile"] = utils.quantize_factor(
        factor_data, quantiles=quantiles, no_raise=True
    )
    factor_data = factor_data.dropna(subset=["factor_quantile"])
    factor_data["factor_quantile"] = factor_data["factor_quantile"].astype(int)
    ic = performance.factor_information_coefficient(factor_data).dropna(how="all")
    mean_returns, standard_error = performance.mean_return_by_quantile(
        factor_data, demeaned=False
    )
    autocorrelation = performance.factor_rank_autocorrelation(factor_data, period=1)
    turnover = pd.DataFrame(
        {
            f"quantile_{quantile}": performance.quantile_turnover(
                factor_data["factor_quantile"], quantile, period=1
            )
            for quantile in range(1, quantiles + 1)
        }
    )
    mean_ic = float(ic[column].mean())
    sigma = float(ic[column].std())
    summary = {
        "engine": "alphalens-reloaded",
        "engine_version": importlib.metadata.version("alphalens-reloaded"),
        "evidence_kind": "DEVELOPMENT_EXPOSED_DIAGNOSTIC",
        "mean_rank_ic": mean_ic if np.isfinite(mean_ic) else None,
        "raw_icir": mean_ic / sigma if sigma > 0 and np.isfinite(sigma) else None,
        "sessions": int(len(ic)),
        "observations": int(len(factor_data)),
        "quantiles": int(quantiles),
        "horizon_sessions": int(horizon),
    }
    return {
        "summary": summary,
        "factor_data": factor_data,
        "ic_by_date": ic,
        "mean_return_by_quantile": mean_returns,
        "standard_error_by_quantile": standard_error,
        "factor_rank_autocorrelation": autocorrelation.rename("rank_autocorrelation").to_frame(),
        "quantile_turnover": turnover,
    }


def cash_funded_target_shares(
    selected: Sequence[str],
    prices: Mapping[str, float],
    *,
    equity: float,
    minimum_cash: float,
    fee_rate: float,
) -> tuple[dict[str, int], dict[str, float]]:
    """Build equal-dollar whole-share targets without spending the cash floor."""
    if not selected or not math.isfinite(equity) or equity <= 0:
        raise ValueError("INVALID_TARGET_INPUT")
    if not 0 <= minimum_cash < 1 or not math.isfinite(fee_rate) or fee_rate < 0:
        raise ValueError("INVALID_TARGET_POLICY")
    per_name = equity * (1 - minimum_cash) / len(selected)
    shares: dict[str, int] = {}
    for security_id in selected:
        price = float(prices[security_id])
        if not math.isfinite(price) or price <= 0:
            raise ValueError(f"INVALID_TARGET_PRICE:{security_id}")
        shares[security_id] = int(math.floor(per_name / price))
    notional = sum(shares[s] * float(prices[s]) for s in selected)
    fee = notional * fee_rate
    floor = equity * minimum_cash
    while equity - notional - fee < floor:
        candidates = [s for s in selected if shares[s] > 0]
        if not candidates:
            raise ValueError("CASH_FLOOR_UNSATISFIABLE")
        security_id = max(candidates, key=lambda s: float(prices[s]))
        shares[security_id] -= 1
        notional -= float(prices[security_id])
        fee = notional * fee_rate
    return shares, {
        "notional": float(notional),
        "fee": float(fee),
        "ending_cash": float(equity - notional - fee),
        "minimum_cash_amount": float(floor),
    }


def vectorbt_order_records(
    raw_records: pd.DataFrame, symbols: Sequence[str], grid: pd.DatetimeIndex
) -> pd.DataFrame:
    """Map vectorbt 1.x DataFrame order records to an auditable table."""
    required = {"id", "col", "idx", "size", "price", "fees", "side"}
    if required - set(raw_records.columns):
        raise ValueError("VECTORBT_ORDER_COLUMNS_MISSING")
    rows = []
    for record in raw_records.to_dict("records"):
        rows.append(
            {
                "order_id": int(record["id"]),
                "execution_at": grid[int(record["idx"])],
                "security_id": symbols[int(record["col"])],
                "side": "BUY" if int(record["side"]) == 0 else "SELL",
                "quantity": float(record["size"]),
                "price": float(record["price"]),
                "fee": float(record["fees"]),
            }
        )
    return pd.DataFrame(rows)


def write_latest_selection_csvs(
    output: Path,
    *,
    ranking: pd.DataFrame,
    targets: pd.DataFrame,
    orders: pd.DataFrame,
    checks: pd.DataFrame,
) -> dict[str, Path]:
    """Write the four user-facing review tables under one explicit directory."""
    root = Path(output) / "latest_selection"
    root.mkdir(parents=True, exist_ok=False)
    tables = {
        "ranking": ranking,
        "target_portfolio": targets,
        "rebalance_orders": orders,
        "checks": checks,
    }
    paths = {}
    for name, frame in tables.items():
        path = root / f"{name}.csv"
        frame.to_csv(path, index=False)
        paths[name] = path
    return paths
