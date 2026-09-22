"""Performance-blind, fail-closed SEC hypothesis-proxy reconstruction.

An automatic proxy retains only a complete SEC-only additive component.
Entangled missing domains require one independently reviewed rule keyed by
the exact original expression hash. Compilation is not compute verification.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import math
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .factors import FUNCTIONS
from .reconstruction import PRICE_FIELDS, _names, _parse
from .sec_factor_inputs import binding_alias
from .sec_field_mapping import SecFieldBinding
from .sec_source_reconstruction import _check_evidence


_HASH = re.compile(r"[0-9a-f]{64}\Z")
_BINARY = {ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow}
_COMPARISON = {ast.Gt, ast.GtE, ast.Lt, ast.LtE, ast.Eq, ast.NotEq}
_ENTITY = {"VERIFIED_SINGLE_CLASS", "VERIFIED_ENTITY_LEVEL_ONLY"}
_SHARE = {"PER_SHARE", "SHARES", "SHARE_COUNT", "SHARES_OUTSTANDING",
          "MARKET_CAP", "ENTERPRISE_VALUE"}
_RULE_FIELDS = {"parent_source_alpha_ids", "proxy_expression", "retained_economic_hypothesis",
                "required_bindings", "discarded_information_domains", "semantic_losses",
                "review_author", "reviewed_at", "rule_version"}
_BINDING_LOSSES = {
    "cap": "SEC_CAP_IS_NOT_BRAIN_DAILY_CAP",
    "sharesout": "SEC_SHARES_ARE_NOT_BRAIN_DAILY_SHARESOUT",
    "enterprise_value": "SEC_ENTERPRISE_VALUE_IS_PROXY",
    "bookvalue_ps": "SEC_BOOK_VALUE_PER_SHARE_IS_PROXY",
}
_SOURCE_CONTEXT_VALUES = {
    "instrumentType": {"EQUITY"}, "region": {"USA"},
    "universe": {"TOP1000", "TOP3000"}, "language": {"FASTEXPR"},
    "pasteurization": {"ON"}, "unitHandling": {"VERIFY"},
    "nanHandling": {"ON", "OFF"}, "maxTrade": {"OFF"},
    "maxPosition": {"OFF"}, "testPeriod": {None, "P1Y", "P1Y0M"},
    "startDate": {"2019-01-01"}, "endDate": {"2023-12-31"},
    "visualization": {False},
}
_REVIEWED_DECAYS = {0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 16, 20}
_REVIEWED_TRUNCATIONS = {0.01, 0.02, 0.05, 0.08}
_REVIEWED_NEUTRALIZATIONS = {"NONE", "MARKET", "SECTOR", "INDUSTRY", "SUBINDUSTRY"}


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _string_list(value: Any) -> list[str]:
    return sorted(set(value)) if isinstance(value, (list, tuple, set)) and all(
        isinstance(item, str) and item for item in value) else []


def _source_expression(source: Mapping[str, Any]) -> str:
    regular = source.get("regular")
    expression = regular.get("code") if isinstance(regular, Mapping) else source.get("expression")
    return expression if isinstance(expression, str) else ""


def _proxy_settings(source: Mapping[str, Any], reasons: set[str]
                    ) -> tuple[dict[str, Any], dict[str, Any], set[str]]:
    """Translate only reviewed source settings; disclose any lost BRAIN behavior."""
    original = source.get("settings")
    if not isinstance(original, Mapping):
        reasons.add("SOURCE_SETTINGS_MISSING")
        return {}, {}, set()
    settings = copy.deepcopy(dict(original))
    for key, value in settings.items():
        if key in {"delay", "decay", "neutralization", "truncation"}:
            continue
        allowed = _SOURCE_CONTEXT_VALUES.get(key)
        if allowed is None:
            reasons.add("UNSUPPORTED_SOURCE_SETTING:" + str(key))
        elif type(value) not in {str, int, float, bool, type(None)} or value not in allowed \
                or type(value) is bool and key != "visualization":
            reasons.add("UNREVIEWED_SOURCE_SETTING_VALUE:" + str(key))
    delay, decay = settings.get("delay"), settings.get("decay")
    if type(delay) is not int or delay not in {0, 1}:
        reasons.add("UNREVIEWED_SOURCE_SETTING_VALUE:delay")
    if type(decay) is not int or decay not in _REVIEWED_DECAYS:
        reasons.add("UNREVIEWED_SOURCE_SETTING_VALUE:decay")
    neutralization = settings.get("neutralization")
    if type(neutralization) is not str or neutralization not in _REVIEWED_NEUTRALIZATIONS:
        reasons.add("UNREVIEWED_SOURCE_SETTING_VALUE:neutralization")
    truncation = settings.get("truncation")
    if truncation is not None and (type(truncation) not in {int, float}
                                   or truncation not in _REVIEWED_TRUNCATIONS):
        reasons.add("UNREVIEWED_SOURCE_SETTING_VALUE:truncation")
    losses: set[str] = set()
    if neutralization is not None and neutralization != "NONE":
        losses.add("BRAIN_NEUTRALIZATION_NOT_REPRODUCED:" + str(neutralization))
    if truncation is not None:
        losses.add("BRAIN_TRUNCATION_NOT_REPRODUCED:" + str(truncation))
    if "universe" in settings:
        losses.add("BRAIN_UNIVERSE_NOT_REPRODUCED:" + str(settings["universe"]))
    for key in ("pasteurization", "nanHandling", "unitHandling"):
        if key in settings:
            losses.add("BRAIN_" + key.upper() + "_NOT_REPRODUCED:" + str(settings[key]))
    return {"delay": delay, "decay": decay, "neutralization": "NONE"}, settings, losses


def _valid_ast(node: ast.AST) -> bool:
    """Only the evaluator's arithmetic grammar, never source Python execution."""
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        return True
    if isinstance(node, ast.Constant) and type(node.value) in {int, float, bool}:
        return type(node.value) is not float or math.isfinite(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return _valid_ast(node.operand)
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        return _valid_ast(node.left) and _valid_ast(node.right)
    if isinstance(node, ast.Compare) and len(node.ops) == len(node.comparators) == 1 \
            and type(node.ops[0]) in _COMPARISON:
        return _valid_ast(node.left) and _valid_ast(node.comparators[0])
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id in FUNCTIONS and not node.keywords and all(
            _valid_ast(arg) for arg in node.args)
    return False


def _terms(node: ast.AST, sign: int = 1) -> list[tuple[int, ast.AST]]:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _terms(node.left, sign) + _terms(node.right, sign)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Sub):
        return _terms(node.left, sign) + _terms(node.right, -sign)
    return [(sign, node)]


def _scalar_term(node: ast.AST) -> tuple[float | int, ast.AST] | None:
    """Strip only a fixed source coefficient at an additive boundary."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        if isinstance(node.left, ast.Constant) and type(node.left.value) in {int, float} \
                and math.isfinite(node.left.value) and node.left.value != 0:
            return node.left.value, node.right
        if isinstance(node.right, ast.Constant) and type(node.right.value) in {int, float} \
                and math.isfinite(node.right.value) and node.right.value != 0:
            return node.right.value, node.left
    return (1, node)


def _separable_nodes(node: ast.AST, fundamentals: set[str], unavailable: set[str]
                     ) -> list[tuple[int | float, ast.AST]]:
    if not _valid_ast(node):
        return []
    names = _names(node)
    if names and names <= fundamentals and not names & unavailable:
        return [(1, node)]
    if not isinstance(node, ast.BinOp) or not isinstance(node.op, (ast.Add, ast.Sub)):
        return []
    selected: list[tuple[int | float, ast.AST]] = []
    for sign, term in _terms(node):
        pair = _scalar_term(term)
        if pair is None:
            continue
        coefficient, component = pair
        names = _names(component)
        if names and names <= fundamentals and not names & unavailable and _valid_ast(component):
            selected.append((sign * coefficient, component))
    return selected


def extract_sec_only_subtrees(expression: str, fundamental_fields: set[str],
                              unavailable_fields: set[str]) -> list[str]:
    """Return maximal SEC-only additive components, never nested mixed operands."""
    try:
        nodes = _separable_nodes(_parse(expression), set(fundamental_fields),
                                 set(unavailable_fields))
    except (SyntaxError, ValueError, TypeError, RecursionError):
        return []
    return [ast.unparse(ast.fix_missing_locations(copy.deepcopy(node))) for _, node in nodes]


def _translated(node: ast.AST, names: Mapping[str, SecFieldBinding]) -> ast.AST:
    transformed = copy.deepcopy(node)

    class Replace(ast.NodeTransformer):
        def visit_Name(self, item: ast.Name) -> ast.AST:
            if item.id in names:
                return ast.copy_location(ast.Name(id=binding_alias(names[item.id]), ctx=ast.Load()), item)
            return item

        def visit_Call(self, item: ast.Call) -> ast.AST:
            # A function identifier is not a field identifier.
            return ast.copy_location(ast.Call(func=item.func,
                                              args=[self.visit(arg) for arg in item.args],
                                              keywords=[]), item)

    return ast.fix_missing_locations(Replace().visit(transformed))


def _combine(nodes: list[tuple[int | float, ast.AST]]) -> ast.AST:
    if len(nodes) == 1:
        coefficient, component = nodes[0]
        return ast.UnaryOp(op=ast.USub(), operand=component) if coefficient < 0 else component
    result: ast.AST | None = None
    for coefficient, component in nodes:
        absolute = abs(coefficient)
        term = (component if absolute == 1 else ast.BinOp(
            left=ast.Constant(value=absolute), op=ast.Mult(), right=component))
        if result is None:
            result = ast.UnaryOp(op=ast.USub(), operand=term) if coefficient < 0 else term
        else:
            result = ast.BinOp(left=result, op=ast.Add() if coefficient > 0 else ast.Sub(), right=term)
    assert result is not None
    return ast.fix_missing_locations(result)


def load_proxy_hypotheses(path: Path | str,
                          bindings: Mapping[str, SecFieldBinding]) -> dict[str, Mapping[str, Any]]:
    """Load only exact-hash, reviewed, SEC-field-only hypothesis rules."""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, dict) or set(document) != {"schema_version", "rules"} \
            or document["schema_version"] != "SEC_PROXY_HYPOTHESES_V1" \
            or not isinstance(document["rules"], dict):
        raise ValueError("PROXY_RULE_SCHEMA_INVALID")
    output: dict[str, Mapping[str, Any]] = {}
    for expression_hash, rule in document["rules"].items():
        if not isinstance(expression_hash, str) or not _HASH.fullmatch(expression_hash):
            raise ValueError("PROXY_RULE_HASH_INVALID")
        if not isinstance(rule, dict) or set(rule) != _RULE_FIELDS:
            raise ValueError("PROXY_RULE_SCHEMA_INVALID:" + expression_hash)
        for key in ("proxy_expression", "retained_economic_hypothesis", "review_author",
                    "reviewed_at", "rule_version"):
            if not isinstance(rule[key], str) or not rule[key].strip():
                raise ValueError("PROXY_RULE_REVIEW_MISSING:" + key)
        try:
            reviewed = datetime.fromisoformat(rule["reviewed_at"].replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("PROXY_RULE_REVIEW_TIMESTAMP_INVALID") from error
        if reviewed.tzinfo is None or reviewed.astimezone(timezone.utc).year < 2020:
            raise ValueError("PROXY_RULE_REVIEW_TIMESTAMP_INVALID")
        for key in ("parent_source_alpha_ids", "required_bindings",
                    "discarded_information_domains", "semantic_losses"):
            if not isinstance(rule[key], list) or not rule[key] or not all(
                isinstance(value, str) and value.strip() for value in rule[key]
            ) or len(rule[key]) != len(set(rule[key])):
                raise ValueError("PROXY_RULE_SCHEMA_INVALID:" + key)
        if any(name not in bindings for name in rule["required_bindings"]):
            raise ValueError("PROXY_RULE_UNBOUND_FIELD")
        try:
            parsed = _parse(rule["proxy_expression"])
        except (SyntaxError, ValueError, TypeError, RecursionError) as error:
            raise ValueError("PROXY_RULE_SYNTAX_INVALID") from error
        if not _valid_ast(parsed):
            raise ValueError("PROXY_RULE_SYNTAX_INVALID")
        if _names(parsed) != set(rule["required_bindings"]):
            raise ValueError("PROXY_RULE_UNBOUND_FIELD")
        output[expression_hash] = copy.deepcopy(rule)
    return output


def _catalog(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, list) and all(isinstance(item, Mapping) for item in value):
        return value
    try:
        return value.to_dict("records") if value is not None else []
    except (AttributeError, TypeError):
        return []


def _discarded_domains(discarded_fields: set[str], demand: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    by_field: dict[str, str] = {}
    for reason in _string_list(demand.get("blocking_reasons")):
        parts = reason.split(":", 2)
        if len(parts) == 3 and parts[0] == "OTHER_DOMAIN_FIELD":
            by_field[parts[1]] = parts[2].upper()
    for field in discarded_fields & PRICE_FIELDS:
        by_field[field] = "MARKET_DATA"
    missing = sorted(field for field in discarded_fields if field not in by_field)
    return sorted({by_field[field] for field in discarded_fields if field in by_field}), missing


def reconstruct_sec_hypothesis_proxy(
    source: Mapping[str, Any], demand_row: Mapping[str, Any],
    bindings: Mapping[str, SecFieldBinding], proxy_rules: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Compile one proxy or return blockers; never inspect historical performance."""
    if not isinstance(source, Mapping) or not isinstance(demand_row, Mapping):
        raise TypeError("SOURCE_AND_DEMAND_MUST_BE_MAPPINGS")
    sid = source.get("id", source.get("alpha_id"))
    expression = _source_expression(source)
    original_hash = _hash(expression)
    base = {"source_alpha_id": sid, "original_expression_hash": original_hash}
    reasons: set[str] = set()
    if not isinstance(sid, str) or not sid:
        reasons.add("SOURCE_ID_MISSING")
    if not expression.strip():
        reasons.add("SOURCE_EXPRESSION_MISSING")
    if demand_row.get("source_alpha_id") != sid:
        reasons.add("SOURCE_DEMAND_ID_MISMATCH")
    if demand_row.get("original_expression_hash") != original_hash:
        reasons.add("ORIGINAL_EXPRESSION_HASH_MISMATCH")
    if demand_row.get("candidate_track") != "SEC_HYPOTHESIS_PROXY":
        reasons.add("PRELIMINARY_TRACK_NOT_PROXY")
    if demand_row.get("demand_class") not in {"FUNDAMENTAL_ONLY", "MIXED_DOMAIN"}:
        reasons.add("NO_FUNDAMENTAL_DEMAND")
    if demand_row.get("identity_status") not in _ENTITY:
        reasons.add("AMBIGUOUS_IDENTITY:" + str(demand_row.get("identity_status")))
    evidence = _check_evidence(demand_row.get("sec_evidence_hashes"), reasons)
    settings, source_settings, settings_losses = _proxy_settings(source, reasons)
    if not isinstance(proxy_rules, Mapping):
        reasons.add("PROXY_RULES_INVALID")
        proxy_rules = {}
    if not expression.strip():
        parsed = None
    else:
        try:
            parsed = _parse(expression)
        except (SyntaxError, ValueError, TypeError, RecursionError):
            parsed = None
            reasons.add("UNSUPPORTED_SYNTAX")
    fundamentals = set(_string_list(demand_row.get("fundamental_fields")))
    other = set(_string_list(demand_row.get("other_domain_fields")))
    rule = proxy_rules.get(original_hash)
    method = "EXPLICIT_HASH_RULE" if rule is not None else "SEPARABLE_SEC_COMPONENT"
    if rule is not None:
        # The public constructor must be fail-closed even when a caller bypasses
        # load_proxy_hypotheses and hands us an in-memory rule dictionary.
        if not isinstance(rule, Mapping) or set(rule) != _RULE_FIELDS:
            reasons.add("PROXY_RULE_NOT_REVIEWED")
            rule = {}
        else:
            if any(not isinstance(rule.get(key), str) or not rule[key].strip()
                   for key in ("proxy_expression", "retained_economic_hypothesis",
                               "review_author", "reviewed_at", "rule_version")):
                reasons.add("PROXY_RULE_NOT_REVIEWED")
            try:
                reviewed = datetime.fromisoformat(str(rule.get("reviewed_at", "")).replace("Z", "+00:00"))
                if reviewed.tzinfo is None or reviewed.astimezone(timezone.utc).year < 2020:
                    reasons.add("PROXY_RULE_NOT_REVIEWED")
            except ValueError:
                reasons.add("PROXY_RULE_NOT_REVIEWED")
            if any(not isinstance(rule.get(key), list) or not rule[key] or
                   not all(isinstance(value, str) and value.strip() for value in rule[key])
                   for key in ("parent_source_alpha_ids", "required_bindings",
                               "discarded_information_domains", "semantic_losses")):
                reasons.add("PROXY_RULE_NOT_REVIEWED")
        if not isinstance(rule, Mapping) or sid not in _string_list(rule.get("parent_source_alpha_ids")):
            reasons.add("PROXY_RULE_SOURCE_MISMATCH")
        try:
            proxy_node = _parse(str(rule.get("proxy_expression", "")))
        except (SyntaxError, ValueError, TypeError, RecursionError):
            proxy_node = None
            reasons.add("PROXY_RULE_SYNTAX_INVALID")
        if proxy_node is not None and (not _valid_ast(proxy_node) or
                                       _names(proxy_node) != set(_string_list(rule.get("required_bindings")))):
            reasons.add("PROXY_RULE_UNBOUND_FIELD")
        if proxy_node is not None and not _names(proxy_node) <= fundamentals:
            reasons.add("PROXY_RULE_UNAUDITED_FIELD")
        domains = _string_list(rule.get("discarded_information_domains"))
        losses = set(_string_list(rule.get("semantic_losses")))
        hypothesis = rule.get("retained_economic_hypothesis")
    else:
        nodes = _separable_nodes(parsed, fundamentals, other) if parsed is not None else []
        if not nodes:
            reasons.add("MIXED_EXPRESSION_NOT_SEPARABLE_AND_NO_FROZEN_RULE")
            proxy_node = None
        else:
            proxy_node = _combine(nodes)
        selected = _names(proxy_node) if proxy_node is not None else set()
        discarded = (_names(parsed) if parsed is not None else set()) - selected
        domains, unclassified = _discarded_domains(discarded, demand_row)
        reasons.update("DISCARDED_DOMAIN_UNCLASSIFIED:" + name for name in unclassified)
        losses = {"MIXED_SOURCE_RELATION_DROPPED"} if discarded else set()
        if discarded and len(nodes) == 1 and nodes[0][0] not in {1, -1}:
            losses.add("SINGLE_COMPONENT_SOURCE_WEIGHT_NORMALIZED")
        hypothesis = ("Fundamental component retained from original source expression: " +
                      ast.unparse(ast.fix_missing_locations(copy.deepcopy(proxy_node)))) if proxy_node is not None else None
    if proxy_node is None:
        return {**base, "build_status": "DEFERRED_DATA", "semantic_status": "UNRESOLVED",
                "blocking_reasons": sorted(reasons)}
    losses.update(settings_losses)
    used_names = _names(proxy_node)
    if not used_names:
        reasons.add("SEC_FIELD_DEPENDENCY_MISSING")
    if not _valid_ast(proxy_node):
        reasons.add("UNSUPPORTED_SYNTAX")
    field_bindings: dict[str, str] = {}
    used_bindings: dict[str, SecFieldBinding] = {}
    catalog = _catalog(demand_row.get("sec_catalog"))
    for name in sorted(used_names):
        binding = bindings.get(name)
        if not isinstance(binding, SecFieldBinding) or binding.brain_field != name:
            reasons.add("UNMAPPED_FIELD:" + name)
            continue
        if binding.allowed_track not in {"SEC_SOURCE_LINKED", "SEC_HYPOTHESIS_PROXY"}:
            reasons.add("FIELD_TRACK_INVALID:" + name)
        if not binding.mapping_evidence.strip():
            reasons.add("FIELD_EVIDENCE_MISSING:" + name)
        matching = [row for row in catalog if row.get("canonical_field") == binding.canonical_field
                    and row.get("supported_basis", row.get("required_basis")) == binding.required_basis]
        if not matching:
            reasons.add(f"SEC_BASIS_NOT_IN_CATALOG:{name}:{binding.required_basis}")
        else:
            usages = {row.get("identity_usage") for row in matching}
            if len(usages) != 1 or not usages <= ({"ENTITY", "ENTITY_LEVEL", "STATEMENT"} | _SHARE):
                reasons.add("SEC_IDENTITY_USAGE_UNREVIEWED:" + name)
            elif usages & _SHARE and demand_row.get("identity_status") != "VERIFIED_SINGLE_CLASS":
                reasons.add("SHARE_CLASS_IDENTITY_REQUIRED:" + name)
        try:
            alias = binding_alias(binding)
        except ValueError:
            reasons.add("SEC_INPUT_ALIAS_INVALID:" + name)
            continue
        field_bindings[alias] = f"{binding.canonical_field}/{binding.required_basis}"
        used_bindings[name] = binding
        if binding.semantic_grade == "HYPOTHESIS_PROXY":
            losses.add(_BINDING_LOSSES.get(name, "SEC_FIELD_PARITY_NOT_CLAIMED:" + name))
    if not isinstance(hypothesis, str) or not hypothesis.strip():
        reasons.add("RETAINED_HYPOTHESIS_MISSING")
    if rule is not None:
        discarded = _names(parsed) - used_names if parsed is not None else set()
        actual_domains, unclassified = _discarded_domains(discarded, demand_row)
        if unclassified:
            reasons.update("DISCARDED_DOMAIN_UNCLASSIFIED:" + name for name in unclassified)
        if not set(actual_domains) <= set(domains):
            reasons.add("PROXY_RULE_DISCARDED_DOMAIN_MISMATCH")
        if discarded and "MIXED_SOURCE_RELATION_DROPPED" not in losses:
            reasons.add("PROXY_RULE_SEMANTIC_LOSS_MISSING")
    if reasons:
        return {**base, "build_status": "DEFERRED_DATA", "semantic_status": "UNRESOLVED",
                "blocking_reasons": sorted(reasons)}
    translated = _translated(proxy_node, used_bindings)
    proxy_expression = ast.unparse(ast.fix_missing_locations(translated))
    expression_ast = ast.dump(translated, include_attributes=False)
    proxy_hash = _hash(expression_ast)
    reconstruction_hash = _hash({"method": method, "source_hash": original_hash,
                                 "component_ast": ast.dump(proxy_node, include_attributes=False),
                                 "explicit_rule": rule if rule is not None else None})
    factor_identity = _hash({"expression_ast": expression_ast, "field_bindings": field_bindings,
                             "settings": settings})
    return {
        **base, "local_factor_id": "sec_hp_" + factor_identity[:20],
        "source_alpha_ids": [sid], "parent_source_alpha_ids": [sid],
        "proxy_expression": proxy_expression, "proxy_expression_hash": proxy_hash,
        "expression": proxy_expression, "expression_hash": proxy_hash,
        "reconstruction_method": method, "reconstruction_rule_hash": reconstruction_hash,
        "retained_economic_hypothesis": hypothesis,
        "source_economic_hypothesis": source.get("economic_hypothesis") if isinstance(source.get("economic_hypothesis"), str) else None,
        "discarded_information_domains": sorted(domains),
        "semantic_losses": sorted(losses), "field_bindings": dict(sorted(field_bindings.items())),
        "required_fields": sorted(field_bindings), "settings": settings,
        "source_settings": source_settings, "source_settings_sha256": _hash(source_settings),
        "build_status": "COMPILED", "semantic_status": "HYPOTHESIS_PROXY",
        "coverage_status": "NOT_MEASURED", "usage_tier": "DIAGNOSTIC_ONLY",
        "provenance_kind": "SEC_HYPOTHESIS_PROXY", "sec_evidence_hashes": evidence,
        "blocking_reasons": [],
    }
