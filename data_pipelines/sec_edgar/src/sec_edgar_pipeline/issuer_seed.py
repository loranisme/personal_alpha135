"""Official SEC ticker-to-CIK bootstrap and universe resolution."""

from __future__ import annotations

import re
from typing import Any, Mapping

import pandas as pd


INDEX_REQUIRED_FIELDS = ("cik", "name", "ticker", "exchange")
UNIVERSE_REQUIRED_COLUMNS = ("security_id", "ticker", "exchange", "security_type")


def normalize_cik(value: str | int) -> str:
    if isinstance(value, bool):
        raise ValueError(f"INVALID_CIK:{value}")
    text = str(value).strip()
    if not text.isascii() or not text.isdigit() or len(text) > 10:
        raise ValueError(f"INVALID_CIK:{value}")
    return text.zfill(10)


def _ticker_key(value: Any) -> str:
    text = str(value).strip().upper()
    return re.sub(r"[-/]", ".", text)


def _exchange_key(value: Any) -> str:
    compact = re.sub(r"[^A-Z0-9]", "", str(value).strip().upper())
    aliases = {
        "XNYS": "NYSE",
        "NEWYORKSTOCKEXCHANGE": "NYSE",
        "XNAS": "NASDAQ",
        "NASDAQGLOBALSELECTMARKET": "NASDAQ",
        "NASDAQGLOBALMARKET": "NASDAQ",
        "NASDAQCAPITALMARKET": "NASDAQ",
        "ARCX": "NYSEARCA",
        "NYSEARCA": "NYSEARCA",
        "BATS": "CBOEBZX",
        "CBOEBZX": "CBOEBZX",
    }
    return aliases.get(compact, compact)


def load_ticker_cik_index(
    payload: Mapping[str, Any], raw_hash: str
) -> pd.DataFrame:
    if not re.fullmatch(r"[0-9a-fA-F]{64}", str(raw_hash)):
        raise ValueError("TICKER_CIK_INDEX_INVALID_RAW_HASH")
    fields = payload.get("fields")
    data = payload.get("data")
    if not isinstance(fields, list) or not isinstance(data, list):
        raise ValueError("TICKER_CIK_INDEX_INVALID_SHAPE")
    if any(field not in fields for field in INDEX_REQUIRED_FIELDS):
        raise ValueError("TICKER_CIK_INDEX_MISSING_FIELDS")
    width = len(fields)
    records: list[dict[str, Any]] = []
    for position, row in enumerate(data):
        if not isinstance(row, list) or len(row) != width:
            raise ValueError(f"TICKER_CIK_INDEX_INVALID_ROW:{position}")
        source = dict(zip(fields, row, strict=True))
        ticker = str(source["ticker"]).strip().upper()
        exchange = str(source["exchange"]).strip()
        if not ticker:
            raise ValueError(f"TICKER_CIK_INDEX_EMPTY_TICKER:{position}")
        records.append(
            {
                "issuer_cik": normalize_cik(source["cik"]),
                "company_name": str(source["name"]).strip(),
                "ticker": ticker,
                "exchange": exchange,
                "ticker_key": _ticker_key(ticker),
                "exchange_key": _exchange_key(exchange),
                "ticker_cik_source_hash": str(raw_hash).lower(),
            }
        )
    columns = [
        "issuer_cik",
        "company_name",
        "ticker",
        "exchange",
        "ticker_key",
        "exchange_key",
        "ticker_cik_source_hash",
    ]
    return pd.DataFrame.from_records(records, columns=columns)


def resolve_universe_ciks(
    universe: pd.DataFrame, ticker_cik_index: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    missing = sorted(set(UNIVERSE_REQUIRED_COLUMNS).difference(universe.columns))
    if missing:
        raise ValueError(f"UNIVERSE_MISSING_COLUMNS:{','.join(missing)}")
    required_index = {
        "issuer_cik",
        "company_name",
        "ticker",
        "exchange",
        "ticker_key",
        "exchange_key",
        "ticker_cik_source_hash",
    }
    missing_index = sorted(required_index.difference(ticker_cik_index.columns))
    if missing_index:
        raise ValueError(f"TICKER_CIK_INDEX_MISSING_COLUMNS:{','.join(missing_index)}")

    resolved_rows: list[dict[str, Any]] = []
    unresolved_rows: list[dict[str, Any]] = []
    for source in universe.to_dict(orient="records"):
        ticker_key = _ticker_key(source["ticker"])
        exchange_key = _exchange_key(source["exchange"])
        ticker_matches = ticker_cik_index[ticker_cik_index["ticker_key"] == ticker_key]
        exact = ticker_matches[ticker_matches["exchange_key"] == exchange_key]
        candidates = exact
        if len(candidates) == 1:
            candidate = candidates.iloc[0]
            resolved_rows.append(
                {
                    **source,
                    "issuer_cik": candidate["issuer_cik"],
                    "sec_company_name": candidate["company_name"],
                    "sec_ticker": candidate["ticker"],
                    "sec_exchange": candidate["exchange"],
                    "ticker_cik_source_hash": candidate["ticker_cik_source_hash"],
                    "resolution_status": "RESOLVED",
                }
            )
            continue
        status = "MISSING_MATCH" if ticker_matches.empty else "CONFLICTING_MATCH"
        unresolved_rows.append(
            {
                **source,
                "resolution_status": status,
                "candidate_count": int(len(candidates)),
            }
        )

    resolved_columns = [
        *universe.columns,
        "issuer_cik",
        "sec_company_name",
        "sec_ticker",
        "sec_exchange",
        "ticker_cik_source_hash",
        "resolution_status",
    ]
    unresolved_columns = [*universe.columns, "resolution_status", "candidate_count"]
    return (
        pd.DataFrame.from_records(resolved_rows, columns=resolved_columns),
        pd.DataFrame.from_records(unresolved_rows, columns=unresolved_columns),
    )
