from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from sec_edgar_pipeline.client import SecClient, SecHttpError
from sec_edgar_pipeline.raw_store import download_cik_bundle


class FakeResponse:
    def __init__(
        self,
        status: int,
        body: bytes,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status
        self.content = body
        self.headers = headers or {}


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = iter(responses)
        self.calls: list[
            tuple[str, dict[str, str], tuple[float, float], bool]
        ] = []

    def get(self, url, headers, timeout, allow_redirects):
        self.calls.append((url, headers, timeout, allow_redirects))
        return next(self.responses)


def test_download_uses_official_per_cik_endpoints_and_declared_header(tmp_path):
    session = FakeSession(
        [
            FakeResponse(200, b'{"cik":320193,"facts":{}}'),
            FakeResponse(200, b'{"cik":"0000320193","filings":{"files":[]}}'),
        ]
    )
    client = SecClient(session, "PersonalAlpha contact@example.com")
    rows = download_cik_bundle(
        client,
        tmp_path,
        "320193",
        datetime(2026, 9, 17, tzinfo=timezone.utc),
    )
    assert [row.source_type for row in rows] == ["companyfacts", "submissions"]
    assert session.calls[0][0] == (
        "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
    )
    assert session.calls[1][0] == (
        "https://data.sec.gov/submissions/CIK0000320193.json"
    )
    assert session.calls[0][1] == {
        "Accept-Encoding": "gzip, deflate",
        "Host": "data.sec.gov",
        "User-Agent": "PersonalAlpha contact@example.com",
    }
    assert session.calls[0][3] is False


def test_client_honors_retry_after_and_retries_transient_status(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("sec_edgar_pipeline.client.time.sleep", sleeps.append)
    session = FakeSession(
        [
            FakeResponse(429, b'{"error":"slow"}', {"Retry-After": "3"}),
            FakeResponse(503, b'{"error":"later"}'),
            FakeResponse(200, b'{"ok":true}'),
        ]
    )
    body, status = SecClient(session, "Alpha a@example.com").get_json(
        "https://data.sec.gov/example.json"
    )
    assert status == 200
    assert json.loads(body) == {"ok": True}
    assert 3.0 in sleeps
    assert len(session.calls) == 3


@pytest.mark.parametrize("status", [400, 403, 404])
def test_client_does_not_retry_permanent_status(status):
    session = FakeSession([FakeResponse(status, b'{"error":"no"}')])
    with pytest.raises(SecHttpError, match=f"SEC_HTTP_STATUS:{status}"):
        SecClient(session, "Alpha a@example.com").get_json(
            "https://data.sec.gov/example.json"
        )
    assert len(session.calls) == 1


def test_client_bounds_transient_retries(monkeypatch):
    monkeypatch.setattr("sec_edgar_pipeline.client.time.sleep", lambda _: None)
    session = FakeSession([FakeResponse(500, b"{}") for _ in range(5)])
    with pytest.raises(SecHttpError, match="SEC_RETRY_EXHAUSTED:500"):
        SecClient(session, "Alpha a@example.com").get_json(
            "https://data.sec.gov/example.json"
        )
    assert len(session.calls) == 4


def test_client_fails_closed_on_redirect_without_following_cross_host_location():
    session = FakeSession(
        [
            FakeResponse(
                302,
                b"{}",
                {"Location": "https://example.com/redirected.json"},
            )
        ]
    )
    with pytest.raises(SecHttpError, match="SEC_HTTP_STATUS:302"):
        SecClient(session, "Alpha a@example.com").get_json(
            "https://data.sec.gov/example.json"
        )
    assert len(session.calls) == 1
    assert session.calls[0][3] is False


@pytest.mark.parametrize(
    ("body", "headers", "message"),
    [
        (b'{"broken":', {}, "SEC_RESPONSE_INVALID_JSON"),
        (b"[]", {}, "SEC_RESPONSE_ROOT_NOT_OBJECT"),
        (b'{"ok":true}', {"Content-Length": "999"}, "SEC_RESPONSE_TRUNCATED"),
    ],
)
def test_client_rejects_invalid_or_truncated_json(body, headers, message):
    session = FakeSession([FakeResponse(200, body, headers)])
    with pytest.raises(ValueError, match=message):
        SecClient(session, "Alpha a@example.com").get_json(
            "https://data.sec.gov/example.json"
        )
