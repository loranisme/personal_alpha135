"""Immutable V5.1 champion and one-time retrospective holdout replay controls."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .alpha_filter_diagnostics import (
    diagnose_factor,
    forward_open_returns,
    weekly_signal_sessions,
)
from .alpha_filter_workflow import flat_equal_weight_composite


PRE2025_START = "2019-01-02"
PRE2025_END = "2024-12-31"
HOLDOUT_START = "2025-01-01"


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


def _digest(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read_json(path: Path | str) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON_ROOT_NOT_OBJECT:{path}")
    return value


def _write_json(path: Path, value: Mapping[str, Any], *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "x" if exclusive else "w"
    with path.open(mode, encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                dict(value),
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
                default=str,
            )
            + "\n"
        )


def _require_empty_output(output: Path) -> None:
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("OUTPUT_DIRECTORY_NOT_EMPTY")
    output.mkdir(parents=True, exist_ok=True)


def _verify_signed(value: Mapping[str, Any], field: str, error: str) -> None:
    declared = value.get(field)
    unsigned = dict(value)
    unsigned.pop(field, None)
    if not declared or declared != _canonical_hash(unsigned):
        raise ValueError(error)


def _verify_proposal(proposal: Mapping[str, Any]) -> None:
    _verify_signed(proposal, "content_hash", "PROPOSAL_CONTENT_HASH_MISMATCH")
    if proposal.get("status") != "READY_FOR_HUMAN_REVIEW" or proposal.get("blockers"):
        raise ValueError("PROPOSAL_NOT_FREEZABLE")


def _selected_decisions(path: Path | str) -> pd.DataFrame:
    decisions = pd.read_csv(path)
    required = {
        "factor_id",
        "factor_version",
        "direction",
        "family_id",
        "mechanism_tag",
        "filter_status",
    }
    if required - set(decisions):
        raise ValueError("FILTER_DECISIONS_SCHEMA_INVALID")
    selected = decisions.loc[decisions.filter_status.eq("SELECTED")].copy()
    if selected[["factor_id", "factor_version"]].duplicated().any():
        raise ValueError("DUPLICATE_SELECTED_FACTOR")
    return selected


def _identity_set(rows: list[dict[str, Any]] | pd.DataFrame) -> set[tuple[str, int]]:
    if isinstance(rows, pd.DataFrame):
        return {
            (str(row.factor_id), int(row.factor_version))
            for row in rows.itertuples(index=False)
        }
    return {
        (str(row["factor_id"]), int(row["factor_version"])) for row in rows
    }


def freeze_full_history_champion(
    *,
    config: Path | str,
    proposal: Path | str,
    decisions: Path | str,
    comparison: Path | str,
    output: Path | str,
) -> dict[str, Any]:
    """Freeze the existing 14-factor full-history champion by content hash."""
    artifact_paths = {
        "config": Path(config),
        "proposal": Path(proposal),
        "decisions": Path(decisions),
        "comparison": Path(comparison),
    }
    destination = Path(output)
    _require_empty_output(destination)
    config_value = _read_json(artifact_paths["config"])
    proposal_value = _read_json(artifact_paths["proposal"])
    _verify_proposal(proposal_value)
    config_hash = _digest(artifact_paths["config"])
    if proposal_value.get("selection_config_hash") != config_hash:
        raise ValueError("CHAMPION_FILTER_CONFIG_HASH_MISMATCH")
    selected = _selected_decisions(artifact_paths["decisions"])
    if _identity_set(proposal_value.get("selected_factors") or []) != _identity_set(selected):
        raise ValueError("CHAMPION_SELECTED_FACTORS_MISMATCH")
    if len(selected) != 14 or int(proposal_value.get("factor_count", -1)) != 14:
        raise ValueError("CHAMPION_MUST_HAVE_14_FACTORS")
    comparison_frame = pd.read_csv(artifact_paths["comparison"])
    champion_rows = comparison_frame.loc[
        comparison_frame.composite_id.eq("V5_1_PROPOSED")
    ]
    if len(champion_rows) != 1 or int(champion_rows.iloc[0].factor_count) != 14:
        raise ValueError("CHAMPION_COMPARISON_ROW_INVALID")
    selected_lookup = {
        (str(row.factor_id), int(row.factor_version)): row
        for row in selected.itertuples(index=False)
    }
    factor_rows = []
    for identity in proposal_value["selected_factors"]:
        key = (str(identity["factor_id"]), int(identity["factor_version"]))
        row = selected_lookup[key]
        factor_rows.append(
            {
                "factor_id": key[0],
                "factor_version": key[1],
                "direction": int(row.direction),
                "family_id": str(row.family_id),
                "mechanism_tag": str(row.mechanism_tag),
                "weight": str(1 / 14),
            }
        )
    metric_row = champion_rows.iloc[0].where(champion_rows.iloc[0].notna(), None).to_dict()
    lock = {
        "schema_version": 1,
        "artifact_type": "V5_1_FULL_HISTORY_CHAMPION_LOCK",
        "status": "IMMUTABLY_FROZEN",
        "champion_id": "V5_1_FULL_HISTORY_14_FACTOR_CHAMPION",
        "factor_count": 14,
        "selected_factors": factor_rows,
        "weighting": "FLAT_EQUAL_WEIGHT",
        "provider": proposal_value["provider"],
        "sample_start": proposal_value["sample_start"],
        "sample_end": proposal_value["sample_end"],
        "filter_run_id": proposal_value["filter_run_id"],
        "filter_config": config_value,
        "filter_config_sha256": config_hash,
        "source_artifact_hashes": {
            key: _digest(path) for key, path in artifact_paths.items()
        },
        "observed_full_history_metrics": metric_row,
        "selection_evidence": "DEVELOPMENT_EXPOSED",
        "independent_oos_claim_allowed": False,
        "mutation_policy": "NEW_CANDIDATE_ID_REQUIRED",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
    }
    lock["content_sha256"] = _canonical_hash(lock)
    lock_path = destination / "champion.lock.json"
    _write_json(lock_path, lock)
    manifest = {
        "schema_version": 1,
        "artifact_type": "V5_1_FULL_HISTORY_CHAMPION_LOCK_MANIFEST",
        "files": {lock_path.name: _digest(lock_path)},
        "source_artifact_hashes": lock["source_artifact_hashes"],
    }
    _write_json(destination / "manifest.json", manifest)
    return lock


def verify_full_history_champion(
    lock_path: Path | str,
    artifacts: Mapping[str, Path | str] | None = None,
) -> bool:
    lock = _read_json(lock_path)
    _verify_signed(lock, "content_sha256", "CHAMPION_LOCK_CONTENT_HASH_MISMATCH")
    if lock.get("status") != "IMMUTABLY_FROZEN" or lock.get("factor_count") != 14:
        raise ValueError("CHAMPION_LOCK_INVALID")
    if artifacts is not None:
        expected = lock.get("source_artifact_hashes") or {}
        if set(artifacts) != set(expected):
            raise ValueError("CHAMPION_ARTIFACT_SET_MISMATCH")
        for key, path in artifacts.items():
            if _digest(path) != expected[key]:
                raise ValueError(f"CHAMPION_ARTIFACT_HASH_MISMATCH:{key}")
    return True


def freeze_pre2025_pool(
    *,
    champion_lock: Path | str,
    proposal: Path | str,
    decisions: Path | str,
    output: Path | str,
) -> dict[str, Any]:
    """Freeze the pre-2025 filter output before any holdout labels are read."""
    verify_full_history_champion(champion_lock)
    champion = _read_json(champion_lock)
    proposal_value = _read_json(proposal)
    _verify_proposal(proposal_value)
    if proposal_value.get("selection_config_hash") != champion["filter_config_sha256"]:
        raise ValueError("PRE2025_FILTER_CONFIG_MISMATCH")
    sample_end = pd.Timestamp(proposal_value.get("sample_end"))
    if pd.isna(sample_end) or sample_end > pd.Timestamp(PRE2025_END):
        raise ValueError("PRE2025_SAMPLE_CROSSES_CUTOFF")
    selected = _selected_decisions(decisions)
    if _identity_set(proposal_value.get("selected_factors") or []) != _identity_set(selected):
        raise ValueError("PRE2025_SELECTED_FACTORS_MISMATCH")
    minimum = int(champion["filter_config"]["active_pool_min"])
    maximum = int(champion["filter_config"]["active_pool_max"])
    if not minimum <= len(selected) <= maximum:
        raise ValueError("PRE2025_POOL_SIZE_OUTSIDE_FROZEN_RANGE")
    selected_lookup = {
        (str(row.factor_id), int(row.factor_version)): row
        for row in selected.itertuples(index=False)
    }
    factors = []
    for identity in proposal_value["selected_factors"]:
        key = (str(identity["factor_id"]), int(identity["factor_version"]))
        row = selected_lookup[key]
        factors.append(
            {
                "factor_id": key[0],
                "factor_version": key[1],
                "direction": int(row.direction),
                "family_id": str(row.family_id),
                "mechanism_tag": str(row.mechanism_tag),
                "weight": str(1 / len(selected)),
            }
        )
    pool = {
        "schema_version": 1,
        "artifact_type": "V5_1_PRE2025_POOL",
        "status": "FROZEN_BEFORE_HOLDOUT_ACCESS",
        "pool_id": "PRE2025_POOL",
        "factor_count": len(factors),
        "selected_factors": factors,
        "weighting": "FLAT_EQUAL_WEIGHT",
        "provider": proposal_value["provider"],
        "training_data_start": PRE2025_START,
        "training_data_end": PRE2025_END,
        "actual_signal_start": proposal_value["sample_start"],
        "actual_signal_end": proposal_value["sample_end"],
        "holdout_not_before": HOLDOUT_START,
        "filter_run_id": proposal_value["filter_run_id"],
        "filter_config_sha256": champion["filter_config_sha256"],
        "champion_lock_sha256": _digest(champion_lock),
        "source_proposal_sha256": _digest(proposal),
        "source_decisions_sha256": _digest(decisions),
        "gate_reapplication_after_holdout": False,
        "reselection_after_holdout_allowed": False,
        "historical_evidence_kind": "RETROSPECTIVE_HOLDOUT_REPLAY",
        "independent_oos_claim_allowed": False,
        "frozen_at": datetime.now(timezone.utc).isoformat(),
    }
    pool["content_sha256"] = _canonical_hash(pool)
    destination = Path(output)
    _require_empty_output(destination)
    pool_path = destination / "pre2025_pool.json"
    _write_json(pool_path, pool)
    manifest = {
        "schema_version": 1,
        "artifact_type": "V5_1_PRE2025_POOL_MANIFEST",
        "files": {pool_path.name: _digest(pool_path)},
        "pool_content_sha256": pool["content_sha256"],
        "created_before_holdout_access": True,
    }
    _write_json(destination / "manifest.json", manifest)
    return pool


def verify_pre2025_pool(pool_path: Path | str, manifest_path: Path | str) -> bool:
    pool = _read_json(pool_path)
    _verify_signed(pool, "content_sha256", "PRE2025_POOL_CONTENT_HASH_MISMATCH")
    if (
        pool.get("pool_id") != "PRE2025_POOL"
        or pool.get("status") != "FROZEN_BEFORE_HOLDOUT_ACCESS"
    ):
        raise ValueError("PRE2025_POOL_INVALID")
    manifest = _read_json(manifest_path)
    if (manifest.get("files") or {}).get(Path(pool_path).name) != _digest(pool_path):
        raise ValueError("PRE2025_POOL_MANIFEST_HASH_MISMATCH")
    if manifest.get("pool_content_sha256") != pool["content_sha256"]:
        raise ValueError("PRE2025_POOL_MANIFEST_CONTENT_MISMATCH")
    return True


def materialize_market_window(
    *, source: Path | str, output: Path | str, start: str, end: str
) -> dict[str, Any]:
    """Write a date-bounded Parquet view without returning post-cutoff rows."""
    source_dir = Path(source)
    destination = Path(output)
    _require_empty_output(destination)
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    if pd.isna(start_ts) or pd.isna(end_ts) or start_ts > end_ts:
        raise ValueError("INVALID_MARKET_WINDOW")
    files: dict[str, str] = {}
    row_counts: dict[str, int] = {}
    actual_starts: list[pd.Timestamp] = []
    actual_ends: list[pd.Timestamp] = []
    for source_path in sorted(source_dir.glob("*.parquet")):
        import pyarrow.parquet as parquet

        date_type = parquet.read_schema(source_path).field("Date").type
        timezone_name = getattr(date_type, "tz", None)
        filter_start = (
            start_ts.tz_localize(timezone_name).to_pydatetime()
            if timezone_name
            else start_ts.to_pydatetime()
        )
        filter_end = (
            end_ts.tz_localize(timezone_name).to_pydatetime()
            if timezone_name
            else end_ts.to_pydatetime()
        )
        panel = pd.read_parquet(
            source_path,
            filters=[
                ("Date", ">=", filter_start),
                ("Date", "<=", filter_end),
            ],
        )
        panel.index = pd.to_datetime(panel.index)
        if panel.index.has_duplicates or not panel.index.is_monotonic_increasing:
            raise ValueError(f"MARKET_PANEL_INDEX_INVALID:{source_path.name}")
        if len(panel):
            lower = pd.Timestamp(filter_start)
            upper = pd.Timestamp(filter_end)
            if panel.index.min() < lower or panel.index.max() > upper:
                raise ValueError(f"MARKET_WINDOW_ESCAPE:{source_path.name}")
            first = pd.Timestamp(panel.index.min())
            last = pd.Timestamp(panel.index.max())
            actual_starts.append(
                first.tz_localize("UTC") if first.tzinfo is None else first.tz_convert("UTC")
            )
            actual_ends.append(
                last.tz_localize("UTC") if last.tzinfo is None else last.tz_convert("UTC")
            )
        output_path = destination / source_path.name
        panel.to_parquet(output_path)
        files[source_path.name] = _digest(output_path)
        row_counts[source_path.name] = int(len(panel))
    if not files:
        raise ValueError("MARKET_DATA_PANELS_REQUIRED")
    manifest = {
        "schema_version": 1,
        "artifact_type": "DATE_BOUNDED_MARKET_WINDOW",
        "requested_start": start_ts.date().isoformat(),
        "requested_end": end_ts.date().isoformat(),
        "actual_start": min(actual_starts).date().isoformat() if actual_starts else None,
        "actual_end": max(actual_ends).date().isoformat() if actual_ends else None,
        "files": files,
        "row_counts": row_counts,
        "post_cutoff_rows_returned": 0,
    }
    manifest["content_sha256"] = _canonical_hash(manifest)
    _write_json(destination / "window_manifest.json", manifest)
    return manifest


def evaluate_fixed_pool(
    *,
    pool: Mapping[str, Any],
    rank_panels: Mapping[str, pd.DataFrame],
    opens: pd.DataFrame,
    closes: pd.DataFrame,
    config: Mapping[str, Any],
    holdout_start: str,
    holdout_end: str,
    portfolio_runner: Callable[[pd.DataFrame], Mapping[str, Any]],
) -> dict[str, Any]:
    """Evaluate a frozen pool on one label window without applying any gate."""
    if pool.get("pool_id") != "PRE2025_POOL":
        raise ValueError("FIXED_POOL_ID_REQUIRED")
    start = pd.Timestamp(holdout_start, tz="UTC")
    end = pd.Timestamp(holdout_end, tz="UTC")
    if start > end:
        raise ValueError("INVALID_HOLDOUT_WINDOW")
    all_signals = weekly_signal_sessions(pd.DatetimeIndex(opens.index))
    signals = pd.DatetimeIndex(
        [signal for signal in all_signals if start <= signal <= end]
    )
    if signals.empty:
        raise ValueError("HOLDOUT_SIGNAL_SESSIONS_EMPTY")
    labels = forward_open_returns(opens, signals, horizon=5)
    adjusted: dict[str, pd.DataFrame] = {}
    factor_metrics: list[dict[str, Any]] = []
    factor_yearly: list[dict[str, Any]] = []
    for factor in pool.get("selected_factors") or []:
        factor_id = str(factor["factor_id"])
        direction = int(factor.get("direction", 0))
        if direction not in {-1, 1}:
            raise ValueError(f"INVALID_FACTOR_DIRECTION:{factor_id}")
        if factor_id not in rank_panels:
            raise ValueError(f"HOLDOUT_RANK_PANEL_MISSING:{factor_id}")
        ranks = rank_panels[factor_id].reindex(index=signals, columns=opens.columns)
        oriented = ranks if direction == 1 else 1 - ranks
        adjusted[factor_id] = oriented
        metrics, yearly = diagnose_factor(oriented, labels, config)
        factor_metrics.append(
            {
                "factor_id": factor_id,
                "factor_version": int(factor["factor_version"]),
                **metrics,
            }
        )
        for row in yearly.to_dict("records"):
            factor_yearly.append(
                {
                    "factor_id": factor_id,
                    "factor_version": int(factor["factor_version"]),
                    **row,
                }
            )
    composite = flat_equal_weight_composite(adjusted)
    composite_metrics, composite_yearly = diagnose_factor(composite, labels, config)
    portfolio_metrics = dict(portfolio_runner(composite))
    return {
        "status": "HISTORICAL_HOLDOUT_REPLAY_COMPLETE",
        "pool_id": "PRE2025_POOL",
        "holdout_start": start.date().isoformat(),
        "holdout_end": end.date().isoformat(),
        "fixed_pool_factor_count": len(adjusted),
        "fixed_factor_ids": list(adjusted),
        "signal_session_count": int(len(signals)),
        "composite_metrics": composite_metrics,
        "composite_yearly": composite_yearly.to_dict("records"),
        "factor_metrics": factor_metrics,
        "factor_yearly": factor_yearly,
        "portfolio_metrics": portfolio_metrics,
        "gate_reapplied": False,
        "pool_modified_after_holdout": False,
    }


def execute_one_time_replay(
    *,
    champion_lock: Path | str,
    pool: Path | str,
    pool_manifest: Path | str,
    output: Path | str,
    holdout_input_hashes: Mapping[str, str],
    loader: Callable[[], Any],
    evaluator: Callable[[Any], Mapping[str, Any]],
) -> dict[str, Any]:
    """Commit one holdout access on disk, then call the loader exactly once."""
    verify_full_history_champion(champion_lock)
    verify_pre2025_pool(pool, pool_manifest)
    frozen_pool = _read_json(pool)
    destination = Path(output)
    destination.mkdir(parents=True, exist_ok=True)
    access_path = destination / "holdout_access_commit.json"
    if access_path.exists():
        raise ValueError("HOLDOUT_ALREADY_OPENED")
    access = {
        "schema_version": 1,
        "artifact_type": "HISTORICAL_HOLDOUT_ACCESS_COMMIT",
        "status": "COMMITTED_BEFORE_LABEL_READ",
        "pool_id": frozen_pool["pool_id"],
        "pool_file_sha256": _digest(pool),
        "pool_content_sha256": frozen_pool["content_sha256"],
        "champion_lock_sha256": _digest(champion_lock),
        "holdout_start": HOLDOUT_START,
        "holdout_input_hashes": dict(holdout_input_hashes),
        "replay_limit": 1,
        "committed_at": datetime.now(timezone.utc).isoformat(),
    }
    access["content_sha256"] = _canonical_hash(access)
    try:
        _write_json(access_path, access, exclusive=True)
    except FileExistsError as exc:
        raise ValueError("HOLDOUT_ALREADY_OPENED") from exc
    try:
        data = loader()
        evaluated = dict(evaluator(data))
        if evaluated.get("pool_id") != "PRE2025_POOL":
            raise ValueError("HOLDOUT_RESULT_POOL_MISMATCH")
        if evaluated.get("gate_reapplied") is not False:
            raise ValueError("HOLDOUT_GATE_REAPPLICATION_FORBIDDEN")
        if evaluated.get("pool_modified_after_holdout") is not False:
            raise ValueError("HOLDOUT_POOL_MUTATION_FORBIDDEN")
        result = {
            **evaluated,
            "artifact_type": "V5_1_HISTORICAL_HOLDOUT_REPLAY",
            "historical_evidence_kind": "RETROSPECTIVE_HOLDOUT_REPLAY",
            "independent_oos_claim_allowed": False,
            "selection_changes_after_result_allowed": False,
            "access_commit_sha256": _digest(access_path),
            "pool_content_sha256": frozen_pool["content_sha256"],
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        result["content_sha256"] = _canonical_hash(result)
        result_path = destination / "holdout_replay_result.json"
        _write_json(result_path, result, exclusive=True)
        manifest = {
            "schema_version": 1,
            "artifact_type": "V5_1_HISTORICAL_HOLDOUT_REPLAY_MANIFEST",
            "files": {
                access_path.name: _digest(access_path),
                result_path.name: _digest(result_path),
            },
            "replay_limit": 1,
        }
        _write_json(destination / "manifest.json", manifest, exclusive=True)
        return result
    except BaseException:
        failure = destination / "holdout_replay_failed.json"
        if not failure.exists():
            _write_json(
                failure,
                {
                    "status": "FAILED_AFTER_HOLDOUT_ACCESS_COMMIT",
                    "access_commit_sha256": _digest(access_path),
                    "retry_allowed": False,
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                },
                exclusive=True,
            )
        raise


def verify_holdout_replay(output: Path | str) -> bool:
    """Verify the committed access, immutable result and delivery manifest."""
    root = Path(output)
    access_path = root / "holdout_access_commit.json"
    result_path = root / "holdout_replay_result.json"
    manifest_path = root / "manifest.json"
    access = _read_json(access_path)
    result = _read_json(result_path)
    manifest = _read_json(manifest_path)
    _verify_signed(access, "content_sha256", "HOLDOUT_ACCESS_HASH_MISMATCH")
    _verify_signed(result, "content_sha256", "HOLDOUT_RESULT_HASH_MISMATCH")
    expected_files = manifest.get("files") or {}
    for path in (access_path, result_path):
        if expected_files.get(path.name) != _digest(path):
            raise ValueError(f"HOLDOUT_MANIFEST_HASH_MISMATCH:{path.name}")
    if manifest.get("replay_limit") != 1 or access.get("replay_limit") != 1:
        raise ValueError("HOLDOUT_REPLAY_LIMIT_INVALID")
    if result.get("access_commit_sha256") != _digest(access_path):
        raise ValueError("HOLDOUT_RESULT_ACCESS_BINDING_MISMATCH")
    if result.get("pool_content_sha256") != access.get("pool_content_sha256"):
        raise ValueError("HOLDOUT_RESULT_POOL_BINDING_MISMATCH")
    if result.get("gate_reapplied") is not False:
        raise ValueError("HOLDOUT_GATE_REAPPLICATION_FORBIDDEN")
    if result.get("pool_modified_after_holdout") is not False:
        raise ValueError("HOLDOUT_POOL_MUTATION_FORBIDDEN")
    return True
