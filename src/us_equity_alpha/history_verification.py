"""Load hashed Tiingo raw-bar snapshots as engineering verification panels."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import requests


FIELDS = ("open", "high", "low", "close", "volume")


def download_tiingo_benchmark(
    output_path,
    *,
    token,
    start_date,
    end_date,
    session=None,
):
    """Download one SPY daily-bar snapshot without persisting credentials."""
    session = session or requests.Session()
    response = session.get(
        "https://api.tiingo.com/tiingo/daily/SPY/prices",
        params={"startDate": start_date, "endDate": end_date},
        headers={"Authorization": f"Token {token}"},
        timeout=45,
        allow_redirects=False,
    )
    if response.status_code != 200:
        raise ValueError(f"TIINGO_BENCHMARK_HTTP_{response.status_code}")
    try:
        rows = response.json()
    except ValueError as exc:
        raise ValueError("TIINGO_BENCHMARK_INVALID_JSON") from exc
    required = set(FIELDS) | {"date"}
    if not isinstance(rows, list) or not rows or any(
        not isinstance(row, dict) or not required <= set(row) for row in rows
    ):
        raise ValueError("TIINGO_BENCHMARK_INVALID_SCHEMA")
    dates = pd.to_datetime([row["date"] for row in rows], utc=True)
    if dates.has_duplicates or not dates.is_monotonic_increasing:
        raise ValueError("TIINGO_BENCHMARK_SESSION_ORDER_INVALID")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(rows, separators=(",", ":")) + "\n"
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(payload)
    temporary.replace(output_path)
    digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
    output_path.with_suffix(".sha256").write_text(digest + "\n")
    return {
        "provider": "tiingo_eod",
        "symbol": "SPY",
        "start_date": dates.min().date().isoformat(),
        "end_date": dates.max().date().isoformat(),
        "rows": len(rows),
        "sha256": digest,
        "historical_pit_verified": False,
        "source_kind": "DEVELOPMENT_DIAGNOSTIC",
    }


def _load_hashed_json(path: Path, symbol: str) -> pd.DataFrame:
    sidecar = path.with_suffix(".sha256")
    if not path.exists() or not sidecar.exists():
        raise ValueError(f"RAW_EVIDENCE_MISSING:{symbol}")
    observed = hashlib.sha256(path.read_bytes()).hexdigest()
    if observed != sidecar.read_text().strip():
        raise ValueError(f"RAW_HASH_MISMATCH:{symbol}")
    frame = pd.DataFrame(json.loads(path.read_text()))
    missing = sorted((set(FIELDS) | {"date"}) - set(frame.columns))
    if missing:
        raise ValueError(f"RAW_FIELDS_MISSING:{symbol}:" + ",".join(missing))
    frame.index = pd.to_datetime(frame["date"], utc=True).dt.normalize()
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError(f"RAW_SESSION_ORDER_INVALID:{symbol}")
    return frame


def load_tiingo_history_snapshot(root, limit=50, benchmark_json=None):
    """Return aligned OHLCV panels; this never certifies PIT coverage."""
    root = Path(root)
    universe = pd.read_csv(root / "universe.csv")
    if "Symbol" not in universe or limit < 1:
        raise ValueError("HISTORY_UNIVERSE_INVALID")
    symbols = universe["Symbol"].head(limit).tolist()
    if len(symbols) != limit or len(symbols) != len(set(symbols)):
        raise ValueError("HISTORY_UNIVERSE_INCOMPLETE_OR_DUPLICATE")
    frames = {
        symbol: _load_hashed_json(root / "raw" / f"{symbol}.json", symbol)
        for symbol in symbols
    }
    benchmark_sha256 = None
    if benchmark_json is not None:
        if "SPY" in frames:
            raise ValueError("BENCHMARK_DUPLICATE:SPY")
        benchmark_path = Path(benchmark_json)
        frames["SPY"] = _load_hashed_json(benchmark_path, "SPY")
        benchmark_sha256 = hashlib.sha256(benchmark_path.read_bytes()).hexdigest()
    index = pd.DatetimeIndex(sorted(set().union(*(frame.index for frame in frames.values()))))
    fields = {
        field: pd.DataFrame(
            {symbol: frame[field].reindex(index) for symbol, frame in frames.items()},
            index=index,
        )
        for field in FIELDS
    }
    evidence = {
        "source_kind": "DEVELOPMENT_DIAGNOSTIC",
        "provider": "tiingo_eod",
        "symbols": len(frames),
        "security_symbols": len(symbols),
        "benchmark_included": "SPY" in frames,
        "benchmark_sha256": benchmark_sha256,
        "start_date": index.min().date().isoformat(),
        "end_date": index.max().date().isoformat(),
        "sessions": len(index),
        "historical_pit_verified": False,
        "survivorship_bias": True,
        "formal_coverage_evidence": False,
    }
    return fields, evidence
