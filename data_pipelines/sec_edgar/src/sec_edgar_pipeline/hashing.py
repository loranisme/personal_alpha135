"""Deterministic hashes and fail-closed release-file verification."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path, PurePath
from typing import Any, Mapping


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_json_hash(value: Any) -> str:
    """Return the SHA-256 digest of canonical UTF-8 JSON."""

    return sha256(_canonical_json_bytes(value)).hexdigest()


def sha256_file(path: Path | str) -> str:
    """Hash a file by streaming bytes from disk."""

    digest = sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_exclusive(path: Path | str, payload: Mapping[str, Any]) -> Path:
    """Write canonical JSON once, refusing to replace an existing artifact."""

    destination = Path(path)
    serialized = _canonical_json_bytes(payload).decode("utf-8")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(serialized)
        handle.write("\n")
    return destination


def _safe_relative_path(value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in PurePath(value).parts:
        raise ValueError(f"OUTPUT_PATH_UNSAFE:{value}")
    return relative


def verify_output_hashes(root: Path | str, output_hashes: Mapping[str, str]) -> None:
    """Verify manifest paths remain within *root* and match declared hashes."""

    release_root = Path(root).resolve()
    for relative_name, expected_hash in output_hashes.items():
        relative = _safe_relative_path(relative_name)
        candidate = release_root / relative
        if not candidate.exists():
            raise ValueError(f"OUTPUT_FILE_MISSING:{relative_name}")
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(release_root):
            raise ValueError(f"OUTPUT_PATH_ESCAPES_ROOT:{relative_name}")
        if not resolved.is_file():
            raise ValueError(f"OUTPUT_FILE_NOT_REGULAR:{relative_name}")
        actual_hash = sha256_file(resolved)
        if actual_hash != expected_hash:
            raise ValueError(f"OUTPUT_HASH_MISMATCH:{relative_name}")
