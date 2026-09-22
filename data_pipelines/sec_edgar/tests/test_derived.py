import json
from pathlib import Path

import pandas as pd

import pytest

from sec_edgar_pipeline.derived import (
    materialize_derived_fields,
    materialize_derived_fields_with_diagnostics,
)


def _definitions():
    return json.loads(
        Path("data_pipelines/sec_edgar/config/derived_fields.json").read_text()
    )["definitions"]


def _rows():
    common = {
        "issuer_cik": "0000000001", "security_id": "sec-1", "unit": "USD",
        "period_end": "2024-12-31", "fiscal_year": 2024,
        "historical_available_at": "2025-02-01T21:00:00Z",
        "available_at": "2025-02-01T21:00:00Z",
        "semantic_grade": "SOURCE_LINKED_DIRECT",
    }
    return pd.DataFrame([
        common | {"canonical_field": "operating_cash_flow", "reported_basis": "TTM", "value": 100.0, "source_fact_id": "ocf"},
        common | {"canonical_field": "capital_expenditure", "reported_basis": "TTM", "value": 30.0, "source_fact_id": "capex", "sign_convention": "POSITIVE_OUTFLOW"},
        common | {"canonical_field": "shares_outstanding_sec", "reported_basis": "INSTANT", "value": 10.0, "unit": "shares", "source_fact_id": "shares", "semantic_grade": "HYPOTHESIS_PROXY"},
    ])


def test_free_cash_flow_requires_both_inputs():
    rows = _rows().query("canonical_field != 'capital_expenditure'")
    result = materialize_derived_fields_with_diagnostics(
        rows, None, _definitions()
    )
    assert result.values.query("canonical_field == 'free_cash_flow'").empty
    diagnostic = result.diagnostics.query(
        "canonical_field == 'free_cash_flow' and reason_code == 'MISSING_INPUT'"
    )
    assert not diagnostic.empty
    assert "capital_expenditure" in diagnostic.iloc[-1].reason_detail


def test_free_cash_flow_uses_positive_outflow():
    result = materialize_derived_fields(_rows(), None, _definitions())
    assert result.query("canonical_field == 'free_cash_flow'").iloc[-1].value == 70.0


def test_market_cap_is_proxy_and_uses_visible_raw_close():
    prices = _price_panel(["2025-02-01T21:00:00Z"], [5.0])
    result = materialize_derived_fields(_rows(), prices, _definitions())
    row = result.query("canonical_field == 'market_cap_sec_proxy'").iloc[-1]
    assert row.semantic_grade == "HYPOTHESIS_PROXY"
    assert row.value == 50.0
    assert row.input_shares == 10.0
    assert row.input_raw_close == 5.0
    assert row.unit == "USD"
    assert row.price_snapshot_hash == "sha256:" + "a" * 64
    assert row.price_event_at == pd.Timestamp("2025-02-01T21:00:00Z")
    assert row.price_provider == "TEST_RAW_CLOSE"
    assert row.price_field == "raw_close"
    assert row.price_adjustment == "unadjusted"
    assert row.price_timestamp_semantics == "OBSERVED_AT_UTC"


def _price_panel(index: list[str], values: list[float]) -> pd.DataFrame:
    prices = pd.DataFrame(
        {"sec-1": values}, index=pd.DatetimeIndex(index)
    )
    prices.attrs["snapshot_hash"] = "sha256:" + "a" * 64
    prices.attrs["currency_by_security"] = {"sec-1": "USD"}
    prices.attrs["provider"] = "TEST_RAW_CLOSE"
    prices.attrs["field"] = "raw_close"
    prices.attrs["adjustment"] = "unadjusted"
    prices.attrs["timestamp_semantics"] = "OBSERVED_AT_UTC"
    return prices


def _aligned_rows(
    field_values: list[tuple[str, str, float, str, str | None]],
    *,
    fiscal_year: int = 2024,
    fiscal_period: str = "FY",
    available: str = "2025-02-01T21:00:00Z",
) -> pd.DataFrame:
    rows = []
    for field, end, value, unit, context in field_values:
        rows.append(
            {
                "issuer_cik": "0000000001",
                "security_id": "sec-1",
                "canonical_field": field,
                "reported_basis": "INSTANT",
                "value": value,
                "unit": unit,
                "period_start": None,
                "period_end": end,
                "fiscal_year": fiscal_year,
                "fiscal_period": fiscal_period,
                "dimensions_signature": context,
                "historical_available_at": available,
                "available_at": available,
                "source_fact_id": f"{field}:{end}:{unit}:{context}",
                "sign_convention": "AS_REPORTED",
                "semantic_grade": "SOURCE_LINKED_DIRECT",
            }
        )
    return pd.DataFrame(rows)


@pytest.mark.parametrize(
    ("liability_end", "liability_unit", "liability_context", "reason"),
    [
        ("2024-09-30", "USD", None, "INPUT_PERIOD_MISMATCH"),
        ("2024-12-31", "EUR", None, "INPUT_CURRENCY_MISMATCH"),
        ("2024-12-31", "USD", "segment-a", "INPUT_CONTEXT_MISMATCH"),
    ],
)
def test_derived_inputs_require_period_currency_and_context_alignment(
    liability_end: str,
    liability_unit: str,
    liability_context: str | None,
    reason: str,
) -> None:
    rows = _aligned_rows(
        [
            ("current_assets", "2024-12-31", 100.0, "USD", None),
            (
                "current_liabilities",
                liability_end,
                40.0,
                liability_unit,
                liability_context,
            ),
        ]
    )

    result = materialize_derived_fields_with_diagnostics(
        rows, None, _definitions()
    )

    assert result.values.query("canonical_field == 'working_capital'").empty
    assert not result.diagnostics.query(
        "canonical_field == 'working_capital' and reason_code == @reason"
    ).empty


def test_market_cap_materializes_each_raw_close_session() -> None:
    prices = _price_panel(
        [
            "2025-02-01T21:00:00Z",
            "2025-02-02T21:00:00Z",
            "2025-02-03T21:00:00Z",
        ],
        [5.0, 5.5, 6.0],
    )

    result = materialize_derived_fields(_rows(), prices, _definitions())
    market_cap = result.query("canonical_field == 'market_cap_sec_proxy'")

    assert market_cap.value.tolist() == [50.0, 55.0, 60.0]
    assert market_cap.historical_available_at.tolist() == list(prices.index)
    assert set(market_cap.unit) == {"USD"}
    assert set(market_cap.price_snapshot_hash) == {"sha256:" + "a" * 64}


def test_price_snapshot_hash_is_mandatory() -> None:
    prices = pd.DataFrame(
        {"sec-1": [5.0]},
        index=pd.DatetimeIndex(["2025-02-01T21:00:00Z"]),
    )
    prices.attrs["currency_by_security"] = {"sec-1": "USD"}
    prices.attrs["provider"] = "TEST_RAW_CLOSE"
    prices.attrs["field"] = "raw_close"
    prices.attrs["adjustment"] = "unadjusted"
    prices.attrs["timestamp_semantics"] = "OBSERVED_AT_UTC"

    with pytest.raises(ValueError, match="PRICE_SNAPSHOT_HASH_REQUIRED"):
        materialize_derived_fields(_rows(), prices, _definitions())


def test_price_snapshot_hash_must_be_a_sha256_digest() -> None:
    prices = _price_panel(["2025-02-01T21:00:00Z"], [5.0])
    prices.attrs["snapshot_hash"] = "sha256:not-a-digest"

    with pytest.raises(ValueError, match="PRICE_SNAPSHOT_HASH_REQUIRED"):
        materialize_derived_fields(_rows(), prices, _definitions())


@pytest.mark.parametrize(
    ("attribute", "value", "error"),
    [
        ("provider", "", "PRICE_PROVIDER_REQUIRED"),
        ("field", "adjusted_close", "PRICE_FIELD_MUST_BE_RAW_CLOSE"),
        ("adjustment", "split_adjusted", "PRICE_ADJUSTMENT_MUST_BE_UNADJUSTED"),
        (
            "timestamp_semantics",
            "SESSION_LABEL",
            "PRICE_TIMESTAMP_SEMANTICS_MUST_BE_OBSERVED_AT_UTC",
        ),
    ],
)
def test_price_lineage_must_prove_unadjusted_raw_close(
    attribute: str, value: str, error: str,
) -> None:
    prices = _price_panel(["2025-02-01T21:00:00Z"], [5.0])
    prices.attrs[attribute] = value

    with pytest.raises(ValueError, match=error):
        materialize_derived_fields(_rows(), prices, _definitions())


def test_price_currency_is_mandatory_and_must_align_for_enterprise_value() -> None:
    prices = _price_panel(["2025-02-01T21:00:00Z"], [5.0])
    prices.attrs["currency_by_security"] = {"sec-1": "EUR"}
    accounting = _aligned_rows(
        [
            ("short_term_debt", "2024-12-31", 1.0, "USD", None),
            ("long_term_debt_current", "2024-12-31", 2.0, "USD", None),
            ("long_term_debt_noncurrent", "2024-12-31", 3.0, "USD", None),
            ("cash_and_cash_equivalents", "2024-12-31", 4.0, "USD", None),
        ]
    )
    rows = pd.concat([_rows(), accounting], ignore_index=True, sort=False)

    result = materialize_derived_fields_with_diagnostics(
        rows, prices, _definitions()
    )

    assert result.values.query(
        "canonical_field == 'enterprise_value_sec_proxy'"
    ).empty
    assert not result.diagnostics.query(
        "canonical_field == 'enterprise_value_sec_proxy' and reason_code == 'INPUT_CURRENCY_MISMATCH'"
    ).empty


def test_enterprise_value_rejects_stale_share_period_hidden_by_daily_market_cap() -> None:
    prices = _price_panel(["2025-02-01T21:00:00Z"], [5.0])
    base = _rows()
    base.loc[
        base.canonical_field.eq("shares_outstanding_sec"), "period_end"
    ] = "2024-09-30"
    accounting = _aligned_rows(
        [
            ("short_term_debt", "2024-12-31", 1.0, "USD", None),
            ("long_term_debt_current", "2024-12-31", 2.0, "USD", None),
            ("long_term_debt_noncurrent", "2024-12-31", 3.0, "USD", None),
            ("cash_and_cash_equivalents", "2024-12-31", 4.0, "USD", None),
        ]
    )

    result = materialize_derived_fields_with_diagnostics(
        pd.concat([base, accounting], ignore_index=True, sort=False),
        prices,
        _definitions(),
    )

    assert result.values.query(
        "canonical_field == 'enterprise_value_sec_proxy'"
    ).empty
    assert not result.diagnostics.query(
        "canonical_field == 'enterprise_value_sec_proxy' and "
        "reason_code == 'INPUT_PERIOD_MISMATCH'"
    ).empty


def test_enterprise_value_preserves_accounting_and_price_lineage() -> None:
    prices = _price_panel(["2025-02-01T21:00:00Z"], [5.0])
    accounting = _aligned_rows(
        [
            ("short_term_debt", "2024-12-31", 1.0, "USD", None),
            ("long_term_debt_current", "2024-12-31", 2.0, "USD", None),
            ("long_term_debt_noncurrent", "2024-12-31", 3.0, "USD", None),
            ("cash_and_cash_equivalents", "2024-12-31", 4.0, "USD", None),
        ]
    )

    row = materialize_derived_fields(
        pd.concat([_rows(), accounting], ignore_index=True, sort=False),
        prices,
        _definitions(),
    ).query("canonical_field == 'enterprise_value_sec_proxy'").iloc[-1]

    assert row.value == 52.0
    assert row.accounting_period_end == "2024-12-31"
    assert row.price_event_at == pd.Timestamp("2025-02-01T21:00:00Z")
    assert row.price_snapshot_hash == "sha256:" + "a" * 64
    assert row.price_field == "raw_close"
    assert row.price_adjustment == "unadjusted"
    assert row.price_timestamp_semantics == "OBSERVED_AT_UTC"


def test_prior_value_requires_exact_previous_fiscal_year_and_same_anchor() -> None:
    current = _aligned_rows(
        [("total_assets", "2024-12-31", 120.0, "USD", None)],
        fiscal_year=2024,
    )
    too_old = _aligned_rows(
        [("total_assets", "2022-12-31", 100.0, "USD", None)],
        fiscal_year=2022,
        available="2023-02-01T21:00:00Z",
    )
    wrong_anchor = _aligned_rows(
        [("total_assets", "2023-09-30", 110.0, "USD", None)],
        fiscal_year=2023,
        fiscal_period="Q3",
        available="2023-11-01T21:00:00Z",
    )

    result = materialize_derived_fields_with_diagnostics(
        pd.concat([current, too_old, wrong_anchor], ignore_index=True),
        None,
        _definitions(),
    )

    assert result.values.query("canonical_field == 'asset_growth'").empty
    diagnostic = result.diagnostics.query(
        "canonical_field == 'asset_growth'"
    ).iloc[-1]
    assert diagnostic.reason_code == "PRIOR_FISCAL_ANCHOR_MISMATCH"
    assert "2023" in diagnostic.reason_detail


def test_prior_value_does_not_skip_missing_previous_fiscal_year() -> None:
    current = _aligned_rows(
        [("total_assets", "2024-12-31", 120.0, "USD", None)],
        fiscal_year=2024,
    )
    too_old = _aligned_rows(
        [("total_assets", "2022-12-31", 100.0, "USD", None)],
        fiscal_year=2022,
        available="2023-02-01T21:00:00Z",
    )

    result = materialize_derived_fields_with_diagnostics(
        pd.concat([current, too_old], ignore_index=True), None, _definitions()
    )

    assert result.values.query("canonical_field == 'asset_growth'").empty
    assert not result.diagnostics.query(
        "canonical_field == 'asset_growth' and reason_code == 'PRIOR_FISCAL_YEAR_MISSING'"
    ).empty


def test_negative_capex_is_normalized_to_positive_outflow() -> None:
    rows = _rows()
    rows.loc[rows.canonical_field.eq("capital_expenditure"), "value"] = -30.0
    rows.loc[
        rows.canonical_field.eq("capital_expenditure"), "sign_convention"
    ] = "POSITIVE_OUTFLOW"

    result = materialize_derived_fields(rows, None, _definitions())
    row = result.query("canonical_field == 'free_cash_flow'").iloc[-1]

    assert row.value == 70.0
    assert json.loads(row.input_transformations) == {
        "capital_expenditure": "ABS_POSITIVE_OUTFLOW"
    }


def test_invalid_denominator_has_auditable_reason_and_no_zero_fill() -> None:
    current = _aligned_rows(
        [("total_assets", "2024-12-31", 120.0, "USD", None)],
        fiscal_year=2024,
    )
    prior = _aligned_rows(
        [("total_assets", "2023-12-31", 0.0, "USD", None)],
        fiscal_year=2023,
        available="2024-02-01T21:00:00Z",
    )

    result = materialize_derived_fields_with_diagnostics(
        pd.concat([current, prior], ignore_index=True), None, _definitions()
    )

    assert result.values.query("canonical_field == 'asset_growth'").empty
    invalid = result.diagnostics.query(
        "canonical_field == 'asset_growth' and reason_code == 'NON_POSITIVE_DENOMINATOR'"
    )
    assert not invalid.empty
    assert invalid.iloc[-1].diagnostic_status == "INVALID"


def test_derived_output_forward_availability_is_maximum_of_input_availability() -> None:
    rows = _rows()
    rows.loc[
        rows.canonical_field.eq("operating_cash_flow"), "available_at"
    ] = "2025-02-10T21:00:00Z"
    rows.loc[
        rows.canonical_field.eq("capital_expenditure"), "available_at"
    ] = "2025-02-06T21:00:00Z"

    row = materialize_derived_fields(rows, None, _definitions()).query(
        "canonical_field == 'free_cash_flow'"
    ).iloc[-1]

    assert row.historical_available_at == pd.Timestamp("2025-02-01T21:00:00Z")
    assert row.available_at == pd.Timestamp("2025-02-10T21:00:00Z")


def test_security_id_cannot_mix_issuer_histories() -> None:
    rows = _rows()
    second_issuer = rows.iloc[[0]].copy()
    second_issuer["issuer_cik"] = "0000000002"

    with pytest.raises(ValueError, match="DERIVED_SECURITY_ISSUER_AMBIGUOUS:sec-1"):
        materialize_derived_fields(
            pd.concat([rows, second_issuer], ignore_index=True), None, _definitions()
        )


def test_unapproved_semantic_input_is_rejected_with_diagnostic() -> None:
    rows = _rows()
    rows.loc[
        rows.canonical_field.eq("operating_cash_flow"), "semantic_grade"
    ] = "UNREVIEWED"

    result = materialize_derived_fields_with_diagnostics(rows, None, _definitions())

    assert result.values.query("canonical_field == 'free_cash_flow'").empty
    rejected = result.diagnostics.query(
        "canonical_field == 'free_cash_flow' and "
        "reason_code == 'INPUT_SEMANTIC_GRADE_UNAPPROVED'"
    )
    assert not rejected.empty
    assert rejected.iloc[-1].diagnostic_status == "INVALID"


def test_proxy_input_cannot_be_promoted_to_source_linked_output() -> None:
    rows = _aligned_rows(
        [
            ("current_assets", "2024-12-31", 100.0, "USD", None),
            ("current_liabilities", "2024-12-31", 40.0, "USD", None),
        ]
    )
    rows.loc[rows.canonical_field.eq("current_assets"), "semantic_grade"] = (
        "HYPOTHESIS_PROXY"
    )

    result = materialize_derived_fields_with_diagnostics(rows, None, _definitions())

    assert result.values.query("canonical_field == 'working_capital'").empty
    assert not result.diagnostics.query(
        "canonical_field == 'working_capital' and "
        "reason_code == 'INPUT_SEMANTIC_GRADE_INCOMPATIBLE'"
    ).empty


def test_capex_without_positive_outflow_contract_is_rejected() -> None:
    rows = _rows()
    rows.loc[
        rows.canonical_field.eq("capital_expenditure"), "sign_convention"
    ] = "AS_REPORTED"

    result = materialize_derived_fields_with_diagnostics(rows, None, _definitions())

    assert result.values.query("canonical_field == 'free_cash_flow'").empty
    assert not result.diagnostics.query(
        "canonical_field == 'free_cash_flow' and "
        "reason_code == 'INPUT_SIGN_CONVENTION_UNAPPROVED'"
    ).empty


def test_negative_debt_component_is_invalid_in_positive_obligation_formula() -> None:
    rows = _aligned_rows(
        [
            ("short_term_debt", "2024-12-31", -1.0, "USD", None),
            ("long_term_debt_current", "2024-12-31", 2.0, "USD", None),
            ("long_term_debt_noncurrent", "2024-12-31", 3.0, "USD", None),
        ]
    )

    result = materialize_derived_fields_with_diagnostics(rows, None, _definitions())

    assert result.values.query("canonical_field == 'total_debt'").empty
    invalid = result.diagnostics.query(
        "canonical_field == 'total_debt' and reason_code == 'NEGATIVE_DEBT_INPUT'"
    )
    assert not invalid.empty
    assert invalid.iloc[-1].diagnostic_status == "INVALID"
