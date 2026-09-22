"""Conservative SEC filing availability rules."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd


def _utc_timestamp(value: object) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def _parse_acceptance(value: str) -> pd.Timestamp:
    compact = str(value).strip()
    if len(compact) == 14 and compact.isdigit():
        return pd.to_datetime(compact, format="%Y%m%d%H%M%S", utc=True)
    return _utc_timestamp(compact)


def _next_session_cutoff(
    filed_date: str, calendar_name: str, cutoff_local_time: str
) -> pd.Timestamp:
    calendar = xcals.get_calendar(calendar_name)
    filed = pd.Timestamp(filed_date).normalize()
    sessions = calendar.sessions_in_range(filed, filed + pd.Timedelta(days=14))
    later = sessions[sessions > filed]
    if later.empty:
        raise ValueError(f"NO_EXCHANGE_SESSION_AFTER_FILED_DATE:{filed_date}")
    session = pd.Timestamp(later[0])
    hour, minute = (int(part) for part in cutoff_local_time.split(":", 1))
    local_date = session.date()
    localized = datetime.combine(
        local_date, time(hour, minute), tzinfo=ZoneInfo("America/New_York")
    )
    scheduled_close = pd.Timestamp(calendar.session_close(session)).tz_convert("UTC")
    return min(pd.Timestamp(localized).tz_convert("UTC"), scheduled_close)


def historical_available_at(
    acceptance: str | None,
    filed_date: str,
    calendar_name: str,
    cutoff_local_time: str,
) -> tuple[pd.Timestamp, str]:
    """Return the first conservative historical signal time for one filing."""
    if acceptance is not None and str(acceptance).strip():
        return _parse_acceptance(str(acceptance)) + pd.Timedelta(minutes=10), "EXACT_ACCEPTANCE"
    return (
        _next_session_cutoff(filed_date, calendar_name, cutoff_local_time),
        "DATE_ONLY_NEXT_SESSION_CUTOFF",
    )


def forward_available_at(
    historical_time: pd.Timestamp, first_seen_at: pd.Timestamp
) -> pd.Timestamp:
    """A forward process cannot use a filing before it actually observed it."""
    return max(_utc_timestamp(historical_time), _utc_timestamp(first_seen_at))
