from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pandas as pd
import pytest

from us_equity_alpha.historical_holdout_replay import (
    evaluate_fixed_pool,
    execute_one_time_replay,
    freeze_full_history_champion,
    freeze_pre2025_pool,
    materialize_market_window,
    verify_full_history_champion,
    verify_holdout_replay,
    verify_pre2025_pool,
)


def _write_json(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    return path


def _canonical_hash(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _champion_inputs(tmp_path: Path) -> dict[str, Path]:
    config = _write_json(
        tmp_path / "filter_config.json",
        {
            "active_pool_min": 8,
            "active_pool_max": 15,
            "primary_frequency": "WEEKLY_REBALANCE_SIGNAL",
            "forward_label": "T_CLOSE_T1_OPEN_T6_OPEN",
        },
    )
    selected = [
        {"factor_id": f"F{index}", "factor_version": 4} for index in range(14)
    ]
    proposal_value = {
            "artifact_type": "V5_1_PROPOSED_ACTIVE_POOL",
            "status": "READY_FOR_HUMAN_REVIEW",
            "factor_count": 14,
            "selected_factors": selected,
            "provider": "yfinance_eod",
            "sample_start": "2019-01-04",
            "sample_end": "2026-09-11",
            "selection_config_hash": hashlib.sha256(config.read_bytes()).hexdigest(),
            "filter_run_id": "full-history-run",
        }
    proposal_value["content_hash"] = _canonical_hash(proposal_value)
    proposal = _write_json(tmp_path / "proposal.json", proposal_value)
    decisions = pd.DataFrame(
        [
            {
                "factor_id": row["factor_id"],
                "factor_version": 4,
                "direction": 1,
                "family_id": f"family-{index}",
                "mechanism_tag": f"mechanism-{index}",
                "filter_status": "SELECTED",
            }
            for index, row in enumerate(selected)
        ]
    )
    decisions_path = tmp_path / "decisions.csv"
    decisions.to_csv(decisions_path, index=False)
    comparison = pd.DataFrame(
        [
            {
                "composite_id": "V5_1_PROPOSED",
                "mean_rank_ic_5d": 0.02,
                "factor_count": 14,
                "selection_evidence": "DEVELOPMENT_EXPOSED",
            }
        ]
    )
    comparison_path = tmp_path / "comparison.csv"
    comparison.to_csv(comparison_path, index=False)
    return {
        "config": config,
        "proposal": proposal,
        "decisions": decisions_path,
        "comparison": comparison_path,
    }


def test_champion_lock_detects_later_artifact_mutation(tmp_path):
    inputs = _champion_inputs(tmp_path)
    lock = freeze_full_history_champion(output=tmp_path / "champion", **inputs)
    assert lock["status"] == "IMMUTABLY_FROZEN"
    assert lock["factor_count"] == 14
    assert verify_full_history_champion(tmp_path / "champion/champion.lock.json", inputs)

    inputs["config"].write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="CHAMPION_ARTIFACT_HASH_MISMATCH:config"):
        verify_full_history_champion(tmp_path / "champion/champion.lock.json", inputs)


def test_pre2025_pool_binds_same_config_and_training_cutoff(tmp_path):
    inputs = _champion_inputs(tmp_path)
    lock = freeze_full_history_champion(output=tmp_path / "champion", **inputs)
    proposal = json.loads(inputs["proposal"].read_text(encoding="utf-8"))
    proposal.update(
        {
            "filter_run_id": "pre2025-run",
            "sample_end": "2024-12-30",
            "selection_config_hash": lock["filter_config_sha256"],
        }
    )
    proposal.pop("content_hash", None)
    proposal["content_hash"] = _canonical_hash(proposal)
    pre_proposal = _write_json(tmp_path / "pre2025_proposal.json", proposal)
    pool = freeze_pre2025_pool(
        champion_lock=tmp_path / "champion/champion.lock.json",
        proposal=pre_proposal,
        decisions=inputs["decisions"],
        output=tmp_path / "pre2025",
    )
    assert pool["pool_id"] == "PRE2025_POOL"
    assert pool["status"] == "FROZEN_BEFORE_HOLDOUT_ACCESS"
    assert pool["training_data_end"] == "2024-12-31"
    assert pool["gate_reapplication_after_holdout"] is False
    assert verify_pre2025_pool(
        tmp_path / "pre2025/pre2025_pool.json",
        tmp_path / "pre2025/manifest.json",
    )

    proposal["sample_end"] = "2025-01-03"
    proposal.pop("content_hash", None)
    proposal["content_hash"] = _canonical_hash(proposal)
    bad = _write_json(tmp_path / "bad_proposal.json", proposal)
    with pytest.raises(ValueError, match="PRE2025_SAMPLE_CROSSES_CUTOFF"):
        freeze_pre2025_pool(
            champion_lock=tmp_path / "champion/champion.lock.json",
            proposal=bad,
            decisions=inputs["decisions"],
            output=tmp_path / "bad_pool",
        )


def test_holdout_is_committed_before_loader_and_can_run_only_once(tmp_path):
    inputs = _champion_inputs(tmp_path)
    lock = freeze_full_history_champion(output=tmp_path / "champion", **inputs)
    proposal = json.loads(inputs["proposal"].read_text(encoding="utf-8"))
    proposal.update(
        {
            "filter_run_id": "pre2025-run",
            "sample_end": "2024-12-30",
            "selection_config_hash": lock["filter_config_sha256"],
        }
    )
    proposal.pop("content_hash", None)
    proposal["content_hash"] = _canonical_hash(proposal)
    pre_proposal = _write_json(tmp_path / "pre2025_proposal.json", proposal)
    freeze_pre2025_pool(
        champion_lock=tmp_path / "champion/champion.lock.json",
        proposal=pre_proposal,
        decisions=inputs["decisions"],
        output=tmp_path / "pre2025",
    )
    replay = tmp_path / "replay"
    calls: list[str] = []

    def loader():
        assert (replay / "holdout_access_commit.json").is_file()
        calls.append("loaded")
        return {"labels": "2025-2026"}

    def evaluator(data):
        assert data == {"labels": "2025-2026"}
        return {
            "status": "HISTORICAL_HOLDOUT_REPLAY_COMPLETE",
            "pool_id": "PRE2025_POOL",
            "gate_reapplied": False,
            "pool_modified_after_holdout": False,
        }

    result = execute_one_time_replay(
        champion_lock=tmp_path / "champion/champion.lock.json",
        pool=tmp_path / "pre2025/pre2025_pool.json",
        pool_manifest=tmp_path / "pre2025/manifest.json",
        output=replay,
        holdout_input_hashes={"open.parquet": "abc"},
        loader=loader,
        evaluator=evaluator,
    )
    assert result["gate_reapplied"] is False
    assert calls == ["loaded"]
    assert verify_holdout_replay(replay)

    with pytest.raises(ValueError, match="HOLDOUT_ALREADY_OPENED"):
        execute_one_time_replay(
            champion_lock=tmp_path / "champion/champion.lock.json",
            pool=tmp_path / "pre2025/pre2025_pool.json",
            pool_manifest=tmp_path / "pre2025/manifest.json",
            output=replay,
            holdout_input_hashes={"open.parquet": "abc"},
            loader=loader,
            evaluator=evaluator,
        )
    assert calls == ["loaded"]


def test_market_window_materialization_excludes_post_cutoff_rows(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    index = pd.to_datetime(["2024-12-30", "2024-12-31", "2025-01-02"])
    pd.DataFrame({"A": [1.0, 2.0, 999.0]}, index=index.rename("Date")).to_parquet(
        source / "open.parquet"
    )
    pd.DataFrame(
        {"A": [1.0, 2.0, 999.0]}, index=index.tz_localize("UTC").rename("Date")
    ).to_parquet(source / "close.parquet")
    manifest = materialize_market_window(
        source=source,
        output=tmp_path / "window",
        start="2019-01-02",
        end="2024-12-31",
    )
    sliced = pd.read_parquet(tmp_path / "window/open.parquet")
    sliced_utc = pd.read_parquet(tmp_path / "window/close.parquet")
    assert sliced.index.tolist() == list(index[:2])
    assert sliced_utc.index.tolist() == list(index[:2].tz_localize("UTC"))
    assert 999.0 not in sliced.to_numpy()
    assert manifest["requested_start"] == "2019-01-02"
    assert manifest["requested_end"] == "2024-12-31"


def test_fixed_pool_evaluation_reports_metrics_without_reapplying_gates():
    sessions = pd.date_range("2025-01-02", periods=40, freq="B", tz="UTC")
    opens = pd.DataFrame(
        {
            "A": range(100, 140),
            "B": range(140, 100, -1),
            "C": [100 + (index % 3) for index in range(40)],
        },
        index=sessions,
        dtype=float,
    )
    signal_index = pd.DatetimeIndex(
        [session for session in sessions if session.weekday() == 4]
    )
    ranks = pd.DataFrame(
        {"A": 1.0, "B": 0.1, "C": 0.5}, index=signal_index
    )
    pool = {
        "pool_id": "PRE2025_POOL",
        "selected_factors": [
            {"factor_id": "F1", "factor_version": 4, "direction": 1}
        ],
    }
    result = evaluate_fixed_pool(
        pool=pool,
        rank_panels={"F1": ranks},
        opens=opens,
        closes=opens,
        config={
            "benchmark_security_id": "SPY",
            "quantiles": 5,
            "full_year_min_signal_sessions": 1,
            "partial_final_year_min_signal_sessions": 1,
        },
        holdout_start="2025-01-02",
        holdout_end="2025-02-28",
        portfolio_runner=lambda scores: {
            "total_return": 0.12,
            "sharpe": 1.1,
            "max_drawdown": -0.04,
            "modeled_fees": 10.0,
        },
    )
    assert result["pool_id"] == "PRE2025_POOL"
    assert result["gate_reapplied"] is False
    assert result["pool_modified_after_holdout"] is False
    assert result["fixed_pool_factor_count"] == 1
    assert result["portfolio_metrics"]["total_return"] == 0.12
