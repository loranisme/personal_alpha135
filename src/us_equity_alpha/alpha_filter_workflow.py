"""File orchestration for the on-demand V5.1 Alpha Filter and pool freeze."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .alpha_filter import (
    apply_filter_gates,
    build_proposed_pool,
    compute_redundancy_conflicts,
    greedy_nonredundant_selection,
)
from .alpha_filter_diagnostics import (
    diagnose_factor,
    forward_open_returns,
    weekly_signal_sessions,
)
from .expansion_registry import load_expansion_snapshot
from .factor_library import compute_registry_factors


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def flat_equal_weight_composite(
    rank_panels: dict[Any, pd.DataFrame],
) -> pd.DataFrame:
    """Average complete aligned rank rows with one equal weight per factor."""
    if not rank_panels:
        raise ValueError("COMPOSITE_FACTOR_PANELS_REQUIRED")
    panels = list(rank_panels.values())
    if any("SPY" in panel.columns for panel in panels):
        raise ValueError("BENCHMARK_IN_FACTOR_CROSS_SECTION")
    common_index = panels[0].index
    common_columns = panels[0].columns
    for panel in panels[1:]:
        common_index = common_index.intersection(panel.index)
        common_columns = common_columns.intersection(panel.columns)
    if common_index.empty or common_columns.empty:
        raise ValueError("COMPOSITE_PANEL_INTERSECTION_EMPTY")
    aligned = [panel.loc[common_index, common_columns] for panel in panels]
    total = aligned[0].copy()
    complete = aligned[0].notna()
    for panel in aligned[1:]:
        total = total.add(panel, fill_value=0)
        complete &= panel.notna()
    return (total / len(aligned)).where(complete)


def compare_composites(
    new_scores: pd.DataFrame,
    baseline_scores: pd.DataFrame,
    labels: pd.DataFrame,
    portfolio_runner,
    metadata: dict[str, Any],
) -> pd.DataFrame:
    """Compare frozen composites without feeding portfolio returns into selection."""
    if metadata.get("filter_decisions_frozen") is not True:
        raise ValueError("FILTER_DECISIONS_NOT_FROZEN")
    for field in ("provider", "universe_hash", "signal_sessions_hash", "forward_label"):
        if metadata.get(f"new_{field}") != metadata.get(f"baseline_{field}"):
            raise ValueError(f"BASELINE_CONTRACT_MISMATCH:{field}")
    config = metadata.get("diagnostic_config")
    if not isinstance(config, dict):
        raise ValueError("DIAGNOSTIC_CONFIG_REQUIRED")
    rows: list[dict[str, Any]] = []
    for composite_id, scores in (
        ("V5_1_PROPOSED", new_scores),
        ("BASELINE_V5_8_FACTOR", baseline_scores),
    ):
        diagnostics, _ = diagnose_factor(scores.copy(), labels.copy(), config)
        portfolio = portfolio_runner(scores.copy())
        required_portfolio = {"total_return", "sharpe", "max_drawdown", "modeled_fees"}
        if not isinstance(portfolio, dict) or required_portfolio - set(portfolio):
            raise ValueError("PORTFOLIO_COMPARISON_SCHEMA_INVALID")
        rows.append(
            {
                "composite_id": composite_id,
                "mean_rank_ic_5d": diagnostics["mean_rank_ic_5d"],
                "icir_5d": diagnostics["icir_5d"],
                "positive_year_ratio": diagnostics["positive_year_ratio"],
                "q5_minus_q1_5d": diagnostics["q5_minus_q1_5d"],
                "weekly_top10_turnover": diagnostics["weekly_top10_turnover"],
                "total_return": portfolio["total_return"],
                "sharpe": portfolio["sharpe"],
                "max_drawdown": portfolio["max_drawdown"],
                "modeled_fees": portfolio["modeled_fees"],
                "portfolio_metrics_used_for_filter_admission": False,
                "selection_evidence": "DEVELOPMENT_EXPOSED",
            }
        )
    return pd.DataFrame(rows)


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON_ROOT_NOT_OBJECT:{path}")
    return value


def _read_frame(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)


def _load_panels(root: Path) -> dict[str, pd.DataFrame]:
    panels: dict[str, pd.DataFrame] = {}
    for path in sorted(root.glob("*.parquet")):
        panel = pd.read_parquet(path)
        panel.index = pd.to_datetime(panel.index, utc=True)
        if panel.index.has_duplicates or not panel.index.is_monotonic_increasing:
            raise ValueError(f"MARKET_PANEL_INDEX_INVALID:{path.name}")
        panel.columns = panel.columns.astype(str)
        panels[path.stem] = panel
    if not panels:
        raise ValueError("MARKET_DATA_PANELS_REQUIRED")
    if "open" not in panels:
        raise ValueError("OPEN_PANEL_REQUIRED")
    return panels


def _factor_definition(
    catalog_row: dict[str, Any],
    base_factors: dict[str, dict[str, Any]],
    expansion_factors: dict[tuple[str, int], dict[str, Any]],
) -> dict[str, Any]:
    factor_id = str(catalog_row["factor_id"])
    version = int(catalog_row["factor_version"])
    if catalog_row["source_kind"] == "V4_BASE":
        if factor_id not in base_factors:
            raise ValueError(f"V4_FACTOR_DEFINITION_MISSING:{factor_id}")
        return dict(base_factors[factor_id])
    expansion = expansion_factors.get((factor_id, version))
    if expansion is None:
        raise ValueError(f"EXPANSION_FACTOR_DEFINITION_MISSING:{factor_id}:v{version}")
    return {
        "local_factor_id": factor_id,
        "expression": expansion["expression"],
        "field_bindings": {field: field for field in expansion["required_fields"]},
        "allowed_providers": expansion["allowed_providers"],
        "build_status": expansion["compute_status"],
        "usage_tier": "DIAGNOSTIC_ONLY",
        "provenance_kind": "LOCAL_RECONSTRUCTION",
        "settings": expansion["settings"],
    }


def _evaluate_one(
    factor: dict[str, Any], provider: str, panels: dict[str, pd.DataFrame]
) -> pd.DataFrame:
    required = set(factor.get("field_bindings") or {})
    requested = set(required)
    if "benchmark_returns" in required:
        requested.add("close")
    missing = requested - set(panels)
    if missing:
        raise ValueError("FACTOR_INPUTS_MISSING:" + ",".join(sorted(missing)))
    inputs = {field: panels[field] for field in requested if field in panels}
    library = {"factors": [factor]}
    return compute_registry_factors(
        library,
        [factor["local_factor_id"]],
        provider,
        inputs,
        usage="diagnostic",
    )[factor["local_factor_id"]]


def _write_rank_cache(
    rank_panel: pd.DataFrame, cache_dir: Path, factor_id: str, version: int
) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", factor_id)
    scratch = cache_dir / f".{safe_id}__v{version}.parquet"
    rank_panel.to_parquet(scratch)
    digest = _digest(scratch)
    destination = cache_dir / f"{safe_id}__v{version}__{digest[:16]}.parquet"
    scratch.replace(destination)
    return destination


def run_alpha_filter_from_files(
    *,
    unified_catalog: Path | str,
    library: Path | str,
    v4_manifest: Path | str,
    expansion_registry: Path | str,
    universe: Path | str,
    market_data: Path | str,
    provider: str,
    policy: Path | str,
    output: Path | str,
) -> dict[str, Any]:
    """Evaluate eligible catalog versions once and write an immutable run bundle."""
    catalog_path = Path(unified_catalog)
    library_path = Path(library)
    v4_manifest_path = Path(v4_manifest)
    universe_path = Path(universe)
    policy_path = Path(policy)
    output_path = Path(output)
    if output_path.exists() and any(output_path.iterdir()):
        raise FileExistsError("OUTPUT_DIRECTORY_NOT_EMPTY")
    catalog = _read_frame(catalog_path)
    required_catalog = {
        "factor_id", "factor_version", "source_kind", "filter_eligible",
        "catalog_exclusion_reasons", "family_id", "mechanism_tag",
        "semantic_status", "warmup_sessions", "direction",
    }
    if required_catalog - set(catalog.columns):
        raise ValueError("UNIFIED_CATALOG_SCHEMA_INVALID")
    if catalog[["factor_id", "factor_version"]].duplicated().any():
        raise ValueError("DUPLICATE_FACTOR_VERSION")
    library_data = _read_object(library_path)
    v4_manifest_data = _read_object(v4_manifest_path)
    expected_library_hash = v4_manifest_data.get("project_factor_library.json")
    if expected_library_hash and expected_library_hash != _digest(library_path):
        raise ValueError("V4_LIBRARY_MANIFEST_MISMATCH")
    policy_data = _read_object(policy_path)
    if policy_data.get("primary_frequency") != "WEEKLY_REBALANCE_SIGNAL":
        raise ValueError("UNSUPPORTED_FILTER_FREQUENCY")
    if policy_data.get("forward_label") != "T_CLOSE_T1_OPEN_T6_OPEN":
        raise ValueError("UNSUPPORTED_FORWARD_LABEL")
    panels = _load_panels(Path(market_data))
    universe_frame = _read_frame(universe_path)
    if "security_id" not in universe_frame:
        raise ValueError("UNIVERSE_SECURITY_ID_REQUIRED")
    security_ids = universe_frame.security_id.astype(str).tolist()
    benchmark = str(policy_data.get("benchmark_security_id", "SPY"))
    if benchmark in security_ids:
        raise ValueError("BENCHMARK_IN_ELIGIBLE_UNIVERSE")
    if len(security_ids) != len(set(security_ids)):
        raise ValueError("DUPLICATE_UNIVERSE_SECURITY_ID")
    common_sessions = panels["open"].index
    signal_sessions = weekly_signal_sessions(common_sessions)
    labels = forward_open_returns(
        panels["open"].reindex(columns=security_ids), signal_sessions, horizon=5
    )
    expansions = load_expansion_snapshot(expansion_registry)
    base_factors = {
        str(row.get("local_factor_id")): row for row in library_data.get("factors", [])
    }
    expansion_factors = {
        (str(row["factor_id"]), int(row["factor_version"])): row for row in expansions
    }

    output_path.mkdir(parents=True, exist_ok=True)
    cache_dir = output_path / "rank_cache"
    cache_dir.mkdir()
    diagnostic_rows: list[dict[str, Any]] = []
    yearly_rows: list[dict[str, Any]] = []
    rank_panels: dict[tuple[str, int], pd.DataFrame] = {}
    for catalog_row in catalog.to_dict("records"):
        identity = {
            "factor_id": str(catalog_row["factor_id"]),
            "factor_version": int(catalog_row["factor_version"]),
            "family_id": catalog_row.get("family_id"),
            "mechanism_tag": catalog_row.get("mechanism_tag"),
            "semantic_status": catalog_row.get("semantic_status"),
            "warmup_sessions": int(catalog_row.get("warmup_sessions", 0)),
            "direction": int(catalog_row.get("direction", 0)),
        }
        eligible = catalog_row["filter_eligible"]
        if isinstance(eligible, str):
            eligible = eligible.strip().lower() == "true"
        if not bool(eligible):
            diagnostic_rows.append(
                identity
                | {
                    "valid_signal_sessions": None,
                    "median_cross_section_coverage": None,
                    "mean_rank_ic_5d": None,
                    "icir_5d": None,
                    "positive_year_ratio": None,
                    "q1_mean_5d": None,
                    "q5_mean_5d": None,
                    "q5_minus_q1_5d": None,
                    "weekly_top10_turnover": None,
                    "filter_status": "CATALOG_INELIGIBLE",
                    "filter_reason": str(catalog_row.get("catalog_exclusion_reasons") or "CATALOG_INELIGIBLE"),
                }
            )
            continue
        try:
            factor = _factor_definition(catalog_row, base_factors, expansion_factors)
            values = _evaluate_one(factor, provider, panels)
            signal_values = values.reindex(index=signal_sessions, columns=security_ids)
            ranks = signal_values.rank(axis=1, pct=True, method="average")
            metrics, yearly = diagnose_factor(ranks, labels, policy_data)
            rank_panels[(identity["factor_id"], identity["factor_version"])] = ranks
            cache_path = _write_rank_cache(
                ranks, cache_dir, identity["factor_id"], identity["factor_version"]
            )
            gated = apply_filter_gates(pd.DataFrame([identity | metrics]), policy_data).iloc[0]
            diagnostic_rows.append(gated.to_dict() | {"rank_cache_file": str(cache_path.relative_to(output_path))})
            if not yearly.empty:
                yearly.insert(0, "factor_version", identity["factor_version"])
                yearly.insert(0, "factor_id", identity["factor_id"])
                yearly_rows.extend(yearly.to_dict("records"))
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            diagnostic_rows.append(
                identity
                | {
                    "valid_signal_sessions": None,
                    "median_cross_section_coverage": None,
                    "mean_rank_ic_5d": None,
                    "icir_5d": None,
                    "positive_year_ratio": None,
                    "q1_mean_5d": None,
                    "q5_mean_5d": None,
                    "q5_minus_q1_5d": None,
                    "weekly_top10_turnover": None,
                    "filter_status": "REJECT_DATA",
                    "filter_reason": f"EVALUATION_ERROR:{str(exc)}",
                }
            )

    diagnostics = pd.DataFrame(diagnostic_rows)
    passing = diagnostics[diagnostics.filter_status.eq("PASS_GATES")]
    conflicts = compute_redundancy_conflicts(passing, rank_panels, policy_data)
    selected_decisions, messages = greedy_nonredundant_selection(
        passing, conflicts, policy_data
    )
    decisions = diagnostics.copy()
    updates = {
        (str(row.factor_id), int(row.factor_version)): (row.filter_status, row.filter_reason)
        for row in selected_decisions.itertuples(index=False)
    }
    for index, row in decisions.iterrows():
        key = (str(row.factor_id), int(row.factor_version))
        if key in updates:
            decisions.loc[index, ["filter_status", "filter_reason"]] = updates[key]

    input_hashes = {
        "unified_catalog": _digest(catalog_path),
        "v4_library": _digest(library_path),
        "v4_manifest": _digest(v4_manifest_path),
        "expansion_registry_snapshot": _canonical_hash(expansions),
        "universe": _digest(universe_path),
        "policy": _digest(policy_path),
        "market_panels": {
            path.name: _digest(path) for path in sorted(Path(market_data).glob("*.parquet"))
        },
    }
    run_id = "v5-1-filter-" + _canonical_hash(
        {"provider": provider, "input_hashes": input_hashes}
    )[:20]
    metadata = {
        "filter_run_id": run_id,
        "selection_config_hash": input_hashes["policy"],
        "v4_manifest_hash": input_hashes["v4_manifest"],
        "expansion_registry_snapshot_hash": input_hashes["expansion_registry_snapshot"],
        "alpha_catalog_hash": input_hashes["unified_catalog"],
        "provider": provider,
        "universe_hash": input_hashes["universe"],
        "sample_start": str(labels.index.min().date()) if len(labels) else None,
        "sample_end": str(labels.index.max().date()) if len(labels) else None,
    }
    proposal = build_proposed_pool(selected_decisions, metadata, policy_data)
    proposal["messages"] = messages
    proposal_without_hash = dict(proposal)
    proposal_without_hash.pop("content_hash")
    proposal["content_hash"] = _canonical_hash(proposal_without_hash)

    paths = {
        "diagnostics": output_path / "alpha_diagnostics.csv",
        "yearly_ic": output_path / "yearly_ic.csv",
        "conflicts": output_path / "redundancy_conflicts.csv",
        "decisions": output_path / "filter_decisions.csv",
        "proposal": output_path / "proposed_active_pool.json",
        "blocked_items": output_path / "blocked_items.csv",
        "config": output_path / "filter_config.json",
        "run_status": output_path / "run_status.json",
        "manifest": output_path / "manifest.json",
    }
    diagnostics.to_csv(paths["diagnostics"], index=False)
    pd.DataFrame(yearly_rows, columns=["factor_id", "factor_version", "year", "valid_signal_sessions", "mean_rank_ic_5d", "year_status"]).to_csv(paths["yearly_ic"], index=False)
    conflicts.to_csv(paths["conflicts"], index=False)
    decisions.to_csv(paths["decisions"], index=False)
    paths["proposal"].write_text(json.dumps(proposal, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    blocked = decisions[~decisions.filter_status.eq("SELECTED")][
        ["factor_id", "factor_version", "filter_status", "filter_reason"]
    ]
    blocked.to_csv(paths["blocked_items"], index=False)
    paths["config"].write_text(json.dumps(policy_data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    run_status = {
        "status": proposal["status"],
        "filter_run_id": run_id,
        "catalog_factor_version_count": int(len(catalog)),
        "selected_factor_count": int(decisions.filter_status.eq("SELECTED").sum()),
        "messages": messages,
        "selection_evidence": "DEVELOPMENT_EXPOSED",
        "historical_pit_verified": False,
        "live_orders_submitted": 0,
    }
    paths["run_status"].write_text(json.dumps(run_status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    artifact_files = [
        path for key, path in paths.items() if key != "manifest"
    ] + sorted(cache_dir.glob("*.parquet"))
    manifest = {
        "schema_version": 1,
        "artifact_type": "V5_1_ALPHA_FILTER_RUN",
        "filter_run_id": run_id,
        "input_hashes": input_hashes,
        "files": {
            str(path.relative_to(output_path)): _digest(path) for path in artifact_files
        },
        "selection_evidence": "DEVELOPMENT_EXPOSED",
        "historical_pit_verified": False,
        "survivorship_bias": True,
        "live_orders_submitted": 0,
    }
    paths["manifest"].write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        **run_status,
        **{key: str(path) for key, path in paths.items()},
        "output": str(output_path),
    }


def freeze_filtered_pool(
    proposal_path: Path | str,
    decisions_path: Path | str,
    output_dir: Path | str,
) -> dict[str, Any]:
    """Freeze human approvals from one proposal into a compatible Active Pool."""
    proposal_file = Path(proposal_path)
    decisions_file = Path(decisions_path)
    output = Path(output_dir)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("OUTPUT_DIRECTORY_NOT_EMPTY")
    proposal = _read_object(proposal_file)
    declared = proposal.get("content_hash")
    unsigned = dict(proposal)
    unsigned.pop("content_hash", None)
    if not declared or declared != _canonical_hash(unsigned):
        raise ValueError("PROPOSAL_CONTENT_HASH_MISMATCH")
    if proposal.get("status") != "READY_FOR_HUMAN_REVIEW" or proposal.get("blockers"):
        raise ValueError("PROPOSAL_NOT_FREEZABLE")
    decisions = pd.read_csv(decisions_file, keep_default_na=False)
    required = {"factor_id", "factor_version", "decision", "comment"}
    if set(decisions.columns) != required:
        raise ValueError("INVALID_REVIEW_DECISION_SCHEMA")
    if decisions[["factor_id", "factor_version"]].duplicated().any():
        raise ValueError("DUPLICATE_REVIEW_DECISION")
    proposed = {
        (str(row["factor_id"]), int(row["factor_version"]))
        for row in proposal["selected_factors"]
    }
    reviewed = {
        (str(row.factor_id), int(row.factor_version))
        for row in decisions.itertuples(index=False)
    }
    if reviewed - proposed:
        raise ValueError("REVIEW_FACTOR_NOT_PROPOSED")
    if reviewed != proposed:
        raise ValueError("REVIEW_DECISION_INCOMPLETE")
    if not decisions.decision.isin(["APPROVE", "REJECT"]).all():
        raise ValueError("INVALID_REVIEW_DECISION")
    approved = decisions[decisions.decision.eq("APPROVE")].copy()
    if not 5 <= len(approved) <= 12:
        raise ValueError("COMPOSITE_BLOCKED_INSUFFICIENT_ALPHA")
    frozen_at = datetime.now(timezone.utc).isoformat()
    weight = str(1 / len(approved))
    selected_factors = [
        {
            "local_factor_id": str(row.factor_id),
            "factor_id": str(row.factor_id),
            "factor_version": int(row.factor_version),
            "direction": 1,
            "weight": weight,
            "comment": str(row.comment),
        }
        for row in approved.itertuples(index=False)
    ]
    pool = {
        "schema_version": 2,
        "status": "FROZEN",
        "active_pool_id": "v5-1-pool-" + declared[:16],
        "library_version": "V4_PLUS_EXPANSION_REGISTRY",
        "v4_manifest_sha256": proposal["v4_manifest_hash"],
        "expansion_registry_snapshot_sha256": proposal["expansion_registry_snapshot_hash"],
        "alpha_catalog_sha256": proposal["alpha_catalog_hash"],
        "provider": proposal["provider"],
        "universe_hash": proposal["universe_hash"],
        "sample_start": proposal["sample_start"],
        "sample_end": proposal["sample_end"],
        "filter_run_id": proposal["filter_run_id"],
        "selection_config_hash": proposal["selection_config_hash"],
        "selected_factors": selected_factors,
        "decision_list": decisions.to_dict("records"),
        "warnings": proposal.get("messages", []),
        "selection_basis": "V5_1_FILTER_PLUS_HUMAN_REVIEW",
        "selection_evidence": "DEVELOPMENT_EXPOSED",
        "historical_pit_verified": False,
        "independent_oos_claim_allowed": False,
        "survivorship_bias": True,
        "active_pool_frozen_at": frozen_at,
        "live_orders_submitted": 0,
    }
    pool["content_sha256"] = _canonical_hash(pool)
    output.mkdir(parents=True, exist_ok=True)
    pool_file = output / "active_pool.json"
    pool_file.write_text(json.dumps(pool, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "artifact_type": "V5_1_FROZEN_ACTIVE_POOL",
        "files": {pool_file.name: _digest(pool_file)},
        "source_proposal_sha256": _digest(proposal_file),
        "review_decisions_sha256": _digest(decisions_file),
        "live_orders_submitted": 0,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return pool
