"""Content-addressed raw SEC storage and finite per-CIK acquisition."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

try:
    import fcntl
except ImportError as exc:  # pragma: no cover - the pipeline supports POSIX hosts only
    raise RuntimeError("SEC_RAW_STORE_REQUIRES_POSIX_FILE_LOCKS") from exc

from .client import SecClient
from .issuer_seed import normalize_cik


PIPELINE_VERSION = "1"
TICKER_CIK_INDEX_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
_SAFE_SUBMISSIONS_FILE = re.compile(r"[A-Za-z0-9._-]+\.json")
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True)
class RawArtifact:
    source_type: str
    cik: str
    source_url: str
    requested_at: str
    first_seen_at: str
    http_status: int
    size_bytes: int
    sha256: str
    object_path: str
    pipeline_version: str
    receipt_time_verified: bool = False


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("OBSERVED_AT_MUST_BE_TIMEZONE_AWARE")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_line(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


@contextmanager
def _ledger_lock(root: Path):
    """Serialize ledger replacement across threads and local processes.

    The stable flock file lives in the operating-system temporary directory,
    outside both the immutable raw root and every published release.  Keeping
    that inode stable avoids the race caused by deleting a sidecar lock while
    another process is waiting on it.  The kernel releases flock automatically
    if a worker exits unexpectedly.
    """

    root_key = str(root.resolve())
    with _THREAD_LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(root_key, threading.RLock())
    lock_root = Path(tempfile.gettempdir()) / "personal_alpha_sec_edgar_locks"
    lock_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_name = hashlib.sha256(root_key.encode("utf-8")).hexdigest() + ".lock"
    lock_path = lock_root / lock_name
    with thread_lock:
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def _verified_object(root: Path, artifact: RawArtifact) -> Path | None:
    relative = Path(artifact.object_path)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    path = root / relative
    if not path.is_file() or path.is_symlink():
        return None
    content = path.read_bytes()
    if len(content) != artifact.size_bytes:
        return None
    if hashlib.sha256(content).hexdigest() != artifact.sha256:
        return None
    return path


def store_raw_response(
    root: Path | str, artifact: RawArtifact, content: bytes
) -> Path:
    root_path = Path(root)
    digest = hashlib.sha256(content).hexdigest()
    if digest != artifact.sha256:
        raise ValueError("RAW_ARTIFACT_HASH_MISMATCH")
    if len(content) != artifact.size_bytes:
        raise ValueError("RAW_ARTIFACT_SIZE_MISMATCH")
    object_path = root_path / "objects" / digest[:2] / f"{digest}.json"
    object_path.parent.mkdir(parents=True, exist_ok=True)
    stored = replace(artifact, object_path=object_path.relative_to(root_path).as_posix())
    ledger = root_path / "download_ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    line = _canonical_line(asdict(stored)) + "\n"
    with _ledger_lock(root_path):
        try:
            descriptor = os.open(
                object_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
        except FileExistsError:
            if object_path.is_symlink():
                raise ValueError(f"RAW_OBJECT_INTEGRITY_FAILURE:{digest}")
            existing = object_path.read_bytes()
            if (
                len(existing) != len(content)
                or hashlib.sha256(existing).hexdigest() != digest
            ):
                raise ValueError(f"RAW_OBJECT_INTEGRITY_FAILURE:{digest}")
        else:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())

        previous = ledger.read_text(encoding="utf-8") if ledger.exists() else ""
        if previous and not previous.endswith("\n"):
            raise ValueError("RAW_LEDGER_NOT_NEWLINE_TERMINATED")
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{ledger.name}.", suffix=".tmp", dir=ledger.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(previous)
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, ledger)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
    return object_path


def find_verified_observation(
    root: Path | str,
    source_url: str,
    requested_at: str | None = None,
) -> RawArtifact | None:
    root_path = Path(root)
    ledger = root_path / "download_ledger.jsonl"
    if not ledger.exists():
        return None
    rows: list[RawArtifact] = []
    for line_number, line in enumerate(ledger.read_text(encoding="utf-8").splitlines(), 1):
        try:
            payload = json.loads(line)
            rows.append(RawArtifact(**payload))
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"RAW_LEDGER_INVALID_ROW:{line_number}") from exc
    for artifact in reversed(rows):
        if (
            artifact.source_url == source_url
            and (requested_at is None or artifact.requested_at == requested_at)
            and _verified_object(root_path, artifact)
        ):
            return artifact
    return None


def _first_seen_for_content(
    root: Path | str, source_url: str, digest: str, fallback: str
) -> str:
    root_path = Path(root)
    ledger = root_path / "download_ledger.jsonl"
    if not ledger.exists():
        return fallback
    first_seen: list[str] = []
    for line_number, line in enumerate(ledger.read_text(encoding="utf-8").splitlines(), 1):
        try:
            artifact = RawArtifact(**json.loads(line))
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"RAW_LEDGER_INVALID_ROW:{line_number}") from exc
        if (artifact.source_url == source_url and artifact.sha256 == digest
                and artifact.receipt_time_verified is True):
            first_seen.append(artifact.first_seen_at)
    return min(first_seen, default=fallback)


def read_raw_artifact(root: Path | str, artifact: RawArtifact) -> bytes:
    path = _verified_object(Path(root), artifact)
    if path is None:
        raise ValueError(f"RAW_OBJECT_NOT_VERIFIED:{artifact.sha256}")
    return path.read_bytes()


def _fetch_artifact(
    client: SecClient,
    raw_root: Path | str,
    *,
    source_type: str,
    cik: str,
    source_url: str,
    observed_at: datetime,
    allow_resume: bool = True,
) -> RawArtifact:
    observed = _utc_iso(observed_at)
    if allow_resume:
        existing = find_verified_observation(
            raw_root, source_url, requested_at=observed
        )
        if existing is not None and existing.receipt_time_verified is True:
            return existing
    body, status = client.get_json(source_url)
    received = _utc_iso(datetime.now(timezone.utc))
    digest = hashlib.sha256(body).hexdigest()
    artifact = RawArtifact(
        source_type=source_type,
        cik=cik,
        source_url=source_url,
        requested_at=observed,
        first_seen_at=_first_seen_for_content(
            raw_root, source_url, digest, received
        ),
        http_status=status,
        size_bytes=len(body),
        sha256=digest,
        object_path="",
        pipeline_version=PIPELINE_VERSION,
        receipt_time_verified=True,
    )
    object_path = store_raw_response(raw_root, artifact, body)
    return replace(artifact, object_path=object_path.relative_to(Path(raw_root)).as_posix())


def download_ticker_cik_index(
    client: SecClient,
    raw_root: Path | str,
    observed_at: datetime,
) -> RawArtifact:
    _utc_iso(observed_at)
    return _fetch_artifact(
        client,
        raw_root,
        source_type="ticker_cik_index",
        cik="0000000000",
        source_url=TICKER_CIK_INDEX_URL,
        observed_at=observed_at,
        allow_resume=True,
    )


def download_cik_bundle(
    client: SecClient,
    raw_root: Path | str,
    cik: str,
    observed_at: datetime,
) -> list[RawArtifact]:
    _utc_iso(observed_at)
    normalized = normalize_cik(cik)
    companyfacts_url = (
        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{normalized}.json"
    )
    submissions_url = f"https://data.sec.gov/submissions/CIK{normalized}.json"
    companyfacts = _fetch_artifact(
        client,
        raw_root,
        source_type="companyfacts",
        cik=normalized,
        source_url=companyfacts_url,
        observed_at=observed_at,
    )
    submissions = _fetch_artifact(
        client,
        raw_root,
        source_type="submissions",
        cik=normalized,
        source_url=submissions_url,
        observed_at=observed_at,
    )
    result = [companyfacts, submissions]
    payload = json.loads(read_raw_artifact(raw_root, submissions))
    older_files = payload.get("filings", {}).get("files", [])
    if not isinstance(older_files, list):
        raise ValueError("SEC_SUBMISSIONS_FILES_NOT_LIST")
    seen_names: set[str] = set()
    for row in older_files:
        name = row.get("name") if isinstance(row, dict) else None
        if not isinstance(name, str) or not _SAFE_SUBMISSIONS_FILE.fullmatch(name):
            raise ValueError("SEC_SUBMISSIONS_UNSAFE_HISTORY_FILENAME")
        if name in seen_names:
            continue
        seen_names.add(name)
        result.append(
            _fetch_artifact(
                client,
                raw_root,
                source_type="submissions_file",
                cik=normalized,
                source_url=f"https://data.sec.gov/submissions/{name}",
                observed_at=observed_at,
            )
        )
    return result


def download_universe(
    client: SecClient,
    raw_root: Path | str,
    universe: pd.DataFrame,
    observed_at: datetime,
) -> pd.DataFrame:
    if "issuer_cik" not in universe.columns:
        raise ValueError("RESOLVED_UNIVERSE_MISSING_ISSUER_CIK")
    records: list[dict[str, Any]] = []
    ciks = sorted({normalize_cik(value) for value in universe["issuer_cik"].dropna()})
    for cik in ciks:
        records.extend(asdict(row) for row in download_cik_bundle(client, raw_root, cik, observed_at))
    return pd.DataFrame.from_records(records, columns=list(RawArtifact.__dataclass_fields__))
