import pandas as pd

from sec_edgar_pipeline.availability import (
    forward_available_at,
    historical_available_at,
)


def test_exact_acceptance_adds_ten_minutes():
    value, precision = historical_available_at(
        "2026-02-03T20:50:00Z", "2026-02-03", "XNYS", "16:00"
    )
    assert value == pd.Timestamp("2026-02-03T21:00:00Z")
    assert precision == "EXACT_ACCEPTANCE"


def test_compact_sec_acceptance_format_is_supported():
    value, precision = historical_available_at(
        "20260203205000", "2026-02-03", "XNYS", "16:00"
    )
    assert value == pd.Timestamp("2026-02-03T21:00:00Z")
    assert precision == "EXACT_ACCEPTANCE"


def test_date_only_record_uses_next_session_cutoff():
    value, precision = historical_available_at(
        None, "2026-07-03", "XNYS", "16:00"
    )
    assert value == pd.Timestamp("2026-07-06T20:00:00Z")
    assert precision == "DATE_ONLY_NEXT_SESSION_CUTOFF"


def test_date_only_record_uses_actual_early_close():
    value, precision = historical_available_at(None, "2025-11-26", "XNYS", "16:00")
    assert value == pd.Timestamp("2025-11-28T18:00:00Z")
    assert precision == "DATE_ONLY_NEXT_SESSION_CUTOFF"


def test_forward_time_cannot_precede_first_observation():
    historical = pd.Timestamp("2026-02-03T21:00:00Z")
    observed = pd.Timestamp("2026-02-04T13:00:00Z")
    assert forward_available_at(historical, observed) == observed
