from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from sec_edgar_pipeline.processing import _apply_identity_review_blocks, _load_ledger, process_sec_raw
from sec_edgar_pipeline.raw_store import RawArtifact, store_raw_response


FIXTURES = Path("data_pipelines/sec_edgar/tests/fixtures/sec")


def test_verified_response_enters_processing_only_after_receipt(tmp_path):
    content = b"{}"
    store_raw_response(
        tmp_path,
        RawArtifact(
            source_type="companyfacts",
            cik="0000320193",
            source_url="https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
            requested_at="2026-09-22T00:00:00Z",
            first_seen_at="2026-09-22T00:05:00Z",
            http_status=200,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            object_path="",
            pipeline_version="1",
            receipt_time_verified=True,
        ),
        content,
    )
    with pytest.raises(ValueError, match="PROCESS_NO_RAW_ARTIFACTS_ASOF"):
        _load_ledger(tmp_path, pd.Timestamp("2026-09-22T00:03:00Z"))
    assert len(_load_ledger(tmp_path, pd.Timestamp("2026-09-22T00:06:00Z"))) == 1


def _store(root: Path, source_type: str, cik: str, url: str, content: bytes) -> None:
    digest = hashlib.sha256(content).hexdigest()
    store_raw_response(
        root,
        RawArtifact(
            source_type=source_type,
            cik=cik,
            source_url=url,
            requested_at="2025-10-31T07:00:00Z",
            first_seen_at="2025-10-31T07:00:00Z",
            http_status=200,
            size_bytes=len(content),
            sha256=digest,
            object_path="",
            pipeline_version="1",
        ),
        content,
    )


def _fixture_raw_root(
    tmp_path: Path,
    *,
    domicile: str | None = "US",
    multiple_sec_tickers: bool = False,
    incorporation_code: str | None = "CA",
) -> Path:
    raw_root = tmp_path / "raw"
    raw_root.mkdir()
    companyfacts = (FIXTURES / "companyfacts_aapl_minimal.json").read_bytes()
    submissions = json.loads((FIXTURES / "submissions_aapl_minimal.json").read_text())
    if incorporation_code is not None:
        submissions["stateOfIncorporation"] = incorporation_code
    if multiple_sec_tickers:
        submissions["tickers"] = ["AAPL", "AAPL-PR"]
        submissions["exchanges"] = ["Nasdaq", "NYSE"]
    _store(
        raw_root,
        "companyfacts",
        "0000320193",
        "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
        companyfacts,
    )
    _store(
        raw_root,
        "submissions",
        "0000320193",
        "https://data.sec.gov/submissions/CIK0000320193.json",
        json.dumps(submissions, sort_keys=True).encode(),
    )
    universe_row = {
        "security_id": "sec-aapl",
        "ticker": "AAPL",
        "exchange": "NASDAQ",
        "security_type": "COMMON_STOCK",
        "as_of": "2025-10-31T00:00:00Z",
        "issuer_cik": "0000320193",
    }
    if domicile is not None:
        universe_row["domicile"] = domicile
    pd.DataFrame([universe_row]).to_csv(raw_root / "resolved_universe_ciks.csv", index=False)
    return raw_root


def test_processing_orchestrates_verified_raw_objects_to_release_inputs(tmp_path):
    raw_root = _fixture_raw_root(tmp_path)

    output = tmp_path / "processed"
    result = process_sec_raw(
        raw_root=raw_root,
        mapping_path="data_pipelines/sec_edgar/mappings/canonical_fields.csv",
        derived_definitions_path="data_pipelines/sec_edgar/config/derived_fields.json",
        output_dir=output,
        start_date="2025-10-31",
        end_date="2025-11-05",
        as_of="2025-11-05T21:00:00Z",
    )

    assert result["status"] == "PASS"
    assert result["security_count"] == 1
    versions = pd.read_parquet(output / "fundamental_versions.parquet")
    assert "total_assets" in set(versions.canonical_field)
    panel = pd.read_parquet(output / "daily_pit_fundamental_panel.parquet")
    assert set(panel.availability_mode) == {
        "HISTORICAL_DIAGNOSTIC",
        "FORWARD_OBSERVED",
    }
    assert (output / "cik_security_map.csv").is_file()
    assert result["identity_binding_mode"] == "EFFECTIVE_WINDOW_ONLY"
    assert panel.session.min() >= pd.Timestamp("2025-10-31")


def test_missing_domicile_does_not_get_inferred_from_sec_listing(tmp_path):
    raw_root = _fixture_raw_root(tmp_path, domicile=None, incorporation_code=None)
    output = tmp_path / "processed"
    with pytest.raises(ValueError, match="PROCESS_NO_USABLE_IDENTITY"):
        process_sec_raw(
            raw_root=raw_root,
            mapping_path="data_pipelines/sec_edgar/mappings/canonical_fields.csv",
            derived_definitions_path="data_pipelines/sec_edgar/config/derived_fields.json",
            output_dir=output,
            start_date="2025-10-31",
            end_date="2025-11-05",
            as_of="2025-11-05T21:00:00Z",
        )
    assert not output.exists()


def test_us_incorporation_code_supplies_current_domicile_with_raw_hash(tmp_path):
    raw_root = _fixture_raw_root(tmp_path, domicile=None, incorporation_code="CA")
    output = tmp_path / "processed"
    result = process_sec_raw(
        raw_root=raw_root,
        mapping_path="data_pipelines/sec_edgar/mappings/canonical_fields.csv",
        derived_definitions_path="data_pipelines/sec_edgar/config/derived_fields.json",
        output_dir=output,
        start_date="2025-10-31",
        end_date="2025-11-05",
        as_of="2025-11-05T21:00:00Z",
    )
    identity = pd.read_csv(output / "cik_security_map.csv", dtype=str)
    assert identity.iloc[0].mapping_status == "VERIFIED_SINGLE_CLASS"
    assert identity.iloc[0].domicile_evidence_field == "stateOfIncorporation"
    assert identity.iloc[0].incorporation_code == "CA"
    assert len(identity.iloc[0].domicile_evidence_raw_hash) == 64
    assert result["domicile_classification_counts"] == {"US_STATE_OR_DC_INCORPORATED": 1}


def test_non_us_incorporation_code_remains_deferred(tmp_path):
    raw_root = _fixture_raw_root(tmp_path, domicile=None, incorporation_code="E9")
    with pytest.raises(ValueError, match="PROCESS_NO_USABLE_IDENTITY"):
        process_sec_raw(
            raw_root=raw_root,
            mapping_path="data_pipelines/sec_edgar/mappings/canonical_fields.csv",
            derived_definitions_path="data_pipelines/sec_edgar/config/derived_fields.json",
            output_dir=tmp_path / "processed",
            start_date="2025-10-31",
            end_date="2025-11-05",
            as_of="2025-11-05T21:00:00Z",
        )


def test_multiple_sec_tickers_keeps_entity_level_statement_field(tmp_path):
    raw_root = _fixture_raw_root(tmp_path, multiple_sec_tickers=True)
    output = tmp_path / "processed"
    result = process_sec_raw(
        raw_root=raw_root,
        mapping_path="data_pipelines/sec_edgar/mappings/canonical_fields.csv",
        derived_definitions_path="data_pipelines/sec_edgar/config/derived_fields.json",
        output_dir=output,
        start_date="2025-10-31",
        end_date="2025-11-05",
        as_of="2025-11-05T21:00:00Z",
    )
    identity = pd.read_csv(output / "cik_security_map.csv")
    assert identity.iloc[0].mapping_status == "VERIFIED_ENTITY_LEVEL_ONLY"
    assert result["security_count"] == 1
    assert "total_assets" in set(pd.read_parquet(output / "fundamental_versions.parquet").canonical_field)


def test_unaligned_derived_inputs_are_omitted_without_discarding_valid_direct_facts(tmp_path):
    raw_root = _fixture_raw_root(tmp_path)
    companyfacts = json.loads((FIXTURES / "companyfacts_aapl_minimal.json").read_text())
    base_fact = companyfacts["facts"]["us-gaap"]["Assets"]["units"]["USD"][0]
    for tag, period_end, value in (
        ("AssetsCurrent", "2025-09-27", 100.0),
        ("LiabilitiesCurrent", "2025-06-30", 40.0),
    ):
        companyfacts["facts"]["us-gaap"][tag] = {
            "label": tag,
            "description": tag,
            "units": {"USD": [{**base_fact, "end": period_end, "val": value}]},
        }
    raw_root = tmp_path / "raw-with-mismatch"
    raw_root.mkdir()
    _store(
        raw_root,
        "companyfacts", "0000320193",
        "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
        json.dumps(companyfacts, sort_keys=True).encode(),
    )
    submissions = json.loads((FIXTURES / "submissions_aapl_minimal.json").read_text())
    submissions["stateOfIncorporation"] = "CA"
    _store(
        raw_root,
        "submissions", "0000320193",
        "https://data.sec.gov/submissions/CIK0000320193.json",
        json.dumps(submissions, sort_keys=True).encode(),
    )
    pd.read_csv(tmp_path / "raw" / "resolved_universe_ciks.csv").to_csv(
        raw_root / "resolved_universe_ciks.csv", index=False
    )
    output = tmp_path / "processed-with-mismatch"
    result = process_sec_raw(
        raw_root=raw_root,
        mapping_path="data_pipelines/sec_edgar/mappings/canonical_fields.csv",
        derived_definitions_path="data_pipelines/sec_edgar/config/derived_fields.json",
        output_dir=output,
        start_date="2025-10-31",
        end_date="2025-11-05",
        as_of="2025-11-05T21:00:00Z",
    )
    versions = pd.read_parquet(output / "fundamental_versions.parquet")
    assert "total_assets" in set(versions.canonical_field)
    assert "working_capital" not in set(versions.canonical_field)
    assert result["derived_diagnostic_counts"]["INPUT_PERIOD_MISMATCH"] > 0


def test_xom_successor_is_explicitly_blocked_pending_reviewed_bridge():
    identity = pd.DataFrame([
        {
            "security_id": "sec-xom",
            "ticker": "XOM",
            "issuer_cik": "0002115436",
            "resolved_issuer_cik": "0002115436",
            "mapping_status": "VERIFIED_SINGLE_CLASS",
            "blocking_reasons": "",
        },
        {
            "security_id": "sec-aapl",
            "ticker": "AAPL",
            "issuer_cik": "0000320193",
            "resolved_issuer_cik": "0000320193",
            "mapping_status": "VERIFIED_SINGLE_CLASS",
            "blocking_reasons": "",
        },
    ])
    result = _apply_identity_review_blocks(identity)
    xom = result.set_index("security_id").loc["sec-xom"]
    assert xom.mapping_status == "UNRESOLVED"
    assert "ISSUER_SUCCESSION_REQUIRES_EFFECTIVE_DATED_BRIDGE" in xom.blocking_reasons
    assert xom.review_block_predecessor_cik == "0000034088"
    assert xom.review_block_evidence_url.startswith("https://www.sec.gov/")
    assert result.set_index("security_id").loc["sec-aapl"].mapping_status == "VERIFIED_SINGLE_CLASS"
