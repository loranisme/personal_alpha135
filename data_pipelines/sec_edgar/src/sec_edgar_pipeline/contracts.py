"""Frozen policy contract for the SEC PIT data product."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date
import json
import math
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class SecPipelinePolicy:
    schema_version: int
    provider: str
    supported_namespaces: tuple[str, ...]
    supported_forms: tuple[str, ...]
    availability_buffer_minutes: int
    exchange_calendar: str
    historical_signal_cutoff: str
    maximum_requests_per_second: float
    start_date: str
    allowed_security_types: tuple[str, ...]
    domicile: str
    taxonomy_policy: str
    historical_availability_mode: str
    forward_availability_mode: str
    raw_store_mode: str
    historical_pit_universe_verified: bool
    selection_evidence: str


_EXPECTED_KEYS = frozenset(field.name for field in fields(SecPipelinePolicy))
_TUPLE_FIELDS = frozenset({"supported_namespaces", "supported_forms", "allowed_security_types"})
_FROZEN_CONSTANTS: dict[str, Any] = {
    "schema_version": 1,
    "provider": "SEC_EDGAR_COMPANYFACTS",
    "supported_namespaces": ("us-gaap", "dei"),
    "supported_forms": ("10-K", "10-K/A", "10-Q", "10-Q/A"),
    "availability_buffer_minutes": 10,
    "exchange_calendar": "XNYS",
    "historical_signal_cutoff": "16:00 America/New_York",
    "allowed_security_types": ("COMMON_STOCK", "REIT"),
    "domicile": "US",
    "taxonomy_policy": "STANDARD_ENTITY_WIDE_ONLY",
    "historical_availability_mode": "ACCEPTANCE_PLUS_TEN_MINUTES",
    "forward_availability_mode": "MAX_HISTORICAL_AND_FIRST_SEEN",
    "raw_store_mode": "CONTENT_ADDRESSED_APPEND_ONLY",
    "historical_pit_universe_verified": False,
    "selection_evidence": "DEVELOPMENT_EXPOSED",
}


def _as_mapping(payload: object) -> Mapping[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("SEC_POLICY_ROOT_MUST_BE_OBJECT")
    return payload


def _validate_keys(payload: Mapping[str, Any]) -> None:
    missing = sorted(_EXPECTED_KEYS - payload.keys())
    if missing:
        raise ValueError(f"SEC_POLICY_MISSING_KEYS:{','.join(missing)}")
    unknown = sorted(payload.keys() - _EXPECTED_KEYS)
    if unknown:
        raise ValueError(f"SEC_POLICY_UNKNOWN_KEYS:{','.join(unknown)}")


def _validate_types(payload: Mapping[str, Any]) -> None:
    if not isinstance(payload["schema_version"], int) or isinstance(payload["schema_version"], bool):
        raise ValueError("SEC_POLICY_SCHEMA_VERSION_INVALID")
    for key in _TUPLE_FIELDS:
        value = payload[key]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"SEC_POLICY_TYPE_INVALID:{key}")
    string_fields = _EXPECTED_KEYS - _TUPLE_FIELDS - {
        "schema_version",
        "availability_buffer_minutes",
        "maximum_requests_per_second",
        "historical_pit_universe_verified",
    }
    for key in string_fields:
        if not isinstance(payload[key], str):
            raise ValueError(f"SEC_POLICY_TYPE_INVALID:{key}")
    if not isinstance(payload["availability_buffer_minutes"], int) or isinstance(
        payload["availability_buffer_minutes"], bool
    ):
        raise ValueError("SEC_POLICY_TYPE_INVALID:availability_buffer_minutes")
    rate = payload["maximum_requests_per_second"]
    if not isinstance(rate, (int, float)) or isinstance(rate, bool):
        raise ValueError("SEC_POLICY_TYPE_INVALID:maximum_requests_per_second")
    if not isinstance(payload["historical_pit_universe_verified"], bool):
        raise ValueError("SEC_POLICY_TYPE_INVALID:historical_pit_universe_verified")


def _validate_frozen_values(payload: Mapping[str, Any]) -> None:
    normalized = dict(payload)
    for key in _TUPLE_FIELDS:
        normalized[key] = tuple(normalized[key])
    if normalized["availability_buffer_minutes"] != 10:
        raise ValueError("SEC_POLICY_AVAILABILITY_BUFFER_MUST_BE_TEN")
    if normalized["exchange_calendar"] != "XNYS":
        raise ValueError("SEC_POLICY_CALENDAR_MUST_BE_XNYS")
    if normalized["provider"] != "SEC_EDGAR_COMPANYFACTS":
        raise ValueError("SEC_POLICY_PROVIDER_INVALID")
    for key, expected in _FROZEN_CONSTANTS.items():
        if key in {"availability_buffer_minutes", "exchange_calendar", "provider"}:
            continue
        if normalized[key] != expected:
            raise ValueError(f"SEC_POLICY_FROZEN_VALUE_INVALID:{key}")


def _validate_ranges(payload: Mapping[str, Any]) -> None:
    rate = float(payload["maximum_requests_per_second"])
    if not math.isfinite(rate):
        raise ValueError("SEC_POLICY_REQUEST_RATE_MUST_BE_FINITE")
    if rate <= 0:
        raise ValueError("SEC_POLICY_REQUEST_RATE_MUST_BE_POSITIVE")
    if rate > 5:
        raise ValueError("SEC_POLICY_REQUEST_RATE_EXCEEDS_FIVE")
    try:
        parsed_start = date.fromisoformat(payload["start_date"])
    except ValueError as exc:
        raise ValueError("SEC_POLICY_START_DATE_INVALID") from exc
    if parsed_start > date(2019, 1, 1):
        raise ValueError("SEC_POLICY_START_DATE_AFTER_2019_01_01")


def load_policy(path: Path | str) -> SecPipelinePolicy:
    """Load and validate the reviewed SEC pipeline policy without defaults."""

    source = Path(path)
    try:
        payload = _as_mapping(json.loads(source.read_text(encoding="utf-8")))
    except json.JSONDecodeError as exc:
        raise ValueError("SEC_POLICY_INVALID_JSON") from exc
    _validate_keys(payload)
    _validate_types(payload)
    _validate_frozen_values(payload)
    _validate_ranges(payload)
    normalized = dict(payload)
    for key in _TUPLE_FIELDS:
        normalized[key] = tuple(normalized[key])
    normalized["maximum_requests_per_second"] = float(normalized["maximum_requests_per_second"])
    return SecPipelinePolicy(**normalized)
