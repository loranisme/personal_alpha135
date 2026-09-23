"""Narrow, evidence-bound exceptions for a reviewed common-share cover fact.

An issuer with multiple SEC tickers remains entity-only by default.  A review
can admit one exact DEI cover observation for the named common security; it
does not turn the entire issuer history into a verified single share class.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

import pandas as pd


SHARE_CLASS_FIELDS = frozenset({
    "shares_outstanding_sec", "weighted_average_basic_shares",
    "weighted_average_diluted_shares", "basic_eps", "diluted_eps",
})
_FIELDS = frozenset({
    "issuer_cik", "ticker", "exchange", "canonical_field", "xbrl_namespace",
    "xbrl_tag", "unit", "accession_number", "period_end", "value",
    "raw_source_hash", "source_fact_id", "filing_url", "semantic_grade",
})
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_CIK = re.compile(r"[0-9]{10}\Z")
_ACCESSION = re.compile(r"[0-9]{10}-[0-9]{2}-[0-9]{6}\Z")


def load_reviewed_share_facts(path: Path | str) -> tuple[dict[str, Any], ...]:
    """Load only exact, SEC-sourced, one-fact common-share assertions."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "reviews"} \
            or payload["schema_version"] != 1 or not isinstance(payload["reviews"], list):
        raise ValueError("REVIEWED_SHARE_FACT_SCHEMA_INVALID")
    reviews: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in payload["reviews"]:
        if not isinstance(row, dict) or set(row) != _FIELDS:
            raise ValueError("REVIEWED_SHARE_FACT_SCHEMA_INVALID")
        if any(not isinstance(row[key], str) or not row[key].strip()
               for key in _FIELDS - {"value"}):
            raise ValueError("REVIEWED_SHARE_FACT_SCHEMA_INVALID")
        parsed = urlparse(row["filing_url"])
        if (parsed.scheme != "https" or parsed.hostname != "www.sec.gov"
                or not parsed.path.startswith("/Archives/edgar/data/")
                or row["accession_number"].replace("-", "") not in parsed.path):
            raise ValueError("REVIEWED_SHARE_FACT_SOURCE_INVALID")
        if (not _CIK.fullmatch(row["issuer_cik"])
                or not _ACCESSION.fullmatch(row["accession_number"])
                or not _SHA.fullmatch(row["raw_source_hash"])
                or not _SHA.fullmatch(row["source_fact_id"])):
            raise ValueError("REVIEWED_SHARE_FACT_IDENTITY_INVALID")
        if (row["canonical_field"] != "shares_outstanding_sec"
                or row["xbrl_namespace"] != "dei"
                or row["xbrl_tag"] != "EntityCommonStockSharesOutstanding"
                or row["unit"] != "shares"
                or row["semantic_grade"] != "HYPOTHESIS_PROXY"):
            raise ValueError("REVIEWED_SHARE_FACT_SEMANTICS_INVALID")
        try:
            value = Decimal(str(row["value"]))
            date = pd.Timestamp(row["period_end"])
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ValueError("REVIEWED_SHARE_FACT_VALUE_INVALID") from exc
        if (not value.is_finite() or value <= 0 or value != value.to_integral_value()
                or date.tzinfo is not None or date.strftime("%Y-%m-%d") != row["period_end"]):
            raise ValueError("REVIEWED_SHARE_FACT_VALUE_INVALID")
        key = (row["issuer_cik"], row["accession_number"], row["period_end"])
        if key in seen:
            raise ValueError("REVIEWED_SHARE_FACT_DUPLICATE")
        seen.add(key)
        reviews.append(dict(row))
    return tuple(reviews)


def retain_reviewed_share_facts(
    mapped: pd.DataFrame,
    identity: Mapping[str, Any],
    reviews: tuple[dict[str, Any], ...],
) -> tuple[pd.DataFrame, int]:
    """For a multi-ticker issuer, keep only exact reviewed share facts."""
    if str(identity.get("mapping_status")) != "VERIFIED_ENTITY_LEVEL_ONLY":
        return mapped.copy(), 0
    if mapped.empty:
        return mapped.copy(), 0
    blocked = mapped.canonical_field.isin(SHARE_CLASS_FIELDS)
    eligible = pd.Series(False, index=mapped.index)
    for review in reviews:
        if any(str(identity.get(key)) != review[key]
               for key in ("issuer_cik", "ticker", "exchange")):
            continue
        exact = blocked.copy()
        for field in (
            "canonical_field", "xbrl_namespace", "xbrl_tag", "unit",
            "accession_number", "period_end", "raw_source_hash", "semantic_grade", "source_fact_id",
        ):
            exact &= mapped[field].astype(str).eq(review[field])
        exact &= mapped.value.map(
            lambda value: Decimal(str(value)) == Decimal(str(review["value"]))
        )
        eligible |= exact
    keep = ~blocked | eligible
    return mapped.loc[keep].copy(), int((~keep).sum())
