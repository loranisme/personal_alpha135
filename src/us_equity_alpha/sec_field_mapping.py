"""Reviewed, performance-blind BRAIN field to SEC series bindings."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class SecFieldBinding:
    brain_field: str
    canonical_field: str
    required_basis: str
    semantic_grade: str
    direction_or_sign_rule: str
    allowed_track: str
    mapping_evidence: str


_FIELDS = tuple(SecFieldBinding.__dataclass_fields__)
_TRACKS = {"SEC_SOURCE_LINKED", "SEC_HYPOTHESIS_PROXY"}
_GRADES = {"SOURCE_LINKED_DIRECT", "SOURCE_LINKED_DERIVED", "HYPOTHESIS_PROXY"}
_SIGNS = {"AS_REPORTED", "POSITIVE", "POSITIVE_OUTFLOW", "SIGNED",
          "POSITIVE_OBLIGATION", "DERIVED_FORMULA"}
_PROXY_ONLY = {"sharesout", "cap", "shares_outstanding_sec", "market_cap_sec_proxy",
               "enterprise_value_sec_proxy", "book_value_per_share"}


def load_sec_field_bindings(path: Path | str, sec_field_catalog: pd.DataFrame) -> dict[str, SecFieldBinding]:
    """Validate explicit bindings against the combined direct and derived SEC catalog.

    Direct duration concepts support fixed TTM assembly from discrete quarters;
    derived rows must advertise their exact output basis in the supplied catalog.
    """
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("SEC_BINDINGS_SCHEMA_INVALID")
    if not {"canonical_field", "semantic_grade"}.issubset(sec_field_catalog.columns) or not (
        {"required_basis", "supported_basis"} & set(sec_field_catalog.columns)
    ):
        raise ValueError("SEC_FIELD_CATALOG_SCHEMA_INVALID")
    catalog = sec_field_catalog.to_dict("records")
    for item in catalog:
        supported = item.get("supported_basis")
        item["basis"] = supported if isinstance(supported, str) and supported.strip() else item.get("required_basis")
    result: dict[str, SecFieldBinding] = {}
    for row in rows:
        if not isinstance(row, dict) or any(not isinstance(row.get(k), str) or not row[k].strip() for k in _FIELDS):
            raise ValueError("SEC_BINDING_INCOMPLETE")
        if set(row) != set(_FIELDS):
            raise ValueError("SEC_BINDING_SCHEMA_INVALID")
        binding = SecFieldBinding(**row)
        if binding.brain_field in result:
            raise ValueError("SEC_BINDING_DUPLICATE_BRAIN_FIELD:" + binding.brain_field)
        if binding.allowed_track not in _TRACKS or binding.semantic_grade not in _GRADES:
            raise ValueError("SEC_BINDING_SEMANTICS_INVALID:" + binding.brain_field)
        if binding.direction_or_sign_rule not in _SIGNS:
            raise ValueError("SEC_BINDING_SIGN_UNDOCUMENTED:" + binding.brain_field)
        candidates = [r for r in catalog if r["canonical_field"] == binding.canonical_field]
        if not candidates:
            raise ValueError("SEC_BINDING_UNKNOWN_FIELD:" + binding.canonical_field)
        compatible = [r for r in candidates if r["basis"] == binding.required_basis or (
            binding.required_basis in {"TTM", "QUARTERLY_DISCRETE"}
            and r["basis"] == "QUARTERLY_DISCRETE_OR_ANNUAL"
        )]
        if not compatible:
            raise ValueError("SEC_BINDING_UNSUPPORTED_BASIS:" + binding.brain_field)
        grades = {r["semantic_grade"] for r in compatible}
        if binding.semantic_grade != "HYPOTHESIS_PROXY" and binding.semantic_grade not in grades:
            raise ValueError("SEC_BINDING_GRADE_MISMATCH:" + binding.brain_field)
        if binding.allowed_track == "SEC_SOURCE_LINKED":
            if binding.semantic_grade == "HYPOTHESIS_PROXY":
                raise ValueError("SEC_BINDING_PROXY_ONLY:" + binding.brain_field)
            if binding.semantic_grade == "SOURCE_LINKED_DIRECT" and any(
                r.get("sign_convention") not in {None, binding.direction_or_sign_rule}
                for r in compatible if r["semantic_grade"] == "SOURCE_LINKED_DIRECT"
            ):
                raise ValueError("SEC_BINDING_SIGN_MISMATCH:" + binding.brain_field)
        if (binding.brain_field in _PROXY_ONLY or binding.canonical_field in _PROXY_ONLY or
                binding.semantic_grade == "HYPOTHESIS_PROXY" or "HYPOTHESIS_PROXY" in grades):
            if binding.allowed_track != "SEC_HYPOTHESIS_PROXY":
                raise ValueError("SEC_BINDING_PROXY_ONLY:" + binding.brain_field)
        result[binding.brain_field] = binding
    return result
