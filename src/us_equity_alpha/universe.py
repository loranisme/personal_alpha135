"""Historical investable-universe and exchange-session helpers."""

from __future__ import annotations

import exchange_calendars as xcals
import pandas as pd


def _as_utc_series(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, utc=True, errors="coerce")


def build_universes(
    securities: pd.DataFrame,
    classifications: pd.DataFrame,
    asof: pd.Timestamp,
    required_history: int,
) -> pd.DataFrame:
    """Build calculation/trading eligibility without using future metadata."""
    cutoff = pd.Timestamp(asof)
    if cutoff.tzinfo is None:
        raise ValueError("ASOF_MUST_BE_TIMEZONE_AWARE")
    cutoff = cutoff.tz_convert("UTC")
    if required_history < 1:
        raise ValueError("REQUIRED_HISTORY_MUST_BE_POSITIVE")

    required_security = {"security_id", "listed_at", "delisted_at", "history_count", "price", "volume"}
    required_classification = {"security_id", "effective_at", "available_at", "industry", "symbol"}
    if required_security - set(securities.columns):
        raise ValueError("SECURITY_MASTER_COLUMNS_MISSING")
    if required_classification - set(classifications.columns):
        raise ValueError("CLASSIFICATION_COLUMNS_MISSING")
    if securities["security_id"].duplicated().any():
        raise ValueError("DUPLICATE_SECURITY_ID")

    master = securities.copy()
    master["listed_at"] = _as_utc_series(master["listed_at"])
    master["delisted_at"] = _as_utc_series(master["delisted_at"])

    history = classifications.copy()
    history["effective_at"] = _as_utc_series(history["effective_at"])
    history["available_at"] = _as_utc_series(history["available_at"])
    visible = history[(history["effective_at"] <= cutoff) & (history["available_at"] <= cutoff)]
    visible = visible.sort_values(["security_id", "effective_at", "available_at"], kind="mergesort")
    visible = visible.groupby("security_id", sort=False).tail(1)
    selected_columns = [column for column in ("security_id", "symbol", "industry", "sector", "subindustry") if column in visible]
    result = master.merge(visible[selected_columns], on="security_id", how="left")

    calculation = (
        result["listed_at"].notna()
        & (result["listed_at"] <= cutoff)
        & (result["delisted_at"].isna() | (cutoff < result["delisted_at"]))
    )
    result["calculation_eligible"] = calculation

    reasons: list[list[str]] = []
    trading: list[bool] = []
    for row in result.itertuples(index=False):
        row_reasons: list[str] = []
        if not bool(getattr(row, "calculation_eligible")):
            row_reasons.append("OUTSIDE_LISTING_LIFETIME")
        history_count = getattr(row, "history_count")
        if pd.isna(history_count) or int(history_count) < required_history:
            row_reasons.append("INSUFFICIENT_HISTORY")
        if pd.isna(getattr(row, "industry", None)):
            row_reasons.append("UNKNOWN_CLASSIFICATION")
        if pd.isna(getattr(row, "price")) or pd.isna(getattr(row, "volume")):
            row_reasons.append("MISSING_MARKET_DATA")
        reasons.append(row_reasons)
        trading.append(not row_reasons)
    result["trading_eligible"] = trading
    result["reasons"] = reasons
    return result


def session_bounds(session: str | pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Return XNYS open/close timestamps in UTC, including DST/early closes."""
    calendar = xcals.get_calendar("XNYS")
    label = pd.Timestamp(session)
    if label.tzinfo is not None:
        label = label.tz_convert("UTC").tz_localize(None)
    label = label.normalize()
    if not calendar.is_session(label):
        raise ValueError("NOT_XNYS_SESSION")
    return calendar.session_open(label), calendar.session_close(label)
