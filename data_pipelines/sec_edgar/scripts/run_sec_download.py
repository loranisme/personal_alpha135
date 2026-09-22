#!/usr/bin/env python3
"""Download a finite SEC Company Facts universe into immutable raw storage."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

from sec_edgar_pipeline.client import SecClient
from sec_edgar_pipeline.issuer_seed import load_ticker_cik_index, resolve_universe_ciks
from sec_edgar_pipeline.raw_store import (
    download_ticker_cik_index,
    download_universe,
    read_raw_artifact,
)


ALLOWED_RAW_ROOT_ENTRIES = {
    "objects",
    "download_ledger.jsonl",
    "resolved_universe_ciks.csv",
    "unresolved_universe_ciks.csv",
    "download_summary.json",
}


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("--observed-at must include a timezone")
    return parsed


def _validate_raw_root(path: Path) -> None:
    if not path.exists():
        return
    foreign = sorted(item.name for item in path.iterdir() if item.name not in ALLOWED_RAW_ROOT_ENTRIES)
    if foreign:
        raise ValueError(f"RAW_ROOT_NOT_EMPTY_OR_RESUMABLE:{','.join(foreign)}")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe-csv", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--user-agent-env", required=True)
    parser.add_argument("--observed-at", type=_parse_datetime, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _validate_raw_root(args.raw_root)
    user_agent = os.environ.get(args.user_agent_env, "").strip()
    if not user_agent:
        raise ValueError(f"SEC_USER_AGENT_ENV_MISSING:{args.user_agent_env}")
    args.raw_root.mkdir(parents=True, exist_ok=True)
    universe = pd.read_csv(args.universe_csv, dtype=str)
    client = SecClient(requests.Session(), user_agent)
    index_artifact = download_ticker_cik_index(client, args.raw_root, args.observed_at)
    index_payload = json.loads(read_raw_artifact(args.raw_root, index_artifact))
    index = load_ticker_cik_index(index_payload, index_artifact.sha256)
    resolved, unresolved = resolve_universe_ciks(universe, index)
    resolved.to_csv(args.raw_root / "resolved_universe_ciks.csv", index=False)
    unresolved.to_csv(args.raw_root / "unresolved_universe_ciks.csv", index=False)
    artifacts = download_universe(client, args.raw_root, resolved, args.observed_at)
    statuses = pd.concat(
        [artifacts["http_status"], pd.Series([index_artifact.http_status])],
        ignore_index=True,
    )
    status_counts = {
        str(int(status)): int(count)
        for status, count in statuses.value_counts().sort_index().items()
    }
    ledger = args.raw_root / "download_ledger.jsonl"
    summary = {
        "requested_cik_count": int(resolved["issuer_cik"].nunique()),
        "completed_cik_count": int(artifacts.loc[artifacts["source_type"] == "companyfacts", "cik"].nunique()),
        "unresolved_ticker_count": int(len(unresolved)),
        "http_status_counts": status_counts,
        "raw_manifest_hash": _sha256(ledger),
    }
    summary_path = args.raw_root / "download_summary.json"
    summary_path.write_text(
        json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
