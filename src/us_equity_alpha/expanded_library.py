"""Performance-blind formula deduplication for an isolated SEC library release."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from .reconstruction import _parse


def _digest(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _canonical_node(node: ast.AST) -> ast.AST:
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        return ast.Name(id=node.id, ctx=ast.Load())
    if isinstance(node, ast.Constant) and type(node.value) in {int, float, bool}:
        value = node.value
        if type(value) is float:
            if not math.isfinite(value):
                raise ValueError("CANONICAL_NONFINITE_CONSTANT")
            if value.is_integer():
                value = int(value)
        return ast.Constant(value=value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return ast.UnaryOp(op=type(node.op)(), operand=_canonical_node(node.operand))
    if isinstance(node, ast.BinOp) and isinstance(
        node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)
    ):
        left, right = _canonical_node(node.left), _canonical_node(node.right)
        if isinstance(node.op, (ast.Add, ast.Mult)) and ast.dump(left) > ast.dump(right):
            left, right = right, left
        return ast.BinOp(left=left, op=type(node.op)(), right=right)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
        return ast.Call(
            func=ast.Name(id=node.func.id.lower(), ctx=ast.Load()),
            args=[_canonical_node(arg) for arg in node.args], keywords=[],
        )
    if isinstance(node, ast.Compare) and len(node.ops) == len(node.comparators) == 1:
        if not isinstance(node.ops[0], (ast.Gt, ast.GtE, ast.Lt, ast.LtE, ast.Eq, ast.NotEq)):
            raise ValueError("CANONICAL_EXPRESSION_UNSUPPORTED")
        return ast.Compare(
            left=_canonical_node(node.left), ops=[type(node.ops[0])()],
            comparators=[_canonical_node(node.comparators[0])],
        )
    raise ValueError("CANONICAL_EXPRESSION_UNSUPPORTED")


def _semantic_bindings(value: Sequence[Mapping[str, Any]] | Mapping[str, Any]) -> list[dict[str, str]]:
    if isinstance(value, Mapping):
        rows = []
        for alias, bound in value.items():
            if isinstance(bound, str) and "/" in bound:
                field, basis = bound.rsplit("/", 1)
            elif isinstance(bound, Mapping):
                field = bound.get("field", bound.get("canonical_field"))
                basis = bound.get("basis", bound.get("required_basis"))
            else:
                field, basis = bound, "UNSPECIFIED"
            rows.append({"alias": alias, "field": field, "basis": basis})
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        rows = [dict(item) for item in value]
    else:
        raise ValueError("CANONICAL_FIELD_BINDINGS_INVALID")
    canonical = []
    for row in rows:
        alias = row.get("alias")
        field = row.get("field", row.get("canonical_field"))
        basis = row.get("basis", row.get("required_basis"))
        if not all(isinstance(item, str) and item for item in (alias, field, basis)):
            raise ValueError("CANONICAL_FIELD_BINDINGS_INVALID")
        canonical.append({"alias": alias, "field": field, "basis": basis})
    if len({row["alias"] for row in canonical}) != len(canonical):
        raise ValueError("CANONICAL_FIELD_BINDINGS_DUPLICATE")
    return sorted(canonical, key=lambda row: (row["alias"], row["field"], row["basis"]))


def canonical_factor_identity(
    expression: str,
    field_bindings: Sequence[Mapping[str, Any]] | Mapping[str, Any],
    settings: Mapping[str, Any],
) -> tuple[str, str]:
    """Return canonical AST and semantic identity hashes, excluding returns."""
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("CANONICAL_EXPRESSION_MISSING")
    if not isinstance(settings, Mapping):
        raise ValueError("CANONICAL_SETTINGS_INVALID")
    tree = _canonical_node(_parse(expression))
    ast_hash = _digest(ast.dump(tree, include_attributes=False))
    identity_hash = _digest({
        "ast_hash": ast_hash,
        "field_bindings": _semantic_bindings(field_bindings),
        "settings": dict(settings),
    })
    return ast_hash, identity_hash


def _identity(record: Mapping[str, Any]) -> str:
    return canonical_factor_identity(
        record["expression"], record["field_bindings"], record["settings"]
    )[1]


def _relationships(candidate: Mapping[str, Any], factor_id: str) -> list[dict[str, Any]]:
    source_ids = candidate.get("source_alpha_ids", [])
    if not isinstance(source_ids, list) or not source_ids or any(
        not isinstance(item, str) or not item for item in source_ids
    ):
        raise ValueError("SEC_CANDIDATE_SOURCE_IDS_INVALID")
    return [{
        "source_alpha_id": source_id,
        "local_factor_id": factor_id,
        "semantic_status": candidate["semantic_status"],
        "provenance_kind": candidate["provenance_kind"],
        "reconstruction_rule_hash": candidate.get("reconstruction_rule_hash", ""),
    } for source_id in sorted(set(source_ids))]


def deduplicate_sec_candidates(
    v4_library: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], pd.DataFrame, list[dict[str, Any]]]:
    """Retain V4 IDs and every source-level strict/proxy relation."""
    base_by_identity: dict[str, str] = {}
    for factor in v4_library.get("factors", []):
        key = _identity(factor)
        existing = base_by_identity.setdefault(key, factor["local_factor_id"])
        if existing != factor["local_factor_id"]:
            raise ValueError("V4_DUPLICATE_SEMANTIC_IDENTITY")

    indexed = []
    for candidate in candidates:
        if candidate.get("build_status") != "COMPUTE_VERIFIED":
            raise ValueError("SEC_CANDIDATE_NOT_COMPUTE_VERIFIED")
        if candidate.get("usage_tier") != "DIAGNOSTIC_ONLY":
            raise ValueError("SEC_CANDIDATE_USAGE_INVALID")
        if candidate.get("provenance_kind") not in {
            "SEC_SOURCE_LINKED", "SEC_HYPOTHESIS_PROXY"
        }:
            raise ValueError("SEC_CANDIDATE_PROVENANCE_INVALID")
        expected_semantics = {
            "SEC_SOURCE_LINKED": "SOURCE_LINKED",
            "SEC_HYPOTHESIS_PROXY": "HYPOTHESIS_PROXY",
        }
        if candidate.get("semantic_status") != expected_semantics[candidate["provenance_kind"]]:
            raise ValueError("SEC_CANDIDATE_TRACK_SEMANTICS_INVALID")
        indexed.append((_identity(candidate), candidate))
    indexed.sort(key=lambda item: (
        item[0], 0 if item[1]["provenance_kind"] == "SEC_SOURCE_LINKED" else 1,
        tuple(item[1].get("source_alpha_ids", [])),
    ))

    unique: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []
    new_by_identity: dict[str, dict[str, Any]] = {}
    for key, candidate in indexed:
        if key in base_by_identity:
            factor_id = base_by_identity[key]
            reason = "DUPLICATE_V4"
        elif key in new_by_identity:
            representative = new_by_identity[key]
            factor_id = representative["local_factor_id"]
            reason = (
                "DUPLICATE_CROSS_TRACK"
                if candidate["provenance_kind"] != representative["provenance_kind"]
                else "DUPLICATE_NEW_SOURCE_LINKED"
                if candidate["provenance_kind"] == "SEC_SOURCE_LINKED"
                else "DUPLICATE_NEW_PROXY"
            )
        else:
            factor_id = "sec_" + key[:20]
            representative = copy.deepcopy(dict(candidate))
            representative["local_factor_id"] = factor_id
            new_by_identity[key] = representative
            unique.append(representative)
            reason = "NEW_UNIQUE"
        links = _relationships(candidate, factor_id)
        relationships.extend(links)
        reports.append({
            "local_factor_id": factor_id,
            "source_alpha_ids": "|".join(link["source_alpha_id"] for link in links),
            "semantic_status": candidate["semantic_status"],
            "provenance_kind": candidate["provenance_kind"],
            "canonical_identity_hash": key,
            "dedup_reason": reason,
        })
    relationship_keys = [(
        row["source_alpha_id"], row["semantic_status"],
        row["reconstruction_rule_hash"]
    ) for row in relationships]
    if len(relationship_keys) != len(set(relationship_keys)):
        raise ValueError("SEC_SOURCE_RELATIONSHIP_DUPLICATE")
    return unique, pd.DataFrame(reports), relationships


def assemble_expanded_library(
    v4_library: Mapping[str, Any], unique_new_factors: Sequence[Mapping[str, Any]],
    source_relationships: Sequence[Mapping[str, Any]],
    *, base_library_manifest_sha256: str | None = None,
    sec_evidence_hashes: Mapping[str, str] | None = None,
) -> Mapping[str, Any]:
    """Build a detached review artifact; caller owns final manifest and hashes."""
    base = copy.deepcopy(list(v4_library.get("factors", [])))
    new = copy.deepcopy(list(unique_new_factors))
    if any(row.get("usage_tier") != "DIAGNOSTIC_ONLY" for row in new):
        raise ValueError("EXPANDED_FACTOR_USAGE_INVALID")
    ids = [row.get("local_factor_id") for row in [*base, *new]]
    if any(not isinstance(item, str) or not item for item in ids) or len(ids) != len(set(ids)):
        raise ValueError("EXPANDED_FACTOR_IDS_INVALID")
    if base_library_manifest_sha256 is None:
        raise ValueError("BASE_LIBRARY_MANIFEST_HASH_REQUIRED")
    if (len(base_library_manifest_sha256) != 64
            or any(ch not in "0123456789abcdef" for ch in base_library_manifest_sha256)):
        raise ValueError("BASE_LIBRARY_MANIFEST_HASH_INVALID")
    if not isinstance(sec_evidence_hashes, Mapping):
        raise ValueError("SEC_LIBRARY_EVIDENCE_REQUIRED")
    required_sec = {
        "dataset_version", "processed_manifest_sha256", "canonical_mapping_hash"
    }
    if (not required_sec.issubset(sec_evidence_hashes)
            or sec_evidence_hashes["dataset_version"] != "SEC_PIT_FUNDAMENTALS_V1"
            or any(
                not isinstance(sec_evidence_hashes[key], str)
                or len(sec_evidence_hashes[key]) != 64
                or any(ch not in "0123456789abcdef" for ch in sec_evidence_hashes[key])
                for key in ("processed_manifest_sha256", "canonical_mapping_hash")
            )):
        raise ValueError("SEC_LIBRARY_EVIDENCE_INVALID")
    return {
        "schema_version": "EXPANDED_ALPHA_LIBRARY_V1",
        "base_library_schema_version": v4_library.get("schema_version"),
        "base_library_manifest_sha256": base_library_manifest_sha256,
        "base_library_content_sha256": _digest(v4_library),
        "sec_dataset_version": sec_evidence_hashes["dataset_version"],
        "sec_evidence_hashes": copy.deepcopy(dict(sec_evidence_hashes)),
        "base_factor_count": len(base),
        "new_unique_factor_count": len(new),
        "final_unique_factor_count": len(base) + len(new),
        "factors": [*base, *new],
        "source_relationships": copy.deepcopy(list(source_relationships)),
        "usage_tier": "DIAGNOSTIC_ONLY",
        "current_library_pointer_updated": False,
    }
