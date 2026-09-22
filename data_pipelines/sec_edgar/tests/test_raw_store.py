from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from multiprocessing import get_context

import pytest

from sec_edgar_pipeline.client import SecClient
from sec_edgar_pipeline.raw_store import (
    RawArtifact,
    download_cik_bundle,
    find_verified_observation,
    store_raw_response,
)
from sec_edgar_pipeline.raw_store import _fetch_artifact

from test_client import FakeResponse, FakeSession


def _artifact(content: bytes = b"{}") -> RawArtifact:
    return RawArtifact(
        source_type="companyfacts",
        cik="0000320193",
        source_url="https://data.sec.gov/a",
        requested_at="2026-09-17T00:00:00Z",
        first_seen_at="2026-09-17T00:00:00Z",
        http_status=200,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        object_path="",
        pipeline_version="1",
    )


def _store_from_process(arguments):
    root, minute = arguments
    artifact = replace(
        _artifact(), requested_at=f"2026-09-17T01:{minute:02d}:00Z"
    )
    return str(store_raw_response(root, artifact, b"{}"))


def test_same_content_reuses_object_but_appends_observation(tmp_path):
    first = store_raw_response(tmp_path, _artifact(), b"{}")
    second = store_raw_response(
        tmp_path,
        replace(_artifact(), requested_at="2026-09-17T01:00:00Z"),
        b"{}",
    )
    assert first == second
    assert first == tmp_path / "objects" / _artifact().sha256[:2] / f"{_artifact().sha256}.json"
    rows = [json.loads(line) for line in (tmp_path / "download_ledger.jsonl").read_text().splitlines()]
    assert len(rows) == 2
    assert all("User-Agent" not in row for row in rows)
    assert rows[0]["object_path"].startswith("objects/")


def test_store_rejects_content_that_does_not_match_metadata(tmp_path):
    with pytest.raises(ValueError, match="RAW_ARTIFACT_HASH_MISMATCH"):
        store_raw_response(tmp_path, _artifact(), b'{"changed":true}')


def test_concurrent_observations_are_all_retained_without_release_lock_artifact(tmp_path):
    observations = [
        replace(_artifact(), requested_at=f"2026-09-17T00:{minute:02d}:00Z")
        for minute in range(40)
    ]
    with ThreadPoolExecutor(max_workers=12) as executor:
        paths = list(
            executor.map(
                lambda artifact: store_raw_response(tmp_path, artifact, b"{}"),
                observations,
            )
        )
    rows = [
        json.loads(line)
        for line in (tmp_path / "download_ledger.jsonl").read_text().splitlines()
    ]
    assert len(rows) == 40
    assert {row["requested_at"] for row in rows} == {
        artifact.requested_at for artifact in observations
    }
    assert len(set(paths)) == 1
    assert not list(tmp_path.rglob("*.lock"))


def test_process_concurrent_observations_are_all_retained(tmp_path):
    context = get_context("spawn")
    with context.Pool(processes=4) as pool:
        paths = pool.map(_store_from_process, [(tmp_path, minute) for minute in range(12)])
    rows = [
        json.loads(line)
        for line in (tmp_path / "download_ledger.jsonl").read_text().splitlines()
    ]
    assert len(rows) == 12
    assert {row["requested_at"] for row in rows} == {
        f"2026-09-17T01:{minute:02d}:00Z" for minute in range(12)
    }
    assert len(set(paths)) == 1
    assert not list(tmp_path.rglob("*.lock"))


def test_resume_only_uses_an_object_with_verified_hash_and_size(tmp_path):
    path = store_raw_response(tmp_path, _artifact(), b"{}")
    resumed = find_verified_observation(tmp_path, _artifact().source_url)
    assert resumed is not None
    assert resumed.sha256 == _artifact().sha256
    assert resumed.object_path.startswith("objects/")
    path.write_bytes(b"tampered")
    assert find_verified_observation(tmp_path, _artifact().source_url) is None


def test_download_resume_skips_verified_urls(tmp_path):
    observed_at = datetime(2026, 9, 17, tzinfo=timezone.utc)
    first_session = FakeSession(
        [
            FakeResponse(200, b'{"cik":320193,"facts":{}}'),
            FakeResponse(200, b'{"cik":"0000320193","filings":{"files":[]}}'),
        ]
    )
    download_cik_bundle(
        SecClient(first_session, "Alpha a@example.com"),
        tmp_path,
        "320193",
        observed_at,
    )
    second_session = FakeSession([])
    rows = download_cik_bundle(
        SecClient(second_session, "Alpha a@example.com"),
        tmp_path,
        "320193",
        observed_at,
    )
    assert len(rows) == 2
    assert second_session.calls == []


def test_first_seen_uses_response_receipt_not_batch_timestamp(tmp_path):
    batch_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
    before_response = datetime.now(timezone.utc)
    artifact = _fetch_artifact(
        SecClient(FakeSession([FakeResponse(200, b'{"cik":320193,"facts":{}}')]),
                  "Alpha a@example.com"),
        tmp_path, source_type="companyfacts", cik="0000320193",
        source_url="https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
        observed_at=batch_time,
    )
    assert artifact.requested_at == "2025-01-01T00:00:00Z"
    assert datetime.fromisoformat(artifact.first_seen_at.replace("Z", "+00:00")) >= before_response
    assert artifact.receipt_time_verified is True


def test_new_observation_time_refreshes_urls_but_preserves_content_first_seen(tmp_path):
    first_at = datetime(2026, 9, 17, tzinfo=timezone.utc)
    second_at = datetime(2026, 9, 18, tzinfo=timezone.utc)
    responses = [
        FakeResponse(200, b'{"cik":320193,"facts":{}}'),
        FakeResponse(200, b'{"cik":"0000320193","filings":{"files":[]}}'),
    ]
    first = download_cik_bundle(
        SecClient(FakeSession(responses), "Alpha a@example.com"),
        tmp_path,
        "320193",
        first_at,
    )
    second_session = FakeSession(responses)
    second = download_cik_bundle(
        SecClient(second_session, "Alpha a@example.com"),
        tmp_path,
        "320193",
        second_at,
    )
    assert len(second_session.calls) == 2
    assert [row.first_seen_at for row in second] == [row.first_seen_at for row in first]
    assert [row.requested_at for row in second] == ["2026-09-18T00:00:00Z"] * 2


def test_download_follows_each_safe_older_submissions_file(tmp_path):
    session = FakeSession(
        [
            FakeResponse(200, b'{"cik":320193,"facts":{}}'),
            FakeResponse(
                200,
                b'{"cik":"0000320193","filings":{"files":[{"name":"CIK0000320193-submissions-001.json"}]}}',
            ),
            FakeResponse(200, b'{"accessionNumber":[]}'),
        ]
    )
    rows = download_cik_bundle(
        SecClient(session, "Alpha a@example.com"),
        tmp_path,
        "320193",
        datetime(2026, 9, 17, tzinfo=timezone.utc),
    )
    assert [row.source_type for row in rows] == [
        "companyfacts",
        "submissions",
        "submissions_file",
    ]
    assert session.calls[-1][0].endswith("/CIK0000320193-submissions-001.json")
