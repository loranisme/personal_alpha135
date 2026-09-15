"""Resume current Alpaca assets and Tiingo raw EOD inputs, then build a snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from us_equity_alpha.alpaca_data import fetch_current_assets
from us_equity_alpha.current_universe import write_current_universe_snapshot


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_status(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--policy", default="config/current_universe_policy.json", type=Path)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--classifications", type=Path)
    args = parser.parse_args()
    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_API_SECRET")
    tiingo_token = os.environ.get("TIINGO_API_KEY")
    if not key or not secret or not tiingo_token:
        print("BLOCKED_DATA_AUTH: ALPACA_API_KEY, ALPACA_API_SECRET and TIINGO_API_KEY are required")
        return 2
    root = args.output
    if root.exists() and (root / "universe").exists():
        print("OUTPUT_ALREADY_FINALIZED")
        return 2
    root.mkdir(parents=True, exist_ok=True)
    raw = root / "raw_tiingo"
    raw.mkdir(exist_ok=True)
    assets, asset_evidence = fetch_current_assets(key, secret)
    assets.to_csv(root / "assets.csv", index=False)
    cutoff = pd.Timestamp(args.as_of)
    if cutoff.tzinfo is None:
        print("AS_OF_TIMEZONE_REQUIRED")
        return 2
    start = (cutoff.date() - timedelta(days=550)).isoformat()
    end = cutoff.date().isoformat()
    structurally_eligible = assets.loc[
        assets.exchange.isin(["NYSE", "NASDAQ", "NYSE_AMERICAN"])
        & assets.security_type.isin(["COMMON_STOCK", "REIT"])
        & assets.status.eq("ACTIVE")
        & assets.tradable.eq(True)  # noqa: E712
    ]
    status = {
        "status": "RUNNING",
        "expected": len(structurally_eligible),
        "complete": 0,
        "errors": [],
        "asset_evidence": asset_evidence,
        "credentials_stored": False,
    }
    session = requests.Session()
    session.headers.update({"Authorization": "Token " + tiingo_token})
    bars: dict[str, pd.DataFrame] = {}
    for row in structurally_eligible.itertuples(index=False):
        path = raw / f"{row.security_id}.json"
        sidecar = raw / f"{row.security_id}.sha256"
        if path.is_file() and sidecar.is_file() and _digest(path) == sidecar.read_text().strip():
            payload = json.loads(path.read_text(encoding="utf-8"))
        else:
            try:
                response = session.get(
                    f"https://api.tiingo.com/tiingo/daily/{row.ticker}/prices",
                    params={"startDate": start, "endDate": end},
                    timeout=45,
                    allow_redirects=False,
                )
            except requests.RequestException as exc:
                status["status"] = "BLOCKED_NETWORK"
                status["errors"].append({"security_id": row.security_id, "reason": type(exc).__name__})
                _write_status(root / "download_status.json", status)
                return 2
            if response.status_code != 200:
                status["errors"].append({"security_id": row.security_id, "http_status": response.status_code})
                _write_status(root / "download_status.json", status)
                continue
            payload = response.json()
            if not isinstance(payload, list):
                status["errors"].append({"security_id": row.security_id, "reason": "INVALID_SCHEMA"})
                _write_status(root / "download_status.json", status)
                continue
            path.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
            sidecar.write_text(_digest(path) + "\n", encoding="utf-8")
        fetched_at = datetime.now(timezone.utc).isoformat()
        frame = pd.DataFrame(payload)
        if {"date", "close", "volume"}.issubset(frame.columns):
            bars[row.security_id] = pd.DataFrame(
                {
                    "date": frame.date,
                    "close": frame.close,
                    "volume": frame.volume,
                    "adjustment": "raw",
                    "source": "tiingo_eod",
                    "available_at": fetched_at,
                }
            )
            status["complete"] += 1
        _write_status(root / "download_status.json", status)
    if status["complete"] != status["expected"]:
        status["status"] = "PARTIAL"
        _write_status(root / "download_status.json", status)
        print("PARTIAL: resume required; no universe snapshot written")
        return 4
    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    classifications = None
    if args.classifications is not None:
        classifications = (
            pd.read_parquet(args.classifications)
            if args.classifications.suffix == ".parquet"
            else pd.read_csv(args.classifications)
        )
    write_current_universe_snapshot(
        assets, bars, cutoff, policy, root / "universe", classifications
    )
    status["status"] = "COMPLETE"
    _write_status(root / "download_status.json", status)
    print("COMPLETE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
