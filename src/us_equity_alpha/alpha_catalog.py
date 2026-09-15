"""Immutable structural catalog and explicitly diagnostic-only review view."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from .proxy_converter import required_history_sessions


CATALOG_COLUMNS = (
    "local_factor_id",
    "library_version",
    "expression_hash",
    "family_id",
    "mechanism_tag",
    "mechanism_status",
    "semantic_status",
    "required_fields",
    "allowed_providers",
    "default_provider_eligible",
    "direction",
    "direction_basis",
    "build_status",
    "usage_status",
    "source_alpha_count",
    "warmup_sessions",
    "catalog_exclusion_reasons",
)

DIAGNOSTIC_VALUE_COLUMNS = (
    "diagnostic_rank_ic_mean_5d",
    "diagnostic_rank_icir_5d",
    "diagnostic_top_bottom_net_spread_5d",
    "diagnostic_turnover_mean",
)

DIAGNOSTIC_EVIDENCE_COLUMNS = (
    "diagnostic_status",
    "diagnostic_sample_start",
    "diagnostic_sample_end",
    "diagnostic_universe_label",
    "diagnostic_provider",
    "diagnostic_cost_model_hash",
    "diagnostic_input_hash",
    "diagnostic_missing_reason",
)

DIAGNOSTIC_COLUMNS = ("local_factor_id",) + DIAGNOSTIC_VALUE_COLUMNS + DIAGNOSTIC_EVIDENCE_COLUMNS
_HEX64 = re.compile(r"[0-9a-f]{64}", re.IGNORECASE)
SUPPORTED_DEFAULT_PROVIDERS = {"alpaca_sip", "tiingo_eod"}


def _validate_policy(policy: Mapping[str, Any], library_version: str) -> None:
    if policy.get("library_version") != library_version:
        raise ValueError("CATALOG_POLICY_LIBRARY_VERSION_MISMATCH")
    if policy.get("default_provider") not in SUPPORTED_DEFAULT_PROVIDERS:
        raise ValueError("UNSUPPORTED_DEFAULT_PROVIDER")
    if tuple(policy.get("diagnostic_value_columns", ())) != DIAGNOSTIC_VALUE_COLUMNS:
        raise ValueError("INVALID_DIAGNOSTIC_POLICY")


def build_alpha_catalog(
    library: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    library_version: str,
) -> pd.DataFrame:
    """Create one structural row per signed V4 local formula."""
    if library_version != "reconstruction-v4":
        raise ValueError("UNSUPPORTED_BASE_LIBRARY_VERSION")
    _validate_policy(policy, library_version)
    default_provider = str(policy["default_provider"])
    factors = library.get("factors")
    if not isinstance(factors, list):
        raise ValueError("INVALID_FACTOR_LIBRARY")
    rows: list[dict[str, Any]] = []
    for factor in factors:
        factor_id = factor.get("local_factor_id")
        if not isinstance(factor_id, str) or not factor_id:
            raise ValueError("INVALID_LOCAL_FACTOR_ID")
        tags = (factor.get("economic_description") or {}).get("mechanism_tags") or []
        mechanism = str(tags[0]) if tags else "unclassified"
        providers = sorted(str(item) for item in factor.get("allowed_providers", ()))
        bindings = factor.get("field_bindings") or {}
        if not isinstance(bindings, Mapping):
            raise ValueError(f"INVALID_FIELD_BINDINGS:{factor_id}")
        fields = sorted(str(item) for item in bindings)
        reasons: list[str] = []
        if default_provider not in providers:
            reasons.append(
                "ALPACA_VWAP_ONLY"
                if default_provider == "tiingo_eod" and "vwap" in fields and "alpaca_sip" in providers
                else "DEFAULT_PROVIDER_UNAVAILABLE"
            )
        if mechanism == "unclassified":
            reasons.append("MECHANISM_UNCLASSIFIED")
        if factor.get("build_status") != "COMPUTE_VERIFIED":
            reasons.append("NOT_COMPUTE_VERIFIED")
        rows.append(
            {
                "local_factor_id": factor_id,
                "library_version": library_version,
                "expression_hash": factor.get("expression_hash"),
                "family_id": factor.get("family_id"),
                "mechanism_tag": mechanism,
                "mechanism_status": "CLASSIFIED" if mechanism != "unclassified" else "UNCLASSIFIED",
                "semantic_status": factor.get("semantic_status"),
                "required_fields": ",".join(fields),
                "allowed_providers": ",".join(providers),
                "default_provider_eligible": not reasons,
                "direction": 1,
                "direction_basis": factor.get("direction_basis"),
                "build_status": factor.get("build_status"),
                "usage_status": "LIBRARY_ONLY",
                "source_alpha_count": len(factor.get("source_alpha_ids") or []),
                "warmup_sessions": required_history_sessions(
                    str(factor.get("expression", "")), factor.get("settings") or {}
                ),
                "catalog_exclusion_reasons": ",".join(reasons),
            }
        )
    result = pd.DataFrame(rows, columns=CATALOG_COLUMNS).sort_values(
        "local_factor_id", kind="stable"
    )
    if result.local_factor_id.duplicated().any():
        raise ValueError("DUPLICATE_LOCAL_FACTOR_ID")
    return result.reset_index(drop=True)


def _empty_diagnostics(catalog: pd.DataFrame) -> pd.DataFrame:
    empty = pd.DataFrame({"local_factor_id": catalog.local_factor_id})
    for column in DIAGNOSTIC_VALUE_COLUMNS + DIAGNOSTIC_EVIDENCE_COLUMNS:
        empty[column] = pd.NA
    empty["diagnostic_missing_reason"] = "NOT_SUPPLIED"
    return empty


def build_catalog_review_view(
    catalog: pd.DataFrame,
    diagnostics: pd.DataFrame | None,
    policy: Mapping[str, Any],
) -> pd.DataFrame:
    """Join scoped history for display without changing structural decisions."""
    _validate_policy(policy, "reconstruction-v4")
    if diagnostics is None:
        normalized = _empty_diagnostics(catalog)
    else:
        if set(diagnostics.columns) != set(DIAGNOSTIC_COLUMNS):
            raise ValueError("INVALID_DIAGNOSTIC_SCHEMA")
        normalized = diagnostics.loc[:, DIAGNOSTIC_COLUMNS].copy()
        if normalized.local_factor_id.duplicated().any():
            raise ValueError("DUPLICATE_DIAGNOSTIC_FACTOR_ID")
        unknown = set(normalized.local_factor_id) - set(catalog.local_factor_id)
        if unknown:
            raise ValueError("UNKNOWN_DIAGNOSTIC_FACTOR_ID")
        populated = normalized.loc[:, DIAGNOSTIC_VALUE_COLUMNS].notna().any(axis=1)
        required = [
            "diagnostic_status",
            "diagnostic_sample_start",
            "diagnostic_sample_end",
            "diagnostic_universe_label",
            "diagnostic_provider",
            "diagnostic_cost_model_hash",
            "diagnostic_input_hash",
        ]
        if populated.any():
            rows = normalized.loc[populated]
            if not rows.diagnostic_status.eq("DIAGNOSTIC_ONLY").all():
                raise ValueError("DIAGNOSTIC_STATUS_REQUIRED")
            if rows.loc[:, required].isna().any(axis=None):
                raise ValueError("DIAGNOSTIC_EVIDENCE_REQUIRED")
            for column in ("diagnostic_cost_model_hash", "diagnostic_input_hash"):
                if not rows[column].astype(str).str.fullmatch(_HEX64).all():
                    raise ValueError("INVALID_DIAGNOSTIC_HASH")
    view = catalog.merge(normalized, on="local_factor_id", how="left", validate="one_to_one")
    missing = view.loc[:, DIAGNOSTIC_VALUE_COLUMNS].isna().all(axis=1)
    view["diagnostic_missing_reason"] = view["diagnostic_missing_reason"].astype("object")
    view.loc[missing & view.diagnostic_missing_reason.isna(), "diagnostic_missing_reason"] = "NOT_SUPPLIED"
    return view


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON_ROOT_NOT_OBJECT")
    return value


def write_alpha_catalog(
    library_path: Path | str,
    manifest_path: Path | str,
    policy_path: Path | str,
    output_dir: Path | str,
    diagnostics_path: Path | str | None = None,
) -> dict[str, Path]:
    """Write a self-describing Catalog bundle into an empty directory."""
    library_file = Path(library_path)
    v4_manifest_file = Path(manifest_path)
    policy_file = Path(policy_path)
    output = Path(output_dir)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("OUTPUT_DIRECTORY_NOT_EMPTY")
    library = _read_object(library_file)
    v4_manifest = _read_object(v4_manifest_file)
    expected_library_hash = v4_manifest.get("project_factor_library.json")
    if expected_library_hash and expected_library_hash != _digest(library_file):
        raise ValueError("V4_LIBRARY_MANIFEST_MISMATCH")
    policy = _read_object(policy_file)
    catalog = build_alpha_catalog(library, policy, library_version="reconstruction-v4")
    diagnostics_file = Path(diagnostics_path) if diagnostics_path is not None else None
    diagnostics = pd.read_csv(diagnostics_file) if diagnostics_file is not None else None
    review = build_catalog_review_view(catalog, diagnostics, policy)
    output.mkdir(parents=True, exist_ok=True)
    paths = {
        "catalog_csv": output / "alpha_catalog.csv",
        "catalog_parquet": output / "alpha_catalog.parquet",
        "review_csv": output / "alpha_catalog_review.csv",
        "summary": output / "catalog_summary.json",
        "manifest": output / "manifest.json",
    }
    catalog.to_csv(paths["catalog_csv"], index=False)
    catalog.to_parquet(paths["catalog_parquet"], index=False)
    review.to_csv(paths["review_csv"], index=False)
    summary = {
        "status": "PASS",
        "library_version": "reconstruction-v4",
        "factor_count": int(len(catalog)),
        "default_provider_eligible_count": int(catalog.default_provider_eligible.sum()),
        "diagnostic_row_count": int(
            review.loc[:, DIAGNOSTIC_VALUE_COLUMNS].notna().any(axis=1).sum()
        ),
    }
    paths["summary"].write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    files = {
        path.name: _digest(path)
        for key, path in paths.items()
        if key != "manifest"
    }
    manifest = {
        "schema_version": 1,
        "artifact_type": "V5_ALPHA_CATALOG",
        "library_version": "reconstruction-v4",
        "input_sha256": {
            "library": _digest(library_file),
            "v4_manifest": _digest(v4_manifest_file),
            "policy": _digest(policy_file),
            "diagnostics": _digest(diagnostics_file) if diagnostics_file else None,
        },
        "files": files,
        "historical_pit_verified": False,
        "live_orders_submitted": 0,
    }
    paths["manifest"].write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if len(pd.read_csv(paths["catalog_csv"])) != len(catalog):
        raise ValueError("CATALOG_CSV_REOPEN_MISMATCH")
    if len(pd.read_parquet(paths["catalog_parquet"])) != len(catalog):
        raise ValueError("CATALOG_PARQUET_REOPEN_MISMATCH")
    if len(pd.read_csv(paths["review_csv"])) != len(review):
        raise ValueError("CATALOG_REVIEW_REOPEN_MISMATCH")
    return paths
