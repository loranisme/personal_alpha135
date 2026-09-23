from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from sec_edgar_pipeline.processing import _apply_identity_review_blocks
from sec_edgar_pipeline.raw_store import RawArtifact, store_raw_response
from sec_edgar_pipeline.succession import verify_xom_successor_bridge


_URL = "https://www.sec.gov/Archives/edgar/data/2115436/000119312526291990/d71068d8k12b.htm"
_TEXT = b"""<html><body>The Redomiciliation Merger became effective on July 1, 2026.
ExxonMobil Holdings Corporation is the successor registrant of ExxonMobil.
Each share was automatically exchanged for one share of common stock.
The common stock will commence trading under the current ExxonMobil ticker symbol XOM.</body></html>"""
_CUTOFF = pd.Timestamp("2026-09-22T16:00:00Z")


def _store(root: Path, cik: str, kind: str, url: str, body: bytes, *, receipt: bool = True) -> str:
    digest = hashlib.sha256(body).hexdigest()
    store_raw_response(root, RawArtifact(
        source_type=kind, cik=cik, source_url=url,
        requested_at="2026-09-22T12:00:00Z", first_seen_at="2026-09-22T12:00:01Z",
        http_status=200, size_bytes=len(body), sha256=digest,
        object_path="", pipeline_version="1", receipt_time_verified=receipt,
    ), body)
    return digest


def _case(tmp_path: Path, *, filing_text: bytes = _TEXT, receipt: bool = True):
    predecessor, successor, filing = (tmp_path / name for name in ("predecessor", "successor", "filing"))
    hashes = {}
    for role, cik, root in (
        ("predecessor", "0000034088", predecessor),
        ("successor", "0002115436", successor),
    ):
        hashes[f"{role}_companyfacts_sha256"] = _store(
            root, cik, "companyfacts", f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
            json.dumps({"cik": int(cik)}).encode(),
        )
        hashes[f"{role}_submissions_sha256"] = _store(
            root, cik, "submissions", f"https://data.sec.gov/submissions/CIK{cik}.json",
            json.dumps({"cik": int(cik), "tickers": ["XOM"] if role == "successor" else [],
                        "exchanges": ["NYSE"] if role == "successor" else []}).encode(),
        )
    hashes["sec_filing_sha256"] = _store(
        filing, "0002115436", "issuer_succession_filing", _URL, filing_text, receipt=receipt,
    )
    review = tmp_path / "review.json"
    review.write_text(json.dumps({"schema_version": 1, "bridges": [{
        "ticker": "XOM", "predecessor_cik": "0000034088", "successor_cik": "0002115436",
        "effective_date": "2026-07-01", "sec_filing_url": _URL, **hashes,
    }]}))
    return predecessor, successor, filing, review


def _verify(case, *, as_of=_CUTOFF):
    predecessor, successor, filing, review = case
    return verify_xom_successor_bridge(
        predecessor_root=predecessor, successor_root=successor, filing_root=filing,
        as_of=as_of, review_path=review,
    )


def test_reviewed_bridge_binds_8k_and_both_issuers_without_historical_backfill(tmp_path: Path):
    reviewed = _verify(_case(tmp_path))
    identity = pd.DataFrame([{
        "security_id": "sec-xom", "ticker": "XOM", "issuer_cik": "0002115436",
        "resolved_issuer_cik": "0002115436", "mapping_status": "VERIFIED_SINGLE_CLASS",
        "blocking_reasons": "", "mapping_source": "SEC_SUBMISSIONS_TICKER_EXCHANGE",
        "effective_from": pd.Timestamp("2026-09-15T09:24:13Z"),
    }])
    result = _apply_identity_review_blocks(identity, reviewed_succession=reviewed)
    assert len(result) == 1
    row = result.iloc[0]
    assert row.mapping_status == "VERIFIED_SINGLE_CLASS"
    assert row.issuer_cik == "0002115436"
    assert pd.Timestamp(row.effective_from) == pd.Timestamp("2026-09-22T12:00:01Z")
    assert reviewed["bridge_first_seen_at"] == pd.Timestamp("2026-09-22T12:00:01Z")
    assert row.succession_filing_sha256 == hashlib.sha256(_TEXT).hexdigest()
    assert row.review_block_predecessor_cik == "0000034088"


@pytest.mark.parametrize("failure", ["unreviewed", "unreceived", "tampered", "wrong_filing", "future_asof"])
def test_succession_fails_closed_on_missing_or_bad_provenance(tmp_path: Path, failure: str):
    case = _case(tmp_path, filing_text=b"<html>unrelated filing</html>" if failure == "wrong_filing" else _TEXT,
                 receipt=failure != "unreceived")
    if failure == "unreviewed":
        case[3].unlink()
    if failure == "tampered":
        ledger = case[2] / "download_ledger.jsonl"
        record = json.loads(ledger.read_text().splitlines()[0])
        (case[2] / record["object_path"]).write_bytes(b"tampered")
    with pytest.raises(ValueError):
        _verify(case, as_of=pd.Timestamp("2026-09-22T11:00:00Z") if failure == "future_asof" else _CUTOFF)


def test_reviewed_successor_starts_no_earlier_than_bridge_observation(tmp_path: Path):
    reviewed = _verify(_case(tmp_path))
    identity = pd.DataFrame([{
        "security_id": "sec-xom", "ticker": "XOM", "issuer_cik": "0002115436",
        "resolved_issuer_cik": "0002115436", "mapping_status": "VERIFIED_SINGLE_CLASS",
        "blocking_reasons": "", "mapping_source": "SEC_SUBMISSIONS_TICKER_EXCHANGE",
        "effective_from": pd.Timestamp("2026-01-01T00:00:00Z"),
    }])
    row = _apply_identity_review_blocks(identity, reviewed_succession=reviewed).iloc[0]
    assert pd.Timestamp(row.effective_from) == pd.Timestamp("2026-09-22T12:00:01Z")
    assert len(_apply_identity_review_blocks(identity)) == 1
    assert _apply_identity_review_blocks(identity).iloc[0].mapping_status == "UNRESOLVED"


def test_newer_successor_snapshot_requires_new_review(tmp_path: Path):
    case = _case(tmp_path)
    body = json.dumps({"cik": 2115436, "new_fact": 1}).encode()
    store_raw_response(case[1], RawArtifact(
        source_type="companyfacts", cik="0002115436",
        source_url="https://data.sec.gov/api/xbrl/companyfacts/CIK0002115436.json",
        requested_at="2026-09-22T13:00:00Z", first_seen_at="2026-09-22T13:00:01Z",
        http_status=200, size_bytes=len(body), sha256=hashlib.sha256(body).hexdigest(),
        object_path="", pipeline_version="1", receipt_time_verified=True,
    ), body)
    with pytest.raises(ValueError, match="SUCCESSION_LATEST_ARTIFACT_NOT_REVIEWED"):
        _verify(case)
