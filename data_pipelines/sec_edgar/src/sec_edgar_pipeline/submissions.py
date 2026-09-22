"""Flatten SEC submissions metadata into an accession lookup."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd


_COLUMN_MAP = {
    "accessionNumber": "accession_number",
    "filingDate": "filed_date",
    "acceptanceDateTime": "acceptance_datetime",
    "form": "form_type",
    "reportDate": "report_date",
}


def _rows_from_columnar(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    lengths = {
        len(value)
        for key, value in payload.items()
        if key in _COLUMN_MAP and isinstance(value, list)
    }
    if not lengths:
        return []
    if len(lengths) != 1:
        raise ValueError("SUBMISSIONS_COLUMN_LENGTH_MISMATCH")
    count = lengths.pop()
    rows: list[dict[str, Any]] = []
    for index in range(count):
        row = {
            target: payload.get(source, [None] * count)[index]
            for source, target in _COLUMN_MAP.items()
        }
        if row["accession_number"]:
            rows.append(row)
    return rows


def _normalize_acceptance(value: Any) -> str | None:
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    if len(text) == 14 and text.isdigit():
        return pd.to_datetime(text, format="%Y%m%d%H%M%S", utc=True).isoformat()
    timestamp = pd.Timestamp(text)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC").isoformat()


def build_accession_index(
    submissions: Mapping[str, Any], older_files: Sequence[Mapping[str, Any]]
) -> pd.DataFrame:
    recent = submissions.get("filings", {}).get("recent", {})
    rows = _rows_from_columnar(recent)
    for payload in older_files:
        rows.extend(_rows_from_columnar(payload.get("filings", {}).get("recent", payload)))
    columns = [
        "accession_number",
        "filed_date",
        "acceptance_datetime",
        "form_type",
        "report_date",
        "acceptance_status",
    ]
    if not rows:
        return pd.DataFrame(columns=columns)
    result: list[dict[str, Any]] = []
    frame = pd.DataFrame(rows)
    for accession, group in frame.groupby("accession_number", sort=True, dropna=False):
        acceptance_values = {
            normalized
            for normalized in (_normalize_acceptance(value) for value in group.acceptance_datetime)
            if normalized is not None
        }
        filing_values = sorted({str(value) for value in group.filed_date if pd.notna(value)})
        form_values = sorted({str(value) for value in group.form_type if pd.notna(value)})
        report_values = sorted({str(value) for value in group.report_date if pd.notna(value)})
        if len(acceptance_values) == 1:
            acceptance = next(iter(acceptance_values))
            status = "EXACT"
        elif not acceptance_values:
            acceptance = None
            status = "DATE_ONLY"
        else:
            acceptance = None
            status = "UNRESOLVED_ACCEPTANCE_CONFLICT"
        result.append(
            {
                "accession_number": str(accession),
                "filed_date": filing_values[-1] if filing_values else None,
                "acceptance_datetime": acceptance,
                "form_type": form_values[-1] if form_values else None,
                "report_date": report_values[-1] if report_values else None,
                "acceptance_status": status,
            }
        )
    return pd.DataFrame(result, columns=columns).sort_values(
        "accession_number", kind="stable"
    ).reset_index(drop=True)
