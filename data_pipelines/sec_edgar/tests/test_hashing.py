from __future__ import annotations

import json
from pathlib import Path

import pytest

from sec_edgar_pipeline.hashing import (
    canonical_json_hash,
    sha256_file,
    verify_output_hashes,
    write_json_exclusive,
)


def test_canonical_hash_is_order_independent() -> None:
    assert canonical_json_hash({"b": 2, "a": 1}) == canonical_json_hash({"a": 1, "b": 2})


def test_canonical_hash_rejects_nan() -> None:
    with pytest.raises(ValueError):
        canonical_json_hash({"value": float("nan")})


def test_exclusive_json_and_hash_verification_detect_tampering(tmp_path: Path) -> None:
    path = write_json_exclusive(tmp_path / "record.json", {"b": 2, "a": 1})
    assert path.read_text(encoding="utf-8") == '{"a":1,"b":2}\n'
    expected = sha256_file(path)
    verify_output_hashes(tmp_path, {"record.json": expected})
    path.write_text('{"a":9}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="OUTPUT_HASH_MISMATCH:record.json"):
        verify_output_hashes(tmp_path, {"record.json": expected})


def test_exclusive_write_refuses_overwrite(tmp_path: Path) -> None:
    path = write_json_exclusive(tmp_path / "record.json", {"a": 1})
    with pytest.raises(FileExistsError):
        write_json_exclusive(path, {"a": 2})


def test_exclusive_write_does_not_leave_file_when_serialization_fails(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    with pytest.raises(ValueError):
        write_json_exclusive(path, {"value": float("nan")})
    assert not path.exists()


@pytest.mark.parametrize("unsafe", ["/tmp/record.json", "../record.json", "nested/../record.json"])
def test_hash_verification_rejects_unsafe_paths(tmp_path: Path, unsafe: str) -> None:
    with pytest.raises(ValueError, match="OUTPUT_PATH_UNSAFE"):
        verify_output_hashes(tmp_path, {unsafe: "0" * 64})


def test_hash_verification_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="OUTPUT_FILE_MISSING:missing.json"):
        verify_output_hashes(tmp_path, {"missing.json": "0" * 64})


def test_hash_verification_rejects_symlink_escaping_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.json"
    outside.write_text(json.dumps({"secret": False}), encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(outside)
    try:
        with pytest.raises(ValueError, match="OUTPUT_PATH_ESCAPES_ROOT:link.json"):
            verify_output_hashes(tmp_path, {"link.json": sha256_file(outside)})
    finally:
        outside.unlink()
