"""Fail-closed issuer-to-security identity resolution for SEC data.

SEC submissions identify issuers.  The project trades securities, so this
module deliberately keeps entity-level and share-class usability separate.
Ticker punctuation is normalized for comparison only; source values remain in
the resulting bridge for auditability.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import re
from typing import Any, Final

import pandas as pd


MAPPING_STATES: Final[frozenset[str]] = frozenset(
    {
        "VERIFIED_SINGLE_CLASS",
        "VERIFIED_ENTITY_LEVEL_ONLY",
        "AMBIGUOUS_MULTI_CLASS",
        "UNRESOLVED",
        "DEFERRED_ISSUER_SCOPE",
    }
)

IDENTITY_COLUMNS: Final[tuple[str, ...]] = (
    "security_id",
    "issuer_cik",
    "ticker",
    "sec_ticker",
    "share_class",
    "security_type",
    "exchange",
    "effective_from",
    "effective_to",
    "mapping_source",
    "mapping_status",
    "blocking_reasons",
    "source_submission_hash",
)

_REQUIRED_UNIVERSE_COLUMNS: Final[frozenset[str]] = frozenset(
    {"security_id", "ticker", "exchange", "security_type"}
)
_SUPPORTED_SECURITY_TYPES: Final[frozenset[str]] = frozenset(
    {"COMMON_STOCK", "REIT"}
)
_FOREIGN_FORMS: Final[frozenset[str]] = frozenset(
    {"20-F", "20-F/A", "40-F", "40-F/A"}
)
_US_DOMICILES: Final[frozenset[str]] = frozenset(
    {"US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"}
)
_EXCHANGE_ALIASES: Final[dict[str, str]] = {
    "NASDAQ": "NASDAQ",
    "NASDAQ GLOBAL SELECT": "NASDAQ",
    "NASDAQ GLOBAL MARKET": "NASDAQ",
    "NASDAQ CAPITAL MARKET": "NASDAQ",
    "NYSE": "NYSE",
    "NEW YORK STOCK EXCHANGE": "NYSE",
    "NYSE ARCA": "NYSEARCA",
    "NYSEARCA": "NYSEARCA",
    "NYSE AMERICAN": "NYSEAMERICAN",
    "NYSEAMERICAN": "NYSEAMERICAN",
}
_ENTITY_USAGES: Final[frozenset[str]] = frozenset(
    {"ENTITY", "ENTITY_LEVEL", "STATEMENT"}
)
_SHARE_CLASS_USAGES: Final[frozenset[str]] = frozenset(
    {
        "PER_SHARE",
        "SHARES",
        "SHARE_COUNT",
        "SHARES_OUTSTANDING",
        "MARKET_CAP",
        "ENTERPRISE_VALUE",
    }
)


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        return False
    return bool(missing) if isinstance(missing, bool) else False


def _text(value: Any) -> str:
    return "" if _is_missing(value) else str(value).strip()


def _first_nonmissing(*values: Any) -> Any:
    for value in values:
        if not _is_missing(value) and _text(value):
            return value
    return None


def _normalize_cik(value: Any) -> str:
    raw = _text(value).upper().removeprefix("CIK")
    if not raw or not raw.isdigit():
        raise ValueError(f"IDENTITY_INVALID_CIK:{value}")
    return raw.zfill(10)


def _normalize_ticker(value: Any) -> str:
    """Normalize only SEC hyphens and provider dots for exact comparison."""

    raw = _text(value).upper()
    return re.sub(r"[.-]", "-", raw)


def _normalize_exchange(value: Any) -> str:
    raw = re.sub(r"\s+", " ", _text(value).upper())
    return _EXCHANGE_ALIASES.get(raw, raw.replace(" ", ""))


def _as_utc_timestamp(value: Any, *, error_code: str) -> pd.Timestamp:
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{error_code}:{value}") from exc
    if pd.isna(timestamp):
        raise ValueError(f"{error_code}:{value}")
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp


def _optional_utc_timestamp(value: Any, *, error_code: str) -> pd.Timestamp | pd.NaT:
    if _is_missing(value) or not _text(value):
        return pd.NaT
    return _as_utc_timestamp(value, error_code=error_code)


def _canonical_payload_hash(payload: Mapping[str, Any]) -> str:
    filtered = {
        str(key): value
        for key, value in payload.items()
        if str(key) not in {"_raw_source_hash", "source_submission_hash", "raw_source_hash"}
    }
    encoded = json.dumps(
        filtered,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _source_hash(payload: Mapping[str, Any]) -> str:
    for key in ("_raw_source_hash", "source_submission_hash", "raw_source_hash", "sha256"):
        value = _text(payload.get(key))
        if value:
            return value
    return _canonical_payload_hash(payload)


def _as_string_list(value: Any) -> list[str]:
    if value is None or isinstance(value, (str, bytes)):
        return [] if value is None else [_text(value)]
    if not isinstance(value, Sequence):
        return []
    return [_text(item) for item in value]


def _recent_forms(payload: Mapping[str, Any]) -> set[str]:
    filings = payload.get("filings")
    if not isinstance(filings, Mapping):
        return set()
    recent = filings.get("recent")
    if not isinstance(recent, Mapping):
        return set()
    return {form.upper() for form in _as_string_list(recent.get("form")) if form}


def _submission_pairs(payload: Mapping[str, Any]) -> list[tuple[str, str]]:
    tickers = _as_string_list(payload.get("tickers"))
    exchanges = _as_string_list(payload.get("exchanges"))
    return [
        (ticker, exchanges[index] if index < len(exchanges) else "")
        for index, ticker in enumerate(tickers)
        if ticker
    ]


def _scope_reasons(row: Mapping[str, Any], matching_payloads: Sequence[Mapping[str, Any]]) -> list[str]:
    reasons: list[str] = []
    security_type = _text(row.get("security_type")).upper()
    if security_type not in _SUPPORTED_SECURITY_TYPES:
        reasons.append(f"UNSUPPORTED_SECURITY_TYPE:{security_type or 'MISSING'}")

    domicile = _text(_first_nonmissing(row.get("domicile"), row.get("country"))).upper()
    if not domicile:
        reasons.append("MISSING_DOMICILE")
    elif domicile not in _US_DOMICILES:
        reasons.append(f"NON_US_DOMICILE:{domicile or 'MISSING'}")

    if _truthy(row.get("is_adr")) or _contains_adr_evidence(row):
        reasons.append("ADR_EVIDENCE")

    foreign_forms = sorted(
        {
            form
            for payload in matching_payloads
            for form in _recent_forms(payload)
            if form in _FOREIGN_FORMS
        }
    )
    reasons.extend(f"FOREIGN_FILING_FORM:{form}" for form in foreign_forms)
    return list(dict.fromkeys(reasons))


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).upper() in {"1", "TRUE", "YES", "Y"}


def _contains_adr_evidence(row: Mapping[str, Any]) -> bool:
    security_type = _text(row.get("security_type")).upper()
    name = _text(_first_nonmissing(row.get("security_name"), row.get("name"))).upper()
    return security_type in {"ADR", "ADS"} or any(
        marker in name
        for marker in ("AMERICAN DEPOSITARY", "DEPOSITARY RECEIPT", " ADR", " ADS")
    )


def _share_class(project_ticker: str, sec_ticker: str) -> str | pd.NA:
    candidate = _text(sec_ticker) or _text(project_ticker)
    match = re.search(r"[.-]([A-Z0-9]+)$", candidate.upper())
    return match.group(1) if match else pd.NA


def _validate_universe(universe: pd.DataFrame) -> None:
    missing = sorted(_REQUIRED_UNIVERSE_COLUMNS.difference(universe.columns))
    if missing:
        raise ValueError(f"IDENTITY_UNIVERSE_MISSING_COLUMNS:{','.join(missing)}")

    intervals_by_security: dict[
        str, list[tuple[pd.Timestamp, pd.Timestamp | pd.NaT]]
    ] = {}
    for _, row in universe.iterrows():
        security_id = _text(row.get("security_id"))
        start_value = _first_nonmissing(
            row.get("effective_from"), row.get("as_of")
        )
        start = _as_utc_timestamp(
            start_value, error_code="IDENTITY_INVALID_EFFECTIVE_FROM"
        )
        end = _optional_utc_timestamp(
            row.get("effective_to"), error_code="IDENTITY_INVALID_EFFECTIVE_TO"
        )
        if not pd.isna(end) and end <= start:
            raise ValueError(f"IDENTITY_INVALID_EFFECTIVE_INTERVAL:{security_id}")
        intervals_by_security.setdefault(security_id, []).append((start, end))

    for security_id, intervals in intervals_by_security.items():
        intervals.sort(key=lambda item: item[0])
        for index in range(1, len(intervals)):
            start = intervals[index][0]
            previous_end = intervals[index - 1][1]
            if pd.isna(previous_end) or start < previous_end:
                raise ValueError(f"IDENTITY_DUPLICATE_SECURITY_ID:{security_id}")


def _intervals_overlap(left: pd.Series, right: pd.Series) -> bool:
    left_start = pd.Timestamp(left["effective_from"])
    right_start = pd.Timestamp(right["effective_from"])
    left_end = left["effective_to"]
    right_end = right["effective_to"]
    left_before_right_ends = pd.isna(right_end) or left_start < pd.Timestamp(right_end)
    right_before_left_ends = pd.isna(left_end) or right_start < pd.Timestamp(left_end)
    return bool(left_before_right_ends and right_before_left_ends)


def _submission_index(
    submissions_by_cik: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for key, payload in submissions_by_cik.items():
        if not isinstance(payload, Mapping):
            raise ValueError(f"IDENTITY_INVALID_SUBMISSION_PAYLOAD:{key}")
        key_cik = _normalize_cik(key)
        payload_cik = _normalize_cik(payload.get("cik", key))
        if key_cik != payload_cik:
            raise ValueError(f"IDENTITY_CIK_CONFLICT:{key_cik}:{payload_cik}")
        entries.append(
            {
                "cik": key_cik,
                "payload": payload,
                "pairs": _submission_pairs(payload),
                "source_hash": _source_hash(payload),
            }
        )
    return entries


def build_cik_security_map(
    universe: pd.DataFrame,
    submissions_by_cik: Mapping[str, Mapping[str, Any]],
    as_of: pd.Timestamp,
) -> pd.DataFrame:
    """Build an effective-dated, auditable SEC issuer/security bridge.

    Matching is exact after the sole permitted ticker normalization (dot versus
    hyphen) and exchange alias normalization.  Any conflicting issuer or scope
    evidence is materialized as a blocked row rather than guessed.
    """

    if not isinstance(universe, pd.DataFrame):
        raise TypeError("IDENTITY_UNIVERSE_MUST_BE_DATAFRAME")
    _validate_universe(universe)
    default_as_of = _as_utc_timestamp(as_of, error_code="IDENTITY_INVALID_AS_OF")
    entries = _submission_index(submissions_by_cik)

    draft_rows: list[dict[str, Any]] = []
    for _, source_row in universe.iterrows():
        row = source_row.to_dict()
        security_id = _text(row.get("security_id"))
        if not security_id:
            raise ValueError("IDENTITY_MISSING_SECURITY_ID")
        ticker = _text(row.get("ticker"))
        normalized_ticker = _normalize_ticker(ticker)
        exchange = _text(row.get("exchange"))
        normalized_exchange = _normalize_exchange(exchange)

        ticker_entries = [
            entry
            for entry in entries
            if normalized_ticker
            and any(_normalize_ticker(sec_ticker) == normalized_ticker for sec_ticker, _ in entry["pairs"])
        ]
        scope_reasons = _scope_reasons(row, [entry["payload"] for entry in ticker_entries])

        compatible: list[tuple[dict[str, Any], str]] = []
        for entry in ticker_entries:
            for sec_ticker, sec_exchange in entry["pairs"]:
                if _normalize_ticker(sec_ticker) != normalized_ticker:
                    continue
                if _normalize_exchange(sec_exchange) == normalized_exchange:
                    compatible.append((entry, sec_ticker))

        base = {
            "security_id": security_id,
            "issuer_cik": pd.NA,
            "ticker": ticker or pd.NA,
            "sec_ticker": pd.NA,
            "share_class": pd.NA,
            "security_type": _text(row.get("security_type")).upper() or pd.NA,
            "exchange": exchange or pd.NA,
            "effective_from": _as_utc_timestamp(
                _first_nonmissing(
                    row.get("effective_from"), row.get("as_of"), default_as_of
                ),
                error_code="IDENTITY_INVALID_EFFECTIVE_FROM",
            ),
            "effective_to": _optional_utc_timestamp(
                row.get("effective_to"), error_code="IDENTITY_INVALID_EFFECTIVE_TO"
            ),
            "mapping_source": "SEC_SUBMISSIONS_TICKER_EXCHANGE",
            "mapping_status": "UNRESOLVED",
            "blocking_reasons": "",
            "source_submission_hash": pd.NA,
        }

        if scope_reasons:
            lineage_matches = compatible
            if not lineage_matches:
                lineage_matches = [
                    (entry, sec_ticker)
                    for entry in ticker_entries
                    for sec_ticker, _ in entry["pairs"]
                    if _normalize_ticker(sec_ticker) == normalized_ticker
                ]
            lineage_ciks = sorted({match[0]["cik"] for match in lineage_matches})
            if len(lineage_ciks) == 1:
                lineage_entry, lineage_ticker = next(
                    match
                    for match in lineage_matches
                    if match[0]["cik"] == lineage_ciks[0]
                )
                base.update(
                    {
                        "issuer_cik": lineage_ciks[0],
                        "sec_ticker": lineage_ticker,
                        "share_class": _share_class(ticker, lineage_ticker),
                        "source_submission_hash": lineage_entry["source_hash"],
                    }
                )
            base["mapping_status"] = "DEFERRED_ISSUER_SCOPE"
            base["blocking_reasons"] = "|".join(scope_reasons)
            draft_rows.append(base)
            continue
        if not normalized_ticker:
            base["blocking_reasons"] = "MISSING_TICKER"
            draft_rows.append(base)
            continue
        if not normalized_exchange:
            base["blocking_reasons"] = "MISSING_EXCHANGE"
            draft_rows.append(base)
            continue
        if not ticker_entries:
            base["blocking_reasons"] = "TICKER_NOT_FOUND"
            draft_rows.append(base)
            continue

        issuer_ciks = sorted({match[0]["cik"] for match in compatible})
        if not compatible:
            base["blocking_reasons"] = "EXCHANGE_CONFLICT"
            draft_rows.append(base)
            continue
        if len(issuer_ciks) != 1:
            base["blocking_reasons"] = "MULTIPLE_ISSUER_MATCHES"
            draft_rows.append(base)
            continue

        issuer_cik = issuer_ciks[0]
        entry, sec_ticker = next(match for match in compatible if match[0]["cik"] == issuer_cik)
        distinct_sec_tickers = {
            _normalize_ticker(candidate)
            for candidate, _ in entry["pairs"]
            if _normalize_ticker(candidate)
        }
        base.update(
            {
                "issuer_cik": issuer_cik,
                "sec_ticker": sec_ticker,
                "share_class": _share_class(ticker, sec_ticker),
                "mapping_status": (
                    "VERIFIED_SINGLE_CLASS"
                    if len(distinct_sec_tickers) == 1
                    else "VERIFIED_ENTITY_LEVEL_ONLY"
                ),
                "blocking_reasons": (
                    "" if len(distinct_sec_tickers) == 1 else "ISSUER_HAS_MULTIPLE_SEC_TICKERS"
                ),
                "source_submission_hash": entry["source_hash"],
            }
        )
        draft_rows.append(base)

    result = pd.DataFrame(draft_rows, columns=IDENTITY_COLUMNS)
    if result.empty:
        return result

    eligible = result["mapping_status"].isin(
        {"VERIFIED_SINGLE_CLASS", "VERIFIED_ENTITY_LEVEL_ONLY"}
    )
    ambiguous_indices: set[int] = set()
    for _, issuer_rows in result.loc[eligible].groupby("issuer_cik", sort=False):
        records = list(issuer_rows.iterrows())
        for position, (left_index, left) in enumerate(records):
            for right_index, right in records[position + 1 :]:
                if left["security_id"] == right["security_id"]:
                    continue
                if _intervals_overlap(left, right):
                    ambiguous_indices.update({left_index, right_index})

    ambiguous = result.index.isin(ambiguous_indices)
    result.loc[ambiguous, "mapping_status"] = "AMBIGUOUS_MULTI_CLASS"
    result.loc[ambiguous, "blocking_reasons"] = result.loc[
        ambiguous, "blocking_reasons"
    ].map(
        lambda current: "|".join(
            dict.fromkeys(
                filter(None, [current, "MULTIPLE_PROJECT_SECURITIES_FOR_ISSUER"])
            )
        )
    )

    return result.sort_values(
        ["security_id", "effective_from", "issuer_cik"],
        kind="mergesort",
        na_position="last",
    ).reset_index(drop=True)


def require_usable_identity(
    mapping: pd.DataFrame, security_id: str, usage: str
) -> pd.Series:
    """Return the latest usable mapping row or fail with a stable reason code."""

    normalized_usage = _text(usage).upper()
    if normalized_usage not in _ENTITY_USAGES | _SHARE_CLASS_USAGES:
        raise ValueError(f"IDENTITY_UNKNOWN_USAGE:{normalized_usage or 'MISSING'}")
    if "security_id" not in mapping.columns or "mapping_status" not in mapping.columns:
        raise ValueError("IDENTITY_MAPPING_SCHEMA_INVALID")

    rows = mapping.loc[mapping["security_id"].map(_text) == _text(security_id)].copy()
    if rows.empty:
        raise ValueError(f"IDENTITY_SECURITY_NOT_FOUND:{security_id}")
    if "effective_from" in rows.columns:
        rows = rows.sort_values("effective_from", kind="mergesort", na_position="first")
    row = rows.iloc[-1]
    status = _text(row.get("mapping_status"))

    allowed = (
        {"VERIFIED_SINGLE_CLASS", "VERIFIED_ENTITY_LEVEL_ONLY"}
        if normalized_usage in _ENTITY_USAGES
        else {"VERIFIED_SINGLE_CLASS"}
    )
    if status not in allowed:
        raise ValueError(
            f"IDENTITY_NOT_USABLE:{normalized_usage}:{security_id}:{status or 'MISSING'}"
        )
    return row.copy()
