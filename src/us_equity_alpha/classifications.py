"""Point-in-time classification inputs for settings-level neutralization."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd


LEVELS = ("MARKET", "SECTOR", "INDUSTRY", "SUBINDUSTRY")
REQUIRED_COLUMNS = {"symbol", "effective_at", "available_at"}


def _verify_bundle(directory: Path) -> tuple[pd.DataFrame, dict[str, Any], str]:
    manifest_path = directory / "manifest.json"
    report_path = directory / "report.json"
    data_path = directory / "classifications.parquet"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, Mapping):
        raise ValueError("CLASSIFICATION_MANIFEST_INVALID")
    for name in ("report.json", "classifications.parquet"):
        digest = manifest.get(name)
        path = directory / name
        if not isinstance(digest, str) or not path.is_file():
            raise ValueError(f"CLASSIFICATION_MANIFEST_ENTRY_MISSING:{name}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"CLASSIFICATION_HASH_MISMATCH:{name}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(report, Mapping) or report.get("classification_evidence_verified") is not True:
        raise ValueError("CLASSIFICATION_EVIDENCE_INVALID")
    frame = pd.read_parquet(data_path)
    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError("CLASSIFICATION_COLUMNS_MISSING:" + ",".join(sorted(missing)))
    if frame.empty:
        raise ValueError("CLASSIFICATION_DATA_EMPTY")
    frame = frame.copy()
    for column in ("effective_at", "available_at"):
        frame[column] = pd.to_datetime(frame[column], utc=True, errors="raise")
    if frame.duplicated().any():
        raise ValueError("CLASSIFICATION_DUPLICATE_ROWS")
    return frame, dict(report), hashlib.sha256(manifest_path.read_bytes()).hexdigest()


def _asof_panel(frame: pd.DataFrame, dates: pd.Index, symbols: pd.Index, level: str) -> pd.DataFrame:
    output = pd.DataFrame(None, index=dates, columns=symbols, dtype=object)
    column = level.lower()
    if column not in frame:
        return output
    ordered = frame.sort_values(
        ["symbol", "effective_at", "available_at"], kind="mergesort"
    )
    for date in dates:
        cutoff = pd.Timestamp(date)
        cutoff = cutoff.tz_localize("UTC") if cutoff.tzinfo is None else cutoff.tz_convert("UTC")
        cutoff = cutoff.normalize() + pd.Timedelta(1, unit="D") - pd.Timedelta(1, unit="ns")
        visible = ordered[
            (ordered["effective_at"] <= cutoff) & (ordered["available_at"] <= cutoff)
        ]
        latest = visible.groupby("symbol", sort=False).tail(1).set_index("symbol")
        output.loc[date] = latest[column].reindex(symbols)
    return output


def market_neutralization_inputs(
    provider_inputs: Mapping[str, Mapping[str, pd.DataFrame]],
) -> tuple[dict[str, dict[str, pd.DataFrame]], dict[str, dict[str, Any]]]:
    panels = {
        provider: {
            "MARKET": pd.DataFrame(
                "USA", index=inputs["close"].index, columns=inputs["close"].columns,
                dtype=object,
            )
        }
        for provider, inputs in provider_inputs.items()
    }
    capabilities = {
        "MARKET": {
            "status": "DERIVED_REQUIRES_QA",
            "provider": "project_calculation_universe",
            "taxonomy": "PROJECT_CALC_UNIVERSE",
            "brain_taxonomy_equivalent": False,
            "historical_pit_verified": True,
        }
    }
    return panels, capabilities


def load_classification_inputs(
    directory: Path | str,
    provider_inputs: Mapping[str, Mapping[str, pd.DataFrame]],
) -> tuple[dict[str, dict[str, pd.DataFrame]], dict[str, dict[str, Any]], dict[str, Any]]:
    """Align a hashed classification bundle to every provider's observed bars."""
    frame, report, manifest_sha256 = _verify_bundle(Path(directory))
    declared = {str(item).upper() for item in report.get("levels", [])}
    panels, capabilities = market_neutralization_inputs(provider_inputs)
    for provider, inputs in provider_inputs.items():
        reference = inputs["close"]
        for level in ("SECTOR", "INDUSTRY", "SUBINDUSTRY"):
            if level in declared:
                panels[provider][level] = _asof_panel(
                    frame, reference.index, reference.columns, level
                )
    for level in ("SECTOR", "INDUSTRY", "SUBINDUSTRY"):
        if level not in declared:
            continue
        finite = sum(
            int(provider_levels[level].notna().to_numpy().sum())
            for provider_levels in panels.values() if level in provider_levels
        )
        if finite:
            capabilities[level] = {
                "status": "VERIFIED_SAMPLE",
                "provider": report.get("provider"),
                "taxonomy": (report.get("taxonomy_by_level") or {}).get(level),
                "brain_taxonomy_equivalent": bool(
                    (report.get("brain_taxonomy_equivalent") or {}).get(level, False)
                ),
                "historical_pit_verified": report.get("historical_pit_verified") is True,
                "real_classification_verified": report.get("real_classification_verified") is True,
                "finite_labels": finite,
            }
    evidence = {"manifest_sha256": manifest_sha256, "report": report}
    return panels, capabilities, evidence
