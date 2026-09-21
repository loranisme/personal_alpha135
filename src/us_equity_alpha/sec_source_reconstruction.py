"""Fail-closed, performance-blind SEC source-linked formula reconstruction.

This stage compiles only. A later stage must establish PIT coverage, deterministic
computation and prefix invariance before calling a formula compute-verified.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import math
import re
from collections.abc import Mapping
from typing import Any

import pandas as pd

from .factors import FUNCTIONS
from .reconstruction import _parse
from .sec_factor_inputs import binding_alias
from .sec_field_mapping import SecFieldBinding


_STRICT_GRADES = {"SOURCE_LINKED_DIRECT", "SOURCE_LINKED_DERIVED"}
_HASH_KEYS = (
    "processed_manifest_sha256", "canonical_mapping_hash",
    "derived_field_catalog_hash", "security_mapping_hash", "pit_contract_hash",
    "universe_snapshot_hash", "raw_manifest_hash",
)
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_FORBIDDEN_FIELD_SEMANTICS = {"cap", "sharesout", "market_cap_sec_proxy",
                              "shares_outstanding_sec", "enterprise_value_sec_proxy",
                              "book_value_per_share"}
_BINARY = {ast.Add: "add", ast.Sub: "subtract", ast.Mult: "multiply",
           ast.Div: "divide", ast.Pow: "power"}
_COMPARE = (ast.Gt, ast.GtE, ast.Lt, ast.LtE, ast.Eq, ast.NotEq)
_VALID_IDENTITY = {"VERIFIED_SINGLE_CLASS", "VERIFIED_ENTITY_LEVEL_ONLY"}
_ENTITY_USAGES = {"ENTITY", "ENTITY_LEVEL", "STATEMENT"}
_SHARE_USAGES = {"PER_SHARE", "SHARES", "SHARE_COUNT", "SHARES_OUTSTANDING",
                 "MARKET_CAP", "ENTERPRISE_VALUE"}


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _list_of_strings(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)) and all(isinstance(v, str) for v in value):
        return sorted(set(value))
    return []


def _source_id(source: Mapping[str, Any]) -> str:
    value = source.get("id", source.get("alpha_id"))
    return value if isinstance(value, str) and value else "UNKNOWN_SOURCE"


def _source_expression(source: Mapping[str, Any]) -> str:
    regular = source.get("regular")
    value = regular.get("code") if isinstance(regular, Mapping) else source.get("expression")
    return value if isinstance(value, str) else ""


def _check_evidence(value: Any, reasons: set[str]) -> dict[str, str]:
    evidence = value if isinstance(value, Mapping) else {}
    if evidence.get("dataset_version") != "SEC_PIT_FUNDAMENTALS_V1":
        reasons.add("SEC_EVIDENCE_MISSING:dataset_version")
    for key in _HASH_KEYS:
        item = evidence.get(key)
        if not isinstance(item, str) or not _HASH_RE.fullmatch(item):
            reasons.add("SEC_EVIDENCE_MISSING:" + key)
    return {key: evidence[key] for key in ("dataset_version", *_HASH_KEYS) if key in evidence}


def _check_settings(source: Mapping[str, Any], reasons: set[str]) -> dict[str, Any]:
    settings = source.get("settings")
    if not isinstance(settings, Mapping):
        reasons.add("SOURCE_SETTINGS_MISSING")
        return {}
    for key in sorted(set(settings) - {"delay", "decay", "neutralization"}):
        reasons.add("UNSUPPORTED_SOURCE_SETTING:" + str(key))
    for key in ("delay", "decay"):
        value = settings.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            reasons.add("UNSUPPORTED_SOURCE_SETTING:" + key)
    neutralization = settings.get("neutralization")
    if neutralization != "NONE":
        reasons.add("UNVERIFIED_GROUP_SETTING:" + str(neutralization))
    return {key: copy.deepcopy(settings[key]) for key in ("delay", "decay", "neutralization")
            if key in settings}


def _catalog_rows(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, pd.DataFrame):
        return value.to_dict("records")
    if isinstance(value, (list, tuple)) and all(isinstance(row, Mapping) for row in value):
        return list(value)
    return []


def _resolve_field(name: str, demand: Mapping[str, Any], bindings: Mapping[str, SecFieldBinding],
                   reasons: set[str], resolved: dict[str, tuple[str, SecFieldBinding]]) -> ast.Name:
    binding = bindings.get(name)
    if not isinstance(binding, SecFieldBinding) or binding.brain_field != name:
        reasons.add("UNMAPPED_FIELD:" + name)
        return ast.Name(id=name, ctx=ast.Load())
    if (binding.allowed_track != "SEC_SOURCE_LINKED" or
            binding.semantic_grade not in _STRICT_GRADES or
            name in _FORBIDDEN_FIELD_SEMANTICS or
            binding.canonical_field in _FORBIDDEN_FIELD_SEMANTICS):
        reasons.add("PROXY_FIELD_NOT_ALLOWED_IN_SOURCE_LINKED:" + name)
        return ast.Name(id=name, ctx=ast.Load())
    if not binding.mapping_evidence.strip():
        reasons.add("FIELD_PARITY_EVIDENCE_MISSING:" + name)
    catalog = _catalog_rows(demand.get("sec_catalog"))
    matching = [row for row in catalog if row.get("canonical_field") == binding.canonical_field
                and row.get("supported_basis", row.get("required_basis")) == binding.required_basis]
    if not matching:
        reasons.add(f"SEC_BASIS_NOT_IN_CATALOG:{name}:{binding.required_basis}")
    else:
        matching = [row for row in matching if row.get("semantic_grade") == binding.semantic_grade]
        if not matching:
            reasons.add("SEC_SEMANTIC_GRADE_MISMATCH:" + name)
        elif len({str(row.get("identity_usage")) for row in matching}) != 1:
            reasons.add("SEC_IDENTITY_USAGE_AMBIGUOUS:" + name)
        else:
            usage = str(matching[0].get("identity_usage"))
            if usage not in _ENTITY_USAGES | _SHARE_USAGES:
                reasons.add("SEC_IDENTITY_USAGE_UNREVIEWED:" + name)
            if usage in _SHARE_USAGES and demand.get("identity_status") != "VERIFIED_SINGLE_CLASS":
                reasons.add("SHARE_CLASS_IDENTITY_REQUIRED:" + name)
    try:
        alias = binding_alias(binding)
    except ValueError:
        reasons.add("SEC_INPUT_ALIAS_INVALID:" + name)
        return ast.Name(id=name, ctx=ast.Load())
    resolved[name] = (alias, binding)
    return ast.Name(id=alias, ctx=ast.Load())


def _transform(node: ast.AST, demand: Mapping[str, Any], bindings: Mapping[str, SecFieldBinding],
               supported: set[str], reasons: set[str],
               resolved: dict[str, tuple[str, SecFieldBinding]]) -> ast.AST:
    """Whitelist the evaluator grammar while substituting only field Name nodes."""
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        if node.id in {"true", "false", "True", "False"}:
            return copy.deepcopy(node)
        return _resolve_field(node.id, demand, bindings, reasons, resolved)
    if isinstance(node, ast.Constant) and type(node.value) in {int, float, bool}:
        if type(node.value) is float and not math.isfinite(node.value):
            reasons.add("NONFINITE_NUMERIC_CONSTANT")
        return copy.deepcopy(node)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return ast.UnaryOp(op=copy.deepcopy(node.op), operand=_transform(
            node.operand, demand, bindings, supported, reasons, resolved))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        operator_name = _BINARY[type(node.op)]
        if operator_name not in supported:
            reasons.add("UNSUPPORTED_OPERATOR:" + operator_name)
        return ast.BinOp(left=_transform(node.left, demand, bindings, supported, reasons, resolved),
                         op=copy.deepcopy(node.op),
                         right=_transform(node.right, demand, bindings, supported, reasons, resolved))
    if isinstance(node, ast.Compare) and len(node.ops) == len(node.comparators) == 1 \
            and isinstance(node.ops[0], _COMPARE):
        return ast.Compare(left=_transform(node.left, demand, bindings, supported, reasons, resolved),
                           ops=copy.deepcopy(node.ops), comparators=[_transform(
                               node.comparators[0], demand, bindings, supported, reasons, resolved)])
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        name = node.func.id
        if name not in supported or name not in FUNCTIONS:
            reasons.add("UNSUPPORTED_OPERATOR:" + name)
        if node.keywords:
            reasons.add("UNSUPPORTED_SYNTAX")
        args = [_transform(arg, demand, bindings, supported, reasons, resolved)
                for arg in node.args]
        return ast.Call(func=copy.deepcopy(node.func), args=args, keywords=[])
    reasons.add("UNSUPPORTED_SYNTAX")
    return ast.Constant(value=0)


def reconstruct_sec_source_linked(
    source: Mapping[str, Any], demand_row: Mapping[str, Any],
    bindings: Mapping[str, SecFieldBinding], supported_operators: set[str],
) -> dict[str, Any]:
    """Compile one strict candidate, or return every known blocker in a ledger row.

    `demand_row` must carry a verified release catalog/evidence and reviewed
    identity status. A preliminary field audit alone never grants admission.
    """
    if not isinstance(source, Mapping) or not isinstance(demand_row, Mapping):
        raise TypeError("SOURCE_AND_DEMAND_MUST_BE_MAPPINGS")
    sid = _source_id(source)
    expression = _source_expression(source)
    reasons: set[str] = set()
    if sid == "UNKNOWN_SOURCE":
        reasons.add("SOURCE_ID_MISSING")
    if not expression.strip():
        reasons.add("SOURCE_EXPRESSION_MISSING")
    if demand_row.get("source_alpha_id") != sid:
        reasons.add("SOURCE_DEMAND_ID_MISMATCH")
    if demand_row.get("candidate_track") != "SEC_SOURCE_LINKED":
        reasons.add("PRELIMINARY_TRACK_NOT_SOURCE_LINKED")
    if demand_row.get("demand_class") != "FUNDAMENTAL_ONLY":
        reasons.add("MIXED_OR_NONFUNDAMENTAL_DEMAND")
    for field in _list_of_strings(demand_row.get("other_domain_fields")):
        reasons.add("OTHER_DOMAIN_FIELD:" + field)
    for field in _list_of_strings(demand_row.get("unmapped_fundamental_fields")):
        reasons.add("UNMAPPED_FIELD:" + field)
    identity = demand_row.get("identity_status")
    if identity not in _VALID_IDENTITY:
        reasons.add("AMBIGUOUS_IDENTITY:" + str(identity))
    evidence = _check_evidence(demand_row.get("sec_evidence_hashes"), reasons)
    settings = _check_settings(source, reasons)
    original_hash = _hash(expression)
    if demand_row.get("original_expression_hash") != original_hash:
        reasons.add("ORIGINAL_EXPRESSION_HASH_MISMATCH")

    translated: ast.AST | None = None
    resolved: dict[str, tuple[str, SecFieldBinding]] = {}
    if expression.strip():
        try:
            original = _parse(expression)
            translated = _transform(original, demand_row, bindings,
                                    supported_operators, reasons, resolved)
        except (SyntaxError, ValueError, TypeError, RecursionError):
            reasons.add("UNSUPPORTED_SYNTAX")
    declared_fields = set(_list_of_strings(demand_row.get("fundamental_fields")))
    if not resolved:
        reasons.add("SEC_FIELD_DEPENDENCY_MISSING")
    if declared_fields != set(resolved) and not any(
            reason.startswith("UNMAPPED_FIELD:") for reason in reasons):
        reasons.add("DEMAND_FIELD_SET_MISMATCH")
    base = {"source_alpha_id": sid, "original_expression_hash": original_hash}
    if reasons:
        return {**base, "build_status": "DEFERRED_DATA", "semantic_status": "UNRESOLVED",
                "blocking_reasons": sorted(reasons)}

    assert translated is not None
    local_expression = ast.unparse(ast.fix_missing_locations(translated))
    field_bindings = {alias: f"{binding.canonical_field}/{binding.required_basis}"
                      for alias, binding in sorted(resolved.values(), key=lambda item: item[0])}
    required = sorted(field_bindings)
    expression_hash = _hash(ast.dump(translated, include_attributes=False))
    factor_identity = _hash({"expression_ast": ast.dump(translated, include_attributes=False),
                             "field_bindings": field_bindings, "settings": settings})
    hypothesis = source.get("economic_hypothesis")
    if not isinstance(hypothesis, str) or not hypothesis.strip():
        hypothesis = "Economic relationship encoded by source formula: " + expression
    return {
        **base,
        "local_factor_id": "sec_sl_" + factor_identity[:20],
        "source_alpha_ids": [sid], "parent_source_alpha_ids": [sid],
        "expression": local_expression, "expression_hash": expression_hash,
        "settings": settings, "field_bindings": field_bindings,
        "required_fields": required, "economic_hypothesis": hypothesis,
        "build_status": "COMPILED", "semantic_status": "SOURCE_LINKED",
        "coverage_status": "NOT_MEASURED", "usage_tier": "DIAGNOSTIC_ONLY",
        "provenance_kind": "SEC_SOURCE_LINKED", "sec_evidence_hashes": evidence,
        "blocking_reasons": [],
    }


def build_sec_source_linked_candidates(
    registry: Mapping[str, Any], demand: pd.DataFrame,
    bindings: Mapping[str, SecFieldBinding], supported_operators: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return strict candidates and one decision for each audited source."""
    records = registry.get("records") if isinstance(registry, Mapping) else None
    if not isinstance(records, list) or not isinstance(demand, pd.DataFrame):
        raise ValueError("SEC_STRICT_INPUT_SCHEMA_INVALID")
    source_by_id = {_source_id(row): row for row in records if isinstance(row, Mapping)}
    if len(source_by_id) != len(records) or "UNKNOWN_SOURCE" in source_by_id:
        raise ValueError("SEC_STRICT_SOURCE_IDS_INVALID")
    if "source_alpha_id" not in demand or demand.source_alpha_id.duplicated().any():
        raise ValueError("SEC_STRICT_DEMAND_IDS_INVALID")
    candidates: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []
    for row in sorted(demand.to_dict("records"), key=lambda item: item["source_alpha_id"]):
        sid = row["source_alpha_id"]
        if sid not in source_by_id:
            raise ValueError("SEC_STRICT_SOURCE_NOT_FOUND:" + str(sid))
        result = reconstruct_sec_source_linked(source_by_id[sid], row,
                                               bindings, supported_operators)
        ledger.append({key: result[key] for key in (
            "source_alpha_id", "original_expression_hash", "build_status",
            "semantic_status", "blocking_reasons")})
        if result["build_status"] == "COMPILED":
            candidates.append(result)
    return candidates, ledger
