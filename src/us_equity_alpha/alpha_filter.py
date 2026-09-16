"""Deterministic gates and lightweight redundancy control for V5.1."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from itertools import combinations
from typing import Any

import numpy as np
import pandas as pd


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


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def apply_filter_gates(
    diagnostics: pd.DataFrame, config: Mapping[str, Any]
) -> pd.DataFrame:
    """Apply frozen data, signal, stability and usability gates in order."""
    required = {
        "direction",
        "valid_signal_sessions",
        "median_cross_section_coverage",
        "mean_rank_ic_5d",
        "positive_year_ratio",
        "q5_minus_q1_5d",
        "weekly_top10_turnover",
    }
    missing = required - set(diagnostics.columns)
    if missing:
        raise ValueError(f"DIAGNOSTIC_COLUMNS_MISSING:{','.join(sorted(missing))}")
    result = diagnostics.copy()
    statuses: list[str] = []
    reasons: list[str] = []
    for row in result.to_dict("records"):
        if row["direction"] != 1:
            raise ValueError("FILTER_DIRECTION_MUST_BE_ONE")
        data_values = (
            row["valid_signal_sessions"],
            row["median_cross_section_coverage"],
        )
        if not all(_finite(value) for value in data_values):
            status, reason = "REJECT_DATA", "MISSING_DATA_DIAGNOSTIC"
        elif float(row["median_cross_section_coverage"]) < float(config["min_median_coverage"]):
            status, reason = "REJECT_DATA", "MEDIAN_COVERAGE_BELOW_MINIMUM"
        elif int(row["valid_signal_sessions"]) < int(config["min_valid_signal_sessions"]):
            status, reason = "REJECT_DATA", "VALID_SIGNAL_SESSIONS_BELOW_MINIMUM"
        elif not _finite(row["mean_rank_ic_5d"]) or not _finite(row["q5_minus_q1_5d"]):
            status, reason = "REJECT_SIGNAL", "MISSING_SIGNAL_DIAGNOSTIC"
        elif config.get("require_positive_mean_rank_ic", True) and float(row["mean_rank_ic_5d"]) <= 0:
            status, reason = "REJECT_SIGNAL", "NONPOSITIVE_MEAN_RANK_IC"
        elif config.get("require_positive_q5_q1", True) and float(row["q5_minus_q1_5d"]) <= 0:
            status, reason = "REJECT_SIGNAL", "NONPOSITIVE_Q5_MINUS_Q1"
        elif not _finite(row["positive_year_ratio"]):
            status, reason = "WATCHLIST_UNSTABLE", "MISSING_POSITIVE_YEAR_RATIO"
        elif float(row["positive_year_ratio"]) < float(config["min_positive_year_ratio"]):
            status, reason = "WATCHLIST_UNSTABLE", "POSITIVE_YEAR_RATIO_BELOW_MINIMUM"
        elif not _finite(row["weekly_top10_turnover"]):
            status, reason = "WATCHLIST_HIGH_TURNOVER", "MISSING_WEEKLY_TOP10_TURNOVER"
        elif float(row["weekly_top10_turnover"]) > float(config["max_weekly_top10_turnover"]):
            status, reason = "WATCHLIST_HIGH_TURNOVER", "WEEKLY_TOP10_TURNOVER_ABOVE_MAXIMUM"
        else:
            status, reason = "PASS_GATES", "PASS_ALL_GATES"
        statuses.append(status)
        reasons.append(reason)
    result["filter_status"] = statuses
    result["filter_reason"] = reasons
    return result


def _key(row: Mapping[str, Any]) -> tuple[str, int]:
    return str(row["factor_id"]), int(row["factor_version"])


def _panel_for(
    rank_panels: Mapping[Any, pd.DataFrame], factor_id: str, version: int
) -> pd.DataFrame:
    if (factor_id, version) in rank_panels:
        return rank_panels[(factor_id, version)]
    if factor_id in rank_panels:
        return rank_panels[factor_id]
    raise ValueError(f"RANK_PANEL_MISSING:{factor_id}:v{version}")


def _pair_metrics(left: pd.DataFrame, right: pd.DataFrame) -> tuple[float | None, float | None]:
    dates = left.index.intersection(right.index)
    columns = left.columns.intersection(right.columns)
    correlations: list[float] = []
    overlaps: list[float] = []
    for date in dates:
        pair = pd.concat(
            [left.loc[date, columns].rename("left"), right.loc[date, columns].rename("right")],
            axis=1,
        ).replace([np.inf, -np.inf], np.nan).dropna()
        if len(pair) >= 2 and pair.left.nunique() >= 2 and pair.right.nunique() >= 2:
            correlation = pair.left.corr(pair.right, method="spearman")
            if pd.notna(correlation):
                correlations.append(abs(float(correlation)))
        if not pair.empty:
            count = max(1, int(math.ceil(len(pair) * 0.10)))
            left_top = set(pair.left.sort_values(ascending=False, kind="stable").head(count).index)
            right_top = set(pair.right.sort_values(ascending=False, kind="stable").head(count).index)
            union = left_top | right_top
            if union:
                overlaps.append(len(left_top & right_top) / len(union))
    median_corr = float(np.median(correlations)) if correlations else None
    mean_overlap = float(np.mean(overlaps)) if overlaps else None
    return median_corr, mean_overlap


def compute_redundancy_conflicts(
    candidates: pd.DataFrame,
    rank_panels: Mapping[Any, pd.DataFrame],
    config: Mapping[str, Any],
) -> pd.DataFrame:
    """Calculate pairwise hard conflicts only for candidates passing all gates."""
    passing = candidates[candidates.filter_status.eq("PASS_GATES")].copy()
    rows: list[dict[str, Any]] = []
    records = passing.to_dict("records")
    for left, right in combinations(records, 2):
        left_key, right_key = _key(left), _key(right)
        median_corr, mean_overlap = _pair_metrics(
            _panel_for(rank_panels, *left_key), _panel_for(rank_panels, *right_key)
        )
        reasons: list[str] = []
        if left.get("family_id") == right.get("family_id"):
            reasons.append("SAME_FAMILY")
        if median_corr is not None and median_corr > float(config["score_corr_conflict"]):
            reasons.append("SCORE_CORRELATION")
        if mean_overlap is not None and mean_overlap > float(config["top10_overlap_conflict"]):
            reasons.append("TOP10_OVERLAP")
        rows.append(
            {
                "left_factor_id": left_key[0],
                "left_factor_version": left_key[1],
                "right_factor_id": right_key[0],
                "right_factor_version": right_key[1],
                "same_family": left.get("family_id") == right.get("family_id"),
                "same_mechanism": left.get("mechanism_tag") == right.get("mechanism_tag"),
                "median_abs_score_correlation": median_corr,
                "mean_top10_jaccard": mean_overlap,
                "hard_conflict": bool(reasons),
                "conflict_reason": "|".join(reasons),
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "left_factor_id", "left_factor_version", "right_factor_id",
            "right_factor_version", "same_family", "same_mechanism",
            "median_abs_score_correlation", "mean_top10_jaccard",
            "hard_conflict", "conflict_reason",
        ],
    )


def _ordered_candidates(candidates: pd.DataFrame) -> pd.DataFrame:
    result = candidates.copy()
    result["_semantic_order"] = result.semantic_status.map(
        {"FULL_INTENT": 0, "PARTIAL_INTENT": 1}
    ).fillna(2)
    return result.sort_values(
        [
            "positive_year_ratio", "icir_5d", "mean_rank_ic_5d",
            "q5_minus_q1_5d", "weekly_top10_turnover",
            "median_cross_section_coverage", "_semantic_order",
            "warmup_sessions", "factor_id", "factor_version",
        ],
        ascending=[False, False, False, False, True, False, True, True, True, True],
        na_position="last",
        kind="stable",
    ).drop(columns="_semantic_order")


def greedy_nonredundant_selection(
    candidates: pd.DataFrame,
    conflicts: pd.DataFrame,
    config: Mapping[str, Any],
) -> tuple[pd.DataFrame, list[str]]:
    """Select in frozen metric order without padding or transitive pruning."""
    passing = candidates[candidates.filter_status.eq("PASS_GATES")].copy()
    ordered = _ordered_candidates(passing)
    hard: dict[frozenset[tuple[str, int]], str] = {}
    for conflict in conflicts.to_dict("records"):
        if bool(conflict.get("hard_conflict")):
            pair = frozenset(
                {
                    (str(conflict["left_factor_id"]), int(conflict["left_factor_version"])),
                    (str(conflict["right_factor_id"]), int(conflict["right_factor_version"])),
                }
            )
            hard[pair] = str(conflict.get("conflict_reason", ""))
    selected: list[tuple[str, int]] = []
    statuses: list[str] = []
    reasons: list[str] = []
    maximum = int(config["active_pool_max"])
    for row in ordered.to_dict("records"):
        candidate = _key(row)
        retained_conflict = next(
            (retained for retained in selected if frozenset({candidate, retained}) in hard),
            None,
        )
        if retained_conflict is not None:
            statuses.append("REJECT_REDUNDANT")
            reasons.append(f"REJECT_REDUNDANT_WITH_{retained_conflict[0]}")
        elif len(selected) >= maximum:
            statuses.append("REJECT_POOL_CAP")
            reasons.append("ACTIVE_POOL_MAX_REACHED")
        else:
            selected.append(candidate)
            statuses.append("SELECTED")
            reasons.append("SELECTED_NONREDUNDANT")
    ordered["filter_status"] = statuses
    ordered["filter_reason"] = reasons
    messages: list[str] = []
    if len(selected) < int(config["active_pool_min"]):
        messages.append("COMPOSITE_BLOCKED_INSUFFICIENT_ALPHA")
    selected_rows = ordered[ordered.filter_status.eq("SELECTED")]
    if selected_rows.mechanism_tag.duplicated().any():
        messages.append("SAME_MECHANISM_INCLUDED")
    return ordered.reset_index(drop=True), messages


def build_proposed_pool(
    decisions: pd.DataFrame,
    run_metadata: Mapping[str, Any],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a deterministic review proposal; never activate it automatically."""
    selected = decisions[decisions.filter_status.eq("SELECTED")]
    identities = [
        {"factor_id": str(row.factor_id), "factor_version": int(row.factor_version)}
        for row in selected.itertuples(index=False)
    ]
    blockers = []
    if len(identities) < int(config["active_pool_min"]):
        blockers.append("COMPOSITE_BLOCKED_INSUFFICIENT_ALPHA")
    if len(identities) > int(config["active_pool_max"]):
        blockers.append("COMPOSITE_BLOCKED_ACTIVE_POOL_EXCEEDS_MAX")
    proposal = {
        "schema_version": 1,
        "artifact_type": "V5_1_PROPOSED_ACTIVE_POOL",
        "status": "READY_FOR_HUMAN_REVIEW" if not blockers else "BLOCKED",
        **dict(run_metadata),
        "selected_factors": identities,
        "factor_count": len(identities),
        "weighting": "FLAT_EQUAL_WEIGHT",
        "blockers": blockers,
        "selection_evidence": "DEVELOPMENT_EXPOSED",
        "historical_pit_verified": False,
        "independent_oos_claim_allowed": False,
        "survivorship_bias": True,
        "live_orders_submitted": 0,
    }
    proposal["content_hash"] = _canonical_hash(proposal)
    return proposal
