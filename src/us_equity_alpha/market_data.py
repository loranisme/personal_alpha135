"""Point-in-time market and fundamental data selection."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pandas as pd


REQUIRED_COLUMNS = {
    "security_id", "field", "value", "event_time", "available_at",
    "revision_id", "source", "unit", "adjustment", "fetched_at",
}


def _utc_timestamp(value: object, name: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        raise ValueError(f"{name}_MUST_BE_TIMEZONE_AWARE")
    return timestamp.tz_convert("UTC")


def load_asof(
    path: Path | str,
    cutoff: pd.Timestamp,
    fields: Iterable[str] | None = None,
) -> pd.DataFrame:
    """Return the latest visible revision for each security, field and event.

    Missing latest revisions remain missing. Earlier non-null revisions are never
    used to fill a later null value.
    """
    asof = _utc_timestamp(cutoff, "CUTOFF")
    frame = pd.read_parquet(path)
    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError("MISSING_COLUMNS:" + ",".join(sorted(missing)))
    if frame.duplicated(keep=False).any():
        raise ValueError("DUPLICATE_ROWS")

    data = frame.copy()
    for column in ("event_time", "available_at", "fetched_at"):
        data[column] = pd.to_datetime(data[column], utc=True, errors="raise")

    series_keys = ["security_id", "field"]
    for column in ("source", "unit", "adjustment"):
        distinct = data.groupby(series_keys, dropna=False)[column].nunique(dropna=False)
        if (distinct > 1).any():
            raise ValueError(f"CONFLICTING_{column.upper()}_PROVENANCE")

    if fields is not None:
        requested = {str(field) for field in fields}
        data = data[data["field"].astype(str).isin(requested)]
    data = data[(data["available_at"] <= asof) & (data["event_time"] <= asof)]
    if data.empty:
        return data.reset_index(drop=True)

    data = data.sort_values(
        ["security_id", "field", "event_time", "available_at", "fetched_at", "revision_id"],
        kind="mergesort",
    )
    latest = data.groupby(
        ["security_id", "field", "event_time"], dropna=False, sort=False
    ).tail(1)
    return latest.reset_index(drop=True)
