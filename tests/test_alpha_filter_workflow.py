import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from us_equity_alpha.alpha_filter import build_proposed_pool
from us_equity_alpha.alpha_filter_workflow import (
    freeze_filtered_pool,
    run_alpha_filter_from_files,
)
from us_equity_alpha.daily_selection import validate_active_pool


def _hash_payload(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def _factor(index: int) -> dict:
    factor_id = f"F{index}"
    return {
        "local_factor_id": factor_id,
        "expression": "rank(close)",
        "expression_hash": hashlib.sha256(factor_id.encode()).hexdigest(),
        "family_id": f"family-{index}",
        "economic_description": {"mechanism_tags": [f"mechanism-{index}"]},
        "semantic_status": "FULL_INTENT",
        "field_bindings": {"close": "close"},
        "allowed_providers": ["tiingo_eod"],
        "direction_basis": "Frozen source sign.",
        "build_status": "COMPUTE_VERIFIED",
        "usage_tier": "DIAGNOSTIC_ONLY",
        "provenance_kind": "LOCAL_RECONSTRUCTION",
        "source_alpha_ids": [f"brain-{index}"],
        "settings": {"delay": 1, "decay": 0, "neutralization": "NONE"},
    }


def _fixture_paths(tmp_path: Path) -> dict:
    library_file = tmp_path / "library.json"
    v4_manifest = tmp_path / "v4-manifest.json"
    catalog_file = tmp_path / "catalog.csv"
    universe_file = tmp_path / "universe.csv"
    market_data = tmp_path / "market"
    expansion_registry = tmp_path / "expansions"
    policy_file = tmp_path / "policy.json"
    output = tmp_path / "filter-run"
    market_data.mkdir()
    expansion_registry.mkdir()
    factors = [_factor(index) for index in range(8)]
    library_file.write_text(json.dumps({"factors": factors}), encoding="utf-8")
    v4_manifest.write_text(
        json.dumps(
            {
                "project_factor_library.json": hashlib.sha256(
                    library_file.read_bytes()
                ).hexdigest()
            }
        ),
        encoding="utf-8",
    )
    catalog = pd.DataFrame(
        [
            {
                "factor_id": factor["local_factor_id"],
                "factor_version": 4,
                "source_kind": "V4_BASE",
                "family_id": factor["family_id"],
                "mechanism_tag": factor["economic_description"]["mechanism_tags"][0],
                "mechanism_status": "CLASSIFIED",
                "semantic_status": "FULL_INTENT",
                "required_fields": "close",
                "allowed_providers": "tiingo_eod",
                "default_provider_eligible": True,
                "direction": 1,
                "build_status": "COMPUTE_VERIFIED",
                "warmup_sessions": 1,
                "lineage_hash": "a" * 64,
                "content_hash": factor["expression_hash"],
                "filter_eligible": True,
                "catalog_exclusion_reasons": "",
            }
            for factor in factors
        ]
    )
    catalog.to_csv(catalog_file, index=False)
    sessions = pd.bdate_range("2022-01-03", periods=700, tz="UTC")
    securities = [f"S{index:02d}" for index in range(20)]
    time = np.arange(len(sessions), dtype=float)[:, None]
    rates = np.arange(1, len(securities) + 1, dtype=float)[None, :] * 0.0001
    values = 100 * np.exp(time * rates)
    open_panel = pd.DataFrame(values, index=sessions, columns=securities)
    open_panel["SPY"] = 100 * np.exp(time[:, 0] * 0.0005)
    open_panel.to_parquet(market_data / "open.parquet")
    open_panel.to_parquet(market_data / "close.parquet")
    pd.DataFrame({"security_id": securities}).to_csv(universe_file, index=False)
    policy = {
        "schema_version": 1,
        "policy_id": "fixture",
        "primary_frequency": "WEEKLY_REBALANCE_SIGNAL",
        "forward_label": "T_CLOSE_T1_OPEN_T6_OPEN",
        "benchmark_security_id": "SPY",
        "quantiles": 5,
        "full_year_min_signal_sessions": 26,
        "partial_final_year_min_signal_sessions": 13,
        "min_median_coverage": 0.95,
        "min_valid_signal_sessions": 100,
        "require_positive_mean_rank_ic": True,
        "require_positive_q5_q1": True,
        "min_positive_year_ratio": 0.60,
        "max_weekly_top10_turnover": 0.80,
        "score_corr_conflict": 0.80,
        "top10_overlap_conflict": 0.70,
        "active_pool_min": 8,
        "active_pool_max": 15,
        "selection_evidence": "DEVELOPMENT_EXPOSED",
        "historical_pit_verified": False,
    }
    policy_file.write_text(json.dumps(policy), encoding="utf-8")
    return {
        "unified_catalog": catalog_file,
        "library": library_file,
        "v4_manifest": v4_manifest,
        "expansion_registry": expansion_registry,
        "universe": universe_file,
        "market_data": market_data,
        "provider": "tiingo_eod",
        "policy": policy_file,
        "output": output,
    }


def test_filter_run_accounts_for_every_catalog_row(tmp_path):
    result = run_alpha_filter_from_files(**_fixture_paths(tmp_path))
    diagnostics = pd.read_csv(result["diagnostics"])
    assert len(diagnostics) == 8
    assert diagnostics[["factor_id", "factor_version"]].duplicated().sum() == 0
    assert result["live_orders_submitted"] == 0
    assert Path(result["manifest"]).is_file()
    comparison = pd.read_csv(result["composite_comparison"])
    assert comparison.composite_id.tolist() == [
        "V5_1_PROPOSED", "BASELINE_V5_8_FACTOR"
    ]
    assert set(comparison.comparison_status) == {"NOT_RUN"}


def test_filter_run_refuses_nonempty_output_directory(tmp_path):
    paths = _fixture_paths(tmp_path)
    paths["output"].mkdir()
    (paths["output"] / "existing.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError, match="OUTPUT_DIRECTORY_NOT_EMPTY"):
        run_alpha_filter_from_files(**paths)


def _reviewed_eight_factor_proposal(tmp_path: Path) -> tuple[Path, Path]:
    rows = []
    for index in range(8):
        rows.append(
            {
                "factor_id": f"F{index}",
                "factor_version": 4,
                "filter_status": "SELECTED",
                "filter_reason": "SELECTED_NONREDUNDANT",
            }
        )
    metadata = {
        "filter_run_id": "run-1",
        "selection_config_hash": "a" * 64,
        "v4_manifest_hash": "b" * 64,
        "expansion_registry_snapshot_hash": "c" * 64,
        "alpha_catalog_hash": "d" * 64,
        "provider": "tiingo_eod",
        "universe_hash": "e" * 64,
        "sample_start": "2020-01-01",
        "sample_end": "2025-12-31",
    }
    proposal = build_proposed_pool(
        pd.DataFrame(rows), metadata, {"active_pool_min": 8, "active_pool_max": 15}
    )
    proposal_path = tmp_path / "proposal.json"
    proposal_path.write_text(json.dumps(proposal), encoding="utf-8")
    decisions = pd.DataFrame(
        [
            {
                "factor_id": f"F{index}",
                "factor_version": 4,
                "decision": "APPROVE",
                "comment": "reviewed",
            }
            for index in range(8)
        ]
    )
    decisions_path = tmp_path / "decisions.csv"
    decisions.to_csv(decisions_path, index=False)
    return proposal_path, decisions_path


def test_frozen_filtered_pool_is_accepted_by_daily_selection(tmp_path):
    proposal, decisions = _reviewed_eight_factor_proposal(tmp_path)
    pool = freeze_filtered_pool(proposal, decisions, tmp_path / "frozen")
    assert len(validate_active_pool(pool, pool["provider"])) == 8
    assert pool["selection_evidence"] == "DEVELOPMENT_EXPOSED"
    assert (tmp_path / "frozen" / "active_pool.json").is_file()


def test_freeze_rejects_unproposed_factor(tmp_path):
    proposal, decisions = _reviewed_eight_factor_proposal(tmp_path)
    frame = pd.read_csv(decisions)
    frame.loc[0, "factor_id"] = "UNPROPOSED"
    frame.to_csv(decisions, index=False)
    with pytest.raises(ValueError, match="REVIEW_FACTOR_NOT_PROPOSED"):
        freeze_filtered_pool(proposal, decisions, tmp_path / "frozen")
