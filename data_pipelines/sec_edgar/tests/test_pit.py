import pandas as pd
import pytest

from sec_edgar_pipeline.pit import build_daily_pit_panel


def _versions():
    return pd.DataFrame([
        {
            "issuer_cik": "0000000001", "security_id": "sec-a",
            "canonical_field": "revenue", "reported_basis": "TTM",
            "value": 100.0, "unit": "USD", "period_end": "2025-12-31",
            "historical_available_at": "2026-02-03T21:00:00Z",
            "first_seen_at": "2026-02-05T13:00:00Z",
            "available_at": "2026-02-05T13:00:00Z",
            "accession_number": "0001", "source_fact_id": "fact-1",
            "semantic_grade": "SOURCE_LINKED_DIRECT", "mapping_version": "v1",
            "definition_hash": None,
        },
        {
            "issuer_cik": "0000000001", "security_id": "sec-a",
            "canonical_field": "revenue", "reported_basis": "TTM",
            "value": 125.0, "unit": "USD", "period_end": "2026-03-31",
            "historical_available_at": "2026-05-01T20:30:00Z",
            "first_seen_at": "2026-05-01T20:31:00Z",
            "available_at": "2026-05-01T20:31:00Z",
            "accession_number": "0002", "source_fact_id": "fact-2",
            "semantic_grade": "SOURCE_LINKED_DIRECT", "mapping_version": "v1",
            "definition_hash": None,
        },
    ])


def test_future_filing_is_absent_before_availability():
    sessions = pd.DatetimeIndex(["2026-02-02", "2026-02-03", "2026-02-04"])
    panel = build_daily_pit_panel(_versions(), sessions, "HISTORICAL_DIAGNOSTIC")
    before = panel[
        panel.session.eq(pd.Timestamp("2026-02-02"))
        & panel.canonical_field.eq("revenue")
    ]
    after = panel[
        panel.session.eq(pd.Timestamp("2026-02-04"))
        & panel.canonical_field.eq("revenue")
    ]
    assert before.empty
    assert after.iloc[0].value == 100.0


def test_forward_mode_respects_first_seen():
    sessions = pd.DatetimeIndex(["2026-02-03", "2026-02-04", "2026-02-05"])
    historical = build_daily_pit_panel(_versions(), sessions, "HISTORICAL_DIAGNOSTIC")
    forward = build_daily_pit_panel(_versions(), sessions, "FORWARD_OBSERVED")
    assert len(historical) > len(forward)
    assert forward.available_at.le(forward.signal_cutoff).all()


@pytest.mark.parametrize(
    ("column", "value", "error"),
    [
        ("available_at", "2026-02-03T20:59:59Z", "PIT_AVAILABLE_AT_INVARIANT"),
        ("period_end", None, "PIT_REQUIRED_TIME_MISSING"),
        ("source_fact_id", "", "PIT_REQUIRED_VALUE_MISSING:source_fact_id"),
    ],
)
def test_pit_panel_fails_closed_on_invalid_timing_or_lineage(
    column: str, value: object, error: str
) -> None:
    versions = _versions().iloc[[0]].copy()
    versions.loc[:, column] = value

    with pytest.raises(ValueError, match=error):
        build_daily_pit_panel(
            versions,
            pd.DatetimeIndex(["2026-02-04"]),
            "HISTORICAL_DIAGNOSTIC",
        )


def test_after_close_filing_enters_next_session():
    versions = _versions().iloc[[0]].copy()
    versions["historical_available_at"] = "2026-02-03T21:05:00Z"
    sessions = pd.DatetimeIndex(["2026-02-03", "2026-02-04"])
    panel = build_daily_pit_panel(versions, sessions, "HISTORICAL_DIAGNOSTIC")
    assert panel.session.tolist() == [pd.Timestamp("2026-02-04")]


def test_later_restatement_of_older_period_does_not_replace_newer_period():
    versions = _versions()
    restatement = versions.iloc[0].copy()
    restatement["value"] = 105.0
    restatement["historical_available_at"] = "2026-06-01T20:00:00Z"
    restatement["available_at"] = "2026-06-01T20:00:00Z"
    restatement["source_fact_id"] = "fact-1a"
    versions = pd.concat([versions, pd.DataFrame([restatement])], ignore_index=True)
    panel = build_daily_pit_panel(
        versions, pd.DatetimeIndex(["2026-06-02"]), "HISTORICAL_DIAGNOSTIC"
    )
    assert panel.iloc[0].value == 125.0


def test_timezone_aware_session_label_keeps_date_and_uses_early_close():
    versions = _versions().iloc[[0]].copy()
    versions["historical_available_at"] = "2025-11-28T17:59:00Z"
    versions["first_seen_at"] = "2025-11-28T17:59:00Z"
    versions["available_at"] = "2025-11-28T17:59:00Z"
    versions["period_end"] = "2025-09-30"
    panel = build_daily_pit_panel(
        versions,
        pd.DatetimeIndex([pd.Timestamp("2025-11-28T00:00:00Z")]),
        "HISTORICAL_DIAGNOSTIC",
    )
    assert panel.iloc[0].session == pd.Timestamp("2025-11-28")
    assert panel.iloc[0].signal_cutoff == pd.Timestamp("2025-11-28T18:00:00Z")


def test_after_early_close_filing_enters_next_exchange_session():
    versions = _versions().iloc[[0]].copy()
    versions["historical_available_at"] = "2025-11-28T18:01:00Z"
    versions["first_seen_at"] = "2025-11-28T18:01:00Z"
    versions["available_at"] = "2025-11-28T18:01:00Z"
    versions["period_end"] = "2025-09-30"
    panel = build_daily_pit_panel(
        versions,
        pd.DatetimeIndex(["2025-11-28", "2025-12-01"]),
        "HISTORICAL_DIAGNOSTIC",
    )
    assert panel.session.tolist() == [pd.Timestamp("2025-12-01")]


def test_forward_restatement_tie_uses_acceptance_then_accession_then_source_id():
    base = _versions().iloc[0].copy()
    base["period_end"] = "2025-12-31"
    base["first_seen_at"] = "2026-02-05T13:00:00Z"
    base["available_at"] = "2026-02-05T13:00:00Z"
    rows = []
    for value, historical, accession, source_id in [
        (101.0, "2026-02-03T20:00:00Z", "0001", "fact-z"),
        (102.0, "2026-02-04T20:00:00Z", "0001", "fact-a"),
        (103.0, "2026-02-04T20:00:00Z", "0002", "fact-a"),
        (104.0, "2026-02-04T20:00:00Z", "0002", "fact-z"),
    ]:
        row = base.copy()
        row["value"] = value
        row["historical_available_at"] = historical
        row["accession_number"] = accession
        row["source_fact_id"] = source_id
        rows.append(row)
    panel = build_daily_pit_panel(
        pd.DataFrame(rows),
        pd.DatetimeIndex(["2026-02-05"]),
        "FORWARD_OBSERVED",
    )
    assert panel.iloc[0].value == 104.0


def test_same_filing_prefers_canonical_mapping_priority_before_source_id():
    first = _versions().iloc[0].copy()
    first["mapping_priority"] = 1
    first["source_fact_id"] = "preferred"
    second = first.copy()
    second["mapping_priority"] = 2
    second["source_fact_id"] = "zz-secondary"
    second["value"] = 999.0
    panel = build_daily_pit_panel(
        pd.DataFrame([first, second]), pd.DatetimeIndex(["2026-02-04"]),
        "HISTORICAL_DIAGNOSTIC",
    )
    assert panel.iloc[0].value == 100.0


def test_restatement_tie_with_conflicting_material_values_fails_closed():
    first = _versions().iloc[0].copy()
    second = first.copy()
    second["value"] = 999.0
    versions = pd.DataFrame([first, second])

    with pytest.raises(ValueError, match="PIT_RESTATEMENT_TIE_AMBIGUOUS"):
        build_daily_pit_panel(
            versions,
            pd.DatetimeIndex(["2026-02-04"]),
            "HISTORICAL_DIAGNOSTIC",
        )
