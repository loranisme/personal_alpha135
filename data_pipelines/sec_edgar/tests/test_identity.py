from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from sec_edgar_pipeline.identity import (
    IDENTITY_COLUMNS,
    MAPPING_STATES,
    build_cik_security_map,
    require_usable_identity,
)


AS_OF = pd.Timestamp("2026-09-15T09:24:13Z")
FIXTURES = Path(__file__).parent / "fixtures" / "sec"


def _submission(
    cik: str,
    tickers: list[str],
    exchanges: list[str],
    *,
    forms: list[str] | None = None,
    source_hash: str = "sha256:fixture",
) -> dict[str, object]:
    return {
        "cik": cik,
        "entityType": "operating",
        "tickers": tickers,
        "exchanges": exchanges,
        "filings": {"recent": {"form": forms or ["10-K", "10-Q"]}},
        "_raw_source_hash": source_hash,
    }


def _universe_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "security_id": "sec-aapl",
        "ticker": "AAPL",
        "exchange": "NASDAQ",
        "security_type": "COMMON_STOCK",
        "domicile": "US",
        "as_of": AS_OF.isoformat(),
    }
    row.update(overrides)
    return row


def test_single_common_share_maps_to_verified_single_class() -> None:
    universe = pd.DataFrame([_universe_row()])
    submissions = {
        "0000320193": _submission(
            "0000320193", ["AAPL"], ["Nasdaq"], source_hash="sha256:aapl"
        )
    }

    result = build_cik_security_map(universe, submissions, AS_OF)

    assert list(result.columns) == list(IDENTITY_COLUMNS)
    assert result.iloc[0].mapping_status == "VERIFIED_SINGLE_CLASS"
    assert result.iloc[0].issuer_cik == "0000320193"
    assert result.iloc[0].sec_ticker == "AAPL"
    assert result.iloc[0].mapping_source == "SEC_SUBMISSIONS_TICKER_EXCHANGE"
    assert result.iloc[0].source_submission_hash == "sha256:aapl"
    assert pd.Timestamp(result.iloc[0].effective_from) == AS_OF
    assert pd.isna(result.iloc[0].effective_to)
    assert require_usable_identity(result, "sec-aapl", "ENTITY").issuer_cik == "0000320193"
    assert require_usable_identity(result, "sec-aapl", "PER_SHARE").issuer_cik == "0000320193"


def test_provider_dot_and_sec_hyphen_are_normalized_only_for_comparison() -> None:
    universe = pd.DataFrame(
        [_universe_row(security_id="sec-brkb", ticker="BRK.B", exchange="NYSE")]
    )
    payload = json.loads((FIXTURES / "submissions_multiclass.json").read_text())

    result = build_cik_security_map(
        universe, {"0001067983": payload}, AS_OF
    )

    row = result.iloc[0]
    assert row.ticker == "BRK.B"
    assert row.sec_ticker == "BRK-B"
    assert row.share_class == "B"
    assert row.mapping_status == "VERIFIED_ENTITY_LEVEL_ONLY"
    assert require_usable_identity(result, "sec-brkb", "ENTITY").issuer_cik == "0001067983"
    with pytest.raises(ValueError, match="IDENTITY_NOT_USABLE:PER_SHARE"):
        require_usable_identity(result, "sec-brkb", "PER_SHARE")


def test_two_project_securities_for_one_cik_block_share_class_usage() -> None:
    universe = pd.DataFrame(
        [
            _universe_row(security_id="sec-a", ticker="BRK.A", exchange="NYSE"),
            _universe_row(security_id="sec-b", ticker="BRK.B", exchange="NYSE"),
        ]
    )
    payload = json.loads((FIXTURES / "submissions_multiclass.json").read_text())

    result = build_cik_security_map(
        universe, {"0001067983": payload}, AS_OF
    )

    assert set(result.mapping_status) == {"AMBIGUOUS_MULTI_CLASS"}
    assert set(result.issuer_cik) == {"0001067983"}
    with pytest.raises(ValueError, match="IDENTITY_NOT_USABLE:PER_SHARE"):
        require_usable_identity(result, "sec-a", "PER_SHARE")
    with pytest.raises(ValueError, match="IDENTITY_NOT_USABLE:ENTITY"):
        require_usable_identity(result, "sec-a", "ENTITY")


@pytest.mark.parametrize(
    ("row_overrides", "submissions", "expected_state", "reason"),
    [
        (
            {"security_id": "sec-adr", "ticker": "BABA", "security_type": "ADR"},
            {"0001577552": _submission("0001577552", ["BABA"], ["NYSE"], forms=["20-F"])},
            "DEFERRED_ISSUER_SCOPE",
            "UNSUPPORTED_SECURITY_TYPE:ADR",
        ),
        (
            {"security_id": "sec-foreign", "ticker": "SHOP", "domicile": "CA"},
            {"0001594805": _submission("0001594805", ["SHOP"], ["NYSE"], forms=["40-F"])},
            "DEFERRED_ISSUER_SCOPE",
            "NON_US_DOMICILE:CA",
        ),
        (
            {"security_id": "sec-foreign-form", "ticker": "XYZ"},
            {"0000000001": _submission("0000000001", ["XYZ"], ["NASDAQ"], forms=["20-F"])},
            "DEFERRED_ISSUER_SCOPE",
            "FOREIGN_FILING_FORM:20-F",
        ),
        (
            {"security_id": "sec-etf", "ticker": "SPY", "security_type": "ETF"},
            {"0000884394": _submission("0000884394", ["SPY"], ["NYSE"])},
            "DEFERRED_ISSUER_SCOPE",
            "UNSUPPORTED_SECURITY_TYPE:ETF",
        ),
        (
            {"security_id": "sec-missing", "ticker": None},
            {},
            "UNRESOLVED",
            "MISSING_TICKER",
        ),
        (
            {"security_id": "sec-conflict", "ticker": "MSFT", "exchange": "NYSE"},
            {"0000789019": _submission("0000789019", ["MSFT"], ["Nasdaq"])},
            "UNRESOLVED",
            "EXCHANGE_CONFLICT",
        ),
    ],
)
def test_blocked_scope_and_unresolved_cases_are_explicit(
    row_overrides: dict[str, object],
    submissions: dict[str, dict[str, object]],
    expected_state: str,
    reason: str,
) -> None:
    result = build_cik_security_map(
        pd.DataFrame([_universe_row(**row_overrides)]), submissions, AS_OF
    )

    row = result.iloc[0]
    assert row.mapping_status == expected_state
    assert reason in row.blocking_reasons
    assert row.mapping_status in MAPPING_STATES


def test_missing_domicile_fails_closed_instead_of_assuming_us() -> None:
    universe = pd.DataFrame(
        [
            {
                "security_id": "sec-aapl",
                "ticker": "AAPL",
                "exchange": "NASDAQ",
                "security_type": "COMMON_STOCK",
                "as_of": AS_OF.isoformat(),
            }
        ]
    )
    submissions = {
        "0000320193": _submission(
            "0000320193", ["AAPL"], ["NASDAQ"], source_hash="sha256:aapl"
        )
    }

    result = build_cik_security_map(universe, submissions, AS_OF)

    row = result.iloc[0]
    assert row.mapping_status == "DEFERRED_ISSUER_SCOPE"
    assert row.blocking_reasons == "MISSING_DOMICILE"
    assert row.issuer_cik == "0000320193"
    assert row.source_submission_hash == "sha256:aapl"


def test_scope_block_preserves_deterministic_submission_lineage() -> None:
    universe = pd.DataFrame(
        [
            _universe_row(
                security_id="sec-adr",
                ticker="BABA",
                exchange="NYSE",
                security_type="ADR",
            )
        ]
    )
    submissions = {
        "0001577552": _submission(
            "0001577552",
            ["BABA"],
            ["NYSE"],
            forms=["20-F"],
            source_hash="sha256:baba-submission",
        )
    }

    first = build_cik_security_map(universe, submissions, AS_OF).iloc[0]
    second = build_cik_security_map(universe, submissions, AS_OF).iloc[0]

    assert first.mapping_status == "DEFERRED_ISSUER_SCOPE"
    assert first.issuer_cik == "0001577552"
    assert first.sec_ticker == "BABA"
    assert first.source_submission_hash == "sha256:baba-submission"
    assert first.source_submission_hash == second.source_submission_hash


def test_conflicting_issuer_matches_are_unresolved_instead_of_guessed() -> None:
    submissions = {
        "0000000001": _submission("0000000001", ["DUP"], ["NYSE"]),
        "0000000002": _submission("0000000002", ["DUP"], ["NYSE"]),
    }
    universe = pd.DataFrame(
        [_universe_row(security_id="sec-dup", ticker="DUP", exchange="NYSE")]
    )

    result = build_cik_security_map(universe, submissions, AS_OF)

    assert result.iloc[0].mapping_status == "UNRESOLVED"
    assert "MULTIPLE_ISSUER_MATCHES" in result.iloc[0].blocking_reasons
    assert pd.isna(result.iloc[0].issuer_cik)


def test_output_sort_and_effective_dates_are_deterministic() -> None:
    universe = pd.DataFrame(
        [
            _universe_row(
                security_id="sec-z",
                ticker="ZZZ",
                effective_from="2024-01-02T00:00:00Z",
                effective_to="2025-01-02T00:00:00Z",
            ),
            _universe_row(
                security_id="sec-a",
                ticker="AAA",
                as_of="2025-02-03T00:00:00Z",
            ),
        ]
    )
    submissions = {
        "0000000001": _submission("0000000001", ["ZZZ"], ["NASDAQ"]),
        "0000000002": _submission("0000000002", ["AAA"], ["NASDAQ"]),
    }

    result = build_cik_security_map(universe, submissions, AS_OF)

    assert result.security_id.tolist() == ["sec-a", "sec-z"]
    assert pd.Timestamp(result.loc[result.security_id == "sec-z", "effective_from"].iloc[0]) == pd.Timestamp("2024-01-02T00:00:00Z")
    assert pd.Timestamp(result.loc[result.security_id == "sec-z", "effective_to"].iloc[0]) == pd.Timestamp("2025-01-02T00:00:00Z")


def test_sequential_project_securities_for_same_issuer_are_not_marked_ambiguous() -> None:
    universe = pd.DataFrame(
        [
            _universe_row(
                security_id="sec-old",
                ticker="XYZ",
                effective_from="2019-01-02T00:00:00Z",
                effective_to="2021-01-04T00:00:00Z",
            ),
            _universe_row(
                security_id="sec-new",
                ticker="XYZ",
                effective_from="2021-01-04T00:00:00Z",
                effective_to=None,
            ),
        ]
    )
    submissions = {
        "0000000001": _submission("0000000001", ["XYZ"], ["NASDAQ"])
    }

    result = build_cik_security_map(universe, submissions, AS_OF)

    assert set(result.mapping_status) == {"VERIFIED_SINGLE_CLASS"}
    assert all(
        "MULTIPLE_PROJECT_SECURITIES_FOR_ISSUER" not in reasons
        for reasons in result.blocking_reasons
    )


def test_unique_security_id_still_rejects_end_not_after_start() -> None:
    universe = pd.DataFrame(
        [
            _universe_row(
                security_id="sec-unique",
                ticker="ONE",
                effective_from="2025-01-02T00:00:00Z",
                effective_to="2025-01-02T00:00:00Z",
            )
        ]
    )

    with pytest.raises(
        ValueError, match="IDENTITY_INVALID_EFFECTIVE_INTERVAL:sec-unique"
    ):
        build_cik_security_map(universe, {}, AS_OF)


def test_invalid_inputs_fail_closed() -> None:
    with pytest.raises(ValueError, match="IDENTITY_UNIVERSE_MISSING_COLUMNS:.*ticker"):
        build_cik_security_map(pd.DataFrame([{"security_id": "x"}]), {}, AS_OF)

    with pytest.raises(ValueError, match="IDENTITY_DUPLICATE_SECURITY_ID:x"):
        build_cik_security_map(
            pd.DataFrame(
                [
                    _universe_row(security_id="x"),
                    _universe_row(security_id="x"),
                ]
            ),
            {},
            AS_OF,
        )

    mapping = pd.DataFrame(columns=IDENTITY_COLUMNS)
    with pytest.raises(ValueError, match="IDENTITY_SECURITY_NOT_FOUND:missing"):
        require_usable_identity(mapping, "missing", "ENTITY")
    with pytest.raises(ValueError, match="IDENTITY_UNKNOWN_USAGE:UNKNOWN"):
        require_usable_identity(
            pd.DataFrame(
                [{column: None for column in IDENTITY_COLUMNS}]
            ).assign(security_id="x", mapping_status="VERIFIED_SINGLE_CLASS"),
            "x",
            "UNKNOWN",
        )
