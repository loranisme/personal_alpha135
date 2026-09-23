import hashlib
import json
from pathlib import Path

import pytest

import pandas as pd

from sec_edgar_pipeline.validation import (
    REQUIRED_MANUAL_FIELDS,
    validate_fixture_contracts,
    validate_manual_issuers,
)


def _manual_raw_fixture(tmp_path: Path, *, processed_value: float | None = None):
    raw_root = tmp_path / "raw"
    raw_root.mkdir()
    accession = "0000000001-25-000001"
    cik = "0000000001"
    filed = "2025-02-03"
    acceptance = "2025-02-03T20:00:00Z"
    historical = "2025-02-03T20:10:00Z"
    facts = {"us-gaap": {}}
    rows = []
    expected = []
    for index, field in enumerate(REQUIRED_MANUAL_FIELDS, 1):
        tag = f"Tag{index}"
        observation = {
            "end": "2024-12-31",
            "val": float(index),
            "accn": accession,
            "fy": 2024,
            "fp": "FY",
            "form": "10-K",
            "filed": filed,
        }
        facts["us-gaap"][tag] = {"units": {"USD": [observation]}}
        source_identity = {
            "namespace": "us-gaap",
            "tag": tag,
            "unit": "USD",
            "accession": accession,
            "start": None,
            "end": "2024-12-31",
            "value": float(index),
            "frame": None,
            "dimensions": None,
            "source_index": 0,
        }
        source_id = hashlib.sha256(
            json.dumps(
                source_identity,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        value = float(index) if processed_value is None else processed_value
        rows.append(
            {
                "issuer_cik": cik,
                "canonical_field": field,
                "accession_number": accession,
                "xbrl_namespace": "us-gaap",
                "xbrl_tag": tag,
                "unit": "USD",
                "value": value,
                "period_end": "2024-12-31",
                "historical_available_at": historical,
                "reported_basis": "INSTANT",
                "source_fact_id": source_id,
            }
        )
        expected.append(
            {
                "issuer_cik": cik,
                "canonical_field": field,
                "accession_number": accession,
                "xbrl_tag": tag,
                "unit": "USD",
                "expected_value": str(value),
                "period_end": "2024-12-31",
                "expected_historical_available_at": historical,
                "expected_reported_basis": "INSTANT",
                "expected_source_fact_id": source_id,
            }
        )
    payload = json.dumps({"cik": 1, "facts": facts}).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    object_path = raw_root / "objects" / f"{digest}.json"
    object_path.parent.mkdir()
    object_path.write_bytes(payload)
    for row in rows:
        row["raw_source_hash"] = digest
    for row in expected:
        row["expected_raw_source_hash"] = digest
    submissions_payload = json.dumps(
        {
            "cik": "0000000001",
            "tickers": ["AAA", "AAA-P"],
            "filings": {
                "recent": {
                    "accessionNumber": [accession],
                    "filingDate": [filed],
                    "acceptanceDateTime": [acceptance],
                    "form": ["10-K"],
                }
            },
        }
    ).encode("utf-8")
    submission_digest = hashlib.sha256(submissions_payload).hexdigest()
    submission_path = raw_root / "objects" / f"{submission_digest}.json"
    submission_path.write_bytes(submissions_payload)
    ledger = raw_root / "download_ledger.jsonl"
    ledger.write_text(
        "\n".join(
            json.dumps(
                {
                    "cik": cik,
                    "source_type": source_type,
                    "source_url": (
                        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
                        if source_type == "companyfacts"
                        else f"https://data.sec.gov/submissions/CIK{cik}.json"
                    ),
                    "http_status": 200,
                    "sha256": source_digest,
                    "size_bytes": len(contents),
                    "object_path": source_path.relative_to(raw_root).as_posix(),
                }
            )
            for source_type, source_digest, source_path, contents in [
                ("companyfacts", digest, object_path, payload),
                ("submissions", submission_digest, submission_path, submissions_payload),
            ]
        ) + "\n",
        encoding="utf-8",
    )
    versions = tmp_path / "versions.parquet"
    pd.DataFrame(rows).to_parquet(versions, index=False)
    manual = tmp_path / "manual.csv"
    pd.DataFrame(expected).to_csv(manual, index=False)
    return {
        "fundamental_versions": versions,
        "manual_expected": manual,
        "raw_root": raw_root,
        "raw_manifest": ledger,
        "submissions_sha256": submission_digest,
    }


def test_fixture_contracts_pass_for_causal_panel():
    versions = pd.DataFrame([{
        "source_fact_id": "f1", "raw_source_hash": "a" * 64,
        "historical_available_at": "2025-02-01T21:00:00Z",
        "first_seen_at": "2025-02-02T12:00:00Z",
        "available_at": "2025-02-02T12:00:00Z",
        "mapping_status": "MAPPED", "canonical_field": "revenue",
        "reported_basis": "TTM", "issuer_cik": "0000000001",
        "security_id": "sec-1",
        "accession_number": "0001", "value": 10.0, "unit": "USD",
    }])
    panel = pd.DataFrame([{
        "source_fact_id": "f1", "signal_cutoff": "2025-02-03T21:00:00Z",
        "selected_available_at": "2025-02-01T21:00:00Z",
        "availability_mode": "HISTORICAL_DIAGNOSTIC",
        "canonical_field": "revenue", "reported_basis": "TTM",
        "issuer_cik": "0000000001", "security_id": "sec-1",
        "accession_number": "0001", "value": 10.0, "unit": "USD",
    }])
    result = validate_fixture_contracts(versions, panel)
    assert result["status"] == "PASS"
    assert all(result["checks"].values())


def test_fixture_contracts_accept_panel_with_copied_availability_columns():
    versions = pd.DataFrame([{
        "source_fact_id": "f1", "raw_source_hash": "a" * 64,
        "historical_available_at": "2025-02-01T21:00:00Z",
        "first_seen_at": "2025-02-02T12:00:00Z",
        "available_at": "2025-02-02T12:00:00Z",
        "mapping_status": "MAPPED", "canonical_field": "revenue",
        "reported_basis": "TTM", "issuer_cik": "0000000001",
        "security_id": "sec-1", "accession_number": "0001",
        "value": 10.0, "unit": "USD",
    }])
    panel = pd.DataFrame([{
        "source_fact_id": "f1", "signal_cutoff": "2025-02-03T21:00:00Z",
        "selected_available_at": "2025-02-01T21:00:00Z",
        "availability_mode": "HISTORICAL_DIAGNOSTIC",
        "historical_available_at": "2025-02-01T21:00:00Z",
        "available_at": "2025-02-02T12:00:00Z",
        "canonical_field": "revenue", "reported_basis": "TTM",
        "issuer_cik": "0000000001", "security_id": "sec-1",
        "accession_number": "0001", "value": 10.0, "unit": "USD",
    }])

    result = validate_fixture_contracts(versions, panel)

    assert result["status"] == "PASS"
    assert result["checks"]["panel_lineage_resolves"] is True


def test_fixture_contracts_fail_on_future_value():
    versions = pd.DataFrame([{
        "source_fact_id": "f1", "raw_source_hash": "a" * 64,
        "historical_available_at": "2025-02-04T21:00:00Z",
        "first_seen_at": "2025-02-04T21:00:00Z",
        "available_at": "2025-02-04T21:00:00Z",
        "mapping_status": "MAPPED", "canonical_field": "revenue",
        "reported_basis": "TTM", "issuer_cik": "0000000001",
        "security_id": "sec-1",
        "accession_number": "0001", "value": 10.0, "unit": "USD",
    }])
    panel = pd.DataFrame([{
        "source_fact_id": "f1", "signal_cutoff": "2025-02-03T21:00:00Z",
        "selected_available_at": "2025-02-04T21:00:00Z",
        "availability_mode": "HISTORICAL_DIAGNOSTIC",
        "canonical_field": "revenue", "reported_basis": "TTM",
        "issuer_cik": "0000000001", "security_id": "sec-1",
        "accession_number": "0001", "value": 10.0, "unit": "USD",
    }])
    result = validate_fixture_contracts(versions, panel)
    assert result["status"] == "FAIL"
    assert result["checks"]["no_future_values"] is False


def test_fixture_contracts_reject_panel_value_or_availability_not_from_source():
    versions = pd.DataFrame([{
        "source_fact_id": "f1", "raw_source_hash": "a" * 64,
        "historical_available_at": "2025-02-01T21:00:00Z",
        "first_seen_at": "2025-02-02T12:00:00Z",
        "available_at": "2025-02-02T12:00:00Z",
        "mapping_status": "MAPPED", "canonical_field": "revenue",
        "reported_basis": "TTM", "issuer_cik": "0000000001",
        "security_id": "sec-1", "accession_number": "0001",
        "value": 10.0, "unit": "USD",
    }])
    panel = pd.DataFrame([{
        "source_fact_id": "f1", "signal_cutoff": "2025-02-03T21:00:00Z",
        "selected_available_at": "2025-01-01T00:00:00Z",
        "availability_mode": "HISTORICAL_DIAGNOSTIC",
        "canonical_field": "revenue", "reported_basis": "TTM",
        "issuer_cik": "0000000001", "security_id": "sec-1",
        "accession_number": "0001", "value": 999.0, "unit": "USD",
    }])

    result = validate_fixture_contracts(versions, panel)

    assert result["status"] == "FAIL"
    assert result["checks"]["panel_values_match_versions"] is False
    assert result["checks"]["panel_selected_availability_matches_source"] is False


def test_fixture_contracts_allow_one_issuer_fact_linked_to_two_security_rows():
    base_version = {
        "source_fact_id": "issuer-fact", "raw_source_hash": "a" * 64,
        "historical_available_at": "2025-02-01T21:00:00Z",
        "first_seen_at": "2025-02-02T12:00:00Z",
        "available_at": "2025-02-02T12:00:00Z",
        "mapping_status": "MAPPED", "canonical_field": "revenue",
        "reported_basis": "TTM", "issuer_cik": "0000000001",
        "accession_number": "0001", "value": 10.0, "unit": "USD",
    }
    versions = pd.DataFrame([
        base_version | {"security_id": "class-a"},
        base_version | {"security_id": "class-b"},
    ])
    base_panel = {
        "source_fact_id": "issuer-fact",
        "signal_cutoff": "2025-02-03T21:00:00Z",
        "selected_available_at": "2025-02-01T21:00:00Z",
        "availability_mode": "HISTORICAL_DIAGNOSTIC",
        "canonical_field": "revenue", "reported_basis": "TTM",
        "issuer_cik": "0000000001", "accession_number": "0001",
        "value": 10.0, "unit": "USD",
    }
    panel = pd.DataFrame([
        base_panel | {"security_id": "class-a"},
        base_panel | {"security_id": "class-b"},
    ])

    result = validate_fixture_contracts(versions, panel)

    assert result["status"] == "PASS"


def test_manual_reconciliation_requires_hash_from_verified_raw_ledger(tmp_path):
    inputs = _manual_raw_fixture(tmp_path)
    expected = pd.read_csv(inputs["manual_expected"], dtype=str, keep_default_na=False)
    expected["expected_raw_source_hash"] = "b" * 64
    expected.to_csv(inputs["manual_expected"], index=False)

    report, _ = validate_manual_issuers(inputs, ["1"])

    assert report.status.eq("FAIL").all()
    assert set(report.reason) == {"EXPECTED_RAW_HASH_NOT_IN_VERIFIED_LEDGER"}


def test_manual_reconciliation_checks_actual_verified_sec_objects(tmp_path):
    inputs = _manual_raw_fixture(tmp_path)

    report, markdown = validate_manual_issuers(inputs, ["1"])

    assert report.status.eq("PASS").all()
    assert set(report.reason) == {"MATCH"}
    assert "Status: **PASS**" in markdown


def test_manual_reconciliation_rejects_self_attested_processed_value(tmp_path):
    inputs = _manual_raw_fixture(tmp_path, processed_value=999.0)

    report, _ = validate_manual_issuers(inputs, ["1"])

    assert report.status.eq("FAIL").all()
    assert set(report.reason) == {"RAW_FACT_ACCESSION_TAG_UNIT_PERIOD_VALUE_MISMATCH"}


def test_manual_reconciliation_rejects_unsupported_ttm_label_on_instant_raw_fact(tmp_path):
    inputs = _manual_raw_fixture(tmp_path)
    versions = pd.read_parquet(inputs["fundamental_versions"])
    versions["reported_basis"] = "TTM"
    versions.to_parquet(inputs["fundamental_versions"], index=False)
    expected = pd.read_csv(inputs["manual_expected"], dtype=str, keep_default_na=False)
    expected["expected_reported_basis"] = "TTM"
    expected.to_csv(inputs["manual_expected"], index=False)

    report, _ = validate_manual_issuers(inputs, ["1"])

    assert report.status.eq("FAIL").all()
    assert set(report.reason) == {"RAW_FACT_BASIS_NOT_DIRECTLY_SUPPORTED"}


def test_manual_reconciliation_rejects_tampered_raw_object(tmp_path):
    inputs = _manual_raw_fixture(tmp_path)
    object_path = next((inputs["raw_root"] / "objects").glob("*.json"))
    object_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="MANUAL_RAW_OBJECT_INVALID"):
        validate_manual_issuers(inputs, ["1"])


def test_manual_unmapped_field_requires_verified_absence_of_approved_tag(tmp_path):
    inputs = _manual_raw_fixture(tmp_path)
    versions = pd.read_parquet(inputs["fundamental_versions"])
    versions = versions[versions.canonical_field.ne("revenue")]
    versions.to_parquet(inputs["fundamental_versions"], index=False)
    expected = pd.read_csv(inputs["manual_expected"], dtype=str, keep_default_na=False)
    expected["review_status"] = "REVIEWED"
    expected.loc[expected.canonical_field.eq("revenue"), "review_status"] = "UNMAPPED"
    expected.to_csv(inputs["manual_expected"], index=False)

    report, markdown = validate_manual_issuers(inputs, ["1"])

    assert report.loc[report.canonical_field.eq("revenue"), "status"].tolist() == ["UNMAPPED"]
    assert report.loc[report.canonical_field.eq("revenue"), "reason"].tolist() == [
        "NO_APPROVED_CANONICAL_TAG_IN_VERIFIED_COMPANYFACTS"
    ]
    assert report.loc[report.canonical_field.ne("revenue"), "status"].eq("PASS").all()
    assert "Status: **BLOCKED**" in markdown


def test_manual_unmapped_cannot_hide_processed_field(tmp_path):
    inputs = _manual_raw_fixture(tmp_path)
    expected = pd.read_csv(inputs["manual_expected"], dtype=str, keep_default_na=False)
    expected["review_status"] = "REVIEWED"
    expected.loc[expected.canonical_field.eq("revenue"), "review_status"] = "UNMAPPED"
    expected.to_csv(inputs["manual_expected"], index=False)

    report, markdown = validate_manual_issuers(inputs, ["1"])

    revenue = report.loc[report.canonical_field.eq("revenue")].iloc[0]
    assert revenue.status == "FAIL"
    assert revenue.reason == "UNMAPPED_CONTRADICTED_BY_PROCESSED_FIELD"
    assert "Status: **FAIL**" in markdown


def test_manual_blocked_identity_does_not_require_missing_raw_predecessor(tmp_path):
    inputs = _manual_raw_fixture(tmp_path)
    expected = pd.read_csv(inputs["manual_expected"], dtype=str, keep_default_na=False)
    expected["review_status"] = "REVIEWED"
    blocked = pd.DataFrame([
        {
            "issuer_cik": "0000034088",
            "canonical_field": field,
            "review_status": "BLOCKED",
            "review_reason": "ISSUER_SUCCESSION_REQUIRES_EFFECTIVE_DATED_BRIDGE",
            "review_evidence_url": "https://www.sec.gov/Archives/edgar/data/2115436/000119312526291990/d71068d8k12b.htm",
        }
        for field in REQUIRED_MANUAL_FIELDS
    ])
    pd.concat([expected, blocked], ignore_index=True).to_csv(inputs["manual_expected"], index=False)

    report, markdown = validate_manual_issuers(inputs, ["1", "0000034088"])

    assert report.loc[report.issuer_cik.eq("0000034088"), "status"].eq("BLOCKED").all()
    assert report.loc[report.issuer_cik.eq("0000034088"), "reason"].eq(
        "ISSUER_SUCCESSION_REQUIRES_EFFECTIVE_DATED_BRIDGE"
    ).all()
    assert "Status: **BLOCKED**" in markdown


def test_manual_block_requires_official_evidence_and_pending_is_not_pass(tmp_path):
    inputs = _manual_raw_fixture(tmp_path)
    expected = pd.read_csv(inputs["manual_expected"], dtype=str, keep_default_na=False)
    expected["review_status"] = "REVIEWED"
    expected.loc[expected.canonical_field.eq("revenue"), "review_status"] = "PENDING"
    expected.loc[expected.canonical_field.eq("total_assets"), "review_status"] = "BLOCKED"
    expected.loc[expected.canonical_field.eq("total_assets"), "review_reason"] = "CONFLICT"
    expected.loc[expected.canonical_field.eq("total_assets"), "review_evidence_url"] = "https://example.com/"
    expected.to_csv(inputs["manual_expected"], index=False)

    report, markdown = validate_manual_issuers(inputs, ["1"])

    assert report.loc[report.canonical_field.eq("revenue"), "status"].tolist() == ["BLOCKED"]
    assert report.loc[report.canonical_field.eq("total_assets"), "reason"].tolist() == [
        "BLOCK_EVIDENCE_INCOMPLETE"
    ]
    assert "Status: **FAIL**" in markdown


def test_manual_share_class_block_requires_hash_verified_sec_submissions(tmp_path):
    inputs = _manual_raw_fixture(tmp_path)
    expected = pd.read_csv(inputs["manual_expected"], dtype=str, keep_default_na=False)
    expected["review_status"] = "REVIEWED"
    mask = expected.canonical_field.eq("shares_outstanding_sec")
    expected.loc[mask, "review_status"] = "BLOCKED"
    expected.loc[mask, "review_reason"] = "SHARE_CLASS_IDENTITY_REQUIRED"
    expected.loc[mask, "review_evidence_url"] = (
        "https://data.sec.gov/submissions/CIK0000000001.json"
    )
    expected.loc[mask, "review_evidence_hash"] = inputs["submissions_sha256"]
    expected.to_csv(inputs["manual_expected"], index=False)

    report, markdown = validate_manual_issuers(inputs, ["1"])

    shares = report.loc[mask].iloc[0]
    assert shares.status == "BLOCKED"
    assert shares.reason == "SHARE_CLASS_IDENTITY_REQUIRED"
    assert "Status: **BLOCKED**" in markdown

    expected.loc[mask, "review_evidence_hash"] = "0" * 64
    expected.to_csv(inputs["manual_expected"], index=False)
    report, markdown = validate_manual_issuers(inputs, ["1"])
    shares = report.loc[mask].iloc[0]
    assert shares.status == "FAIL"
    assert shares.reason == "BLOCK_RAW_EVIDENCE_NOT_VERIFIED"
    assert "Status: **FAIL**" in markdown
