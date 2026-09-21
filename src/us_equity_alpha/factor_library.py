"""Read-only adapter from the project factor library to the T3 registry view."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from .sec_factor_inputs import verify_sec_input_evidence


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


def compute_registry_factors(library: Mapping[str, Any], factor_ids: Sequence[str],
                             provider: str, inputs: Mapping[str, Any], *, usage: str
                             ) -> dict[str, Any]:
    """Evaluate frozen local definitions from ordinary provider bar panels.

    This adapter does not activate a release or bypass the research gate. The
    one supported derived benchmark is always rebuilt from this provider's SPY
    close panel, rather than accepting an unbound caller-supplied return series.
    """
    import pandas as pd
    from .factors import evaluate_factor

    view = build_registry_view(library, factor_ids, usage=usage)
    fields = dict(inputs)
    benchmark_close = fields.get("close")
    benchmark_spy = None
    if benchmark_close is not None and "SPY" in benchmark_close.columns:
        benchmark_spy = benchmark_close["SPY"] / benchmark_close["SPY"].shift(1) - 1
        for field_name, panel in list(fields.items()):
            if isinstance(panel, pd.DataFrame) and "SPY" in panel.columns:
                fields[field_name] = panel.drop(columns="SPY")
    output = {}
    for factor in view:
        if factor.get("provenance_kind") != "LOCAL_RECONSTRUCTION":
            raise ValueError("RECONSTRUCTION_ADAPTER_REQUIRED")
        if provider not in factor.get("allowed_providers", []):
            raise ValueError(f"PROVIDER_NOT_BOUND:{provider}")
        required = set(factor["field_bindings"])
        if "benchmark_returns" in required:
            close = fields.get("close")
            if close is None or benchmark_spy is None:
                raise ValueError("BENCHMARK_SPY_REQUIRED")
            fields["benchmark_returns"] = pd.DataFrame(
                {symbol: benchmark_spy for symbol in close.columns}, index=close.index
            )
        missing = required - set(fields)
        if missing:
            raise ValueError("FACTOR_INPUTS_MISSING:" + ",".join(sorted(missing)))
        output[factor["local_factor_id"]] = evaluate_factor(
            factor["expression"], fields, factor["settings"]
        )
    return output


def compute_sec_registry_factors(
    library: Mapping[str, Any], factor_ids: Sequence[str],
    inputs: Mapping[str, pd.DataFrame], input_evidence: Mapping[str, Any],
    usage: str = "diagnostic",
) -> dict[str, pd.DataFrame]:
    """Evaluate diagnostic SEC definitions against one hash-bound PIT input set."""
    from .factors import evaluate_factor

    if usage != "diagnostic":
        raise ValueError("SEC_DIAGNOSTIC_ONLY")
    verify_sec_input_evidence(inputs, input_evidence)
    view = build_registry_view(library, factor_ids, usage="diagnostic")
    required_evidence = (
        "dataset_version", "processed_manifest_sha256", "canonical_mapping_hash",
        "derived_field_catalog_hash", "security_mapping_hash", "pit_contract_hash",
    )
    output: dict[str, pd.DataFrame] = {}
    for factor in view:
        factor_id = factor["local_factor_id"]
        if factor.get("provenance_kind") not in {"SEC_SOURCE_LINKED", "SEC_HYPOTHESIS_PROXY"}:
            raise ValueError(f"SEC_PROVENANCE_INVALID:{factor_id}")
        if factor.get("usage_tier") != "DIAGNOSTIC_ONLY":
            raise ValueError(f"SEC_DIAGNOSTIC_ONLY:{factor_id}")
        declared = factor.get("field_bindings")
        if not isinstance(declared, Mapping) or set(declared) != set(inputs):
            raise ValueError(f"SEC_INPUT_SET_MISMATCH:{factor_id}")
        if factor["provenance_kind"] == "SEC_SOURCE_LINKED" and any(
            track != "SEC_SOURCE_LINKED"
            for track in input_evidence["binding_tracks_by_alias"].values()
        ):
            raise ValueError(f"SEC_STRICT_PROXY_INPUT:{factor_id}")
        for alias, value in declared.items():
            field, basis = input_evidence["field_basis_by_alias"][alias]
            allowed = (f"{field}/{basis}", alias)
            structured = {"canonical_field": field, "required_basis": basis}
            if value not in allowed and value != structured:
                raise ValueError(f"SEC_FACTOR_FIELD_BASIS_MISMATCH:{factor_id}:{alias}")
        required_fields = factor.get("required_fields")
        if required_fields is not None and set(required_fields) != set(inputs):
            raise ValueError(f"SEC_INPUT_SET_MISMATCH:{factor_id}")
        expected_evidence = factor.get("sec_evidence_hashes")
        if not isinstance(expected_evidence, Mapping) or any(
            expected_evidence.get(key) != input_evidence[key] for key in required_evidence
        ):
            raise ValueError(f"SEC_FACTOR_EVIDENCE_MISMATCH:{factor_id}")
        output[factor_id] = evaluate_factor(
            factor["expression"], inputs, factor["settings"]
        )
    return output
