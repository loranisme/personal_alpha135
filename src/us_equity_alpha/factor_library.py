"""Read-only adapter from the project factor library to the T3 registry view."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def build_registry_view(library: Mapping[str, Any], factor_ids: Sequence[str],
                        *, usage: str) -> list[dict[str, Any]]:
    if usage not in {"diagnostic", "research"}:
        raise ValueError("INVALID_USAGE")
    indexed = {row.get("local_factor_id"): row for row in library.get("factors", [])}
    view = []
    for factor_id in factor_ids:
        if factor_id not in indexed:
            raise ValueError(f"FROZEN_FACTOR_ID_MISSING:{factor_id}")
        row = indexed[factor_id]
        if usage == "research":
            if row.get("usage_tier") != "RESEARCH_ELIGIBLE":
                raise ValueError(f"FACTOR_NOT_RESEARCH_ELIGIBLE:{factor_id}")
            if row.get("build_status") != "COMPUTE_VERIFIED":
                raise ValueError(f"FACTOR_NOT_COMPUTE_VERIFIED:{factor_id}")
        elif row.get("build_status") not in {"COMPILED", "COMPUTE_VERIFIED"}:
            raise ValueError(f"FACTOR_NOT_COMPILED:{factor_id}")
        view.append(dict(row))
    return view
