"""Credential-safe, allowlisted, read-only BRAIN metadata client."""

from __future__ import annotations

import getpass
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests


BASE_URL = "https://api.worldquantbrain.com/"
ALLOWED_PATHS = ("/authentication", "/users/self/alphas", "/alphas/", "/data-fields")


class BrainSyncError(RuntimeError): pass
class AuthActionRequired(BrainSyncError): pass
class PageSyncError(BrainSyncError): pass


class BrainClient:
    def __init__(self, transport=None, sleep=time.sleep, max_retries: int = 2, timeout: float = 30.0):
        self.transport = transport or requests.Session()
        self.sleep = sleep
        self.max_retries = max_retries
        self.timeout = timeout

    def _request(self, method: str, path: str, **kwargs):
        if method not in {"GET", "POST"} or (method == "POST" and path != "/authentication"):
            raise BrainSyncError("METHOD_NOT_ALLOWED")
        if not any(path == allowed or (allowed.endswith("/") and path.startswith(allowed)) for allowed in ALLOWED_PATHS):
            raise BrainSyncError("ENDPOINT_NOT_ALLOWED")
        url = urljoin(BASE_URL, path.lstrip("/"))
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
        valid_shape = isinstance(payload, dict) and (payload.get("authenticated") is True or isinstance(payload.get("user"), dict))
        if response.status_code >= 400 or not valid_shape:
            raise BrainSyncError("AUTH_FAILED")
        cookie_header = response.headers.get("Set-Cookie", "")
        cookie = cookie_header.split(";", 1)[0] if cookie_header else ""
        target = Path(session_file); target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"schema_version": 1, "cookie": cookie}) + "\n", encoding="utf-8")
        os.chmod(target, 0o600)
        return {"status": "AUTHENTICATED"}

    def _get_page(self, cursor: str | None, page_size: int):
        for attempt in range(self.max_retries + 1):
            response = self._request("GET", "/users/self/alphas", params={"limit": page_size, **({"cursor": cursor} if cursor else {})})
            if response.status_code not in (429, 500, 502, 503, 504):
                break
            if attempt == self.max_retries:
                raise PageSyncError("PAGE_RETRY_EXHAUSTED")
            delay = float(response.headers.get("Retry-After", "0") or 0)
            self.sleep(max(0.0, delay))
        if response.status_code >= 400:
            raise PageSyncError("PAGE_REQUEST_FAILED")
        try:
            payload = response.json()
        except Exception as exc:
            raise PageSyncError("MALFORMED_PAGE") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list) or any(not isinstance(row, dict) for row in payload["results"]):
            raise PageSyncError("MALFORMED_PAGE")
        return payload

    def sync_library(self, output_dir: Path | str, page_size: int = 100, max_pages: int | None = None) -> dict[str, Any]:
        destination = Path(output_dir); destination.mkdir(parents=True, exist_ok=True)
        manifest_path = destination / "sync_manifest.json"
        cursor = None; pages = 0; records = []; seen_cursors = set(); reason = "UNKNOWN_TOTAL"
        if manifest_path.exists():
            prior = json.loads(manifest_path.read_text(encoding="utf-8"))
            if prior.get("status") != "PARTIAL":
                raise BrainSyncError("OUTPUT_DIRECTORY_NOT_RESUMABLE")
            cursor = prior.get("resume_cursor")
            pages = int(prior.get("pages_completed", 0))
            for page_path in sorted(destination.glob("raw_page_*.json")):
                page = json.loads(page_path.read_text(encoding="utf-8"))
                if not isinstance(page, dict) or not isinstance(page.get("results"), list):
                    raise BrainSyncError("INVALID_RESUME_PAGE")
                records.extend(page["results"])
                if page.get("next") is not None:
                    seen_cursors.add(str(page["next"]))
        while max_pages is None or pages < max_pages:
            try:
                payload = self._get_page(cursor, page_size)
            except PageSyncError:
                manifest = {"status": "PARTIAL", "sync_scope_complete": False, "resume_cursor": cursor, "pages_completed": pages}
                manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
                raise
            (destination / f"raw_page_{pages + 1:05d}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            records.extend(payload["results"]); pages += 1
            next_cursor = payload.get("next")
            if next_cursor is not None and next_cursor in seen_cursors:
                reason = "REPEATED_CURSOR"; break
            if next_cursor is None:
                if isinstance(payload.get("count"), int):
                    reason = "DECLARED_COUNT_REACHED" if len({str(r.get('id')) for r in records if r.get('id') is not None}) == payload["count"] else "COUNT_DRIFT"
                break
            seen_cursors.add(next_cursor); cursor = str(next_cursor)
        else:
            reason = "MAX_PAGES_REACHED"
        ids = [str(row.get("id")) for row in records if row.get("id") is not None]
        unique = len(set(ids)); complete = reason == "DECLARED_COUNT_REACHED"
        result = {"status": "COMPLETE" if complete else "PARTIAL", "unique_alpha_count": unique, "record_count": len(records), "duplicate_record_count": len(ids) - unique, "sync_scope_complete": complete, "search_history_complete": False, "completion_reason": reason, "resume_cursor": None if complete else cursor, "pages_completed": pages}
        manifest_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        return result


def login_interactive(client: BrainClient, session_file: Path | str, input_fn=input, getpass_fn=getpass.getpass):
    return client.authenticate(input_fn("BRAIN username: "), getpass_fn("BRAIN password: "), session_file)
