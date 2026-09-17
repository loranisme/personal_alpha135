"""Freeze V5.1, build PRE2025_POOL, then execute one historical replay."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import pandas as pd

from run_v5_1_yfinance_long_history import _gate_outputs, _portfolio_runner
from us_equity_alpha.alpha_filter_workflow import run_alpha_filter_from_files
from us_equity_alpha.historical_holdout_replay import (
    evaluate_fixed_pool,
    execute_one_time_replay,
    freeze_full_history_champion,
    freeze_pre2025_pool,
    materialize_market_window,
    verify_full_history_champion,
    verify_pre2025_pool,
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False, default=str)
        + "\n",
        encoding="utf-8",
    )


def _champion_artifacts(args: argparse.Namespace) -> dict[str, Path]:
    root = args.full_history_evidence
    return {
        "config": args.policy,
        "proposal": root / "proposed_active_pool.json",
        "decisions": root / "filter_decisions.csv",
        "comparison": root / "composite_comparison.csv",
    }


def _freeze_champion(args: argparse.Namespace) -> dict:
    return freeze_full_history_champion(
        **_champion_artifacts(args), output=args.output
    )


def _copy_pre2025_evidence(filter_dir: Path, destination: Path) -> None:
    evidence = destination / "filter_evidence"
    evidence.mkdir()
    names = (
        "gate_funnel.csv",
        "gate_pass_factors.csv",
        "final_selected_factors.csv",
        "redundancy_deletions.csv",
        "run_status.json",
        "proposed_active_pool.json",
    )
    files = {}
    for name in names:
        source = filter_dir / name
        if source.exists():
            target = evidence / name
            shutil.copy2(source, target)
            files[str(target.relative_to(destination))] = _digest(target)
    _write_json(
        destination / "filter_evidence_manifest.json",
        {
            "artifact_type": "PRE2025_FILTER_EVIDENCE_MANIFEST",
            "files": files,
            "gate_or_threshold_changed": False,
            "holdout_labels_read": False,
        },
    )


def _build_pre2025(args: argparse.Namespace) -> dict:
    artifacts = _champion_artifacts(args)
    verify_full_history_champion(args.champion_lock, artifacts)
    if args.private_output.exists() and any(args.private_output.iterdir()):
        raise FileExistsError("PRIVATE_OUTPUT_DIRECTORY_NOT_EMPTY")
    args.private_output.mkdir(parents=True, exist_ok=True)
    source_inputs = args.full_private_run / "inputs"
    market = args.private_output / "inputs/market_panels"
    window = materialize_market_window(
        source=source_inputs / "market_panels",
        output=market,
        start="2019-01-02",
        end="2024-12-31",
    )
    filter_dir = args.private_output / "filter"
    result = run_alpha_filter_from_files(
        unified_catalog=args.full_private_run / "catalog/unified_alpha_catalog.parquet",
        library=source_inputs / "adapted_v4_library_yfinance.json",
        v4_manifest=source_inputs / "adapted_v4_manifest_yfinance.json",
        expansion_registry=source_inputs / "empty_expansion_registry",
        universe=source_inputs / "universe_500.csv",
        market_data=market,
        provider="yfinance_eod",
        policy=args.policy,
        output=filter_dir,
    )
    gate_summary = _gate_outputs(filter_dir)
    pool = freeze_pre2025_pool(
        champion_lock=args.champion_lock,
        proposal=filter_dir / "proposed_active_pool.json",
        decisions=filter_dir / "filter_decisions.csv",
        output=args.pool_output,
    )
    _copy_pre2025_evidence(filter_dir, args.pool_output)
    summary = {
        "status": "PRE2025_POOL_FROZEN",
        "pool_content_sha256": pool["content_sha256"],
        "filter_run_id": result["filter_run_id"],
        "gate_summary": gate_summary,
        "market_window_content_sha256": window["content_sha256"],
        "training_data_start": "2019-01-02",
        "training_data_end": "2024-12-31",
        "holdout_labels_read": False,
        "gate_or_threshold_changed": False,
        "selection_evidence": "DEVELOPMENT_EXPOSED",
        "independent_oos_claim_allowed": False,
    }
    _write_json(args.private_output / "pre2025_run_summary.json", summary)
    _write_json(args.pool_output / "pre2025_run_summary.json", summary)
    return summary


def _rank_cache_inputs(full_private_run: Path, pool: dict) -> tuple[dict[str, str], dict[str, str]]:
    decisions_path = full_private_run / "filter/filter_decisions.csv"
    decisions = pd.read_csv(decisions_path)
    selected_paths: dict[str, str] = {}
    hashes: dict[str, str] = {
        "filter_decisions.csv": _digest(decisions_path),
        "open.parquet": _digest(full_private_run / "inputs/market_panels/open.parquet"),
        "close.parquet": _digest(full_private_run / "inputs/market_panels/close.parquet"),
    }
    for factor in pool["selected_factors"]:
        factor_id = str(factor["factor_id"])
        factor_version = int(factor["factor_version"])
        row = decisions.loc[
            decisions.factor_id.astype(str).eq(factor_id)
            & decisions.factor_version.astype(int).eq(factor_version)
        ]
        if len(row) != 1 or pd.isna(row.iloc[0].rank_cache_file):
            raise ValueError(f"FULL_HISTORY_RANK_CACHE_MISSING:{factor_id}:v{factor_version}")
        relative = str(row.iloc[0].rank_cache_file)
        path = full_private_run / "filter" / relative
        selected_paths[factor_id] = relative
        hashes[relative] = _digest(path)
    return selected_paths, hashes


def _replay(args: argparse.Namespace) -> dict:
    artifacts = _champion_artifacts(args)
    verify_full_history_champion(args.champion_lock, artifacts)
    verify_pre2025_pool(args.pool, args.pool_manifest)
    pool = json.loads(args.pool.read_text(encoding="utf-8"))
    selected_paths, input_hashes = _rank_cache_inputs(args.full_private_run, pool)

    def loader() -> dict:
        universe = pd.read_csv(args.full_private_run / "inputs/universe_500.csv")
        security_ids = universe.security_id.astype(str).tolist()
        panels = args.full_private_run / "inputs/market_panels"
        opens = pd.read_parquet(panels / "open.parquet")
        closes = pd.read_parquet(panels / "close.parquet")
        opens.index = pd.to_datetime(opens.index, utc=True)
        closes.index = pd.to_datetime(closes.index, utc=True)
        opens = opens.reindex(columns=security_ids)
        closes = closes.reindex(columns=security_ids)
        ranks = {}
        for factor_id, relative in selected_paths.items():
            panel = pd.read_parquet(args.full_private_run / "filter" / relative)
            panel.index = pd.to_datetime(panel.index, utc=True)
            ranks[factor_id] = panel.reindex(columns=security_ids)
        return {"opens": opens, "closes": closes, "ranks": ranks}

    champion = json.loads(args.champion_lock.read_text(encoding="utf-8"))
    champion_ids = {
        str(factor["factor_id"]) for factor in champion["selected_factors"]
    }

    def evaluator(data: dict) -> dict:
        config = json.loads(args.policy.read_text(encoding="utf-8"))
        result = evaluate_fixed_pool(
            pool=pool,
            rank_panels=data["ranks"],
            opens=data["opens"],
            closes=data["closes"],
            config=config,
            holdout_start="2025-01-01",
            holdout_end=args.holdout_end,
            portfolio_runner=_portfolio_runner(data["opens"], data["closes"]),
        )
        pool_ids = set(result["fixed_factor_ids"])
        result["full_history_champion_overlap_count"] = len(pool_ids & champion_ids)
        result["full_history_champion_union_count"] = len(pool_ids | champion_ids)
        result["full_history_champion_jaccard"] = (
            len(pool_ids & champion_ids) / len(pool_ids | champion_ids)
        )
        result["full_history_champion_used_for_holdout_selection"] = False
        result["gate_or_threshold_changed"] = False
        return result

    return execute_one_time_replay(
        champion_lock=args.champion_lock,
        pool=args.pool,
        pool_manifest=args.pool_manifest,
        output=args.output,
        holdout_input_hashes=input_hashes,
        loader=loader,
        evaluator=evaluator,
    )


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--full-history-evidence", type=Path, required=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    freeze = commands.add_parser("freeze-champion")
    _common(freeze)
    freeze.add_argument("--output", type=Path, required=True)

    pre = commands.add_parser("build-pre2025")
    _common(pre)
    pre.add_argument("--champion-lock", type=Path, required=True)
    pre.add_argument("--full-private-run", type=Path, required=True)
    pre.add_argument("--private-output", type=Path, required=True)
    pre.add_argument("--pool-output", type=Path, required=True)

    replay = commands.add_parser("replay")
    _common(replay)
    replay.add_argument("--champion-lock", type=Path, required=True)
    replay.add_argument("--pool", type=Path, required=True)
    replay.add_argument("--pool-manifest", type=Path, required=True)
    replay.add_argument("--full-private-run", type=Path, required=True)
    replay.add_argument("--holdout-end", default="2026-09-14")
    replay.add_argument("--output", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "freeze-champion":
        result = _freeze_champion(args)
    elif args.command == "build-pre2025":
        result = _build_pre2025(args)
    else:
        result = _replay(args)
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
