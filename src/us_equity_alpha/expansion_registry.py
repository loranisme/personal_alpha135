"""Append-only definitions for factors created after the immutable V4 library."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = {
    "factor_id",
    "factor_version",
    "expression",
    "economic_hypothesis",
    "mechanism_tag",
    "required_fields",
    "allowed_providers",
    "settings",
    "direction",
    "parent_factor_ids",
    "generation_method",
    "created_at",
    "supersedes_factor_id",
    "compute_status",
}

ALLOWED_GENERATION_METHODS = {
    "HUMAN_AUTHORED",
    "FORMULA_TRANSFORM",
    "IMPORTED_SOURCE",
}

ALLOWED_COMPUTE_STATUSES = {
    "REGISTERED",
    "COMPUTE_VERIFIED",
    "COMPUTE_FAILED",
    "SUPERSEDED",
    "RETIRED",
}

_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _sorted_unique_strings(value: Any, field: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"EXPANSION_INVALID_{field.upper()}")
    normalized = sorted(set(value))
    if not normalized and not allow_empty:
        raise ValueError(f"EXPANSION_INVALID_{field.upper()}")
    return normalized


def _normalize(definition: Mapping[str, Any]) -> dict[str, Any]:
    if set(definition) != REQUIRED_FIELDS:
        unknown = sorted(set(definition) - REQUIRED_FIELDS)
        missing = sorted(REQUIRED_FIELDS - set(definition))
        if unknown:
            raise ValueError(f"EXPANSION_UNKNOWN_FIELDS:{','.join(unknown)}")
        raise ValueError(f"EXPANSION_MISSING_FIELDS:{','.join(missing)}")

    factor_id = definition["factor_id"]
    if not isinstance(factor_id, str) or _SAFE_ID.fullmatch(factor_id) is None:
        raise ValueError("EXPANSION_INVALID_FACTOR_ID")
    version = definition["factor_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError("EXPANSION_INVALID_FACTOR_VERSION")
    if definition["direction"] != 1:
        raise ValueError("EXPANSION_DIRECTION_MUST_BE_ONE")
    if definition["generation_method"] not in ALLOWED_GENERATION_METHODS:
        raise ValueError("EXPANSION_INVALID_GENERATION_METHOD")
    if definition["compute_status"] not in ALLOWED_COMPUTE_STATUSES:
        raise ValueError("EXPANSION_INVALID_COMPUTE_STATUS")
    for field in ("expression", "economic_hypothesis", "mechanism_tag", "created_at"):
        if not isinstance(definition[field], str) or not definition[field].strip():
            raise ValueError(f"EXPANSION_INVALID_{field.upper()}")
    if not isinstance(definition["settings"], Mapping):
        raise ValueError("EXPANSION_INVALID_SETTINGS")
    supersedes = definition["supersedes_factor_id"]
    if supersedes is not None and (
        not isinstance(supersedes, str) or _SAFE_ID.fullmatch(supersedes) is None
    ):
        raise ValueError("EXPANSION_INVALID_SUPERSEDES_FACTOR_ID")

    record = dict(definition)
    record["required_fields"] = _sorted_unique_strings(
        definition["required_fields"], "required_fields"
    )
    record["allowed_providers"] = _sorted_unique_strings(
        definition["allowed_providers"], "allowed_providers"
    )
    record["parent_factor_ids"] = _sorted_unique_strings(
        definition["parent_factor_ids"], "parent_factor_ids", allow_empty=True
    )
    record["settings"] = dict(definition["settings"])
    record["expression_hash"] = hashlib.sha256(
        record["expression"].encode("utf-8")
    ).hexdigest()
    record["lineage_hash"] = _hash(
        {
            "generation_method": record["generation_method"],
            "parent_factor_ids": record["parent_factor_ids"],
            "supersedes_factor_id": record["supersedes_factor_id"],
        }
    )
    record["content_hash"] = _hash(record)
    return record


def register_expansion(
    definition: Mapping[str, Any], registry_dir: Path | str
) -> dict[str, Any]:
    """Normalize and exclusively create one immutable factor-version record."""
    if not isinstance(definition, Mapping):
        raise ValueError("EXPANSION_DEFINITION_NOT_OBJECT")
    record = _normalize(definition)
    root = Path(registry_dir)
    factor_dir = root / record["factor_id"]
    factor_dir.mkdir(parents=True, exist_ok=True)
    path = factor_dir / f"v{record['factor_version']}.json"
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
    except FileExistsError as exc:
        raise FileExistsError(
            f"EXPANSION_VERSION_EXISTS:{record['factor_id']}:v{record['factor_version']}"
        ) from exc
    return {**record, "path": str(path.resolve())}


def load_expansion_snapshot(registry_dir: Path | str) -> list[dict[str, Any]]:
    """Load all registered versions after verifying their immutable hashes."""
    root = Path(registry_dir)
    if not root.exists():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(root.glob("*/v*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"EXPANSION_RECORD_NOT_OBJECT:{path}")
        declared = payload.pop("content_hash", None)
        if declared != _hash(payload):
            raise ValueError(f"EXPANSION_CONTENT_HASH_MISMATCH:{path}")
        payload["content_hash"] = declared
        records.append(payload)
    identities = [(row["factor_id"], row["factor_version"]) for row in records]
    if len(identities) != len(set(identities)):
        raise ValueError("EXPANSION_DUPLICATE_IDENTITY")
    return sorted(records, key=lambda row: (row["factor_id"], row["factor_version"]))

