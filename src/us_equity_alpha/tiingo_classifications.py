"""Tiingo Fundamentals metadata adapter for current live classifications.

Tiingo does not document classification history or a subindustry field.  The
adapter therefore exposes sector/industry as current-only local taxonomies and
uses the full SIC code as an explicit subindustry proxy.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd
import requests


URL = "https://api.tiingo.com/tiingo/fundamentals/meta"


def _label(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if value.lower().startswith("field not available") or value.lower() in {"null", "none", "n/a"}:
        return None
    return value


def fetch_current_classifications(
    token: str,
    *,
    session: Any = None,
    fetched_at: pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if not token:
        raise ValueError("TIINGO_API_KEY_REQUIRED")
    transport = session or requests.Session()
    try:
        response = transport.get(
            URL, headers={"Authorization": f"Token {token}"}, timeout=30
        )
    except requests.RequestException as exc:
        raise ValueError("TIINGO_CLASSIFICATION_NETWORK_ERROR") from exc
    if response.status_code != 200:
        raise ValueError(f"TIINGO_CLASSIFICATION_HTTP_{response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("TIINGO_CLASSIFICATION_INVALID_JSON") from exc
    if not isinstance(payload, list):
        raise ValueError("TIINGO_CLASSIFICATION_INVALID_SCHEMA")

    timestamp = fetched_at or pd.Timestamp.now(tz="UTC")
    timestamp = pd.Timestamp(timestamp)
    if timestamp.tzinfo is None:
        raise ValueError("FETCHED_AT_MUST_BE_TIMEZONE_AWARE")
    timestamp = timestamp.tz_convert("UTC")
    rows = []
    for item in payload:
        if not isinstance(item, dict) or not item.get("ticker"):
            continue
        if item.get("isActive") is False:
            continue
        sic_code = _label(str(item.get("sicCode", "")))
        try:
            subindustry = f"SIC:{int(sic_code):04d}" if sic_code and sic_code.isdigit() and 0 < int(sic_code) <= 9999 else None
        except (TypeError, ValueError):
            subindustry = None
        rows.append({
            "symbol": str(item["ticker"]).strip().upper(),
            "security_id": item.get("permaTicker"),
            "effective_at": timestamp,
            "available_at": timestamp,
            "market": "USA",
            "sector": _label(item.get("sector")),
            "industry": _label(item.get("industry")),
            "subindustry": subindustry,
            "sic_code": sic_code if subindustry else None,
        })
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise ValueError("TIINGO_CLASSIFICATION_EMPTY_OR_DUPLICATE")
    ambiguous = sorted(frame.loc[frame["symbol"].duplicated(keep=False), "symbol"].unique().tolist())
    frame = frame.loc[~frame["symbol"].isin(ambiguous)].reset_index(drop=True)
    if frame.empty:
        raise ValueError("TIINGO_CLASSIFICATION_EMPTY_OR_DUPLICATE")
    evidence = {
        "classification_evidence_verified": True,
        "real_classification_verified": True,
        "historical_pit_verified": False,
        "provider": "tiingo_fundamentals_meta",
        "endpoint": URL,
        "levels": ["SECTOR", "INDUSTRY", "SUBINDUSTRY"],
        "taxonomy_by_level": {
            "SECTOR": "TIINGO_SECTOR",
            "INDUSTRY": "TIINGO_INDUSTRY",
            "SUBINDUSTRY": "LOCAL_FULL_SIC_CODE_PROXY",
        },
        "brain_taxonomy_equivalent": {
            "SECTOR": False, "INDUSTRY": False, "SUBINDUSTRY": False,
        },
        "fetched_at": timestamp.isoformat(),
        "row_count": len(frame),
        "excluded_ambiguous_symbols": ambiguous,
        "coverage": {level: int(frame[level.lower()].notna().sum()) for level in ("SECTOR", "INDUSTRY", "SUBINDUSTRY")},
        "coverage_status": "COMPLETE" if frame[["sector", "industry", "subindustry"]].notna().all().all() else "PARTIAL",
        "missing_classification_policy": "KEEP_MISSING_NO_SIC_LABEL_FALLBACK",
        "contains_credentials": False,
    }
    return frame, evidence


def write_classification_bundle(
    frame: pd.DataFrame, evidence: dict[str, Any], output_dir: Path | str
) -> None:
    output = Path(output_dir)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("OUTPUT_DIRECTORY_NOT_EMPTY")
    output.mkdir(parents=True, exist_ok=True)
    data = output / "classifications.parquet"
    report = output / "report.json"
    frame.to_parquet(data, index=False)
    report.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (data, report)
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
