"""Deterministic, auditable derived SEC fundamental fields.

The module deliberately refuses to guess when accounting inputs do not share a
period, reporting currency, or XBRL context. The diagnostic API is the source
of truth; the convenience API returns only successfully materialised values.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class DerivedMaterializationResult:
    """Successful derived values together with rejected/unavailable attempts."""

    values: pd.DataFrame
    diagnostics: pd.DataFrame


_VALUE_EXTRA_COLUMNS = [
    "input_source_fact_ids", "input_transformations", "definition_hash",
    "semantic_grade", "derivation_rule", "input_shares", "input_raw_close",
    "price_snapshot_hash", "price_event_at", "price_provider", "price_field",
    "price_adjustment", "price_timestamp_semantics", "accounting_period_end",
]
_DIAGNOSTIC_COLUMNS = [
    "issuer_cik", "security_id", "canonical_field", "historical_available_at",
    "diagnostic_status", "reason_code", "reason_detail", "definition_hash",
]
_PRIOR_FIELDS: dict[str, tuple[str, ...]] = {
    "asset_growth": ("total_assets",),
    "revenue_growth": ("revenue",),
    "roa": ("total_assets",),
    "roe": ("stockholders_equity",),
    "inventory_turnover": ("inventory",),
}
_RATIO_FIELDS = {"asset_growth", "revenue_growth", "roa", "roe", "inventory_turnover"}
_DAILY_FIELDS = {"market_cap_sec_proxy", "enterprise_value_sec_proxy"}
_NON_CURRENCY_UNITS = {"", "SHARE", "SHARES", "RATIO", "PURE", "NUMBER"}
_APPROVED_INPUT_GRADES = {
    "SOURCE_LINKED_DIRECT",
    "SOURCE_LINKED_DERIVED",
    "HYPOTHESIS_PROXY",
}
_REQUIRED_SIGN_CONVENTIONS = {
    "capital_expenditure": {"POSITIVE_OUTFLOW"},
}
_SHA256_PATTERN = re.compile(r"(?:sha256:)?[0-9a-f]{64}")


def _definition_items(
    definitions: Mapping[str, Any] | Sequence[Mapping[str, Any]],
) -> list[tuple[str, Mapping[str, Any]]]:
    if isinstance(definitions, Mapping):
        items = [(str(name), value) for name, value in definitions.items()]
    else:
        items = [(str(row["canonical_field"]), row) for row in definitions]
    by_name = dict(items)
    ordered: list[tuple[str, Mapping[str, Any]]] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visited:
            return
        if name in visiting:
            raise ValueError(f"DERIVED_DEFINITION_CYCLE:{name}")
        visiting.add(name)
        definition = by_name[name]
        for dependency in definition.get("required_inputs", []):
            if dependency in by_name:
                visit(str(dependency))
        visiting.remove(name)
        visited.add(name)
        ordered.append((name, definition))

    for name, _ in items:
        visit(name)
    return ordered


def _definition_hash(name: str, definition: Mapping[str, Any]) -> str:
    payload = json.dumps(
        {"canonical_field": name, **definition}, sort_keys=True,
        separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _timestamp(value: Any) -> pd.Timestamp:
    result = pd.Timestamp(value)
    if result.tzinfo is None:
        raise ValueError("DERIVED_EVENT_TIMEZONE_REQUIRED")
    return result.tz_convert("UTC")


def _normalise_optional(value: Any) -> str:
    return "" if value is None or pd.isna(value) else str(value)


def _latest(state: pd.DataFrame, field: str, basis: str) -> pd.Series | None:
    rows = state[state.canonical_field.eq(field) & state.reported_basis.eq(basis)]
    if rows.empty:
        return None
    ordered = rows.assign(
        _period_end=pd.to_datetime(rows.period_end, errors="coerce"),
        _source=rows.source_fact_id.astype(str),
    ).sort_values(
        ["_period_end", "historical_available_at", "_source"], kind="stable"
    )
    return ordered.iloc[-1].drop(labels=["_period_end", "_source"])


def _same_fiscal_anchor(current: pd.Series, prior: pd.Series) -> bool:
    current_period = _normalise_optional(current.get("fiscal_period")).upper()
    prior_period = _normalise_optional(prior.get("fiscal_period")).upper()
    if current_period and prior_period and current_period != prior_period:
        return False
    current_end = pd.to_datetime(current.get("period_end"), errors="coerce")
    prior_end = pd.to_datetime(prior.get("period_end"), errors="coerce")
    if pd.isna(current_end) or pd.isna(prior_end):
        return False
    return (current_end.month, current_end.day) == (prior_end.month, prior_end.day)


def _prior_year(
    state: pd.DataFrame, field: str, basis: str, current: pd.Series,
) -> tuple[pd.Series | None, str | None, str | None]:
    fiscal_year = pd.to_numeric(
        pd.Series([current.get("fiscal_year")]), errors="coerce"
    ).iloc[0]
    if pd.isna(fiscal_year):
        return None, "PRIOR_FISCAL_YEAR_MISSING", f"{field}: current fiscal year is missing"
    target_year = int(fiscal_year) - 1
    rows = state[state.canonical_field.eq(field) & state.reported_basis.eq(basis)].copy()
    row_years = pd.to_numeric(rows.fiscal_year, errors="coerce")
    target = rows.loc[row_years.eq(target_year)]
    if target.empty:
        return (
            None, "PRIOR_FISCAL_YEAR_MISSING",
            f"{field}: exact prior fiscal year {target_year} is unavailable",
        )
    anchored = target[target.apply(lambda row: _same_fiscal_anchor(current, row), axis=1)]
    if anchored.empty:
        observed = sorted({
            f"{_normalise_optional(row.get('fiscal_period'))}:{_normalise_optional(row.get('period_end'))}"
            for _, row in target.iterrows()
        })
        return (
            None, "PRIOR_FISCAL_ANCHOR_MISMATCH",
            f"{field}: fiscal year {target_year} has anchors {observed}, not the current anchor",
        )
    return (
        anchored.sort_values(
            ["historical_available_at", "period_end", "source_fact_id"], kind="stable"
        ).iloc[-1], None, None,
    )


def _prepare_prices(
    price_panel: pd.DataFrame | None,
) -> tuple[
    pd.DataFrame | None,
    str | None,
    Mapping[str, str],
    Mapping[str, str],
]:
    if price_panel is None:
        return None, None, {}, {}
    snapshot_hash = price_panel.attrs.get("snapshot_hash")
    if not isinstance(snapshot_hash, str) or not _SHA256_PATTERN.fullmatch(snapshot_hash):
        raise ValueError("PRICE_SNAPSHOT_HASH_REQUIRED")
    currencies = price_panel.attrs.get("currency_by_security")
    if not isinstance(currencies, Mapping):
        raise ValueError("PRICE_CURRENCY_BY_SECURITY_REQUIRED")
    metadata = {
        "provider": str(price_panel.attrs.get("provider", "")).strip(),
        "field": str(price_panel.attrs.get("field", "")).strip().lower(),
        "adjustment": str(price_panel.attrs.get("adjustment", "")).strip().lower(),
        "timestamp_semantics": str(
            price_panel.attrs.get("timestamp_semantics", "")
        ).strip().upper(),
    }
    if not metadata["provider"]:
        raise ValueError("PRICE_PROVIDER_REQUIRED")
    if metadata["field"] != "raw_close":
        raise ValueError("PRICE_FIELD_MUST_BE_RAW_CLOSE")
    if metadata["adjustment"] != "unadjusted":
        raise ValueError("PRICE_ADJUSTMENT_MUST_BE_UNADJUSTED")
    if metadata["timestamp_semantics"] != "OBSERVED_AT_UTC":
        raise ValueError("PRICE_TIMESTAMP_SEMANTICS_MUST_BE_OBSERVED_AT_UTC")
    panel = price_panel.copy()
    panel.index = pd.to_datetime(panel.index, utc=True, errors="raise")
    if panel.index.has_duplicates:
        raise ValueError("PRICE_SESSION_DUPLICATE")
    return (
        panel.sort_index(), snapshot_hash,
        {str(key): str(value) for key, value in currencies.items()},
        metadata,
    )


def _validate_input_semantics(
    field: str, row: pd.Series, output_grade: str,
) -> tuple[str | None, str | None]:
    semantic_grade = _normalise_optional(row.get("semantic_grade")).upper()
    if semantic_grade not in _APPROVED_INPUT_GRADES:
        return (
            "INPUT_SEMANTIC_GRADE_UNAPPROVED",
            f"{field}: semantic_grade={semantic_grade or 'MISSING'}",
        )
    if (
        output_grade == "SOURCE_LINKED_DERIVED"
        and semantic_grade == "HYPOTHESIS_PROXY"
    ):
        return (
            "INPUT_SEMANTIC_GRADE_INCOMPATIBLE",
            f"{field}: HYPOTHESIS_PROXY cannot produce SOURCE_LINKED_DERIVED",
        )
    required_signs = _REQUIRED_SIGN_CONVENTIONS.get(field)
    if required_signs is not None:
        sign = _normalise_optional(row.get("sign_convention")).upper()
        if sign not in required_signs:
            return (
                "INPUT_SIGN_CONVENTION_UNAPPROVED",
                f"{field}: sign_convention={sign or 'MISSING'}; required={sorted(required_signs)}",
            )
    return None, None


def _price_at(
    panel: pd.DataFrame | None, security_id: str, event: pd.Timestamp,
) -> float | None:
    if panel is None or security_id not in panel.columns or event not in panel.index:
        return None
    value = panel.at[event, security_id]
    return None if pd.isna(value) else float(value)


def _currency_units(rows: Mapping[str, pd.Series]) -> dict[str, str]:
    result: dict[str, str] = {}
    for field, row in rows.items():
        unit = _normalise_optional(row.get("unit"))
        if unit.upper() not in _NON_CURRENCY_UNITS:
            result[field] = unit
    return result


def _validate_alignment(
    name: str, selected: Mapping[str, pd.Series], price_currency: str | None,
) -> tuple[str | None, str | None]:
    current = {field: row for field, row in selected.items() if not field.startswith("prior:")}
    accounting = current
    period_ends = {
        _normalise_optional(
            row.get("accounting_period_end")
            if name == "enterprise_value_sec_proxy"
            and field == "market_cap_sec_proxy"
            else row.get("period_end")
        )
        for field, row in accounting.items()
        if _normalise_optional(
            row.get("accounting_period_end")
            if name == "enterprise_value_sec_proxy"
            and field == "market_cap_sec_proxy"
            else row.get("period_end")
        )
    }
    if len(period_ends) > 1:
        return "INPUT_PERIOD_MISMATCH", f"period_end values do not align: {sorted(period_ends)}"

    contexts = {
        _normalise_optional(row.get("dimensions_signature")) for row in selected.values()
    }
    if len(contexts) > 1:
        return "INPUT_CONTEXT_MISMATCH", f"dimensions_signature values do not align: {sorted(contexts)}"

    currencies = _currency_units(selected)
    unique_currencies = set(currencies.values())
    if price_currency is not None:
        unique_currencies.add(price_currency)
        currencies["raw_close"] = price_currency
    if len(unique_currencies) > 1:
        return "INPUT_CURRENCY_MISMATCH", f"reporting currencies do not align: {currencies}"
    return None, None


def _normalise_input(field: str, row: pd.Series) -> tuple[float, str | None]:
    value = float(row.value)
    convention = _normalise_optional(row.get("sign_convention")).upper()
    positive_conventions = {
        "POSITIVE_OUTFLOW", "POSITIVE_EXPENSE", "POSITIVE_ADD_BACK",
        "POSITIVE_ADDBACK", "POSITIVE_OBLIGATION",
    }
    if convention in positive_conventions and value < 0:
        transform = "ABS_POSITIVE_OUTFLOW" if convention == "POSITIVE_OUTFLOW" else f"ABS_{convention}"
        return abs(value), transform
    return value, None


def _calculate(
    name: str, inputs: dict[str, float], prior: dict[str, float],
    raw_close: float | None,
) -> tuple[float | None, str | None, str | None]:
    if name == "total_debt":
        negative = sorted(
            field
            for field in (
                "short_term_debt",
                "long_term_debt_current",
                "long_term_debt_noncurrent",
            )
            if inputs[field] < 0
        )
        if negative:
            return (
                None,
                "NEGATIVE_DEBT_INPUT",
                f"debt balances must be non-negative: {negative}",
            )
        return inputs["short_term_debt"] + inputs["long_term_debt_current"] + inputs["long_term_debt_noncurrent"], None, None
    if name == "working_capital":
        return inputs["current_assets"] - inputs["current_liabilities"], None, None
    if name == "free_cash_flow":
        return inputs["operating_cash_flow"] - inputs["capital_expenditure"], None, None
    if name == "ebit":
        return inputs["net_income"] + inputs["income_tax_expense"] + inputs["interest_expense"], None, None
    if name == "ebitda":
        return inputs["ebit"] + inputs["depreciation_and_amortization"], None, None
    if name == "book_value_per_share":
        denominator = inputs["shares_outstanding_sec"]
        if denominator <= 0:
            return None, "NON_POSITIVE_DENOMINATOR", "shares_outstanding_sec must be positive"
        return inputs["stockholders_equity"] / denominator, None, None
    if name == "asset_growth":
        denominator = prior["total_assets"]
        if denominator <= 0:
            return None, "NON_POSITIVE_DENOMINATOR", "prior total_assets must be positive"
        return inputs["total_assets"] / denominator - 1, None, None
    if name == "revenue_growth":
        denominator = prior["revenue"]
        if denominator <= 0:
            return None, "NON_POSITIVE_DENOMINATOR", "prior revenue must be positive"
        return inputs["revenue"] / denominator - 1, None, None
    if name == "roa":
        denominator = (inputs["total_assets"] + prior["total_assets"]) / 2
        if denominator <= 0:
            return None, "NON_POSITIVE_DENOMINATOR", "average total_assets must be positive"
        return inputs["net_income"] / denominator, None, None
    if name == "roe":
        denominator = (inputs["stockholders_equity"] + prior["stockholders_equity"]) / 2
        if denominator <= 0:
            return None, "NON_POSITIVE_DENOMINATOR", "average stockholders_equity must be positive"
        return inputs["net_income"] / denominator, None, None
    if name == "inventory_turnover":
        denominator = (inputs["inventory"] + prior["inventory"]) / 2
        if denominator <= 0:
            return None, "NON_POSITIVE_DENOMINATOR", "average inventory must be positive"
        return (inputs["revenue"] - inputs["gross_profit"]) / denominator, None, None
    if name == "market_cap_sec_proxy":
        if raw_close is None or raw_close <= 0:
            return None, "NON_POSITIVE_PRICE", "raw_close must be positive"
        if inputs["shares_outstanding_sec"] <= 0:
            return None, "NON_POSITIVE_INPUT", "shares_outstanding_sec must be positive"
        return inputs["shares_outstanding_sec"] * raw_close, None, None
    if name == "enterprise_value_sec_proxy":
        return inputs["market_cap_sec_proxy"] + inputs["total_debt"] - inputs["cash_and_cash_equivalents"], None, None
    raise ValueError(f"DERIVED_FORMULA_NOT_IMPLEMENTED:{name}")


def _output_unit(
    name: str, selected: Mapping[str, pd.Series], price_currency: str | None,
) -> str:
    if name in _RATIO_FIELDS:
        return "ratio"
    if name == "market_cap_sec_proxy":
        return str(price_currency)
    currencies = list(_currency_units(selected).values())
    currency = currencies[0] if currencies else ""
    if name == "book_value_per_share":
        return f"{currency}/share"
    return currency


def _diagnostic(
    *, issuer_cik: Any, security_id: Any, name: str, event: pd.Timestamp,
    definition_hash: str, reason_code: str, reason_detail: str,
    invalid: bool = False,
) -> dict[str, Any]:
    return {
        "issuer_cik": issuer_cik, "security_id": security_id,
        "canonical_field": name, "historical_available_at": event,
        "diagnostic_status": "INVALID" if invalid else "UNAVAILABLE",
        "reason_code": reason_code, "reason_detail": reason_detail,
        "definition_hash": definition_hash,
    }


def materialize_derived_fields_with_diagnostics(
    period_versions: pd.DataFrame,
    price_panel: pd.DataFrame | None,
    definitions: Mapping[str, Any] | Sequence[Mapping[str, Any]],
) -> DerivedMaterializationResult:
    """Materialise derived fields and record every semantic rejection reason."""

    definition_items = _definition_items(definitions)
    prices, price_snapshot_hash, currency_by_security, price_metadata = _prepare_prices(
        price_panel
    )
    if period_versions.empty:
        return DerivedMaterializationResult(
            pd.DataFrame(columns=_VALUE_EXTRA_COLUMNS),
            pd.DataFrame(columns=_DIAGNOSTIC_COLUMNS),
        )
    required = {
        "issuer_cik", "security_id", "canonical_field", "reported_basis",
        "value", "period_end", "fiscal_year", "historical_available_at",
        "available_at", "source_fact_id", "unit", "semantic_grade",
    }
    missing_columns = sorted(required.difference(period_versions.columns))
    if missing_columns:
        raise ValueError(f"DERIVED_INPUT_COLUMNS_MISSING:{','.join(missing_columns)}")

    frame = period_versions.copy()
    for column in ("historical_available_at", "available_at"):
        frame[column] = pd.to_datetime(frame[column], utc=True, errors="raise")
    for optional in ("fiscal_period", "dimensions_signature", "sign_convention", "period_start"):
        if optional not in frame:
            frame[optional] = None

    issuer_counts = frame.groupby("security_id", dropna=False).issuer_cik.nunique(
        dropna=False
    )
    ambiguous = sorted(str(value) for value in issuer_counts[issuer_counts.ne(1)].index)
    if ambiguous:
        raise ValueError("DERIVED_SECURITY_ISSUER_AMBIGUOUS:" + ",".join(ambiguous))

    output: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    value_columns = list(dict.fromkeys([*frame.columns, *_VALUE_EXTRA_COLUMNS]))
    for security_id, security_rows in frame.groupby("security_id", sort=False):
        security_id = str(security_id)
        issuer_cik = security_rows.iloc[0].issuer_cik
        accounting_events = {_timestamp(value) for value in security_rows.historical_available_at.unique()}
        price_events: set[pd.Timestamp] = set()
        if prices is not None and security_id in prices.columns:
            price_events = {
                timestamp for timestamp in prices.index
                if not pd.isna(prices.at[timestamp, security_id])
            }
        events = sorted(accounting_events | price_events)
        generated_state = pd.DataFrame(columns=value_columns)
        seen_values: set[tuple[str, str]] = set()
        seen_diagnostics: set[tuple[str, pd.Timestamp, str, str]] = set()

        for event in events:
            is_accounting_event = event in accounting_events
            is_price_event = event in price_events
            base_state = security_rows.loc[security_rows.historical_available_at.le(event)]
            state = pd.concat(
                [base_state, generated_state.loc[generated_state.historical_available_at.le(event)]],
                ignore_index=True, sort=False,
            )
            for name, definition in definition_items:
                if name in _DAILY_FIELDS and not is_price_event:
                    continue
                if name not in _DAILY_FIELDS and not is_accounting_event:
                    continue
                definition_hash = _definition_hash(name, definition)
                required_basis = definition["required_basis"]
                selected: dict[str, pd.Series] = {}
                input_values: dict[str, float] = {}
                prior_values: dict[str, float] = {}
                transformations: dict[str, str] = {}
                raw_close = _price_at(prices, security_id, event)
                price_currency = currency_by_security.get(security_id) if is_price_event else None

                if "raw_close" in definition["required_inputs"] and price_currency is None:
                    reason, detail = (
                        "PRICE_CURRENCY_MISSING",
                        f"raw-close currency is unavailable for {security_id}",
                    )
                    key = (name, event, reason, detail)
                    if key not in seen_diagnostics:
                        diagnostics.append(_diagnostic(
                            issuer_cik=issuer_cik, security_id=security_id, name=name,
                            event=event, definition_hash=definition_hash,
                            reason_code=reason, reason_detail=detail,
                        ))
                        seen_diagnostics.add(key)
                    continue

                input_failure: tuple[str, str, bool] | None = None
                for field in definition["required_inputs"]:
                    if field == "raw_close":
                        if raw_close is None:
                            input_failure = (
                                "MISSING_INPUT",
                                f"required input is unavailable: {field}",
                                False,
                            )
                        continue
                    row = _latest(state, field, required_basis[field])
                    if row is None or pd.isna(row.value):
                        input_failure = (
                            "MISSING_INPUT",
                            f"required input is unavailable: {field}",
                            False,
                        )
                        break
                    semantic_reason, semantic_detail = _validate_input_semantics(
                        field, row, str(definition["semantic_grade"])
                    )
                    if semantic_reason is not None:
                        input_failure = (
                            semantic_reason,
                            str(semantic_detail),
                            True,
                        )
                        break
                    selected[field] = row
                    value, transformation = _normalise_input(field, row)
                    input_values[field] = value
                    if transformation is not None:
                        transformations[field] = transformation
                if input_failure is not None:
                    reason, detail, invalid = input_failure
                    key = (name, event, reason, detail)
                    if key not in seen_diagnostics:
                        diagnostics.append(_diagnostic(
                            issuer_cik=issuer_cik, security_id=security_id, name=name,
                            event=event, definition_hash=definition_hash,
                            reason_code=reason, reason_detail=detail, invalid=invalid,
                        ))
                        seen_diagnostics.add(key)
                    continue

                alignment_reason, alignment_detail = _validate_alignment(
                    name, selected, price_currency if name in _DAILY_FIELDS else None
                )
                if alignment_reason is not None:
                    key = (name, event, alignment_reason, str(alignment_detail))
                    if key not in seen_diagnostics:
                        diagnostics.append(_diagnostic(
                            issuer_cik=issuer_cik, security_id=security_id, name=name,
                            event=event, definition_hash=definition_hash,
                            reason_code=alignment_reason,
                            reason_detail=str(alignment_detail), invalid=True,
                        ))
                        seen_diagnostics.add(key)
                    continue

                prior_failure: tuple[str, str, bool] | None = None
                for field in _PRIOR_FIELDS.get(name, ()):
                    prior_row, reason, detail = _prior_year(
                        state, field, required_basis[field], selected[field]
                    )
                    if prior_row is None:
                        prior_failure = (str(reason), str(detail), False)
                        break
                    semantic_reason, semantic_detail = _validate_input_semantics(
                        field, prior_row, str(definition["semantic_grade"])
                    )
                    if semantic_reason is not None:
                        prior_failure = (
                            semantic_reason,
                            str(semantic_detail),
                            True,
                        )
                        break
                    selected[f"prior:{field}"] = prior_row
                    value, transformation = _normalise_input(field, prior_row)
                    prior_values[field] = value
                    if transformation is not None:
                        transformations[f"prior:{field}"] = transformation
                if prior_failure is not None:
                    reason, detail, invalid = prior_failure
                    key = (name, event, reason, detail)
                    if key not in seen_diagnostics:
                        diagnostics.append(_diagnostic(
                            issuer_cik=issuer_cik, security_id=security_id, name=name,
                            event=event, definition_hash=definition_hash,
                            reason_code=reason, reason_detail=detail, invalid=invalid,
                        ))
                        seen_diagnostics.add(key)
                    continue

                prior_alignment_reason, prior_alignment_detail = _validate_alignment(
                    name, selected, price_currency if name in _DAILY_FIELDS else None
                )
                if prior_alignment_reason is not None:
                    key = (name, event, prior_alignment_reason, str(prior_alignment_detail))
                    if key not in seen_diagnostics:
                        diagnostics.append(_diagnostic(
                            issuer_cik=issuer_cik, security_id=security_id, name=name,
                            event=event, definition_hash=definition_hash,
                            reason_code=prior_alignment_reason,
                            reason_detail=str(prior_alignment_detail), invalid=True,
                        ))
                        seen_diagnostics.add(key)
                    continue

                value, invalid_reason, invalid_detail = _calculate(
                    name, input_values, prior_values, raw_close
                )
                if invalid_reason is not None or value is None or not np.isfinite(value):
                    reason = invalid_reason or "NON_FINITE_RESULT"
                    detail = invalid_detail or "derived result is not finite"
                    key = (name, event, reason, detail)
                    if key not in seen_diagnostics:
                        diagnostics.append(_diagnostic(
                            issuer_cik=issuer_cik, security_id=security_id, name=name,
                            event=event, definition_hash=definition_hash,
                            reason_code=reason, reason_detail=detail, invalid=True,
                        ))
                        seen_diagnostics.add(key)
                    continue

                source_ids = sorted(str(row.source_fact_id) for row in selected.values())
                identity_payload = {
                    "definition_hash": definition_hash,
                    "input_source_fact_ids": source_ids,
                    "price_event": event.isoformat() if name in _DAILY_FIELDS else None,
                    "price_snapshot_hash": price_snapshot_hash if name in _DAILY_FIELDS else None,
                    "raw_close": raw_close if name == "market_cap_sec_proxy" else None,
                    "price_provider": price_metadata.get("provider") if name in _DAILY_FIELDS else None,
                    "price_field": price_metadata.get("field") if name in _DAILY_FIELDS else None,
                    "price_adjustment": price_metadata.get("adjustment") if name in _DAILY_FIELDS else None,
                    "price_timestamp_semantics": price_metadata.get("timestamp_semantics") if name in _DAILY_FIELDS else None,
                }
                identity_json = json.dumps(identity_payload, sort_keys=True, separators=(",", ":"))
                identity = (name, identity_json)
                if identity in seen_values:
                    continue

                anchor = next(iter(selected.values()))
                row = anchor.to_dict()
                accounting_period_end = _normalise_optional(
                    anchor.get("accounting_period_end")
                    if name == "enterprise_value_sec_proxy"
                    else anchor.get("period_end")
                )
                if name in _DAILY_FIELDS:
                    row.update({
                        "period_start": event.date().isoformat(),
                        "period_end": event.date().isoformat(),
                        "fiscal_year": event.year,
                        "fiscal_period": "DAILY",
                    })
                all_availability = [pd.Timestamp(item.available_at) for item in selected.values()]
                row.update({
                    "canonical_field": name,
                    "reported_basis": "DAILY_PROXY" if name in _DAILY_FIELDS else required_basis[definition["required_inputs"][0]],
                    "value": float(value),
                    "unit": _output_unit(name, selected, price_currency),
                    "historical_available_at": event,
                    "available_at": max([event, *all_availability]),
                    "source_fact_id": "derived-field:" + hashlib.sha256(identity_json.encode("utf-8")).hexdigest(),
                    "input_source_fact_ids": json.dumps(source_ids, sort_keys=True),
                    "input_transformations": json.dumps(transformations, sort_keys=True),
                    "definition_hash": definition_hash,
                    "semantic_grade": definition["semantic_grade"],
                    "derivation_rule": definition["formula"],
                    "input_shares": input_values.get("shares_outstanding_sec"),
                    "input_raw_close": raw_close if name == "market_cap_sec_proxy" else None,
                    "price_snapshot_hash": price_snapshot_hash if name in _DAILY_FIELDS else None,
                    "price_event_at": event if name in _DAILY_FIELDS else None,
                    "price_provider": price_metadata.get("provider") if name in _DAILY_FIELDS else None,
                    "price_field": price_metadata.get("field") if name in _DAILY_FIELDS else None,
                    "price_adjustment": price_metadata.get("adjustment") if name in _DAILY_FIELDS else None,
                    "price_timestamp_semantics": price_metadata.get("timestamp_semantics") if name in _DAILY_FIELDS else None,
                    "accounting_period_end": accounting_period_end,
                })
                output.append(row)
                generated_state = pd.concat(
                    [generated_state, pd.DataFrame([row])], ignore_index=True, sort=False
                )
                state = pd.concat([state, pd.DataFrame([row])], ignore_index=True, sort=False)
                seen_values.add(identity)

    values = pd.DataFrame(output)
    if values.empty:
        values = pd.DataFrame(columns=value_columns)
    else:
        values = values.sort_values(
            ["security_id", "historical_available_at", "canonical_field", "source_fact_id"],
            kind="stable",
        ).reset_index(drop=True)
    diagnostic_frame = pd.DataFrame(diagnostics, columns=_DIAGNOSTIC_COLUMNS)
    if not diagnostic_frame.empty:
        diagnostic_frame = diagnostic_frame.sort_values(
            ["security_id", "historical_available_at", "canonical_field", "reason_code"],
            kind="stable",
        ).reset_index(drop=True)
    return DerivedMaterializationResult(values, diagnostic_frame)


def materialize_derived_fields(
    period_versions: pd.DataFrame,
    price_panel: pd.DataFrame | None,
    definitions: Mapping[str, Any] | Sequence[Mapping[str, Any]],
) -> pd.DataFrame:
    """Return only successfully materialised values."""

    return materialize_derived_fields_with_diagnostics(
        period_versions, price_panel, definitions
    ).values
