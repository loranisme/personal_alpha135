import pandas as pd

from sec_edgar_pipeline.periods import _materialize_ttm, materialize_period_bases, select_versions_asof


def _cumulative_rows(amended_fy: bool = False):
    rows = []
    values = [
        ("Q1", "2024-03-31", 10.0, "2024-05-01T21:00:00Z", "q1"),
        ("Q2", "2024-06-30", 25.0, "2024-08-01T21:00:00Z", "h1"),
        ("Q3", "2024-09-30", 40.0, "2024-11-01T21:00:00Z", "m9"),
        ("FY", "2024-12-31", 60.0, "2025-02-01T21:00:00Z", "fy"),
    ]
    for fp, end, value, available, fact_id in values:
        rows.append({
            "issuer_cik": "0000000001", "security_id": "sec-1",
            "canonical_field": "revenue", "unit": "USD",
            "xbrl_namespace": "us-gaap", "xbrl_tag": "Revenues",
            "fiscal_year": 2024, "fiscal_period": fp,
            "period_start": "2024-01-01", "period_end": end,
            "duration_days": (pd.Timestamp(end) - pd.Timestamp("2024-01-01")).days + 1,
            "fact_type": "DURATION", "reported_basis": "RAW_REPORTED",
            "historical_available_at": available, "available_at": available,
            "source_fact_id": fact_id, "value": value,
        })
    if amended_fy:
        rows.append(rows[-1] | {
            "value": 62.0,
            "historical_available_at": "2025-03-15T21:00:00Z",
            "available_at": "2025-03-15T21:00:00Z",
            "source_fact_id": "fy-amendment",
        })
    return pd.DataFrame(rows)


def test_discrete_quarters_follow_q1_h1_9m_fy_identity():
    result = materialize_period_bases(_cumulative_rows())
    values = (
        result.query("reported_basis == 'QUARTERLY_DISCRETE'")
        .sort_values("period_end")
        .drop_duplicates(["fiscal_period"], keep="last")
        .value.tolist()
    )
    assert values == [10.0, 15.0, 15.0, 20.0]


def test_restatement_changes_q4_only_after_it_is_available():
    result = materialize_period_bases(_cumulative_rows(amended_fy=True))
    before = select_versions_asof(
        result, pd.Timestamp("2025-03-01T21:00:00Z"), "historical_available_at"
    )
    after = select_versions_asof(
        result, pd.Timestamp("2025-04-01T21:00:00Z"), "historical_available_at"
    )
    assert before.query("reported_basis == 'QUARTERLY_DISCRETE' and fiscal_period == 'Q4'").iloc[0].value == 20.0
    assert after.query("reported_basis == 'QUARTERLY_DISCRETE' and fiscal_period == 'Q4'").iloc[0].value == 22.0


def test_incompatible_cumulative_tags_are_not_subtracted():
    rows = _cumulative_rows()
    rows.loc[rows.fiscal_period.eq("Q2"), "xbrl_tag"] = "SalesRevenueNet"
    result = materialize_period_bases(rows)
    assert result.query("reported_basis == 'QUARTERLY_DISCRETE' and fiscal_period == 'Q2'").empty


def test_ttm_requires_four_visible_discrete_quarters():
    result = materialize_period_bases(_cumulative_rows())
    ttm = result.query("reported_basis == 'TTM'")
    assert len(ttm) == 1
    assert ttm.iloc[0].value == 60.0


def test_derived_quarter_metadata_uses_the_actual_discrete_window():
    result = materialize_period_bases(_cumulative_rows())
    discrete = (
        result.query("reported_basis == 'QUARTERLY_DISCRETE'")
        .sort_values("historical_available_at")
        .drop_duplicates("fiscal_period", keep="last")
        .set_index("fiscal_period")
    )
    assert discrete.loc["Q1", "period_start"] == "2024-01-01"
    assert discrete.loc["Q1", "period_end"] == "2024-03-31"
    assert discrete.loc["Q1", "duration_days"] == 91
    assert discrete.loc["Q2", "period_start"] == "2024-04-01"
    assert discrete.loc["Q2", "period_end"] == "2024-06-30"
    assert discrete.loc["Q2", "duration_days"] == 91
    assert discrete.loc["Q3", "period_start"] == "2024-07-01"
    assert discrete.loc["Q3", "period_end"] == "2024-09-30"
    assert discrete.loc["Q3", "duration_days"] == 92
    assert discrete.loc["Q4", "period_start"] == "2024-10-01"
    assert discrete.loc["Q4", "period_end"] == "2024-12-31"
    assert discrete.loc["Q4", "duration_days"] == 92


def test_ttm_metadata_spans_the_actual_four_quarter_window():
    result = materialize_period_bases(_cumulative_rows())
    row = result.query("reported_basis == 'TTM'").iloc[0]
    assert row.period_start == "2024-01-01"
    assert row.period_end == "2024-12-31"
    assert row.duration_days == 366


def test_asof_tie_prefers_the_reviewed_mapping_priority():
    base = {
        "issuer_cik": "0000000001",
        "security_id": "sec-1",
        "canonical_field": "revenue",
        "reported_basis": "ANNUAL",
        "fiscal_year": 2024,
        "fiscal_period": "FY",
        "historical_available_at": "2025-02-01T21:00:00Z",
        "available_at": "2025-02-01T21:00:00Z",
        "accession_number": "0001-25-000001",
    }
    versions = pd.DataFrame([
        base | {"mapping_priority": 1, "source_fact_id": "preferred", "value": 100.0},
        base | {"mapping_priority": 2, "source_fact_id": "zz-secondary", "value": 999.0},
    ])
    selected = select_versions_asof(
        versions,
        pd.Timestamp("2025-03-01T21:00:00Z"),
        "historical_available_at",
    )
    assert len(selected) == 1
    assert selected.iloc[0].source_fact_id == "preferred"
    assert selected.iloc[0].mapping_priority == 1


def test_ttm_keeps_actual_quarter_when_later_filing_relabels_a_comparative():
    rows = []
    for source_id, fiscal_year, fiscal_period, start, end, value, available in (
        ("q4-2025", 2025, "Q4", "2025-06-29", "2025-09-27", 10, "2025-10-31T21:00:00Z"),
        ("q4-2024-comparative", 2025, "Q4", "2024-06-30", "2024-09-28", 9, "2026-01-15T21:00:00Z"),
        ("q1-2026", 2026, "Q1", "2025-09-28", "2025-12-27", 20, "2026-01-30T21:00:00Z"),
        ("q2-2026", 2026, "Q2", "2025-12-28", "2026-03-28", 30, "2026-05-01T21:00:00Z"),
        ("q3-2026", 2026, "Q3", "2026-03-29", "2026-06-27", 40, "2026-07-31T21:00:00Z"),
    ):
        rows.append({
            "issuer_cik": "0000320193", "security_id": "sec-aapl",
            "canonical_field": "revenue", "unit": "USD",
            "xbrl_namespace": "us-gaap", "xbrl_tag": "RevenueFromContractWithCustomerExcludingAssessedTax",
            "fiscal_year": fiscal_year, "fiscal_period": fiscal_period,
            "period_start": start, "period_end": end,
            "historical_available_at": available, "available_at": available,
            "source_fact_id": source_id, "value": value,
        })
    result = _materialize_ttm(pd.DataFrame(rows))
    latest = [row for row in result if row["period_end"] == "2026-06-27"]
    assert len(latest) == 1
    assert latest[0]["value"] == 100
    assert "q4-2025" in latest[0]["input_source_fact_ids"]
    assert "q4-2024-comparative" not in latest[0]["input_source_fact_ids"]


def test_comparative_m9_cannot_replace_current_m9_in_quarter_derivation():
    # CompanyFacts fy/fp are filing labels. The 2025 comparative M9 below is
    # repeated in a 2026 Q3 filing and has fy=2026 despite a 2024 start.
    observations = [
        ("2025-q1", 2025, "Q1", "2024-09-29", "2024-12-28", 20_000, "2025-01-31T21:00:00Z"),
        ("2025-h1", 2025, "Q2", "2024-09-29", "2025-03-29", 50_000, "2025-05-02T21:00:00Z"),
        ("2025-m9", 2025, "Q3", "2024-09-29", "2025-06-28", 84_544, "2025-08-01T21:00:00Z"),
        ("2025-fy", 2025, "FY", "2024-09-29", "2025-09-27", 112_010, "2025-10-31T21:00:00Z"),
        ("2026-q1", 2026, "Q1", "2025-09-28", "2025-12-27", 42_097, "2026-01-30T21:00:00Z"),
        ("2026-h1", 2026, "Q2", "2025-09-28", "2026-03-28", 71_675, "2026-05-01T21:00:00Z"),
        ("2025-h1-comparative", 2026, "Q2", "2024-09-29", "2025-03-29", 50_000, "2026-05-02T21:00:00Z"),
        ("2026-m9", 2026, "Q3", "2025-09-28", "2026-06-27", 101_464, "2026-07-31T21:00:00Z"),
        ("2025-m9-comparative", 2026, "Q3", "2024-09-29", "2025-06-28", 84_544, "2026-08-01T21:00:00Z"),
    ]
    rows = pd.DataFrame([
        {
            "issuer_cik": "0000320193", "security_id": "sec-aapl",
            "canonical_field": "net_income", "unit": "USD",
            "xbrl_namespace": "us-gaap", "xbrl_tag": "NetIncomeLoss",
            "fiscal_year": fy, "fiscal_period": fp,
            "period_start": start, "period_end": end,
            "duration_days": (pd.Timestamp(end) - pd.Timestamp(start)).days + 1,
            "fact_type": "DURATION", "reported_basis": "RAW_REPORTED",
            "historical_available_at": available, "available_at": available,
            "source_fact_id": source_id, "value": value,
        }
        for source_id, fy, fp, start, end, value, available in observations
    ])

    result = materialize_period_bases(rows)
    comparative = result.query(
        "reported_basis == 'QUARTERLY_DISCRETE' and "
        "fiscal_period == 'Q3' and period_end == '2025-06-28'"
    )
    assert set(comparative.fiscal_year) == {2025}
    pre_release = result.loc[
        result.historical_available_at.lt(pd.Timestamp("2026-07-31T21:00:00Z"))
    ].query("reported_basis == 'TTM' and period_end == '2026-06-27'")
    assert pre_release.empty
    for cutoff in ("2026-07-31T22:00:00Z", "2026-08-02T22:00:00Z"):
        visible = result.loc[result.historical_available_at.le(pd.Timestamp(cutoff))]
        current = visible.query(
            "reported_basis == 'TTM' and period_end == '2026-06-27'"
        )
        assert not current.empty
        assert current.iloc[-1].value == 128_930
        assert current.iloc[-1].period_start == "2025-06-29"
