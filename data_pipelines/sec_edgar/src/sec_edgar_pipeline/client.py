"""Rate-limited HTTP client for official SEC JSON resources."""

from __future__ import annotations

import json
import time
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

import requests


TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})
ALLOWED_SEC_HOSTS = frozenset({"data.sec.gov", "www.sec.gov"})


class SecHttpError(RuntimeError):
    """Raised when an SEC request cannot return a valid JSON object."""


class SecClient:
    """Small, deterministic SEC client with conservative retry behavior."""

    def __init__(
        self,
        session: requests.Session,
        user_agent: str,
        maximum_requests_per_second: float = 5.0,
    ) -> None:
        user_agent = str(user_agent).strip()
        if not user_agent or "@" not in user_agent:
            raise ValueError("SEC_USER_AGENT_MUST_INCLUDE_CONTACT")
        if not 0 < float(maximum_requests_per_second) <= 5.0:
            raise ValueError("SEC_REQUEST_RATE_OUT_OF_RANGE")
        self._session = session
        # requests.Session otherwise merges its generic Accept/Connection
        # defaults into each call.  The acquisition contract permits only the
        # three headers supplied explicitly in get_json.
        session_headers = getattr(self._session, "headers", None)
        if session_headers is not None:
            session_headers.clear()
        self._user_agent = user_agent
        self._minimum_interval = 1.0 / float(maximum_requests_per_second)
        self._last_request_started: float | None = None
        self._maximum_attempts = 4
        self._timeout = (10.0, 30.0)

    def _pace(self) -> None:
        now = time.monotonic()
        if self._last_request_started is not None:
            remaining = self._minimum_interval - (now - self._last_request_started)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_started = time.monotonic()

    @staticmethod
    def _retry_after_seconds(value: str | None) -> float | None:
        if value is None:
            return None
        try:
            return min(60.0, max(0.0, float(value)))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
                if retry_at.tzinfo is None:
                    return None
                return min(60.0, max(0.0, retry_at.timestamp() - time.time()))
            except (TypeError, ValueError, OverflowError):
                return None

    @staticmethod
    def _validate_json_object(body: bytes, headers: Any) -> None:
        content_length = headers.get("Content-Length") if headers is not None else None
        content_encoding = (
            str(headers.get("Content-Encoding", "")).strip().lower()
            if headers is not None
            else ""
        )
        # requests exposes decompressed bytes in ``content``.  A compressed wire
        # length therefore cannot be compared with the decoded body length.
        if content_length is not None and content_encoding in {"", "identity"}:
            try:
                if int(content_length) != len(body):
                    raise ValueError("SEC_RESPONSE_TRUNCATED")
            except (TypeError, ValueError) as exc:
                if str(exc) == "SEC_RESPONSE_TRUNCATED":
                    raise
                raise ValueError("SEC_RESPONSE_INVALID_CONTENT_LENGTH") from exc
        try:
            decoded = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("SEC_RESPONSE_INVALID_JSON") from exc
        if not isinstance(decoded, dict):
            raise ValueError("SEC_RESPONSE_ROOT_NOT_OBJECT")

    def get_json(self, url: str) -> tuple[bytes, int]:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_SEC_HOSTS:
            raise ValueError("SEC_URL_NOT_ALLOWED")
        headers = {
            "Accept-Encoding": "gzip, deflate",
            "Host": parsed.hostname,
            "User-Agent": self._user_agent,
        }
        last_status: int | None = None
        for attempt in range(self._maximum_attempts):
            self._pace()
            try:
                response = self._session.get(
                    url,
                    headers=headers,
                    timeout=self._timeout,
                    allow_redirects=False,
                )
            except requests.RequestException as exc:
                if attempt + 1 == self._maximum_attempts:
                    raise SecHttpError("SEC_REQUEST_RETRY_EXHAUSTED") from exc
                time.sleep(float(2**attempt))
                continue
            status = int(response.status_code)
            last_status = status
            if status == 200:
                body = bytes(response.content)
                self._validate_json_object(body, response.headers)
                return body, status
            if status not in TRANSIENT_STATUSES:
                raise SecHttpError(f"SEC_HTTP_STATUS:{status}")
            if attempt + 1 == self._maximum_attempts:
                break
            retry_after = self._retry_after_seconds(response.headers.get("Retry-After"))
            time.sleep(retry_after if retry_after is not None else float(2**attempt))
        raise SecHttpError(f"SEC_RETRY_EXHAUSTED:{last_status}")
