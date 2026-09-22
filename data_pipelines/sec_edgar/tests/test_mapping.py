import pandas as pd

from sec_edgar_pipeline.mapping import apply_canonical_mapping, load_mapping_catalog


def _catalog():
    return load_mapping_catalog(
        "data_pipelines/sec_edgar/mappings/canonical_fields.csv"
    )


def test_assets_maps_only_from_reviewed_standard_tag():
    facts = pd.DataFrame([
        {
            "xbrl_namespace": "us-gaap",
            "xbrl_tag": "Assets",
            "unit": "USD",
            "fact_type": "INSTANT",
            "filed_date": "2024-02-01",
            "value": 10.0,
            "mapping_status": "UNMAPPED",
        },
        {
            "xbrl_namespace": "custom",
            "xbrl_tag": "AdjustedAssets",
            "unit": "USD",
            "fact_type": "INSTANT",
            "filed_date": "2024-02-01",
            "value": 99.0,
            "mapping_status": "DEFERRED_CUSTOM_TAG",
        },
    ])
    mapped, deferred = apply_canonical_mapping(facts, _catalog())
    assert mapped.iloc[0].canonical_field == "total_assets"
    assert deferred.iloc[0].mapping_status == "DEFERRED_CUSTOM_TAG"


def test_wrong_unit_or_basis_never_falls_back():
    facts = pd.DataFrame([{
        "xbrl_namespace": "us-gaap",
        "xbrl_tag": "Assets",
        "unit": "shares",
        "fact_type": "INSTANT",
        "filed_date": "2024-02-01",
        "value": 10.0,
        "mapping_status": "UNMAPPED",
    }])
    mapped, deferred = apply_canonical_mapping(facts, _catalog())
    assert mapped.empty
    assert deferred.iloc[0].mapping_status == "DEFERRED_UNIT_OR_BASIS"


def test_unknown_standard_tag_is_deferred_semantic():
    facts = pd.DataFrame([{
        "xbrl_namespace": "us-gaap",
        "xbrl_tag": "NotReviewedConcept",
        "unit": "USD",
        "fact_type": "DURATION",
        "filed_date": "2024-02-01",
        "value": 10.0,
        "mapping_status": "UNMAPPED",
    }])
    mapped, deferred = apply_canonical_mapping(facts, _catalog())
    assert mapped.empty
    assert deferred.iloc[0].mapping_status == "DEFERRED_SEMANTIC"


def test_catalog_has_every_approved_base_field():
    required = {
        "total_assets", "total_liabilities", "stockholders_equity",
        "current_assets", "current_liabilities", "cash_and_cash_equivalents",
        "inventory", "accounts_receivable", "goodwill", "short_term_debt",
        "long_term_debt_current", "long_term_debt_noncurrent",
        "deferred_tax_assets", "deferred_tax_liabilities",
        "deferred_tax_valuation_allowance", "revenue", "gross_profit",
        "operating_income", "net_income", "research_and_development_expense",
        "interest_expense", "income_tax_expense",
        "depreciation_and_amortization", "operating_cash_flow",
        "capital_expenditure", "dividends_paid", "income_taxes_paid",
        "shares_outstanding_sec", "weighted_average_basic_shares",
        "weighted_average_diluted_shares", "basic_eps", "diluted_eps",
    }
    assert required <= set(_catalog().canonical_field)


def test_mapping_priority_is_propagated_to_mapped_fact():
    facts = pd.DataFrame([{
        "xbrl_namespace": "us-gaap",
        "xbrl_tag": "SalesRevenueNet",
        "unit": "USD",
        "fact_type": "DURATION",
        "filed_date": "2024-02-01",
        "value": 10.0,
        "mapping_status": "UNMAPPED",
    }])
    mapped, deferred = apply_canonical_mapping(facts, _catalog())
    assert deferred.empty
    row = mapped.iloc[0]
    assert row.canonical_field == "revenue"
    assert row.mapping_priority == 2
