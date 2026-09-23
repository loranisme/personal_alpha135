"""Explicit standard-taxonomy to canonical-field mapping."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd


CATALOG_COLUMNS = {
    "mapping_version", "canonical_field", "taxonomy", "tag", "unit",
    "fact_type", "statement", "required_basis", "priority", "effective_from",
    "effective_to", "semantic_grade", "sign_convention", "review_evidence",
}
OPTIONAL_CATALOG_COLUMNS = {"issuer_cik"}


def _row_hash(row: dict) -> str:
    payload = json.dumps(
        row, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_mapping_catalog(path: Path | str) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    if not CATALOG_COLUMNS.issubset(frame.columns) or not set(frame.columns).issubset(
        CATALOG_COLUMNS | OPTIONAL_CATALOG_COLUMNS
    ):
        missing = sorted(CATALOG_COLUMNS - set(frame.columns))
        unknown = sorted(set(frame.columns) - CATALOG_COLUMNS - OPTIONAL_CATALOG_COLUMNS)
        raise ValueError(f"MAPPING_CATALOG_SCHEMA_INVALID:missing={missing}:unknown={unknown}")
    if frame.empty:
        raise ValueError("MAPPING_CATALOG_EMPTY")
    if "issuer_cik" not in frame:
        frame["issuer_cik"] = ""
    if frame.issuer_cik.map(lambda cik: bool(cik) and (len(cik) != 10 or not cik.isdigit())).any():
        raise ValueError("MAPPING_ISSUER_CIK_INVALID")
    frame["priority"] = pd.to_numeric(frame.priority, errors="raise").astype(int)
    if (frame.priority < 1).any():
        raise ValueError("MAPPING_PRIORITY_INVALID")
    exact_key = ["taxonomy", "tag", "unit", "fact_type", "issuer_cik", "effective_from", "effective_to", "priority"]
    if frame.duplicated(exact_key).any():
        raise ValueError("MAPPING_DUPLICATE_ACTIVE_KEY")
    for _, group in frame.groupby(["taxonomy", "tag", "unit", "fact_type", "issuer_cik", "priority"]):
        ordered = group.assign(
            _from=pd.to_datetime(group.effective_from, errors="raise"),
            _to=pd.to_datetime(group.effective_to.replace("", "2262-04-11"), errors="raise"),
        ).sort_values("_from")
        previous_end = None
        for _, row in ordered.iterrows():
            if previous_end is not None and row["_from"] <= previous_end:
                raise ValueError("MAPPING_OVERLAPPING_EQUAL_PRIORITY_RANGE")
            previous_end = row["_to"]
    frame["mapping_id"] = [
        _row_hash({key: value for key, value in row.items()})
        for row in frame.to_dict("records")
    ]
    return frame.sort_values(
        ["canonical_field", "priority", "taxonomy", "tag"], kind="stable"
    ).reset_index(drop=True)


def _normalize_unit(value: object) -> str:
    return str(value).strip().lower().replace(" ", "")


def apply_canonical_mapping(
    raw_facts: pd.DataFrame, catalog: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    required = {"xbrl_namespace", "xbrl_tag", "unit", "fact_type", "mapping_status", "filed_date"}
    if not required.issubset(raw_facts.columns):
        raise ValueError("RAW_FACT_MAPPING_COLUMNS_MISSING")
    mapped_rows: list[dict] = []
    deferred_rows: list[dict] = []
    candidates = catalog.copy()
    candidates["_unit"] = candidates.unit.map(_normalize_unit)
    candidates["_from"] = pd.to_datetime(candidates.effective_from, errors="raise")
    candidates["_to"] = pd.to_datetime(
        candidates.effective_to.replace("", "2262-04-11"), errors="raise"
    )
    # CompanyFacts may contain thousands of unmapped tags. Index the small,
    # reviewed whitelist once instead of scanning it for every raw fact.
    by_tag: dict[tuple[str, str], list[dict]] = {}
    for binding in candidates.to_dict("records"):
        by_tag.setdefault((binding["taxonomy"], binding["tag"]), []).append(binding)
    for bindings in by_tag.values():
        bindings.sort(key=lambda item: (
            item["priority"], item["canonical_field"], item["mapping_id"]
        ))
    for source in raw_facts.to_dict("records"):
        if source["mapping_status"] != "UNMAPPED":
            deferred_rows.append(dict(source))
            continue
        filed = pd.Timestamp(source["filed_date"])
        tag_matches = by_tag.get(
            (str(source["xbrl_namespace"]), str(source["xbrl_tag"])), []
        )
        source_cik = str(source.get("issuer_cik") or "").zfill(10)
        tag_matches = [
            binding for binding in tag_matches
            if not binding["issuer_cik"] or binding["issuer_cik"] == source_cik
        ]
        active_matches = [
            binding for binding in tag_matches
            if binding["_from"] <= filed <= binding["_to"]
        ]
        unit = _normalize_unit(source["unit"])
        exact = [
            binding for binding in active_matches
            if binding["_unit"] == unit
            and binding["fact_type"] == str(source["fact_type"])
        ]
        if not exact:
            row = dict(source)
            row["mapping_status"] = (
                "DEFERRED_UNIT_OR_BASIS" if active_matches else "DEFERRED_SEMANTIC"
            )
            deferred_rows.append(row)
            continue
        best_priority = exact[0]["priority"]
        best = [binding for binding in exact if binding["priority"] == best_priority]
        if len(best) != 1:
            row = dict(source)
            row["mapping_status"] = "DEFERRED_SEMANTIC_AMBIGUITY"
            deferred_rows.append(row)
            continue
        binding = best[0]
        row = dict(source)
        row.update({
            "canonical_field": binding["canonical_field"],
            "mapping_version": binding["mapping_version"],
            "mapping_status": "MAPPED",
            "mapping_id": binding["mapping_id"],
            "mapping_priority": int(binding["priority"]),
            "required_basis": binding["required_basis"],
            "semantic_grade": binding["semantic_grade"],
            "sign_convention": binding["sign_convention"],
        })
        mapped_rows.append(row)
    mapped = pd.DataFrame(mapped_rows)
    deferred = pd.DataFrame(deferred_rows)
    return mapped, deferred


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    catalog = load_mapping_catalog(args.catalog)
    print(json.dumps({
        "status": "PASS",
        "mapping_count": len(catalog),
        "canonical_field_count": int(catalog.canonical_field.nunique()),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
