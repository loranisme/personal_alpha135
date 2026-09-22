"""Normalize SEC Company Facts without overwriting filing versions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

import pandas as pd

from .availability import forward_available_at, historical_available_at


SUPPORTED_NAMESPACES = {"us-gaap", "dei"}
SUPPORTED_FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A"}


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _duration_days(start: Any, end: Any) -> int | None:
    if not start or not end:
        return None
    return int((pd.Timestamp(end) - pd.Timestamp(start)).days) + 1


def _dimensions_signature(observation: Mapping[str, Any]) -> str | None:
    dimensions = observation.get("dimensions")
    if not dimensions:
        return None
    return _canonical_hash(dimensions)


def normalize_companyfacts(
    companyfacts: Mapping[str, Any],
    accession_index: pd.DataFrame,
    identity_row: Mapping[str, Any],
    raw_source_hash: str,
    first_seen_at: pd.Timestamp,
    pipeline_version: str,
) -> pd.DataFrame:
    """Expand a Company Facts payload to one immutable row per observation."""
    if len(raw_source_hash) != 64:
        raise ValueError("RAW_SOURCE_HASH_INVALID")
    accession_lookup = (
        accession_index.set_index("accession_number").to_dict("index")
        if not accession_index.empty
        else {}
    )
    records: list[dict[str, Any]] = []
    facts = companyfacts.get("facts", {})
    for namespace in sorted(facts):
        concepts = facts[namespace]
        if not isinstance(concepts, Mapping):
            continue
        for tag in sorted(concepts):
            concept = concepts[tag]
            units = concept.get("units", {}) if isinstance(concept, Mapping) else {}
            for unit in sorted(units):
                observations = units[unit]
                if not isinstance(observations, list):
                    continue
                for source_index, observation in enumerate(observations):
                    if not isinstance(observation, Mapping):
                        continue
                    form = str(observation.get("form") or "")
                    if form not in SUPPORTED_FORMS:
                        continue
                    accession = str(observation.get("accn") or "")
                    metadata = accession_lookup.get(accession, {})
                    filed_date = str(
                        metadata.get("filed_date") or observation.get("filed") or ""
                    )
                    if not filed_date:
                        continue
                    acceptance = metadata.get("acceptance_datetime")
                    acceptance_status = str(
                        metadata.get("acceptance_status")
                        or ("EXACT" if acceptance else "DATE_ONLY")
                    )
                    if acceptance_status == "UNRESOLVED_ACCEPTANCE_CONFLICT":
                        historical_time = pd.NaT
                        available_time = pd.NaT
                        precision = acceptance_status
                    else:
                        historical_time, precision = historical_available_at(
                            acceptance, filed_date, "XNYS", "16:00"
                        )
                        available_time = forward_available_at(historical_time, first_seen_at)
                    start = observation.get("start")
                    end = observation.get("end")
                    dimensions_signature = _dimensions_signature(observation)
                    if namespace not in SUPPORTED_NAMESPACES:
                        mapping_status = "DEFERRED_CUSTOM_TAG"
                    elif dimensions_signature is not None:
                        mapping_status = "DEFERRED_DIMENSION"
                    else:
                        mapping_status = "UNMAPPED"
                    fact_type = "DURATION" if start else "INSTANT"
                    source_identity = {
                        "namespace": namespace,
                        "tag": tag,
                        "unit": unit,
                        "accession": accession,
                        "start": start,
                        "end": end,
                        "value": observation.get("val"),
                        "frame": observation.get("frame"),
                        "dimensions": dimensions_signature,
                        "source_index": source_index,
                    }
                    records.append(
                        {
                            "issuer_cik": str(identity_row.get("issuer_cik") or "").zfill(10),
                            "security_id": identity_row.get("security_id"),
                            "ticker_at_source": identity_row.get("ticker"),
                            "xbrl_namespace": namespace,
                            "xbrl_tag": tag,
                            "canonical_field": None,
                            "mapping_version": None,
                            "mapping_status": mapping_status,
                            "value": observation.get("val"),
                            "unit": unit,
                            "decimals": observation.get("decimals"),
                            "period_start": start,
                            "period_end": end,
                            "fact_type": fact_type,
                            "duration_days": _duration_days(start, end),
                            "fiscal_year": observation.get("fy"),
                            "fiscal_period": observation.get("fp"),
                            "reported_basis": "RAW_REPORTED",
                            "form_type": form,
                            "accession_number": accession,
                            "filed_date": filed_date,
                            "acceptance_datetime": acceptance,
                            "acceptance_status": acceptance_status,
                            "historical_available_at": historical_time,
                            "first_seen_at": pd.Timestamp(first_seen_at).tz_convert("UTC"),
                            "available_at": available_time,
                            "availability_precision": precision,
                            "amendment_flag": form.endswith("/A"),
                            "frame": observation.get("frame"),
                            "dimensions_signature": dimensions_signature,
                            "source_fact_id": _canonical_hash(source_identity),
                            "raw_source_hash": raw_source_hash,
                            "pipeline_version": str(pipeline_version),
                        }
                    )
    columns = [
        "issuer_cik", "security_id", "ticker_at_source", "xbrl_namespace",
        "xbrl_tag", "canonical_field", "mapping_version", "mapping_status",
        "value", "unit", "decimals", "period_start", "period_end", "fact_type",
        "duration_days", "fiscal_year", "fiscal_period", "reported_basis",
        "form_type", "accession_number", "filed_date", "acceptance_datetime",
        "acceptance_status", "historical_available_at", "first_seen_at", "available_at",
        "availability_precision", "amendment_flag", "frame",
        "dimensions_signature", "source_fact_id", "raw_source_hash",
        "pipeline_version",
    ]
    return pd.DataFrame(records, columns=columns).sort_values(
        ["issuer_cik", "xbrl_namespace", "xbrl_tag", "unit", "period_end", "historical_available_at", "source_fact_id"],
        kind="stable",
        na_position="first",
    ).reset_index(drop=True)
