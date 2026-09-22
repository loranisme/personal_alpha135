"""Materialize daily exchange-session point-in-time fundamental values."""

from __future__ import annotations

import exchange_calendars as xcals
import pandas as pd


MODES = {
    "HISTORICAL_DIAGNOSTIC": "historical_available_at",
    "FORWARD_OBSERVED": "available_at",
}


def _session_label(session: pd.Timestamp) -> pd.Timestamp:
    """Preserve the date encoded by an exchange session label.

    Exchange labels are commonly represented as UTC midnight.  Converting that
    instant to New York time changes it to the preceding calendar day, so the
    label must be treated as a date rather than an instant.
    """

    value = pd.Timestamp(session)
    return pd.Timestamp(year=value.year, month=value.month, day=value.day)


def _signal_cutoff(
    session: pd.Timestamp, calendar: xcals.ExchangeCalendar
) -> tuple[pd.Timestamp, pd.Timestamp]:
    label = _session_label(session)
    try:
        close = calendar.session_close(label)
    except Exception as exc:
        raise ValueError(f"PIT_NOT_EXCHANGE_SESSION:{label.date()}") from exc
    return label, pd.Timestamp(close).tz_convert("UTC")


def build_daily_pit_panel(
    versions: pd.DataFrame, sessions: pd.DatetimeIndex, mode: str
) -> pd.DataFrame:
    if mode not in MODES:
        raise ValueError(f"PIT_MODE_INVALID:{mode}")
    required = {
        "issuer_cik", "security_id", "canonical_field", "reported_basis", "value",
        "unit", "period_end", "historical_available_at", "first_seen_at", "available_at",
        "accession_number", "source_fact_id", "semantic_grade", "mapping_version",
        "definition_hash",
    }
    if not required.issubset(versions.columns):
        raise ValueError("PIT_VERSION_COLUMNS_MISSING")
    if pd.DatetimeIndex(sessions).duplicated().any():
        raise ValueError("PIT_DUPLICATE_SESSION")
    source = versions.copy()
    for column in ("historical_available_at", "first_seen_at", "available_at"):
        source[column] = pd.to_datetime(source[column], utc=True, errors="raise")
    source["period_end"] = pd.to_datetime(source.period_end, errors="raise")
    if source[
        [
            "historical_available_at",
            "first_seen_at",
            "available_at",
            "period_end",
        ]
    ].isna().any().any():
        raise ValueError("PIT_REQUIRED_TIME_MISSING")
    if (
        source.available_at.lt(source.historical_available_at)
        | source.available_at.lt(source.first_seen_at)
    ).any():
        raise ValueError("PIT_AVAILABLE_AT_INVARIANT")
    for column in (
        "issuer_cik",
        "security_id",
        "canonical_field",
        "reported_basis",
        "unit",
        "accession_number",
        "source_fact_id",
    ):
        if source[column].isna().any() or source[column].astype(str).str.strip().eq("").any():
            raise ValueError(f"PIT_REQUIRED_VALUE_MISSING:{column}")
    availability = MODES[mode]
    calendar = xcals.get_calendar("XNYS")
    rows: list[dict] = []
    for session in sorted(pd.DatetimeIndex(sessions)):
        label, cutoff = _signal_cutoff(pd.Timestamp(session), calendar)
        visible = source[source[availability] <= cutoff]
        if visible.empty:
            continue
        keys = ["security_id", "canonical_field", "reported_basis"]
        for _, group in visible.groupby(keys, sort=False, dropna=False):
            latest_period = group.period_end.max()
            eligible_period = group[group.period_end.eq(latest_period)]
            # Once an observation is visible, filing chronology determines
            # which restatement supersedes another.  In forward mode several
            # versions can share the same first_seen/available_at timestamp;
            # historical acceptance, accession, and source ID break those ties
            # without depending on input row order.
            order_columns = [
                "historical_available_at",
                "accession_number",
                "_mapping_priority_precedence",
                "source_fact_id",
            ]
            eligible_period = eligible_period.copy()
            eligible_period["_mapping_priority_precedence"] = (
                pd.to_numeric(eligible_period["mapping_priority"], errors="coerce")
                if "mapping_priority" in eligible_period else pd.Series(
                    float("inf"), index=eligible_period.index
                )
            ).fillna(float("inf"))
            ordered = eligible_period.sort_values(
                order_columns, ascending=[True, True, False, True], kind="stable"
            )
            winning_keys = ordered.iloc[-1][order_columns]
            tied = eligible_period[
                eligible_period[order_columns].eq(winning_keys).all(axis=1)
            ]
            material_columns = [
                "issuer_cik",
                "value",
                "unit",
                "semantic_grade",
                "mapping_version",
                "definition_hash",
            ]
            if len(tied[material_columns].drop_duplicates()) > 1:
                identity = ":".join(str(ordered.iloc[-1][key]) for key in keys)
                raise ValueError(f"PIT_RESTATEMENT_TIE_AMBIGUOUS:{identity}")
            selected = ordered.iloc[-1]
            rows.append({
                "session": label,
                "signal_cutoff": cutoff,
                "security_id": selected.security_id,
                "issuer_cik": selected.issuer_cik,
                "canonical_field": selected.canonical_field,
                "reported_basis": selected.reported_basis,
                "value": selected.value,
                "unit": selected.unit,
                "selected_available_at": selected[availability],
                "historical_available_at": selected.historical_available_at,
                "first_seen_at": selected.first_seen_at,
                "available_at": selected.available_at,
                "accession_number": selected.accession_number,
                "source_fact_id": selected.source_fact_id,
                "semantic_grade": selected.semantic_grade,
                "mapping_version": selected.mapping_version,
                "definition_hash": selected.definition_hash,
                "availability_mode": mode,
            })
    columns = [
        "session", "signal_cutoff", "security_id", "issuer_cik", "canonical_field",
        "reported_basis", "value", "unit", "selected_available_at",
        "historical_available_at", "first_seen_at", "available_at", "accession_number",
        "source_fact_id", "semantic_grade", "mapping_version", "definition_hash",
        "availability_mode",
    ]
    return pd.DataFrame(rows, columns=columns).sort_values(
        ["availability_mode", "canonical_field", "reported_basis", "session", "security_id"],
        kind="stable",
    ).reset_index(drop=True)
