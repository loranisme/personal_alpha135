from __future__ import annotations

import pandas as pd
import pytest

from sec_edgar_pipeline.issuer_seed import (
    load_ticker_cik_index,
    normalize_cik,
    resolve_universe_ciks,
)


def test_normalize_cik_zero_pads_and_rejects_non_numeric_values():
    assert normalize_cik("320193") == "0000320193"
    assert normalize_cik(320193) == "0000320193"
    with pytest.raises(ValueError, match="INVALID_CIK"):
        normalize_cik("32A193")


def test_official_ticker_index_resolves_each_download_cik_once():
    payload = {
        "data": [["0000320193", "Apple Inc.", "AAPL", "Nasdaq"]],
        "fields": ["cik", "name", "ticker", "exchange"],
    }
    index = load_ticker_cik_index(payload, "a" * 64)
    universe = pd.DataFrame(
        [
            {
                "security_id": "sec-aapl",
                "ticker": "AAPL",
                "exchange": "NASDAQ",
                "security_type": "COMMON_STOCK",
            }
        ]
    )
    resolved, unresolved = resolve_universe_ciks(universe, index)
    assert unresolved.empty
    assert resolved.iloc[0].issuer_cik == "0000320193"
    assert resolved.iloc[0].ticker_cik_source_hash == "a" * 64


def test_resolution_normalizes_share_class_punctuation_and_exchange_aliases():
    payload = {
        "data": [[1067983, "Berkshire Hathaway", "BRK-B", "NYSE"]],
        "fields": ["cik", "name", "ticker", "exchange"],
    }
    index = load_ticker_cik_index(payload, "b" * 64)
    universe = pd.DataFrame(
        [{"security_id": "brkb", "ticker": "BRK.B", "exchange": "XNYS", "security_type": "COMMON_STOCK"}]
    )
    resolved, unresolved = resolve_universe_ciks(universe, index)
    assert unresolved.empty
    assert resolved["issuer_cik"].tolist() == ["0001067983"]


def test_missing_and_conflicting_matches_are_unresolved():
    payload = {
        "data": [
            [1, "One", "DUP", "NYSE"],
            [2, "Two", "DUP", "NYSE"],
        ],
        "fields": ["cik", "name", "ticker", "exchange"],
    }
    index = load_ticker_cik_index(payload, "c" * 64)
    universe = pd.DataFrame(
        [
            {"security_id": "dup", "ticker": "DUP", "exchange": "NYSE", "security_type": "COMMON_STOCK"},
            {"security_id": "missing", "ticker": "NONE", "exchange": "NYSE", "security_type": "COMMON_STOCK"},
        ]
    )
    resolved, unresolved = resolve_universe_ciks(universe, index)
    assert resolved.empty
    assert set(unresolved["resolution_status"]) == {"CONFLICTING_MATCH", "MISSING_MATCH"}


def test_duplicate_index_rows_fail_closed_as_conflicting():
    payload = {
        "data": [
            [1, "One", "DUP", "NYSE"],
            [1, "One", "DUP", "NYSE"],
        ],
        "fields": ["cik", "name", "ticker", "exchange"],
    }
    index = load_ticker_cik_index(payload, "d" * 64)
    universe = pd.DataFrame(
        [{"security_id": "dup", "ticker": "DUP", "exchange": "NYSE", "security_type": "COMMON_STOCK"}]
    )
    resolved, unresolved = resolve_universe_ciks(universe, index)
    assert resolved.empty
    assert unresolved.iloc[0].resolution_status == "CONFLICTING_MATCH"
    assert unresolved.iloc[0].candidate_count == 2
