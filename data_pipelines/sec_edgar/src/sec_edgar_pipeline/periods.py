"""Deterministic point-in-time accounting period transformations."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pandas as pd


def _utc_series(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, utc=True, errors="raise")


def _label(row: Mapping[str, Any]) -> str | None:
    fp = str(row.get("fiscal_period") or "").upper()
    duration = row.get("duration_days")
    if pd.isna(duration):
        return None
    duration = int(duration)
    if fp == "Q1" and 45 <= duration <= 120:
        return "Q1"
    if fp == "Q2":
        return "Q2_DIRECT" if duration <= 120 else "H1" if duration <= 220 else None
    if fp == "Q3":
        return "Q3_DIRECT" if duration <= 120 else "M9" if duration <= 300 else None
    if fp == "Q4" and duration <= 120:
        return "Q4_DIRECT"
    if fp == "FY" and 300 <= duration <= 400:
        return "FY"
    return None


def _compatible(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return all(
        str(left.get(key)) == str(right.get(key))
        for key in ("issuer_cik", "security_id", "canonical_field", "unit", "xbrl_namespace", "xbrl_tag", "period_start")
    )


def _period_window(start: Any, end: Any) -> tuple[str, str, int]:
    start_time = pd.Timestamp(start).normalize()
    end_time = pd.Timestamp(end).normalize()
    if start_time > end_time:
        raise ValueError("PERIOD_WINDOW_START_AFTER_END")
    return (
        start_time.date().isoformat(),
        end_time.date().isoformat(),
        int((end_time - start_time).days) + 1,
    )


def _precedence_frame(frame: pd.DataFrame) -> pd.DataFrame:
    ordered = frame.copy()
    if "accession_number" in ordered:
        ordered["_accession_precedence"] = ordered.accession_number.fillna("").astype(str)
    else:
        ordered["_accession_precedence"] = ""
    if "mapping_priority" in ordered:
        ordered["_mapping_priority_precedence"] = pd.to_numeric(
            ordered.mapping_priority, errors="coerce"
        ).fillna(2**31 - 1)
    else:
        ordered["_mapping_priority_precedence"] = 2**31 - 1
    return ordered


def _sort_by_precedence(frame: pd.DataFrame, availability_column: str) -> pd.DataFrame:
    return _precedence_frame(frame).sort_values(
        [
            availability_column,
            "_accession_precedence",
            "_mapping_priority_precedence",
            "source_fact_id",
        ],
        ascending=[True, True, False, True],
        kind="stable",
    ).drop(
        columns=["_accession_precedence", "_mapping_priority_precedence"]
    )


def _derived_quarter(
    end_row: Mapping[str, Any],
    prior_row: Mapping[str, Any] | None,
    fiscal_period: str,
) -> dict[str, Any] | None:
    if prior_row is not None and not _compatible(end_row, prior_row):
        return None
    value = float(end_row["value"])
    input_rows = [end_row]
    if prior_row is not None:
        value -= float(prior_row["value"])
        input_rows.append(prior_row)
    result = dict(end_row)
    period_start = end_row.get("period_start")
    if prior_row is not None:
        period_start = pd.Timestamp(str(prior_row["period_end"])) + pd.Timedelta(days=1)
    period_start, period_end, duration_days = _period_window(
        period_start, end_row["period_end"]
    )
    availability = max(pd.Timestamp(row["historical_available_at"]) for row in input_rows)
    forward = max(pd.Timestamp(row["available_at"]) for row in input_rows)
    source_ids = sorted(str(row["source_fact_id"]) for row in input_rows)
    result.update({
        "value": value,
        "fiscal_period": fiscal_period,
        "reported_basis": "QUARTERLY_DISCRETE",
        "period_start": period_start,
        "period_end": period_end,
        "duration_days": duration_days,
        "historical_available_at": availability,
        "available_at": forward,
        "source_fact_id": "derived-quarter:" + "+".join(source_ids),
        "input_source_fact_ids": json.dumps(source_ids),
        "derivation_rule": "DIRECT" if prior_row is None else "CUMULATIVE_DIFFERENCE",
    })
    if prior_row is not None or fiscal_period == "Q1":
        # The SEC fy value belongs to the filing. Comparative facts in a
        # later filing can carry the later fy even though their period starts
        # in the prior accounting year.
        fiscal_start = pd.Timestamp(str(end_row["period_start"]))
        result["fiscal_year"] = (
            fiscal_start + pd.DateOffset(years=1) - pd.Timedelta(days=1)
        ).year
    return result


def _materialize_discrete(group: pd.DataFrame) -> list[dict[str, Any]]:
    ordered = _sort_by_precedence(group, "historical_available_at")
    state_by_start: dict[str, dict[str, dict[str, Any]]] = {}
    seen: set[tuple[str, str]] = set()
    output: list[dict[str, Any]] = []
    for source in ordered.to_dict("records"):
        label = _label(source)
        if label is None:
            continue
        if label.endswith("_DIRECT"):
            candidates = [(label[:2], source, None)]
        else:
            # A cumulative period's start identifies the actual accounting
            # year. Its fy/fp metadata may refer to a later comparative filing.
            state = state_by_start.setdefault(str(source["period_start"]), {})
            state[label] = source
            candidates = [
                ("Q1", state.get("Q1"), None),
                ("Q2", state.get("H1"), state.get("Q1")),
                ("Q3", state.get("M9"), state.get("H1")),
                ("Q4", state.get("FY"), state.get("M9")),
            ]
        for fiscal_period, end_row, prior_row in candidates:
            if end_row is None or (prior_row is None and fiscal_period != "Q1" and not label.endswith("_DIRECT")):
                continue
            derived = _derived_quarter(end_row, prior_row, fiscal_period)
            if derived is None:
                continue
            identity = (fiscal_period, derived["source_fact_id"])
            if identity not in seen:
                output.append(derived)
                seen.add(identity)
    return output


def _materialize_ttm(discrete: pd.DataFrame) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    if discrete.empty:
        return output
    group_keys = ["issuer_cik", "security_id", "canonical_field", "unit", "xbrl_namespace", "xbrl_tag"]
    for _, group in discrete.groupby(group_keys, dropna=False, sort=False):
        # SEC CompanyFacts `fy`/`fp` describe the filing context and can be
        # reused for prior-year comparative facts. The actual accounting
        # quarter is identified by its period end, not the filing's labels.
        state: dict[str, dict[str, Any]] = {}
        seen: set[str] = set()
        ordered = _sort_by_precedence(group, "historical_available_at")
        for source in ordered.to_dict("records"):
            state[str(source["period_end"])] = source
            latest = sorted(state.values(), key=lambda row: pd.Timestamp(row["period_end"]))[-4:]
            if len(latest) != 4 or len({str(row["period_end"]) for row in latest}) != 4:
                continue
            ends = [pd.Timestamp(row["period_end"]) for row in latest]
            if any((right - left).days < 70 or (right - left).days > 120 for left, right in zip(ends, ends[1:])):
                continue
            source_ids = sorted(str(row["source_fact_id"]) for row in latest)
            identity = "+".join(source_ids)
            if identity in seen:
                continue
            result = dict(latest[-1])
            period_start, period_end, duration_days = _period_window(
                min(pd.Timestamp(row["period_start"]) for row in latest),
                max(pd.Timestamp(row["period_end"]) for row in latest),
            )
            result.update({
                "value": sum(float(row["value"]) for row in latest),
                "fiscal_period": "TTM",
                "reported_basis": "TTM",
                "period_start": period_start,
                "period_end": period_end,
                "duration_days": duration_days,
                "historical_available_at": max(pd.Timestamp(row["historical_available_at"]) for row in latest),
                "available_at": max(pd.Timestamp(row["available_at"]) for row in latest),
                "source_fact_id": "derived-ttm:" + identity,
                "input_source_fact_ids": json.dumps(source_ids),
                "derivation_rule": "SUM_LATEST_FOUR_DISCRETE_QUARTERS",
            })
            output.append(result)
            seen.add(identity)
    return output


def materialize_period_bases(mapped_versions: pd.DataFrame) -> pd.DataFrame:
    if mapped_versions.empty:
        return mapped_versions.copy()
    required = {
        "issuer_cik", "security_id", "canonical_field", "unit", "fact_type",
        "reported_basis", "historical_available_at", "available_at", "source_fact_id",
        "period_end", "fiscal_year", "fiscal_period", "value",
    }
    if not required.issubset(mapped_versions.columns):
        raise ValueError("PERIOD_INPUT_COLUMNS_MISSING")
    raw = mapped_versions.copy()
    raw["historical_available_at"] = _utc_series(raw.historical_available_at)
    raw["available_at"] = _utc_series(raw.available_at)
    generated: list[dict[str, Any]] = []
    for source in raw.query("fact_type == 'INSTANT'").to_dict("records"):
        row = dict(source)
        row["reported_basis"] = "INSTANT"
        row["derivation_rule"] = "COPY_INSTANT"
        row["input_source_fact_ids"] = json.dumps([str(source["source_fact_id"])])
        generated.append(row)
    duration = raw.query("fact_type == 'DURATION'")
    for source in duration.to_dict("records"):
        if _label(source) == "FY":
            row = dict(source)
            row["reported_basis"] = "ANNUAL"
            row["derivation_rule"] = "COPY_COMPLETE_FISCAL_YEAR"
            row["input_source_fact_ids"] = json.dumps([str(source["source_fact_id"])])
            generated.append(row)
    group_keys = ["issuer_cik", "security_id", "canonical_field", "unit", "xbrl_namespace", "xbrl_tag"]
    discrete_rows: list[dict[str, Any]] = []
    for _, group in duration.groupby(group_keys, dropna=False, sort=False):
        discrete_rows.extend(_materialize_discrete(group))
    generated.extend(discrete_rows)
    generated.extend(_materialize_ttm(pd.DataFrame(discrete_rows)))
    result = pd.concat([raw, pd.DataFrame(generated)], ignore_index=True, sort=False)
    return result.sort_values(
        ["issuer_cik", "security_id", "canonical_field", "historical_available_at", "reported_basis", "source_fact_id"],
        kind="stable",
    ).reset_index(drop=True)


def select_versions_asof(
    versions: pd.DataFrame, cutoff: pd.Timestamp, availability_column: str
) -> pd.DataFrame:
    if availability_column not in {"historical_available_at", "available_at"}:
        raise ValueError("UNSUPPORTED_AVAILABILITY_COLUMN")
    frame = versions.copy()
    frame[availability_column] = pd.to_datetime(frame[availability_column], utc=True, errors="raise")
    cutoff = pd.Timestamp(cutoff)
    if cutoff.tzinfo is None:
        raise ValueError("ASOF_TIMEZONE_REQUIRED")
    frame = frame.loc[frame[availability_column] <= cutoff.tz_convert("UTC")]
    keys = ["issuer_cik", "security_id", "canonical_field", "reported_basis", "fiscal_year", "fiscal_period"]
    selected = (
        _precedence_frame(frame).sort_values(
            [
                availability_column,
                "_accession_precedence",
                "_mapping_priority_precedence",
                "source_fact_id",
            ],
            ascending=[True, True, False, True],
            kind="stable",
        )
        .groupby(keys, dropna=False, sort=False)
        .tail(1)
        .reset_index(drop=True)
    )
    return selected.drop(
        columns=["_accession_precedence", "_mapping_priority_precedence"],
        errors="ignore",
    )
