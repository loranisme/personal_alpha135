"""Small authenticated provider probes that never persist credentials."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests


def _prepare_output(path: Path | str) -> Path:
    output = Path(path)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("OUTPUT_DIRECTORY_NOT_EMPTY")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _write_result(output: Path, result: dict[str, Any]) -> dict[str, Any]:
    (output / "probe_result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def probe_data(provider: str, output_dir: Path | str) -> dict[str, Any]:
    """Fetch at most one fixed SPY sample; response bodies stay local."""
    output = _prepare_output(output_dir)
    provider = provider.lower()
    if provider == "alpaca":
        key = os.getenv("APCA_API_KEY_ID")
        secret = os.getenv("APCA_API_SECRET_KEY")
        if not key or not secret:
            return _write_result(output, {"provider": provider, "status": "NOT_RUN_AUTH_REQUIRED", "sample_count": 0})
        url = "https://data.alpaca.markets/v2/stocks/SPY/bars"
        headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
        params = {"timeframe": "1Day", "start": "2024-01-02T00:00:00Z", "end": "2024-01-06T00:00:00Z", "limit": 5, "adjustment": "raw"}
        required = {"t", "o", "h", "l", "c", "v", "vw"}
    elif provider == "tiingo":
        token = os.getenv("TIINGO_API_KEY")
        if not token:
            return _write_result(output, {"provider": provider, "status": "NOT_RUN_AUTH_REQUIRED", "sample_count": 0})
        url = "https://api.tiingo.com/tiingo/daily/SPY/prices"
        headers = {"Authorization": f"Token {token}"}
        params = {"startDate": "2024-01-02", "endDate": "2024-01-06", "format": "json"}
        required = {"date", "open", "high", "low", "close", "volume", "divCash", "splitFactor"}
    else:
        raise ValueError("UNSUPPORTED_PROVIDER")

    try:
        response = requests.get(url, headers=headers, params=params, timeout=20)
        payload = response.json() if response.status_code == 200 else None
    except (requests.RequestException, ValueError) as exc:
        return _write_result(output, {"provider": provider, "status": "ERROR", "sample_count": 0, "error_type": type(exc).__name__})
    if response.status_code in (401, 403):
        return _write_result(output, {"provider": provider, "status": "AUTH_OR_ENTITLEMENT_DENIED", "http_status": response.status_code, "sample_count": 0})
    if response.status_code != 200:
        return _write_result(output, {"provider": provider, "status": "HTTP_ERROR", "http_status": response.status_code, "sample_count": 0})

    rows = payload.get("bars", []) if provider == "alpaca" and isinstance(payload, dict) else payload
    rows = rows if isinstance(rows, list) else []
    valid = bool(rows) and all(isinstance(row, dict) and required <= set(row) for row in rows)
    if rows:
        (output / "raw_sample.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    return _write_result(output, {
        "provider": provider,
        "status": "PASS" if valid else "PARTIAL_SCHEMA_MISMATCH",
        "sample_count": len(rows),
        "required_fields_present": valid,
        "credentials_persisted": False,
    })
