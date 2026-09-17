"""Small, frozen weekly diagnostics used by the V5.1 Alpha Filter."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd


def _validate_sessions(sessions: pd.DatetimeIndex) -> None:
    if not isinstance(sessions, pd.DatetimeIndex):
        raise ValueError("SESSION_INDEX_REQUIRED")
    if sessions.has_duplicates or not sessions.is_monotonic_increasing:
        raise ValueError("SESSION_INDEX_INVALID")


def weekly_signal_sessions(sessions: pd.DatetimeIndex) -> list[pd.Timestamp]:
    """Return the prior session for each first exchange session of a new ISO week."""
    _validate_sessions(sessions)
    result: list[pd.Timestamp] = []
    previous_week: tuple[int, int] | None = None
    for position, session in enumerate(sessions):
        iso = session.isocalendar()
        week = (int(iso.year), int(iso.week))
        if position and week != previous_week:
            result.append(sessions[position - 1])
        previous_week = week
    return result


def forward_open_returns(
    opens: pd.DataFrame,
    signal_sessions: Sequence[pd.Timestamp],
    horizon: int = 5,
) -> pd.DataFrame:
    """Calculate open[t+1+horizon] / open[t+1] - 1 on exchange-session offsets."""
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1:
        raise ValueError("INVALID_FORWARD_HORIZON")
    _validate_sessions(opens.index)
    if opens.columns.has_duplicates:
        raise ValueError("DUPLICATE_SECURITY_ID")
    positions = {session: position for position, session in enumerate(opens.index)}
    result = pd.DataFrame(index=pd.DatetimeIndex(signal_sessions), columns=opens.columns, dtype=float)
    for signal in result.index:
        if signal not in positions:
            raise ValueError(f"SIGNAL_SESSION_NOT_FOUND:{signal}")
        entry_position = positions[signal] + 1
        exit_position = entry_position + horizon
        if exit_position >= len(opens.index):
            continue
        entry = pd.to_numeric(opens.iloc[entry_position], errors="coerce")
        exit_ = pd.to_numeric(opens.iloc[exit_position], errors="coerce")
        valid = np.isfinite(entry) & np.isfinite(exit_) & entry.gt(0)
        result.loc[signal] = (exit_ / entry - 1).where(valid)
    return result


def _top_set(row: pd.Series, fraction: float = 0.10) -> set[str]:
    valid = pd.to_numeric(row, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if valid.empty:
        return set()
    count = max(1, int(math.ceil(len(valid) * fraction)))
    ordered = sorted(valid.items(), key=lambda item: (-float(item[1]), str(item[0])))
    return {str(security_id) for security_id, _ in ordered[:count]}


def weekly_top10_turnover(ranks: pd.DataFrame) -> float | None:
    """Return one minus prior-set retention for consecutive weekly rank rows."""
    if ranks.empty or len(ranks) < 2:
        return None
    values: list[float] = []
    previous: set[str] | None = None
    for _, row in ranks.iterrows():
        current = _top_set(row)
        if previous:
            values.append(1 - len(current & previous) / len(previous))
        previous = current
    return float(np.mean(values)) if values else None


def _safe_float(value: float) -> float | None:
    return float(value) if np.isfinite(value) else None


def diagnose_factor(
    ranks: pd.DataFrame,
    forward_returns: pd.DataFrame,
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Calculate the exact compact diagnostic set under the frozen weekly label."""
    benchmark = str(config.get("benchmark_security_id", "SPY"))
    if benchmark in ranks.columns or benchmark in forward_returns.columns:
        raise ValueError("BENCHMARK_IN_FACTOR_CROSS_SECTION")
    if not ranks.index.equals(forward_returns.index) or not ranks.columns.equals(
        forward_returns.columns
    ):
        raise ValueError("FACTOR_LABEL_ALIGNMENT_MISMATCH")
    _validate_sessions(pd.DatetimeIndex(ranks.index))
    if ranks.columns.has_duplicates:
        raise ValueError("DUPLICATE_SECURITY_ID")
    quantiles = int(config.get("quantiles", 5))
    if quantiles < 2:
        raise ValueError("INVALID_QUANTILE_COUNT")

    numeric_scores = ranks.apply(pd.to_numeric, errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )
    numeric_returns = forward_returns.apply(pd.to_numeric, errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )
    percentile = numeric_scores.rank(axis=1, pct=True, method="average")
    coverage = (numeric_scores.notna() & numeric_returns.notna()).sum(axis=1) / max(
        len(numeric_scores.columns), 1
    )
    ic_values: dict[pd.Timestamp, float] = {}
    quantile_returns: dict[int, list[float]] = {number: [] for number in range(1, quantiles + 1)}
    for session in percentile.index:
        pair = pd.concat(
            [percentile.loc[session].rename("rank"), numeric_returns.loc[session].rename("return")],
            axis=1,
        ).dropna()
        if len(pair) >= 2 and pair["rank"].nunique() >= 2 and pair["return"].nunique() >= 2:
            value = pair["rank"].corr(pair["return"], method="spearman")
            if pd.notna(value):
                ic_values[pd.Timestamp(session)] = float(value)
        if not pair.empty:
            buckets = np.ceil(pair["rank"] * quantiles).clip(1, quantiles).astype(int)
            for number, values in pair.groupby(buckets, sort=True)["return"]:
                quantile_returns[int(number)].extend(float(value) for value in values)

    ic = (
        pd.Series(ic_values, dtype=float).sort_index()
        if ic_values
        else pd.Series(index=pd.DatetimeIndex([]), dtype=float)
    )
    mean_ic = float(ic.mean()) if not ic.empty else np.nan
    sigma = float(ic.std(ddof=1)) if len(ic) > 1 else np.nan
    icir = mean_ic / sigma if np.isfinite(sigma) and sigma > 0 else np.nan
    yearly_rows: list[dict[str, Any]] = []
    full_minimum = int(config.get("full_year_min_signal_sessions", 26))
    partial_minimum = int(config.get("partial_final_year_min_signal_sessions", 13))
    max_year = int(ic.index.year.max()) if not ic.empty else None
    for year, values in ic.groupby(ic.index.year):
        count = int(values.notna().sum())
        if count >= full_minimum:
            status = "FULL_YEAR"
        elif int(year) == max_year and count >= partial_minimum:
            status = "PARTIAL_YEAR"
        else:
            status = "INSUFFICIENT"
        yearly_rows.append(
            {
                "year": int(year),
                "valid_signal_sessions": count,
                "mean_rank_ic_5d": _safe_float(float(values.mean())),
                "year_status": status,
            }
        )
    yearly = pd.DataFrame(
        yearly_rows,
        columns=["year", "valid_signal_sessions", "mean_rank_ic_5d", "year_status"],
    )
    eligible_years = yearly[yearly.year_status.isin(["FULL_YEAR", "PARTIAL_YEAR"])]
    positive_year_ratio = (
        float(eligible_years.mean_rank_ic_5d.gt(0).mean()) if not eligible_years.empty else None
    )
    q1 = float(np.mean(quantile_returns[1])) if quantile_returns[1] else np.nan
    q_last = (
        float(np.mean(quantile_returns[quantiles])) if quantile_returns[quantiles] else np.nan
    )
    metrics = {
        "valid_signal_sessions": int(len(ic)),
        "median_cross_section_coverage": _safe_float(float(coverage.median())),
        "mean_rank_ic_5d": _safe_float(mean_ic),
        "icir_5d": _safe_float(icir),
        "positive_year_ratio": positive_year_ratio,
        "q1_mean_5d": _safe_float(q1),
        "q5_mean_5d": _safe_float(q_last),
        "q5_minus_q1_5d": _safe_float(q_last - q1),
        "weekly_top10_turnover": weekly_top10_turnover(percentile),
    }
    return metrics, yearly
