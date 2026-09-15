"""Deterministic, performance-blind conversion of BRAIN definitions.

This module creates auditable local candidates.  It never claims that a proxy
reproduces the source field or inherits the source Alpha's performance.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .factors import evaluate_factor
from .factors import FUNCTIONS


ALLOWED_CAPABILITY_STATES = {"VERIFIED_SAMPLE", "DERIVED_REQUIRES_QA"}
GROUP_OPERATORS = {"group_rank", "group_neutralize", "group_zscore", "group_mean",
                   "group_scale", "group_backfill"}
DESCRIPTION_MARKERS = {
    "fundamental_quality": {"cash flow", "cashflow", "quality", "income", "debt", "profit"},
    "analyst_expectations": {"analyst", "forecast", "estimate", "revision", "guidance"},
    "option_forward_risk": {"implied volatility", "option", "skew", "put", "call"},
    "news_attention": {"news", "event", "headline"},
    "social_sentiment": {"social", "sentiment", "buzz"},
    "price_volume": {"price", "return", "momentum", "reversal", "volume", "vwap"},
}


def conversion_policy() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "policy_id": "performance_blind_local_proxy_v1",
        "allowed_capability_states": sorted(ALLOWED_CAPABILITY_STATES),
        "allowed_transformations": [
            "IDENTITY_FIELD_BINDING",
            "EXPLICIT_DERIVED_FIELD_BINDING",
            "EXPLICIT_LOCAL_SETTINGS_NEUTRALIZATION",
        ],
        "disallowed_transformations": [
            "PERFORMANCE_SELECTED_PROXY",
            "FUNDAMENTAL_TO_PRICE_MOMENTUM",
            "IMPLIED_VOLATILITY_PREMIUM_TO_REALIZED_VOLATILITY_LEVEL",
            "SILENT_GROUP_OPERATOR_REMOVAL",
            "AUTOMATIC_SETTINGS_NEUTRALIZATION_REMOVAL",
            "UNVERIFIED_CAPABILITY_ADMISSION",
        ],
        "admitted_usage_tier": "DIAGNOSTIC_ONLY",
        "automatic_release_activation": False,
    }


def proxy_templates() -> list[dict[str, Any]]:
    return [
        {"template_id": "identity_market_field_v1", "mechanism": "price_volume",
         "binding": "FIELD", "semantic_status": "MECHANISM_SUPPORTED"},
        {"template_id": "raw_close_return_1d_v1", "mechanism": "price_volume",
         "binding": "close / ts_delay(close, 1) - 1", "semantic_status": "PARTIAL_MECHANISM"},
        {"template_id": "mean_share_volume_20d_v1", "mechanism": "price_volume",
         "binding": "ts_mean(volume, 20)", "semantic_status": "PARTIAL_MECHANISM"},
        {"template_id": "raw_close_volatility_v1", "mechanism": "realized_risk",
         "binding": "ts_std_dev(close / ts_delay(close, 1) - 1, WINDOW)",
         "semantic_status": "PARTIAL_MECHANISM"},
    ]


def default_market_capabilities(
    provider_fields: Mapping[str, set[str]] | None = None,
    coverage_scope: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build capabilities from observed provider fields and supplied evidence.

    The no-argument form is a test/design contract. Production callers pass the
    fields and coverage derived from verified provider-run manifests.
    """
    fields_by_provider = provider_fields or {
        "alpaca_sip": {"open", "high", "low", "close", "volume", "vwap"},
        "tiingo_eod": {"open", "high", "low", "close", "volume"},
    }
    scope: Any = copy.deepcopy(coverage_scope) if coverage_scope is not None else {
        "status": "DECLARED_DEFAULT_CONTRACT_NOT_RUN_EVIDENCE"
    }
    capabilities = {
        field: {
            "capability_id": f"market_bar__{field}",
            "status": "VERIFIED_SAMPLE",
            "binding": field,
            "providers": sorted(
                provider for provider, available in fields_by_provider.items() if field in available
            ),
            "coverage_scope": scope,
            "historical_pit_verified": False,
        }
        for field in ("open", "high", "low", "close", "volume", "vwap")
        if any(field in available for available in fields_by_provider.values())
    }
    common_price_providers = sorted(
        provider for provider, available in fields_by_provider.items() if "close" in available
    )
    capabilities["returns"] = {
        "capability_id": "derived__raw_close_return_1d",
        "status": "DERIVED_REQUIRES_QA",
        "binding": "close / ts_delay(close, 1) - 1",
        "providers": common_price_providers,
        "coverage_scope": scope,
        "definition": "Simple close-to-close raw-price change; not total return.",
        "historical_pit_verified": False,
    }
    capabilities["adv20"] = {
        "capability_id": "derived__mean_share_volume_20d",
        "status": "DERIVED_REQUIRES_QA",
        "binding": "ts_mean(volume, 20)",
        "providers": sorted(
            provider for provider, available in fields_by_provider.items() if "volume" in available
        ),
        "coverage_scope": scope,
        "definition": "20-session mean share volume; not dollar ADV.",
        "historical_pit_verified": False,
    }
    for window in (30, 60, 120):
        field = f"historical_volatility_{window}"
        capabilities[field] = {
            "capability_id": f"derived__raw_close_volatility_{window}d",
            "status": "DERIVED_REQUIRES_QA",
            "binding": f"ts_std_dev(close / ts_delay(close, 1) - 1, {window})",
            "providers": common_price_providers,
            "coverage_scope": scope,
            "definition": f"Unannualized population standard deviation of {window} session raw close returns.",
            "historical_pit_verified": False,
        }
    return capabilities


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def build_source_view(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return the converter's allowlisted view without platform performance."""
    view = []
    for record in records:
        regular = (record.get("raw") or {}).get("regular") or {}
        view.append({
            "alpha_id": record.get("alpha_id"),
            "definition_hash": record.get("definition_hash"),
            "expression": regular.get("code"),
            "description": regular.get("description"),
            "settings": copy.deepcopy(record.get("settings") or {}),
            "dependencies": copy.deepcopy(record.get("dependencies") or {}),
            "provenance": copy.deepcopy(record.get("provenance") or {}),
        })
    return view


def _field_mechanism(field_id: str, dataset: str) -> str:
    text = field_id.lower()
    if dataset in {"fundamental2", "fundamental6"} or any(
        token in text for token in ("cashflow", "income", "asset", "debt", "equity", "revenue")
    ):
        return "fundamental_quality"
    if dataset in {"analyst4", "model16"} or any(
        token in text for token in ("analyst", "estimate", "forecast", "revision")
    ):
        return "analyst_expectations"
    if dataset in {"option8", "option9"} or any(
        token in text for token in ("volatility", "option", "skew", "put", "call")
    ):
        return "option_forward_risk" if "historical_volatility" not in text else "realized_risk"
    if dataset in {"news12", "news18"}:
        return "news_attention"
    if dataset in {"socialmedia8", "socialmedia12"}:
        return "social_sentiment"
    if dataset == "pv13":
        return "relationship_network"
    if dataset == "pv1" or field_id in {"open", "high", "low", "close", "volume", "vwap", "returns", "adv20"}:
        return "price_volume"
    return "unknown"


def _description_mechanisms(description: str | None) -> set[str]:
    if not description:
        return set()
    lowered = description.lower()
    return {name for name, words in DESCRIPTION_MARKERS.items() if any(word in lowered for word in words)}


def _hypothesis_card(source: Mapping[str, Any], field_map: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    fields = [str(item.get("identifier")) for item in source["dependencies"].get("fields", [])]
    mechanisms = sorted({_field_mechanism(field, str(field_map.get(field, {}).get("dataset", "UNKNOWN")))
                         for field in fields})
    mechanisms = [item for item in mechanisms if item != "unknown"] or ["unknown"]
    described = _description_mechanisms(source.get("description"))
    formula_set = set(mechanisms)
    conflict = bool(described and not described.intersection(formula_set))
    return {
        "source_alpha_id": source["alpha_id"],
        "source_definition_hash": source.get("definition_hash"),
        "hypothesis_origin": "EXPLICIT_DESCRIPTION" if source.get("description") else "INFERRED_FROM_FORMULA",
        "description": source.get("description"),
        "mechanisms": mechanisms,
        "field_roles": [{"field_id": field,
                         "dataset": field_map.get(field, {}).get("dataset", "UNKNOWN"),
                         "description": field_map.get(field, {}).get("description")}
                        for field in fields],
        "description_formula_conflict": conflict,
        "uncertainty": "DESCRIPTION_FORMULA_CONFLICT" if conflict else "MECHANISM_NOT_CAUSALLY_VERIFIED",
    }


def _substitute(expression: str, bindings: Mapping[str, str]) -> str:
    result = expression
    for field in sorted(bindings, key=len, reverse=True):
        replacement = bindings[field]
        if replacement != field:
            result = re.sub(rf"\b{re.escape(field)}\b", f"({replacement})", result)
    return " ".join(result.split())


_METADATA_SETTINGS = {"startDate", "endDate", "testPeriod", "visualization"}
_SCOPE_SETTINGS = {"instrumentType", "region", "universe"}
_IMPLEMENTED_SETTINGS = ("delay", "decay", "neutralization")


def _settings_audit(
    settings: Mapping[str, Any],
    neutralization_capabilities: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, str]:
    audit: dict[str, str] = {}
    for key in settings:
        if key in {"delay", "decay"}:
            audit[key] = "PRESERVED"
        elif key == "neutralization":
            level = str(settings[key]).upper()
            capability = (neutralization_capabilities or {}).get(level)
            if level == "NONE":
                audit[key] = "PRESERVED"
            elif capability and capability.get("status") in ALLOWED_CAPABILITY_STATES:
                audit[key] = "LOCAL_PROXY_PRESERVED"
            else:
                audit[key] = "UNSUPPORTED_BLOCKING"
        elif key in _SCOPE_SETTINGS:
            audit[key] = "REPLACED_BY_PROJECT_CALC_UNIVERSE"
        elif key == "language":
            audit[key] = "TRANSLATED_TO_LOCAL_SAFE_AST"
        elif key in _METADATA_SETTINGS:
            audit[key] = "METADATA_ONLY_NOT_APPLIED"
        else:
            audit[key] = "NOT_IMPLEMENTED_LOCAL"
    return audit


def _definition_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    """Settings that may alter the source definition or local interpretation."""
    return {key: copy.deepcopy(value) for key, value in settings.items()
            if key not in _METADATA_SETTINGS}


def _deferred(source: Mapping[str, Any], card: Mapping[str, Any], blockers: list[str],
              settings_audit: Mapping[str, str],
              semantic_status: str = "INSUFFICIENT_LINK") -> dict[str, Any]:
    return {
        "source_alpha_id": source["alpha_id"],
        "decision": "DEFERRED",
        "semantic_status": semantic_status,
        "blockers": sorted(set(blockers)),
        "hypothesis_card_ref": source["alpha_id"],
        "source_definition_hash": source.get("definition_hash"),
        "source_settings": copy.deepcopy(source.get("settings") or {}),
        "settings_audit": dict(settings_audit),
    }


def convert_library(source_view: Sequence[Mapping[str, Any]],
                    field_map: Mapping[str, Mapping[str, Any]],
                    capabilities: Mapping[str, Mapping[str, Any]],
                    neutralization_capabilities: Mapping[str, Mapping[str, Any]] | None = None,
                    ) -> dict[str, Any]:
    """Convert every source to an admitted factor or an explicit deferral."""
    cards: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    factors_by_lineage: dict[str, dict[str, Any]] = {}

    for source in source_view:
        card = _hypothesis_card(source, field_map)
        cards.append(card)
        deps = source.get("dependencies") or {}
        fields = [str(item.get("identifier")) for item in deps.get("fields", [])]
        operators = set(deps.get("operators", []))
        settings = source.get("settings") or {}
        settings_audit = _settings_audit(settings, neutralization_capabilities)
        blockers: list[str] = []
        if not source.get("expression") or not deps.get("safe", False):
            blockers.append("SOURCE_EXPRESSION_UNSAFE")
        if card["description_formula_conflict"]:
            blockers.append("DESCRIPTION_FORMULA_CONFLICT")
        if operators & GROUP_OPERATORS:
            blockers.append("GROUP_RELATION_IS_ESSENTIAL")
        neutralization = str(settings.get("neutralization", "NONE")).upper()
        neutralization_capability = (neutralization_capabilities or {}).get(neutralization)
        if neutralization != "NONE" and (
            not neutralization_capability
            or neutralization_capability.get("status") not in ALLOWED_CAPABILITY_STATES
        ):
            blockers.append("SETTINGS_NEUTRALIZATION_UNSUPPORTED:" + neutralization)
        if any("implied_volatility" in field for field in fields):
            blockers.append("FORWARD_LOOKING_OPTION_INFORMATION_LOST")

        bindings: dict[str, str] = {}
        for field in fields:
            capability = capabilities.get(field)
            if not capability or capability.get("status") not in ALLOWED_CAPABILITY_STATES:
                blockers.append(f"CAPABILITY_NOT_VERIFIED:{field}")
            elif not capability.get("binding"):
                blockers.append(f"CAPABILITY_BINDING_MISSING:{field}")
            else:
                bindings[field] = str(capability["binding"])

        unsupported = operators - set(FUNCTIONS)
        if unsupported:
            blockers.extend(f"OPERATOR_NOT_IMPLEMENTED:{name}" for name in sorted(unsupported))
        if blockers:
            decisions.append(_deferred(source, card, blockers, settings_audit))
            continue

        local_settings = {
            "delay": settings.get("delay"),
            "decay": settings.get("decay", 0),
            "neutralization": settings.get("neutralization", "NONE"),
        }
        expression = _substitute(str(source["expression"]), bindings)
        try:
            ast.parse(expression, mode="exec")
        except (SyntaxError, ValueError):
            decisions.append(_deferred(
                source, card, ["TARGET_EXPRESSION_UNSAFE"], settings_audit,
                semantic_status="ENGINEERING_BLOCKED",
            ))
            continue
        changed_fields = sorted(field for field in fields if bindings[field] != field)
        neutralization_is_proxy = neutralization != "NONE" and not bool(
            neutralization_capability.get("brain_taxonomy_equivalent", False)
            if neutralization_capability else False
        )
        is_proxy = bool(changed_fields) or neutralization_is_proxy
        semantic_status = "PARTIAL_MECHANISM" if is_proxy else "MECHANISM_SUPPORTED"
        provenance_kind = "LOCAL_PROXY" if is_proxy else "LOCAL_DIRECT"
        neutralization_definition = None
        if neutralization_capability is not None:
            neutralization_definition = {
                key: copy.deepcopy(neutralization_capability.get(key))
                for key in ("taxonomy", "brain_taxonomy_equivalent")
            }
        lineage_payload = {
            "expression": expression,
            "settings": local_settings,
            "bindings": bindings,
            "universe_contract": "PROJECT_CALC_UNIVERSE",
            "source_definition_settings": _definition_settings(settings),
        }
        if neutralization_definition is not None:
            lineage_payload["neutralization_definition"] = neutralization_definition
        lineage_hash = hashlib.sha256(_canonical(lineage_payload).encode()).hexdigest()
        factor = factors_by_lineage.get(lineage_hash)
        if factor is None:
            local_id = ("local_proxy__" if is_proxy else "local_direct__") + lineage_hash[:16]
            family_signature = {
                "mechanisms": card["mechanisms"],
                "expression_shape": re.sub(r"(?<![A-Za-z_])\d+(?:\.\d+)?", "N", expression),
            }
            factor = {
                "local_factor_id": local_id,
                "version": 1,
                "family_id": "family__" + hashlib.sha256(
                    _canonical(family_signature).encode()).hexdigest()[:16],
                "provenance_kind": provenance_kind,
                "source_alpha_ids": [],
                "source_definition_hashes": [],
                "source_lineage": [],
                "expression": expression,
                "expression_hash": hashlib.sha256(expression.encode()).hexdigest(),
                "field_bindings": bindings,
                "settings": local_settings,
                "settings_audit": {key: settings_audit[key] for key in _IMPLEMENTED_SETTINGS
                                   if key in settings_audit},
                "universe_contract": "PROJECT_CALC_UNIVERSE",
                "semantic_status": semantic_status,
                "build_status": "COMPILED",
                "usage_tier": "DIAGNOSTIC_ONLY",
                "brain_value_parity": "NOT_CLAIMED" if is_proxy else "UNVERIFIED",
                "preserved": card["mechanisms"],
                "lost": (
                    [f"brain_field_definition_parity:{field}" for field in changed_fields]
                    + (["brain_neutralization_taxonomy_parity"] if neutralization_is_proxy else [])
                ),
                "new_exposures": (
                    [f"local_binding:{field}" for field in changed_fields]
                    + ([f"local_neutralization_taxonomy:{neutralization}"]
                       if neutralization_is_proxy else [])
                ),
                "neutralization_capability": copy.deepcopy(neutralization_capability),
                "lineage_hash": lineage_hash,
                "checks": {"performance_blind_conversion": True},
            }
            factors_by_lineage[lineage_hash] = factor
        factor["source_alpha_ids"].append(source["alpha_id"])
        factor["source_definition_hashes"].append(source.get("definition_hash"))
        factor["source_lineage"].append({
            "source_alpha_id": source["alpha_id"],
            "source_definition_hash": source.get("definition_hash"),
            "source_settings": copy.deepcopy(settings),
            "settings_audit": settings_audit,
        })
        decisions.append({
            "source_alpha_id": source["alpha_id"],
            "decision": "ADMITTED",
            "local_factor_id": factor["local_factor_id"],
            "semantic_status": semantic_status,
            "hypothesis_card_ref": source["alpha_id"],
            "blockers": [],
            "source_settings": copy.deepcopy(settings),
            "settings_audit": settings_audit,
        })

    factors = list(factors_by_lineage.values())
    decision_counts = Counter(row["decision"] for row in decisions)
    kind_by_id = {row["local_factor_id"]: row["provenance_kind"] for row in factors}
    admitted_by_kind = Counter(
        kind_by_id[row["local_factor_id"]]
        for row in decisions if row["decision"] == "ADMITTED"
    )
    deferred = [row for row in decisions if row["decision"] == "DEFERRED"]
    deferred_blockers = Counter(
        blocker.split(":", 1)[0] for row in deferred for blocker in row["blockers"]
    )
    card_by_id = {row["source_alpha_id"]: row for row in cards}
    deferred_mechanisms = Counter(
        mechanism for row in deferred for mechanism in card_by_id[row["source_alpha_id"]]["mechanisms"]
    )
    return {
        "schema_version": 1,
        "cards": cards,
        "decisions": decisions,
        "factors": factors,
        "summary": {
            "source_count": len(source_view),
            "card_count": len(cards),
            "admitted_source_count": decision_counts["ADMITTED"],
            "deferred_source_count": decision_counts["DEFERRED"],
            "direct_source_count": admitted_by_kind["LOCAL_DIRECT"],
            "proxy_source_count": admitted_by_kind["LOCAL_PROXY"],
            "unique_factor_count": len(factors),
            "family_count": len({row["family_id"] for row in factors}),
            "hypothesis_judged_count": sum(
                row["mechanisms"] != ["unknown"] and not row["description_formula_conflict"]
                for row in cards
            ),
            "compute_verified_count": sum(row["build_status"] == "COMPUTE_VERIFIED" for row in factors),
            "research_eligible_count": sum(row["usage_tier"] == "RESEARCH_ELIGIBLE" for row in factors),
            "performance_inputs_used": False,
            "deferred_blocker_counts": dict(sorted(deferred_blockers.items())),
            "deferred_mechanism_counts": dict(sorted(deferred_mechanisms.items())),
        },
    }


def _node_required_history(node: ast.AST, variables: dict[str, int] | None = None) -> int:
    """Return a conservative number of prior rows needed for one finite value."""
    environment = variables if variables is not None else {}
    if isinstance(node, ast.Module):
        result = 0
        for item in node.body:
            if (
                isinstance(item, ast.Assign)
                and len(item.targets) == 1
                and isinstance(item.targets[0], ast.Name)
            ):
                result = _node_required_history(item.value, environment)
                environment[item.targets[0].id] = result
            else:
                result = _node_required_history(item, environment)
        return result
    if isinstance(node, ast.Assign):
        return _node_required_history(node.value, environment)
    if isinstance(node, ast.Expr):
        return _node_required_history(node.value, environment)
    if isinstance(node, ast.Name):
        return environment.get(node.id, 0)
    if isinstance(node, ast.Constant):
        return 0
    if isinstance(node, ast.UnaryOp):
        return _node_required_history(node.operand, environment)
    if isinstance(node, ast.BinOp):
        return max(
            _node_required_history(node.left, environment),
            _node_required_history(node.right, environment),
        )
    if isinstance(node, ast.Compare):
        return max(
            [_node_required_history(node.left, environment)]
            + [_node_required_history(item, environment) for item in node.comparators]
        )
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        histories = [_node_required_history(argument, environment) for argument in node.args]
        name = node.func.id
        if name == "ts_delay" and len(node.args) >= 2 and isinstance(node.args[-1], ast.Constant):
            return histories[0] + int(node.args[-1].value)
        if name == "ts_delta" and len(node.args) >= 2 and isinstance(node.args[-1], ast.Constant):
            return histories[0] + int(node.args[-1].value)
        rolling = {
            "ts_mean", "ts_rank", "ts_std_dev", "ts_sum",
            "ts_corr", "ts_covariance", "ts_arg_min", "ts_arg_max", "ts_scale",
        }
        if name in rolling and node.args and isinstance(node.args[-1], ast.Constant):
            return max(histories[:-1], default=0) + int(node.args[-1].value) - 1
        return max(histories, default=0)
    return max(
        (_node_required_history(child, environment) for child in ast.iter_child_nodes(node)),
        default=0,
    )


def verify_factors(conversion: Mapping[str, Any],
                   provider_inputs: Mapping[str, Mapping[str, pd.DataFrame]],
                   neutralization_inputs: Mapping[str, Mapping[str, pd.DataFrame]] | None = None,
                   *, prefix_rows: int = 5) -> tuple[dict[str, Any], dict[str, dict[str, pd.DataFrame]]]:
    """Run compiled candidates on real/synthetic aligned inputs without labels."""
    result = copy.deepcopy(conversion)
    matrices: dict[str, dict[str, pd.DataFrame]] = {}

    def max_abs_group_mean(values: pd.DataFrame, groups: pd.DataFrame) -> float:
        maximum = 0.0
        for timestamp in values.index:
            row = values.loc[timestamp]
            labels = groups.loc[timestamp]
            valid = row.notna() & labels.notna()
            if valid.any():
                means = row[valid].groupby(labels[valid], sort=False).mean().abs()
                if not means.empty:
                    maximum = max(maximum, float(means.max()))
        return maximum

    for factor in result["factors"]:
        checks: dict[str, Any] = {}
        factor_matrices: dict[str, pd.DataFrame] = {}
        failed = False
        tree = ast.parse(factor["expression"], mode="exec")
        called = {node.func.id for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        assigned = {
            node.id for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
        }
        loaded = {
            node.id for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        required_inputs = loaded - called - assigned - {
            "true", "false", "True", "False"
        }
        required_history = (
            _node_required_history(tree)
            + int(factor["settings"].get("delay", 0))
            + max(int(factor["settings"].get("decay", 0)) - 1, 0)
        )
        for provider, inputs in provider_inputs.items():
            neutralization = str(factor["settings"].get("neutralization", "NONE")).upper()
            group_matrix = None
            if neutralization != "NONE":
                group_matrix = (neutralization_inputs or {}).get(provider, {}).get(neutralization)
                if group_matrix is None:
                    checks[provider] = {
                        "status": "NOT_APPLICABLE",
                        "missing_inputs": [f"neutralization:{neutralization}"],
                    }
                    continue
            missing = sorted(required_inputs - set(inputs))
            if missing:
                checks[provider] = {"status": "NOT_APPLICABLE", "missing_inputs": missing}
                continue
            try:
                full = evaluate_factor(
                    factor["expression"], inputs, factor["settings"], group_matrix
                )
                prefix_inputs = {name: frame.iloc[:-prefix_rows] for name, frame in inputs.items()}
                prefix_groups = group_matrix.iloc[:-prefix_rows] if group_matrix is not None else None
                prefix = evaluate_factor(
                    factor["expression"], prefix_inputs, factor["settings"], prefix_groups
                )
                pd.testing.assert_frame_equal(full.iloc[:-prefix_rows], prefix)
                finite = int(np.isfinite(full.to_numpy(dtype=float)).sum())
                if finite == 0:
                    row_count = min(len(frame) for frame in inputs.values())
                    if row_count <= required_history:
                        checks[provider] = {
                            "status": "INSUFFICIENT_SAMPLE", "rows": row_count,
                            "required_history": required_history,
                        }
                        continue
                    raise ValueError("NO_FINITE_FACTOR_VALUES")
                check = {"status": "PASS", "finite_values": finite,
                         "prefix_invariant": True}
                if group_matrix is not None:
                    neutrality_error = max_abs_group_mean(full, group_matrix)
                    if neutrality_error > 1e-12:
                        raise ValueError("NEUTRALIZATION_GROUP_MEAN_NONZERO")
                    check["max_abs_group_mean"] = neutrality_error
                checks[provider] = check
                factor_matrices[provider] = full
            except (ValueError, TypeError, AssertionError, KeyError, ArithmeticError) as exc:
                failed = True
                checks[provider] = {"status": "FAIL", "error_code": str(exc)[:160]}
        factor["checks"].update(checks)
        passed = any(item["status"] == "PASS" for item in checks.values())
        insufficient_only = checks and all(
            item["status"] in {"INSUFFICIENT_SAMPLE", "NOT_APPLICABLE"}
            for item in checks.values()
        ) and any(item["status"] == "INSUFFICIENT_SAMPLE" for item in checks.values())
        if insufficient_only:
            factor["build_status"] = "COMPILED"
            factor["usage_tier"] = "DIAGNOSTIC_ONLY"
        elif failed or not passed:
            factor["build_status"] = "ENGINEERING_FAILED"
            factor["usage_tier"] = "DIAGNOSTIC_ONLY"
        else:
            factor["build_status"] = "COMPUTE_VERIFIED"
            factor["usage_tier"] = "DIAGNOSTIC_ONLY"
            matrices[factor["local_factor_id"]] = factor_matrices
    result["summary"]["compute_verified_count"] = sum(
        row["build_status"] == "COMPUTE_VERIFIED" for row in result["factors"]
    )
    result["summary"]["engineering_failed_count"] = sum(
        row["build_status"] == "ENGINEERING_FAILED" for row in result["factors"]
    )
    result["summary"]["insufficient_sample_count"] = sum(
        row["build_status"] == "COMPILED" for row in result["factors"]
    )
    result["summary"]["compute_verified_source_count"] = sum(
        len(row["source_alpha_ids"])
        for row in result["factors"] if row["build_status"] == "COMPUTE_VERIFIED"
    )
    return result, matrices


def write_conversion_artifacts(conversion: Mapping[str, Any],
                               matrices: Mapping[str, Mapping[str, pd.DataFrame]],
                               capabilities: Mapping[str, Any], output_dir: Path | str) -> None:
    """Write immutable conversion evidence into an empty directory."""
    output = Path(output_dir)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("OUTPUT_DIRECTORY_NOT_EMPTY")
    output.mkdir(parents=True, exist_ok=True)
    (output / "data_capabilities.json").write_text(
        json.dumps(capabilities, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output / "conversion_policy.json").write_text(
        json.dumps(conversion_policy(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output / "proxy_templates.json").write_text(
        json.dumps(proxy_templates(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    for name, rows in (("hypothesis_cards.jsonl", conversion["cards"]),
                       ("proxy_decisions.jsonl", conversion["decisions"])):
        (output / name).write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
            encoding="utf-8",
        )
    library = {"schema_version": conversion["schema_version"], "factors": conversion["factors"]}
    (output / "project_factor_library.json").write_text(
        json.dumps(library, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output / "conversion_report.json").write_text(
        json.dumps(conversion["summary"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    for factor_id, providers in matrices.items():
        folder = output / "factor_values" / factor_id
        folder.mkdir(parents=True, exist_ok=True)
        for provider, frame in providers.items():
            frame.to_parquet(folder / f"{provider}.parquet")
            frame.iloc[-1].sort_values(ascending=False).rename("score").to_csv(
                folder / f"{provider}_latest_ranking.csv"
            )
    manifest = {
        str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output.rglob("*")) if path.is_file()
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
