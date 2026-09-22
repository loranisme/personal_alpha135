from __future__ import annotations

import json
from pathlib import Path

import pytest

from sec_edgar_pipeline.contracts import load_policy


def _approved_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "provider": "SEC_EDGAR_COMPANYFACTS",
        "supported_namespaces": ["us-gaap", "dei"],
        "supported_forms": ["10-K", "10-K/A", "10-Q", "10-Q/A"],
        "availability_buffer_minutes": 10,
        "exchange_calendar": "XNYS",
        "historical_signal_cutoff": "16:00 America/New_York",
        "maximum_requests_per_second": 5.0,
        "start_date": "2019-01-01",
        "allowed_security_types": ["COMMON_STOCK", "REIT"],
        "domicile": "US",
        "taxonomy_policy": "STANDARD_ENTITY_WIDE_ONLY",
        "historical_availability_mode": "ACCEPTANCE_PLUS_TEN_MINUTES",
        "forward_availability_mode": "MAX_HISTORICAL_AND_FIRST_SEEN",
        "raw_store_mode": "CONTENT_ADDRESSED_APPEND_ONLY",
        "historical_pit_universe_verified": False,
        "selection_evidence": "DEVELOPMENT_EXPOSED",
    }


def _write_policy(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_policy_freezes_approved_timing_and_scope(tmp_path: Path) -> None:
    policy = load_policy(_write_policy(tmp_path, _approved_payload()))
    assert policy.availability_buffer_minutes == 10
    assert policy.supported_forms == ("10-K", "10-K/A", "10-Q", "10-Q/A")
    assert policy.allowed_security_types == ("COMMON_STOCK", "REIT")
    assert policy.historical_pit_universe_verified is False


def test_repository_policy_is_valid() -> None:
    policy = load_policy("data_pipelines/sec_edgar/config/sec_pipeline.json")
    assert policy.provider == "SEC_EDGAR_COMPANYFACTS"
    assert policy.start_date == "2019-01-01"


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("availability_buffer_minutes", 0, "SEC_POLICY_AVAILABILITY_BUFFER_MUST_BE_TEN"),
        ("maximum_requests_per_second", 5.01, "SEC_POLICY_REQUEST_RATE_EXCEEDS_FIVE"),
        ("exchange_calendar", "XNAS", "SEC_POLICY_CALENDAR_MUST_BE_XNYS"),
        ("start_date", "2019-01-02", "SEC_POLICY_START_DATE_AFTER_2019_01_01"),
        ("provider", "OTHER", "SEC_POLICY_PROVIDER_INVALID"),
    ],
)
def test_policy_rejects_changed_constants(
    tmp_path: Path, key: str, value: object, message: str
) -> None:
    payload = _approved_payload()
    payload[key] = value
    with pytest.raises(ValueError, match=message):
        load_policy(_write_policy(tmp_path, payload))


def test_policy_accepts_an_earlier_start_and_slower_rate(tmp_path: Path) -> None:
    payload = _approved_payload()
    payload["start_date"] = "2018-01-01"
    payload["maximum_requests_per_second"] = 2.5
    policy = load_policy(_write_policy(tmp_path, payload))
    assert policy.start_date == "2018-01-01"
    assert policy.maximum_requests_per_second == 2.5


@pytest.mark.parametrize(
    "non_finite_rate",
    [float("nan"), float("inf"), float("-inf")],
    ids=["json-NaN", "json-Infinity", "json-negative-Infinity"],
)
def test_policy_rejects_non_finite_json_request_rate(
    tmp_path: Path, non_finite_rate: float
) -> None:
    payload = _approved_payload()
    payload["maximum_requests_per_second"] = non_finite_rate
    path = _write_policy(tmp_path, payload)
    serialized = path.read_text(encoding="utf-8")
    assert any(token in serialized for token in ("NaN", "Infinity", "-Infinity"))
    with pytest.raises(ValueError, match="SEC_POLICY_REQUEST_RATE_MUST_BE_FINITE"):
        load_policy(path)


def test_policy_rejects_unknown_key(tmp_path: Path) -> None:
    payload = _approved_payload()
    payload["unreviewed_option"] = True
    with pytest.raises(ValueError, match="SEC_POLICY_UNKNOWN_KEYS:unreviewed_option"):
        load_policy(_write_policy(tmp_path, payload))


def test_policy_rejects_missing_key(tmp_path: Path) -> None:
    payload = _approved_payload()
    del payload["taxonomy_policy"]
    with pytest.raises(ValueError, match="SEC_POLICY_MISSING_KEYS:taxonomy_policy"):
        load_policy(_write_policy(tmp_path, payload))


def test_policy_rejects_invalid_json_shape(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="SEC_POLICY_ROOT_MUST_BE_OBJECT"):
        load_policy(path)


def test_derived_field_catalog_has_complete_versioned_contract() -> None:
    catalog_path = Path("data_pipelines/sec_edgar/config/derived_fields.json")
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["catalog_id"] == "SEC_DERIVED_FIELDS_V1"
    assert set(payload["definitions"]) == {
        "total_debt",
        "working_capital",
        "free_cash_flow",
        "ebit",
        "ebitda",
        "roa",
        "roe",
        "book_value_per_share",
        "asset_growth",
        "revenue_growth",
        "inventory_turnover",
        "market_cap_sec_proxy",
        "enterprise_value_sec_proxy",
    }
    required_keys = {
        "formula",
        "required_inputs",
        "required_basis",
        "unit_rule",
        "sign_convention",
        "missing_value_rule",
        "semantic_grade",
    }
    for definition in payload["definitions"].values():
        assert set(definition) == required_keys
        assert set(definition["required_inputs"]) == set(definition["required_basis"])
        assert "missing" in definition["missing_value_rule"]

    grades = {
        name: definition["semantic_grade"]
        for name, definition in payload["definitions"].items()
    }
    assert {name for name, grade in grades.items() if grade == "HYPOTHESIS_PROXY"} == {
        "book_value_per_share",
        "market_cap_sec_proxy",
        "enterprise_value_sec_proxy",
    }
