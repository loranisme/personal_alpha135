"""Frozen V4 deferred-source demand matrix; source performance is never read."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from .sec_field_mapping import SecFieldBinding


EXPECTED_COUNTS = {"FUNDAMENTAL_ONLY": 86, "MIXED_DOMAIN": 203, "NO_FUNDAMENTAL": 280}


def _hash_expression(expression: str) -> str:
    return hashlib.sha256(json.dumps(expression, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def audit_fundamental_deferred_sources(
    registry: Mapping[str, Any], v4_source_index: pd.DataFrame,
    brain_field_catalog: Sequence[Mapping[str, Any]], bindings: Mapping[str, SecFieldBinding],
) -> pd.DataFrame:
    """Classify every V4 deferred source using its missing fields and saved category."""
    records = registry.get("records")
    if not isinstance(records, list) or len(records) != 886 or registry.get("sync_scope_complete") is not True:
        raise ValueError("INPUT_VERSION_MISMATCH:registry")
    source_by_id = {r.get("id"): r for r in records if isinstance(r, dict)}
    if len(source_by_id) != 886 or None in source_by_id:
        raise ValueError("INPUT_VERSION_MISMATCH:source_ids")
    required_columns = {"source_alpha_id", "decision", "missing_fields"}
    if not required_columns.issubset(v4_source_index.columns):
        raise ValueError("INPUT_VERSION_MISMATCH:v4_columns")
    if len(v4_source_index) != 886 or v4_source_index.source_alpha_id.nunique() != 886:
        raise ValueError("INPUT_VERSION_MISMATCH:v4_index")
    if set(v4_source_index.source_alpha_id) != set(source_by_id):
        raise ValueError("INPUT_VERSION_MISMATCH:source_join")
    categories = {f.get("id"): f.get("category", {}).get("id") for f in brain_field_catalog}
    if len(categories) != len(brain_field_catalog):
        raise ValueError("INPUT_VERSION_MISMATCH:field_catalog_duplicates")
    output = []
    for source in v4_source_index.itertuples(index=False):
        if source.decision != "DEFERRED":
            continue
        names = sorted({x.strip() for x in str(source.missing_fields).split("|") if x.strip() and x.strip() != "nan"})
        unknown = [name for name in names if name not in categories or not categories[name]]
        if unknown:
            raise ValueError("INPUT_VERSION_MISMATCH:unknown_brain_fields:" + ",".join(unknown))
        fundamental = [name for name in names if categories[name] == "fundamental"]
        other = [name for name in names if categories[name] != "fundamental"]
        mapped = [name for name in fundamental if name in bindings]
        unmapped = [name for name in fundamental if name not in bindings]
        demand_class = ("FUNDAMENTAL_ONLY" if fundamental and not other else
                        "MIXED_DOMAIN" if fundamental else "NO_FUNDAMENTAL")
        required = [vars(bindings[name]).copy() for name in mapped]
        reasons = ["UNMAPPED_FUNDAMENTAL_FIELD:" + name for name in unmapped]
        reasons += ["OTHER_DOMAIN_FIELD:" + name + ":" + categories[name] for name in other]
        if not fundamental:
            candidate_track = "OUT_OF_SCOPE"
        elif not mapped:
            candidate_track = "DEFERRED"
        elif other or unmapped or any(bindings[name].allowed_track == "SEC_HYPOTHESIS_PROXY" for name in mapped):
            candidate_track = "SEC_HYPOTHESIS_PROXY"
        else:
            candidate_track = "SEC_SOURCE_LINKED"
        if candidate_track in {"SEC_SOURCE_LINKED", "SEC_HYPOTHESIS_PROXY"}:
            reasons.append("FORMULA_AND_PIT_REVIEW_PENDING")
        record = source_by_id[source.source_alpha_id]
        expression = record.get("regular", {}).get("code")
        if not isinstance(expression, str) or not expression:
            raise ValueError("INPUT_VERSION_MISMATCH:expression:" + source.source_alpha_id)
        output.append(dict(source_alpha_id=source.source_alpha_id,
                           original_expression_hash=_hash_expression(expression), missing_fields=names,
                           fundamental_fields=fundamental, other_domain_fields=other,
                           mapped_fields=mapped, unmapped_fundamental_fields=unmapped,
                           required_bindings=required, demand_class=demand_class,
                           candidate_track=candidate_track, blocking_reasons=reasons))
    result = pd.DataFrame(output).sort_values("source_alpha_id").reset_index(drop=True)
    if len(result) != 569 or result.demand_class.value_counts().to_dict() != EXPECTED_COUNTS:
        raise ValueError("INPUT_VERSION_MISMATCH:demand_counts:" + str(dict(Counter(result.demand_class))))
    return result
