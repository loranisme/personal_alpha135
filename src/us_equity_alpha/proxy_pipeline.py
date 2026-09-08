"""File pipeline for T2b conversion using credential-free saved snapshots."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from .proxy_converter import (
    build_source_view,
    convert_library,
    default_market_capabilities,
    verify_factors,
    write_conversion_artifacts,
)


BAR_FIELDS = ("open", "high", "low", "close", "volume", "vwap")
PROVIDER_CONTRACTS = {
    "alpaca_sip": {"mode": "alpaca", "feed": "sip"},
    "tiingo_eod": {"mode": "tiingo", "feed": None},
}


def _verify_manifest(directory: Path) -> dict[str, str]:
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, Mapping):
        raise ValueError("SOURCE_MANIFEST_INVALID")
    for name, digest in manifest.items():
        if not isinstance(name, str) or not isinstance(digest, str):
            raise ValueError("SOURCE_MANIFEST_INVALID")
        path = (directory / name).resolve()
        if directory.resolve() not in path.parents:
            raise ValueError(f"SOURCE_MANIFEST_PATH_INVALID:{name}")
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"SOURCE_HASH_MISMATCH:{name}")
    raw_files = {path.name for path in directory.glob("raw_*.json")}
    unlisted = raw_files - set(manifest)
    if unlisted:
        raise ValueError("SOURCE_RAW_NOT_IN_MANIFEST:" + ",".join(sorted(unlisted)))
    return dict(manifest)


def _provider_report(provider: str, directory: Path, manifest: Mapping[str, str]) -> dict[str, Any]:
    contract = PROVIDER_CONTRACTS.get(provider)
    if contract is None:
        raise ValueError(f"PROVIDER_UNSUPPORTED:{provider}")
    if "report.json" not in manifest:
        raise ValueError(f"PROVIDER_REPORT_NOT_IN_MANIFEST:{provider}")
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    expected_feed = contract["feed"]
    if (
        not isinstance(report, Mapping)
        or report.get("mode") != contract["mode"]
        or report.get("real_market_data_verified") is not True
        or (expected_feed is not None and str(report.get("feed", "")).lower() != expected_feed)
    ):
        raise ValueError(f"PROVIDER_EVIDENCE_MISMATCH:{provider}")
    return dict(report)


def _load_provider_bundle(
    provider_runs: Mapping[str, Path | str],
) -> tuple[dict[str, dict[str, pd.DataFrame]], dict[str, dict[str, Any]]]:
    providers: dict[str, dict[str, pd.DataFrame]] = {}
    evidence: dict[str, dict[str, Any]] = {}
    for provider, raw_directory in provider_runs.items():
        directory = Path(raw_directory)
        manifest = _verify_manifest(directory)
        report = _provider_report(provider, directory, manifest)
        series_by_field: dict[str, dict[str, pd.Series]] = {field: {} for field in BAR_FIELDS}
        files = sorted(directory.glob("raw_*.json"))
        if not files:
            raise ValueError(f"SOURCE_BARS_MISSING:{provider}")
        for path in files:
            symbol = path.stem.removeprefix("raw_")
            rows = json.loads(path.read_text(encoding="utf-8"))
            frame = pd.DataFrame(rows)
            if "date" not in frame or frame.empty:
                raise ValueError(f"SOURCE_BAR_SCHEMA_INVALID:{provider}:{symbol}")
            index = pd.Index(pd.to_datetime(frame["date"], utc=True).dt.date)
            index.name = None
            if index.duplicated().any():
                raise ValueError(f"SOURCE_DUPLICATE_DATE:{provider}:{symbol}")
            for field in BAR_FIELDS:
                if field in frame:
                    series_by_field[field][symbol] = pd.Series(
                        pd.to_numeric(frame[field], errors="coerce").to_numpy(), index=index
                    )
        provider_fields = {
            field: pd.DataFrame(columns).sort_index()
            for field, columns in series_by_field.items() if columns
        }
        if not {"open", "high", "low", "close", "volume"}.issubset(provider_fields):
            raise ValueError(f"SOURCE_BAR_FIELDS_MISSING:{provider}")
        close = provider_fields["close"]
        providers[provider] = provider_fields
        evidence[provider] = {
            "mode": report["mode"],
            "feed": report.get("feed"),
            "status": report.get("status"),
            "real_market_data_verified": True,
            "historical_pit_verified": report.get("historical_pit_verified") is True,
            "symbols": sorted(map(str, close.columns)),
            "start_date": str(close.index.min()),
            "end_date": str(close.index.max()),
            "sessions": len(close.index),
            "report_sha256": manifest["report.json"],
        }
    return providers, evidence


def load_provider_inputs(provider_runs: Mapping[str, Path | str]) -> dict[str, dict[str, pd.DataFrame]]:
    """Load available bar fields without manufacturing absent observations."""
    providers, _ = _load_provider_bundle(provider_runs)
    return providers


def run_proxy_pipeline(registry_path: Path | str, field_mapping_path: Path | str,
                       provider_runs: Mapping[str, Path | str], output_dir: Path | str) -> dict[str, Any]:
    registry = json.loads(Path(registry_path).read_text(encoding="utf-8"))
    mappings = json.loads(Path(field_mapping_path).read_text(encoding="utf-8"))
    if not isinstance(registry, Mapping) or not isinstance(registry.get("records"), list):
        raise ValueError("REGISTRY_SCHEMA_INVALID")
    if registry.get("sync_scope_complete") is not True:
        raise ValueError("REGISTRY_SYNC_SCOPE_INCOMPLETE")
    alpha_ids = [row.get("alpha_id") for row in registry["records"] if isinstance(row, Mapping)]
    if len(alpha_ids) != len(registry["records"]) or any(not isinstance(item, str) or not item for item in alpha_ids):
        raise ValueError("REGISTRY_ALPHA_ID_INVALID")
    if len(alpha_ids) != len(set(alpha_ids)):
        raise ValueError("REGISTRY_ALPHA_ID_DUPLICATE")
    if len(alpha_ids) != 886:
        raise ValueError(
            f"REGISTRY_SOURCE_COUNT_MISMATCH:expected=886:observed={len(alpha_ids)}"
        )
    if not isinstance(mappings, list):
        raise ValueError("FIELD_MAPPING_SCHEMA_INVALID")
    by_field = {str(row["field_id"]): row for row in mappings if isinstance(row, Mapping) and row.get("field_id")}
    inputs, provider_evidence = _load_provider_bundle(provider_runs)
    capabilities = default_market_capabilities(
        {provider: set(fields) for provider, fields in inputs.items()}, provider_evidence
    )
    conversion = convert_library(build_source_view(registry["records"]), by_field, capabilities)
    verified, matrices = verify_factors(conversion, inputs)
    verified["summary"]["input_evidence"] = {
        "registry_sha256": hashlib.sha256(Path(registry_path).read_bytes()).hexdigest(),
        "field_mapping_sha256": hashlib.sha256(Path(field_mapping_path).read_bytes()).hexdigest(),
        "provider_manifest_sha256": {
            provider: hashlib.sha256((Path(path) / "manifest.json").read_bytes()).hexdigest()
            for provider, path in provider_runs.items()
        },
        "provider_evidence": provider_evidence,
    }
    write_conversion_artifacts(verified, matrices, capabilities, output_dir)
    return verified["summary"]
