"""Credential-safe, allowlisted, read-only BRAIN metadata client."""

from __future__ import annotations

import getpass
import json
import os
import time
from http.cookies import SimpleCookie
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import requests


BASE_URL = "https://api.worldquantbrain.com/"
ALLOWED_PATHS = ("/authentication", "/users/self/alphas", "/alphas/", "/data-fields")


class BrainSyncError(RuntimeError): pass
class AuthActionRequired(BrainSyncError): pass
class PageSyncError(BrainSyncError): pass


class BrainClient:
    def __init__(self, transport=None, sleep=time.sleep, now=lambda: datetime.now(timezone.utc), max_retries: int = 2, timeout: float = 30.0):
        self.transport = transport or requests.Session()
        self.sleep = sleep
        self.now = now
        self.max_retries = max_retries
        self.timeout = timeout
        self.authenticated_contract_verified = False

    def _request(self, method: str, path: str, **kwargs):
        parsed = urlparse(path)
        if parsed.scheme or parsed.netloc:
            base = urlparse(BASE_URL)
            if parsed.scheme != base.scheme or parsed.netloc != base.netloc or parsed.fragment:
                raise BrainSyncError("ENDPOINT_NOT_ALLOWED")
            request_path = parsed.path
            url = path
        else:
            request_path = path
            url = urljoin(BASE_URL, path.lstrip("/"))
        if method not in {"GET", "POST"} or (method == "POST" and request_path != "/authentication"):
            raise BrainSyncError("METHOD_NOT_ALLOWED")
        if not any(request_path == allowed or (allowed.endswith("/") and request_path.startswith(allowed)) for allowed in ALLOWED_PATHS):
            raise BrainSyncError("ENDPOINT_NOT_ALLOWED")
        response = self.transport.request(method, url, timeout=self.timeout, allow_redirects=False, **kwargs)
        if 300 <= response.status_code < 400:
            raise BrainSyncError("REDIRECT_BLOCKED")
        return response

    def authenticate(self, username: str, password: str, session_file: Path | str) -> dict[str, Any]:
        response = self._request("POST", "/authentication", auth=(username, password))
        try:
            payload = response.json()
        except Exception:
            payload = None
        if response.status_code in (401, 403) or (isinstance(payload, dict) and any(payload.get(k) for k in ("verificationRequired", "actionRequired"))):
            raise AuthActionRequired("AUTH_ACTION_REQUIRED")
        user = payload.get("user") if isinstance(payload, dict) else None
        valid_shape = isinstance(user, dict) and isinstance(user.get("id"), str) and bool(user["id"].strip())
        if response.status_code >= 400 or not valid_shape:
            raise BrainSyncError("AUTH_RESPONSE_INVALID")
        cookie_header = response.headers.get("Set-Cookie", "")
        parsed_cookies = SimpleCookie()
        try:
            parsed_cookies.load(cookie_header)
        except Exception as exc:
            raise BrainSyncError("AUTH_SESSION_MISSING") from exc
        cookies = {name: morsel.value for name, morsel in parsed_cookies.items() if morsel.value}
        if not cookies:
            raise BrainSyncError("AUTH_SESSION_MISSING")
        cookie_header_value = "; ".join(f"{name}={value}" for name, value in cookies.items())
        capability = self._request("GET", "/users/self/alphas", params={"limit": 1}, headers={"Cookie": cookie_header_value})
        if capability.status_code in (401, 403):
            raise AuthActionRequired("AUTH_ACTION_REQUIRED")
        try:
            capability_payload = capability.json()
        except Exception as exc:
            raise BrainSyncError("AUTH_METADATA_INVALID") from exc
        if isinstance(capability_payload, dict) and any(capability_payload.get(key) for key in ("verificationRequired", "actionRequired")):
            raise AuthActionRequired("AUTH_ACTION_REQUIRED")
        if capability.status_code >= 400 or not isinstance(capability_payload, dict) or not isinstance(capability_payload.get("results"), list):
            raise BrainSyncError("AUTH_METADATA_INVALID")
        target = Path(session_file); target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"schema_version": 1, "cookies": cookies, "metadata_capability_verified": True}) + "\n", encoding="utf-8")
        os.chmod(target, 0o600)
        self.authenticated_contract_verified = True
        self._write_evidence(target.parent, authenticated=True, pagination_payload=capability_payload)
        return {"status": "AUTHENTICATED"}

    def _retry_delay(self, value: str | None) -> float:
        if not value:
            return 0.0
        try:
            delay = float(value)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                delay = (retry_at - self.now()).total_seconds()
            except (TypeError, ValueError, OverflowError) as exc:
                raise PageSyncError("INVALID_RETRY_AFTER") from exc
        return max(0.0, delay)

    def _pagination_request(self, token: Any, page_size: int) -> tuple[str, dict[str, Any] | None, str]:
        if token is None:
            return "/users/self/alphas", {"limit": page_size}, "initial"
        if isinstance(token, int) and not isinstance(token, bool):
            return "/users/self/alphas", {"limit": page_size, "offset": token}, "offset"
        if not isinstance(token, str):
            raise PageSyncError("UNSUPPORTED_PAGINATION")
        parsed = urlparse(token)
        if parsed.scheme or parsed.netloc:
            base = urlparse(BASE_URL)
            if parsed.scheme != base.scheme or parsed.netloc != base.netloc or parsed.path != "/users/self/alphas" or parsed.fragment:
                raise PageSyncError("UNSUPPORTED_PAGINATION")
            query = parse_qs(parsed.query, keep_blank_values=True)
            if not ("offset" in query or "cursor" in query):
                raise PageSyncError("UNSUPPORTED_PAGINATION")
            return token, None, "next_url"
        return "/users/self/alphas", {"limit": page_size, "cursor": token}, "cursor"

    def _get_page(self, token: Any, page_size: int):
        target, params, scheme = self._pagination_request(token, page_size)
        for attempt in range(self.max_retries + 1):
            request_kwargs = {"params": params} if params is not None else {}
            response = self._request("GET", target, **request_kwargs)
            if response.status_code not in (429, 500, 502, 503, 504):
                break
            if attempt == self.max_retries:
                raise PageSyncError("PAGE_RETRY_EXHAUSTED")
            self.sleep(self._retry_delay(response.headers.get("Retry-After")))
        if response.status_code >= 400:
            raise PageSyncError("PAGE_REQUEST_FAILED")
        try:
            payload = response.json()
        except Exception as exc:
            raise PageSyncError("MALFORMED_PAGE") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list) or any(not isinstance(row, dict) for row in payload["results"]):
            raise PageSyncError("MALFORMED_PAGE")
        return payload, scheme

    def _write_evidence(self, destination: Path, authenticated: bool, pagination_payload: dict[str, Any] | None = None, scheme: str | None = None) -> None:
        observed_fields = sorted(key for key in ("count", "next", "previous") if pagination_payload is not None and key in pagination_payload)
        evidence = {
            "schema_version": 1,
            "queried_endpoint": "GET /users/self/alphas",
            "public_documentation_status": "UNVERIFIED_IN_THIS_RUN",
            "authenticated_contract_verified": authenticated,
            "observed_pagination_fields": observed_fields,
            "observed_pagination_scheme": scheme or "not_observed",
            "contains_response_body_or_secrets": False,
        }
        (destination / "evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def sync_library(self, output_dir: Path | str, page_size: int = 100, max_pages: int | None = None) -> dict[str, Any]:
        destination = Path(output_dir); destination.mkdir(parents=True, exist_ok=True)
        manifest_path = destination / "sync_manifest.json"
        cursor = None; pages = 0; records = []; seen_cursors = set(); reason = "UNKNOWN_TOTAL"
        declared_counts: list[int | None] = []
        last_payload = None; pagination_scheme = "initial"
        if manifest_path.exists():
            prior = json.loads(manifest_path.read_text(encoding="utf-8"))
            if prior.get("status") != "PARTIAL":
                raise BrainSyncError("OUTPUT_DIRECTORY_NOT_RESUMABLE")
            cursor = prior.get("resume_cursor")
            pages = int(prior.get("pages_completed", 0))
            page_paths = sorted(destination.glob("raw_page_*.json"))
            if len(page_paths) != pages:
                raise BrainSyncError("INVALID_RESUME_CHAIN")
            last_resume_next = None
            for page_path in page_paths:
                page = json.loads(page_path.read_text(encoding="utf-8"))
                if not isinstance(page, dict) or not isinstance(page.get("results"), list):
                    raise BrainSyncError("INVALID_RESUME_PAGE")
                records.extend(page["results"])
                declared_counts.append(page.get("count") if isinstance(page.get("count"), int) and not isinstance(page.get("count"), bool) else None)
                if page.get("next") is not None:
                    last_resume_next = page["next"]
                    seen_cursors.add(str(page["next"]))
            if last_resume_next != cursor:
                raise BrainSyncError("INVALID_RESUME_CHAIN")
        while max_pages is None or pages < max_pages:
            try:
                payload, pagination_scheme = self._get_page(cursor, page_size)
            except PageSyncError:
                manifest = {"status": "PARTIAL", "sync_scope_complete": False, "resume_cursor": cursor, "pages_completed": pages}
                manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
                self._write_evidence(destination, authenticated=self.authenticated_contract_verified, pagination_payload=last_payload, scheme=pagination_scheme)
                raise
            last_payload = payload
            (destination / f"raw_page_{pages + 1:05d}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            records.extend(payload["results"]); pages += 1
            declared_counts.append(payload.get("count") if isinstance(payload.get("count"), int) and not isinstance(payload.get("count"), bool) else None)
            next_cursor = payload.get("next")
            if next_cursor is not None and not isinstance(next_cursor, (str, int)):
                reason = "UNSUPPORTED_PAGINATION"; break
            if next_cursor is not None and str(next_cursor) in seen_cursors:
                reason = "REPEATED_CURSOR"; break
            if next_cursor is None:
                if declared_counts and all(count is not None for count in declared_counts):
                    stable = len(set(declared_counts)) == 1
                    reason = "DECLARED_COUNT_REACHED" if stable and len({str(r.get('id')) for r in records if r.get('id') is not None}) == declared_counts[0] else "COUNT_DRIFT"
                break
            seen_cursors.add(str(next_cursor)); cursor = next_cursor
        else:
            reason = "MAX_PAGES_REACHED"
        ids = [str(row.get("id")) for row in records if row.get("id") is not None]
        unique = len(set(ids)); complete = reason == "DECLARED_COUNT_REACHED"
        result = {"status": "COMPLETE" if complete else "PARTIAL", "unique_alpha_count": unique, "record_count": len(records), "duplicate_record_count": len(ids) - unique, "sync_scope_complete": complete, "search_history_complete": False, "completion_reason": reason, "resume_cursor": None if complete else cursor, "pages_completed": pages}
        manifest_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        self._write_evidence(destination, authenticated=self.authenticated_contract_verified, pagination_payload=last_payload, scheme=pagination_scheme)
        return result


def login_interactive(client: BrainClient, session_file: Path | str, input_fn=input, getpass_fn=getpass.getpass):
    return client.authenticate(input_fn("BRAIN username: "), getpass_fn("BRAIN password: "), session_file)
