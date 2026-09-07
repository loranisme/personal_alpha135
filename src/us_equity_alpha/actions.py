"""Point-in-time normalization for splits and cash distributions."""

from __future__ import annotations

import pandas as pd


def normalize_actions(actions: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    asof = pd.Timestamp(cutoff)
    if asof.tzinfo is None:
        raise ValueError("CUTOFF_MUST_BE_TIMEZONE_AWARE")
    asof = asof.tz_convert("UTC")
    required = {"security_id", "kind", "effective_at", "available_at"}
    if required - set(actions.columns):
        raise ValueError("ACTION_COLUMNS_MISSING")
    data = actions.copy()
    for column in ("effective_at", "available_at", "pay_date"):
        if column in data:
            data[column] = pd.to_datetime(data[column], utc=True, errors="coerce")
    data = data[(data["available_at"] <= asof) & (data["effective_at"] <= asof)]
    key_columns = [column for column in ("security_id", "kind", "effective_at", "available_at", "ratio", "amount", "pay_date") if column in data]
    if data.duplicated(key_columns, keep=False).any():
        raise ValueError("DUPLICATE_ACTION")
    return data.sort_values(["effective_at", "security_id", "kind"], kind="mergesort").reset_index(drop=True)
