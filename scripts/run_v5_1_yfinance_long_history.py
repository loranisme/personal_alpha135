"""Run the frozen V5.1 Alpha Filter on the saved 2019-2026 Yahoo sample."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from us_equity_alpha.alpha_catalog import write_unified_catalog
from us_equity_alpha.alpha_filter_diagnostics import (
    forward_open_returns,
    weekly_signal_sessions,
)
from us_equity_alpha.alpha_filter_workflow import (
    compare_composites,
    flat_equal_weight_composite,
    run_alpha_filter_from_files,
)


PROVIDER = "yfinance_eod"
START = pd.Timestamp("2019-01-02", tz="UTC")
END = pd.Timestamp("2026-09-14", tz="UTC")
BASELINE_ID = "BASELINE_V5_8_FACTOR"
NEW_ID = "V5_1_PROPOSED"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_hash(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
            default=str,
        ).encode()
    ).hexdigest()


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False, default=str)
        + "\n",
        encoding="utf-8",
    )


def _load_raw_field(source: Path, field: str) -> pd.DataFrame:
    raw = source / "raw_batches"
    parts = [
        pd.read_parquet(path)
        for path in sorted(raw.glob(f"batch_[0-9][0-9][0-9]_{field}.parquet"))
    ]
    if not parts:
        raise ValueError(f"YFINANCE_BATCH_FIELD_MISSING:{field}")
    panel = pd.concat(parts, axis=1)
    panel = panel.loc[:, ~panel.columns.duplicated()].sort_index()
    if field == "close":
        for path in sorted(raw.glob("repair_round_*_close.parquet")):
            panel = pd.read_parquet(path).combine_first(panel).sort_index()
    for path in sorted(raw.glob(f"repair_ohlcv_round_*_{field}.parquet")):
        panel = pd.read_parquet(path).combine_first(panel).sort_index()
    panel.index = pd.to_datetime(panel.index, utc=True)
    return panel.loc[(panel.index >= START) & (panel.index <= END)]


def _prepare_inputs(source: Path, output: Path) -> tuple[Path, Path, dict]:
    universe_source = pd.read_csv(source / "universe_500_current_snapshot.csv")
    if len(universe_source) != 500:
        raise ValueError("YFINANCE_UNIVERSE_NOT_500")
    if universe_source.security_id.duplicated().any() or universe_source.ticker.duplicated().any():
        raise ValueError("YFINANCE_UNIVERSE_DUPLICATE")
    tickers = universe_source.ticker.astype(str).tolist()
    security_ids = universe_source.security_id.astype(str).tolist()
    rename = dict(zip(tickers, security_ids, strict=True))
    raw = {field: _load_raw_field(source, field) for field in ("open", "high", "low", "close", "volume")}
    common_sessions = raw["open"].index
    for panel in raw.values():
        common_sessions = common_sessions.intersection(panel.index)
    common_sessions = common_sessions.sort_values()
    if common_sessions.empty:
        raise ValueError("YFINANCE_COMMON_SESSIONS_EMPTY")
    market = output / "market_panels"
    market.mkdir(parents=True)
    panels: dict[str, pd.DataFrame] = {}
    for field, panel in raw.items():
        selected = panel.reindex(index=common_sessions, columns=tickers + ["SPY"])
        selected = selected.rename(columns=rename)
        selected.to_parquet(market / f"{field}.parquet")
        panels[field] = selected
    panels["vwap"] = (panels["high"] + panels["low"] + panels["close"]) / 3
    panels["vwap"].to_parquet(market / "vwap.parquet")
    spy_return = panels["close"]["SPY"].pct_change(fill_method=None)
    benchmark = pd.DataFrame(
        np.repeat(spy_return.to_numpy()[:, None], len(security_ids), axis=1),
        index=common_sessions,
        columns=security_ids,
    )
    benchmark.to_parquet(market / "benchmark_returns.parquet")
    universe = universe_source.copy()
    universe.to_csv(output / "universe_500.csv", index=False)
    cross_coverage = panels["open"].reindex(columns=security_ids).notna().mean(axis=1)
    evidence = {
        "run_purpose": "LONG_HISTORY_ENGINEERING_FILTER_DIAGNOSTIC",
        "source": "Yahoo Finance via saved yfinance batch files",
        "provider_adapter": PROVIDER,
        "source_price_adjustment": "auto_adjust=True",
        "vwap_binding": "DERIVED_TYPICAL_PRICE_(HIGH+LOW+CLOSE)/3",
        "requested_range": "2019-01-02 to 2026-09-14",
        "actual_start": str(common_sessions.min().date()),
        "actual_end": str(common_sessions.max().date()),
        "sessions": int(len(common_sessions)),
        "universe_count": int(len(universe)),
        "median_open_cross_section_coverage": float(cross_coverage.median()),
        "minimum_open_cross_section_coverage": float(cross_coverage.min()),
        "membership": "CURRENT_SNAPSHOT_SURVIVORSHIP_BIASED",
        "selection_evidence": "DEVELOPMENT_EXPOSED",
        "historical_pit_verified": False,
        "independent_oos_claim_allowed": False,
        "live_orders_submitted": 0,
    }
    _write_json(output / "source_evidence.json", evidence)
    return market, output / "universe_500.csv", evidence


def _adapt_library(source_library: Path, output: Path) -> tuple[Path, Path]:
    library = json.loads(source_library.read_text(encoding="utf-8"))
    for factor in library.get("factors", []):
        providers = list(factor.get("allowed_providers") or [])
        if PROVIDER not in providers:
            providers.append(PROVIDER)
        factor["allowed_providers"] = sorted(set(providers))
    library["diagnostic_provider_adapter"] = {
        "provider": PROVIDER,
        "formula_or_setting_changes": 0,
        "allowed_provider_binding_added": True,
        "vwap_binding": "DERIVED_TYPICAL_PRICE_(HIGH+LOW+CLOSE)/3",
        "evidence": "DEVELOPMENT_EXPOSED",
    }
    adapted = output / "adapted_v4_library_yfinance.json"
    _write_json(adapted, library)
    manifest = output / "adapted_v4_manifest_yfinance.json"
    _write_json(
        manifest,
        {
            "artifact_type": "V4_DIAGNOSTIC_PROVIDER_ADAPTER",
            "project_factor_library.json": _digest(adapted),
            "source_v4_library_sha256": _digest(source_library),
            "formula_or_setting_changes": 0,
            "provider": PROVIDER,
            "selection_evidence": "DEVELOPMENT_EXPOSED",
        },
    )
    return adapted, manifest


def _gate_outputs(filter_dir: Path) -> dict:
    diagnostics = pd.read_csv(filter_dir / "alpha_diagnostics.csv")
    decisions = pd.read_csv(filter_dir / "filter_decisions.csv")
    conflicts = pd.read_csv(filter_dir / "redundancy_conflicts.csv")
    status = diagnostics.filter_status.fillna("")
    stages = [
        ("Catalog versions", len(diagnostics), ""),
        ("Catalog eligible", int(status.ne("CATALOG_INELIGIBLE").sum()), "CATALOG_INELIGIBLE"),
        (
            "Data gate passed",
            int((~status.isin(["CATALOG_INELIGIBLE", "REJECT_DATA"])).sum()),
            "REJECT_DATA",
        ),
        (
            "Signal gate passed",
            int(
                (~status.isin(["CATALOG_INELIGIBLE", "REJECT_DATA", "REJECT_SIGNAL"])).sum()
            ),
            "REJECT_SIGNAL",
        ),
        (
            "Stability gate passed",
            int(
                (~status.isin(
                    [
                        "CATALOG_INELIGIBLE",
                        "REJECT_DATA",
                        "REJECT_SIGNAL",
                        "WATCHLIST_UNSTABLE",
                    ]
                )).sum()
            ),
            "WATCHLIST_UNSTABLE",
        ),
        ("Usability gate passed", int(status.eq("PASS_GATES").sum()), "WATCHLIST_HIGH_TURNOVER"),
        (
            "Final selected after redundancy and cap",
            int(decisions.filter_status.eq("SELECTED").sum()),
            "REJECT_REDUNDANT_OR_POOL_CAP",
        ),
    ]
    funnel_rows = []
    previous = None
    for order, (stage, remaining, reason) in enumerate(stages, 1):
        funnel_rows.append(
            {
                "stage_order": order,
                "stage": stage,
                "remaining_factor_versions": remaining,
                "removed_at_stage": None if previous is None else previous - remaining,
                "removal_status": reason,
            }
        )
        previous = remaining
    funnel = pd.DataFrame(funnel_rows)
    funnel.to_csv(filter_dir / "gate_funnel.csv", index=False)
    diagnostics.loc[diagnostics.filter_status.eq("PASS_GATES")].to_csv(
        filter_dir / "gate_pass_factors.csv", index=False
    )
    decisions.loc[decisions.filter_status.eq("SELECTED")].to_csv(
        filter_dir / "final_selected_factors.csv", index=False
    )
    redundant = decisions.loc[decisions.filter_status.eq("REJECT_REDUNDANT")].copy()
    if not redundant.empty:
        redundant["retained_factor_id"] = redundant.filter_reason.str.replace(
            "REJECT_REDUNDANT_WITH_", "", regex=False
        )
        conflict_lookup = {}
        for row in conflicts.itertuples(index=False):
            left = str(row.left_factor_id)
            right = str(row.right_factor_id)
            conflict_lookup[frozenset((left, right))] = {
                "conflict_reason": row.conflict_reason,
                "median_abs_score_correlation": row.median_abs_score_correlation,
                "mean_top10_jaccard": row.mean_top10_jaccard,
                "same_mechanism": row.same_mechanism,
            }
        details = []
        for row in redundant.to_dict("records"):
            details.append(
                row
                | conflict_lookup.get(
                    frozenset((str(row["factor_id"]), str(row["retained_factor_id"]))),
                    {},
                )
            )
        redundant = pd.DataFrame(details)
    redundant.to_csv(filter_dir / "redundancy_deletions.csv", index=False)
    return {
        "catalog_count": int(len(diagnostics)),
        "gate_pass_count": int(diagnostics.filter_status.eq("PASS_GATES").sum()),
        "selected_count": int(decisions.filter_status.eq("SELECTED").sum()),
        "redundancy_reject_count": int(decisions.filter_status.eq("REJECT_REDUNDANT").sum()),
        "pool_cap_reject_count": int(decisions.filter_status.eq("REJECT_POOL_CAP").sum()),
    }


def _load_rank(filter_dir: Path, decisions: pd.DataFrame, factor_id: str) -> pd.DataFrame:
    row = decisions.loc[decisions.factor_id.eq(factor_id)]
    if len(row) != 1 or pd.isna(row.iloc[0].get("rank_cache_file")):
        raise ValueError(f"RANK_CACHE_MISSING:{factor_id}")
    return pd.read_parquet(filter_dir / str(row.iloc[0].rank_cache_file))


def _portfolio_runner(opens: pd.DataFrame, closes: pd.DataFrame):
    import vectorbt as vbt

    def run(scores: pd.DataFrame) -> dict:
        sessions = opens.index
        targets = pd.DataFrame(np.nan, index=sessions, columns=opens.columns, dtype=float)
        positions = {session: index for index, session in enumerate(sessions)}
        for signal, row in scores.iterrows():
            location = positions.get(signal)
            if location is None or location + 1 >= len(sessions):
                continue
            execution = sessions[location + 1]
            valid = row.dropna().index.intersection(opens.columns[opens.loc[execution].gt(0)])
            ordered = row.reindex(valid).sort_values(ascending=False, kind="stable")
            count = max(1, int(math.ceil(len(ordered) * 0.10)))
            selected = ordered.head(count).index
            targets.loc[execution] = 0.0
            targets.loc[execution, selected] = 0.95 / count
        # Scores remain missing before a security has observable history, so the
        # engine will not target it.  A constant pre-listing sentinel keeps the
        # vectorized simulator finite without backfilling future market prices.
        clean_close = closes.where(closes.gt(0)).ffill().fillna(1.0)
        clean_open = opens.where(opens.gt(0)).ffill().fillna(1.0)
        portfolio = vbt.Portfolio.from_orders(
            close=clean_close,
            size=targets,
            size_type="targetpercent",
            price=clean_open,
            init_cash=100_000.0,
            fees=0.001,
            slippage=0.0,
            cash_sharing=True,
            group_by=True,
            call_seq="auto",
            direction="longonly",
            freq="1D",
        )
        value = portfolio.value()
        returns = value.pct_change(fill_method=None).fillna(0.0)
        volatility = float(returns.std() * math.sqrt(252))
        orders = portfolio.orders.records_readable
        return {
            "total_return": float(value.iloc[-1] / value.iloc[0] - 1),
            "sharpe": float(returns.mean() * 252 / volatility) if volatility > 0 else None,
            "max_drawdown": float((value / value.cummax() - 1).min()),
            "modeled_fees": float(orders["Fees"].sum()),
        }

    return run


def _compare_composites(filter_dir: Path, market: Path, universe_path: Path, policy_path: Path, baseline_path: Path) -> str:
    decisions = pd.read_csv(filter_dir / "filter_decisions.csv")
    selected = decisions.loc[decisions.filter_status.eq("SELECTED"), "factor_id"].astype(str).tolist()
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))["factor_ids"]
    if not 8 <= len(selected) <= 15:
        return "BLOCKED_ACTIVE_POOL_SIZE"
    new_scores = flat_equal_weight_composite(
        {factor_id: _load_rank(filter_dir, decisions, factor_id) for factor_id in selected}
    )
    baseline_scores = flat_equal_weight_composite(
        {factor_id: _load_rank(filter_dir, decisions, factor_id) for factor_id in baseline}
    )
    ids = pd.read_csv(universe_path).security_id.astype(str).tolist()
    opens = pd.read_parquet(market / "open.parquet").reindex(columns=ids)
    closes = pd.read_parquet(market / "close.parquet").reindex(columns=ids)
    signals = weekly_signal_sessions(opens.index)
    labels = forward_open_returns(opens, signals, horizon=5)
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    signal_hash = _canonical_hash([str(item) for item in signals])
    universe_hash = _digest(universe_path)
    metadata = {
        "filter_decisions_frozen": True,
        "new_provider": PROVIDER,
        "baseline_provider": PROVIDER,
        "new_universe_hash": universe_hash,
        "baseline_universe_hash": universe_hash,
        "new_signal_sessions_hash": signal_hash,
        "baseline_signal_sessions_hash": signal_hash,
        "new_forward_label": "T_CLOSE_T1_OPEN_T6_OPEN",
        "baseline_forward_label": "T_CLOSE_T1_OPEN_T6_OPEN",
        "diagnostic_config": policy,
    }
    comparison = compare_composites(
        new_scores,
        baseline_scores,
        labels,
        _portfolio_runner(opens, closes),
        metadata,
    )
    comparison["comparison_status"] = "COMPLETE"
    comparison["missing_reason"] = ""
    comparison["factor_count"] = [len(selected), len(baseline)]
    comparison["provider"] = PROVIDER
    comparison["forward_label"] = "T_CLOSE_T1_OPEN_T6_OPEN"
    comparison.to_csv(filter_dir / "composite_comparison.csv", index=False)
    new_scores.to_parquet(filter_dir / "new_composite_scores.parquet")
    baseline_scores.to_parquet(filter_dir / "baseline_composite_scores.parquet")
    return "COMPLETE"


def _refresh_manifest(filter_dir: Path, source_evidence: dict, summary: dict, comparison_status: str) -> None:
    manifest_path = filter_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name in (
        "gate_funnel.csv",
        "gate_pass_factors.csv",
        "final_selected_factors.csv",
        "redundancy_deletions.csv",
        "composite_comparison.csv",
        "new_composite_scores.parquet",
        "baseline_composite_scores.parquet",
    ):
        path = filter_dir / name
        if path.exists():
            manifest["files"][name] = _digest(path)
    manifest["run_purpose"] = source_evidence["run_purpose"]
    manifest["data_source"] = source_evidence["source"]
    manifest["provider_adapter"] = PROVIDER
    manifest["composite_comparison_status"] = comparison_status
    manifest["gate_summary"] = summary
    manifest["alpha_effectiveness_claim_allowed"] = False
    _write_json(manifest_path, manifest)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--base-catalog", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        raise FileExistsError("OUTPUT_DIRECTORY_NOT_EMPTY")
    args.output.mkdir(parents=True, exist_ok=True)
    market, universe, source_evidence = _prepare_inputs(args.source, args.output / "inputs")
    adapted_library, adapted_manifest = _adapt_library(args.library, args.output / "inputs")
    expansion = args.output / "inputs/empty_expansion_registry"
    expansion.mkdir()
    catalog = args.output / "catalog"
    write_unified_catalog(
        adapted_library,
        args.base_catalog,
        expansion,
        PROVIDER,
        catalog,
    )
    filter_dir = args.output / "filter"
    result = run_alpha_filter_from_files(
        unified_catalog=catalog / "unified_alpha_catalog.parquet",
        library=adapted_library,
        v4_manifest=adapted_manifest,
        expansion_registry=expansion,
        universe=universe,
        market_data=market,
        provider=PROVIDER,
        policy=args.policy,
        output=filter_dir,
    )
    gate_summary = _gate_outputs(filter_dir)
    comparison_status = _compare_composites(
        filter_dir, market, universe, args.policy, args.baseline
    )
    _refresh_manifest(filter_dir, source_evidence, gate_summary, comparison_status)
    final = {
        "status": result["status"],
        "filter_run_id": result["filter_run_id"],
        "gate_summary": gate_summary,
        "composite_comparison_status": comparison_status,
        "selection_evidence": "DEVELOPMENT_EXPOSED",
        "historical_pit_verified": False,
        "independent_oos_claim_allowed": False,
        "live_orders_submitted": 0,
    }
    _write_json(args.output / "run_summary.json", final)
    print(json.dumps(final, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
