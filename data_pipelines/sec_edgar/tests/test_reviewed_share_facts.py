from __future__ import annotations

import json

import pandas as pd
import pytest

from sec_edgar_pipeline.reviewed_share_facts import (
    load_reviewed_share_facts,
    retain_reviewed_share_facts,
)


def _review() -> dict[str, object]:
    return {
        "issuer_cik": "0000019617",
        "ticker": "JPM",
        "exchange": "NYSE",
        "canonical_field": "shares_outstanding_sec",
        "xbrl_namespace": "dei",
        "xbrl_tag": "EntityCommonStockSharesOutstanding",
        "unit": "shares",
        "accession_number": "0001628280-26-008131",
        "period_end": "2026-01-31",
        "value": 2697032375,
        "raw_source_hash": "a" * 64,
        "source_fact_id": "b" * 64,
        "filing_url": "https://www.sec.gov/Archives/edgar/data/19617/000162828026008131/jpm-20251231.htm",
        "semantic_grade": "HYPOTHESIS_PROXY",
    }


def _mapped() -> pd.DataFrame:
    base = _review()
    return pd.DataFrame([
        {**base, "value": 2697032375.0},
        {**base, "period_end": "2025-12-31", "value": 2696200000.0,
         "xbrl_namespace": "us-gaap", "xbrl_tag": "CommonStockSharesOutstanding",
         "source_fact_id": "fact-two"},
        {**base, "canonical_field": "weighted_average_basic_shares", "value": 2776500000.0,
         "source_fact_id": "fact-three"},
        {**base, "canonical_field": "total_assets", "value": 4424900000000.0,
         "source_fact_id": "fact-four"},
    ])


def test_reviewed_multiclass_share_fact_allows_only_exact_cover_observation(tmp_path):
    path = tmp_path / "reviews.json"
    path.write_text(json.dumps({"schema_version": 1, "reviews": [_review()]}))
    reviews = load_reviewed_share_facts(path)
    identity = pd.Series({"issuer_cik": "0000019617", "ticker": "JPM",
                          "exchange": "NYSE", "mapping_status": "VERIFIED_ENTITY_LEVEL_ONLY"})
    selected, excluded = retain_reviewed_share_facts(_mapped(), identity, reviews)
    assert set(selected.source_fact_id) == {"b" * 64, "fact-four"}
    assert excluded == 2


def test_reviewed_share_fact_fails_closed_when_raw_hash_or_value_changes(tmp_path):
    path = tmp_path / "reviews.json"
    path.write_text(json.dumps({"schema_version": 1, "reviews": [_review()]}))
    reviews = load_reviewed_share_facts(path)
    identity = pd.Series({"issuer_cik": "0000019617", "ticker": "JPM",
                          "exchange": "NYSE", "mapping_status": "VERIFIED_ENTITY_LEVEL_ONLY"})
    changed = _mapped()
    changed.loc[0, "raw_source_hash"] = "b" * 64
    selected, excluded = retain_reviewed_share_facts(changed, identity, reviews)
    assert set(selected.source_fact_id) == {"fact-four"}
    assert excluded == 3


def test_reviewed_share_fact_rejects_unverified_filing_url(tmp_path):
    row = _review()
    row["filing_url"] = "https://example.com/fake-filing"
    path = tmp_path / "reviews.json"
    path.write_text(json.dumps({"schema_version": 1, "reviews": [row]}))
    with pytest.raises(ValueError, match="REVIEWED_SHARE_FACT_SOURCE_INVALID"):
        load_reviewed_share_facts(path)
