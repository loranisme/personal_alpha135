"""Run the approved V4 three-alpha engineering acceptance sample.

The run is deliberately development-exposed and can never activate a release or
submit an order.  It consumes only immutable local snapshots with verified hashes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from pathlib import Path

import exchange_calendars as xcals
import numpy as np
import pandas as pd

from us_equity_alpha.evidence import EvidenceLedger
from us_equity_alpha.event_backtest import run_event_backtest
from us_equity_alpha.factor_library import compute_registry_factors
from us_equity_alpha.proxy_pipeline import _verify_manifest
from us_equity_alpha.reporting import canonical_hash
from us_equity_alpha.sample_workflow import (
    V4_SAMPLE_FACTOR_IDS,
    alphalens_detail_tables,
    cash_funded_target_shares,
    combine_factor_panels,
    freeze_sample_contract,
    vectorbt_order_records,
    weekly_rebalance_dates,
    write_latest_selection_csvs,
)
from us_equity_alpha.universe import session_bounds
from us_equity_alpha.validation import build_trade_labels


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LIBRARY_ROOT = PROJECT_ROOT / "private/project_factor_library/reconstruction-v4"
DATA_ROOT = PROJECT_ROOT / "private/runs/expanded-500-2020-2025-v1"


def _json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False, default=str)
        + "\n",
        encoding="utf-8",
    )


def _records(frame: pd.DataFrame) -> list[dict]:
    return frame.astype(object).where(pd.notna(frame), None).to_dict("records")


def _verify_file(path: Path, sidecar: Path) -> str:
    observed = hashlib.sha256(path.read_bytes()).hexdigest()
    expected = sidecar.read_text(encoding="utf-8").strip()
    if observed != expected:
        raise ValueError(f"RAW_HASH_MISMATCH:{path.stem}")
    return observed


def _load_inputs(symbols: list[str], sessions: pd.DatetimeIndex):
    frames: dict[str, pd.DataFrame] = {}
    hashes = []
    coverage = []
    for symbol in symbols:
        path = DATA_ROOT / "raw" / f"{symbol}.json"
        digest = _verify_file(path, path.with_suffix(".sha256"))
        frame = pd.DataFrame(json.loads(path.read_text(encoding="utf-8")))
        frame.index = pd.to_datetime(frame.pop("date"), utc=True).dt.tz_localize(None).dt.normalize()
        if frame.index.has_duplicates:
            raise ValueError(f"DUPLICATE_RAW_SESSION:{symbol}")
        frame = frame.reindex(sessions)
        frames[symbol] = frame
        coverage.append(
            {
                "security_id": symbol,
                "first_valid": frame["open"].first_valid_index(),
                "last_valid": frame["open"].last_valid_index(),
                "valid_sessions": int(frame["open"].notna().sum()),
                "missing_sessions": int(frame["open"].isna().sum()),
            }
        )
        hashes.append({"kind": "SECURITY_BAR", "security_id": symbol, "path": str(path.relative_to(PROJECT_ROOT)), "sha256": digest})
    spy_path = DATA_ROOT / "benchmark/SPY.json"
    spy_hash = _verify_file(spy_path, spy_path.with_suffix(".sha256"))
    spy = pd.DataFrame(json.loads(spy_path.read_text(encoding="utf-8")))
    spy.index = pd.to_datetime(spy.pop("date"), utc=True).dt.tz_localize(None).dt.normalize()
    spy = spy.reindex(sessions)
    frames["SPY"] = spy
    hashes.append({"kind": "BENCHMARK_BAR", "security_id": "SPY", "path": str(spy_path.relative_to(PROJECT_ROOT)), "sha256": spy_hash})
    fields = {}
    for field in ("open", "high", "low", "close", "volume"):
        fields[field] = pd.DataFrame({symbol: frame[field] for symbol, frame in frames.items()}, index=sessions)
    return frames, fields, pd.DataFrame(coverage), pd.DataFrame(hashes)


def _build_labels(scores: pd.DataFrame, adjusted_open: pd.DataFrame, horizon: int) -> pd.DataFrame:
    signal_rows = []
    for session, values in scores.iterrows():
        cutoff = session_bounds(session)[1]
        for security_id, score in values.items():
            if pd.notna(score) and np.isfinite(score):
                signal_rows.append({"signal_cutoff": cutoff, "security_id": security_id, "score": float(score)})
    trade_prices = adjusted_open.copy()
    trade_prices.index = pd.DatetimeIndex([session_bounds(session)[0] for session in trade_prices.index])
    return build_trade_labels(pd.DataFrame(signal_rows), trade_prices, horizon)


def _rank_frame(values: pd.Series, factor_panels: dict[str, pd.DataFrame], session: pd.Timestamp, sectors: dict[str, str]) -> pd.DataFrame:
    rows = []
    for security_id, score in values.dropna().items():
        row = {
            "security_id": security_id,
            "composite_score": float(score),
            "current_snapshot_sector": sectors.get(security_id),
            "eligible": True,
        }
        for factor_id in V4_SAMPLE_FACTOR_IDS:
            value = factor_panels[factor_id].at[session, security_id]
            row[factor_id] = float(value) if pd.notna(value) else None
        rows.append(row)
    ranked = pd.DataFrame(rows).sort_values(["composite_score", "security_id"], ascending=[False, True], kind="stable")
    ranked.insert(0, "rank", range(1, len(ranked) + 1))
    return ranked


def _simulate(
    composite: pd.DataFrame,
    raw_frames: dict[str, pd.DataFrame],
    symbols: list[str],
    evaluation_sessions: pd.DatetimeIndex,
    minimum_cash: float,
    fee_rate: float,
):
    opens = pd.DataFrame({s: raw_frames[s].loc[evaluation_sessions, "open"] for s in symbols}, index=evaluation_sessions)
    closes = pd.DataFrame({s: raw_frames[s].loc[evaluation_sessions, "close"] for s in symbols}, index=evaluation_sessions)
    grid = pd.DatetimeIndex([stamp for session in evaluation_sessions for stamp in session_bounds(session)])
    marks = pd.DataFrame(index=grid, columns=symbols, dtype=float)
    for index, session in enumerate(evaluation_sessions):
        marks.iloc[2 * index] = opens.loc[session]
        marks.iloc[2 * index + 1] = closes.loc[session]
    sizes = pd.DataFrame(0.0, index=grid, columns=symbols)
    fill_prices = marks.copy()
    cash = 100000.0
    receivables = 0.0
    positions = {symbol: 0 for symbol in symbols}
    expected_equity = []
    events = []
    trades = []
    rebalance_pairs = dict(weekly_rebalance_dates(evaluation_sessions))
    for index, session in enumerate(evaluation_sessions):
        at_open, _ = session_bounds(session)
        for symbol in symbols:
            ratio = raw_frames[symbol].at[session, "splitFactor"]
            if pd.notna(ratio) and float(ratio) != 1.0:
                new_position = positions[symbol] * float(ratio)
                if not math.isclose(new_position, round(new_position), abs_tol=1e-10):
                    raise ValueError(f"FRACTIONAL_SPLIT:{symbol}:{session.date()}")
                positions[symbol] = int(round(new_position))
                events.append({"event_id": f"split-{symbol}-{session.date()}", "kind": "split", "at": at_open, "security_id": symbol, "ratio": float(ratio)})
            dividend = raw_frames[symbol].at[session, "divCash"]
            if pd.notna(dividend) and float(dividend) > 0:
                receivables += positions[symbol] * float(dividend)
                events.append({"event_id": f"dividend-{symbol}-{session.date()}", "kind": "dividend_ex", "at": at_open, "security_id": symbol, "amount_per_share": float(dividend)})
        signal_session = rebalance_pairs.get(session)
        if signal_session is not None:
            scores = composite.loc[signal_session].dropna()
            eligible = [s for s in scores.sort_values(ascending=False, kind="stable").index if pd.notna(opens.at[session, s]) and opens.at[session, s] > 0]
            selected = eligible[: max(1, math.ceil(len(eligible) * 0.10))]
            if selected:
                equity = cash + receivables + sum(positions[s] * float(opens.at[session, s]) for s in symbols if positions[s])
                per_name = equity * (1 - minimum_cash) / len(selected)
                desired = {s: int(math.floor(per_name / float(opens.at[session, s]))) if s in selected else 0 for s in symbols}
                changes = {s: desired[s] - positions[s] for s in symbols}
                buy_budget = max(0.0, cash - minimum_cash * equity)
                for s in selected:
                    if changes[s] > 0:
                        affordable = int(math.floor(buy_budget / (float(opens.at[session, s]) * (1 + fee_rate))))
                        changes[s] = min(changes[s], affordable)
                        buy_budget -= changes[s] * float(opens.at[session, s]) * (1 + fee_rate)
                for s in symbols:
                    quantity = int(changes[s])
                    if not quantity:
                        continue
                    price = float(opens.at[session, s])
                    fee = abs(quantity) * price * fee_rate
                    cash -= quantity * price + fee
                    positions[s] += quantity
                    sizes.at[at_open, s] = quantity
                    trades.append({"signal_session": signal_session, "execution_at": at_open, "security_id": s, "quantity": quantity, "side": "BUY" if quantity > 0 else "SELL", "reference_price": price, "notional": abs(quantity) * price, "fee": fee})
        for row_number, prices in enumerate((opens.loc[session], closes.loc[session])):
            held_missing = [s for s in symbols if positions[s] and (pd.isna(prices[s]) or prices[s] <= 0)]
            if held_missing:
                raise ValueError(f"MISSING_HELD_MARK:{session.date()}:{','.join(held_missing)}")
            expected_equity.append(cash + receivables + sum(positions[s] * float(prices[s]) for s in symbols if positions[s]))
    engine = run_event_backtest(
        marks.fillna(0.0),
        initial_cash=100000.0,
        sizes=sizes,
        fill_prices=fill_prices,
        fee_rate=fee_rate,
        events=events,
    )
    np.testing.assert_allclose(engine["snapshots"]["equity"], expected_equity, rtol=1e-10, atol=1e-8)
    return engine, pd.DataFrame(trades), sizes


def _portfolio_outputs(engine, trades, raw_frames, symbols, sessions):
    close_times = pd.DatetimeIndex([session_bounds(session)[1] for session in sessions])
    daily = engine["snapshots"].loc[close_times].copy()
    daily.index = sessions
    daily.index.name = "session"
    daily["strategy_return"] = daily["equity"].pct_change()
    if len(daily):
        daily.iloc[0, daily.columns.get_loc("strategy_return")] = daily["equity"].iloc[0] / 100000.0 - 1
    spy = raw_frames["SPY"].loc[sessions, "adjClose"]
    security_adj = pd.DataFrame({s: raw_frames[s].loc[sessions, "adjClose"] for s in symbols}, index=sessions)
    ew_return = security_adj.pct_change(fill_method=None).mean(axis=1, skipna=True)
    ew_return.iloc[0] = 0.0
    benchmark = pd.DataFrame(index=sessions)
    benchmark["SPY_total_return_index"] = spy / spy.dropna().iloc[0]
    benchmark["eligible_universe_equal_weight_index"] = (1 + ew_return.fillna(0)).cumprod()
    benchmark["strategy_index"] = daily["equity"] / 100000.0
    benchmark.index.name = "session"
    returns = daily["strategy_return"].fillna(0)
    elapsed_years = max((sessions[-1] - sessions[0]).days / 365.25, 1 / 252)
    final_index = float(benchmark["strategy_index"].iloc[-1])
    drawdown = benchmark["strategy_index"] / benchmark["strategy_index"].cummax() - 1
    volatility = float(returns.std() * math.sqrt(252))
    mean_return = float(returns.mean() * 252)
    total_traded = float(trades["notional"].sum()) if len(trades) else 0.0
    summary = {
        "engine": engine["engine"],
        "engine_version": engine["engine_version"],
        "evidence_kind": engine["evidence_kind"],
        "start": str(sessions[0].date()),
        "end": str(sessions[-1].date()),
        "sessions": int(len(sessions)),
        "total_return": final_index - 1,
        "cagr": final_index ** (1 / elapsed_years) - 1 if final_index > 0 else None,
        "annualized_volatility": volatility,
        "sharpe_zero_rate": mean_return / volatility if volatility > 0 else None,
        "max_drawdown": float(drawdown.min()),
        "ending_equity": float(daily["equity"].iloc[-1]),
        "total_traded_notional": total_traded,
        "total_fees": float(trades["fee"].sum()) if len(trades) else 0.0,
        "engine_order_count": int(engine["engine_order_count"]),
        "unpaid_dividend_receivables": float(daily["receivables"].iloc[-1]),
        "independent_cash_nav_reconciliation": "PASS",
        "formal_backtest_allowed": False,
        "limitations": engine["limitations"],
    }
    annual = []
    for year, group in benchmark.groupby(benchmark.index.year):
        start = benchmark.loc[: group.index[0]].iloc[-2]["strategy_index"] if benchmark.index.get_loc(group.index[0]) > 0 else 1.0
        annual.append({"year": int(year), "strategy_return": float(group["strategy_index"].iloc[-1] / start - 1), "spy_return": float(group["SPY_total_return_index"].iloc[-1] / (benchmark.loc[: group.index[0]].iloc[-2]["SPY_total_return_index"] if benchmark.index.get_loc(group.index[0]) > 0 else 1.0) - 1), "universe_equal_weight_return": float(group["eligible_universe_equal_weight_index"].iloc[-1] / (benchmark.loc[: group.index[0]].iloc[-2]["eligible_universe_equal_weight_index"] if benchmark.index.get_loc(group.index[0]) > 0 else 1.0) - 1)})
    return daily, benchmark, pd.DataFrame(annual), summary


def _write_audit_report(out: Path, summary: dict) -> None:
    a = summary["alphalens"]
    v = summary["vectorbt"]
    text = f"""# V4 三 Alpha 端到端工作流样例验收报告

## 结论

工程工作流状态为 **PASS**：V4 血缘、Tiingo 哈希原始数据、三因子重新计算、复合评分、Alphalens Reloaded 明细、vectorbt 订单与权益核算、最后一期排名/目标组合/调仓草稿及证据账本均已生成并核对。

正式研究与发布状态为 **BLOCKED**。本样例使用当前成分股快照中的 50 只股票，存在存活偏差；没有满足最低 500 只 PIT 股票池、历史 PIT 分类、完整公司行动付款日、真实执行成本和前瞻证据要求。`live_orders_submitted=0`。

## 冻结样例

- 因子数量：3；选择依据为稳定 ID 与字段类型差异，未按本次收益挑选。
- 合成方法：三个横截面百分位排名等权；任一因子缺失则复合分数缺失。
- 标签：信号日收盘冻结，下一交易日开盘进入，5 个交易日后开盘退出。
- 组合：每周首个交易日，读取上一交易日信号；Long Top 10%，等权，最低现金 5%，整数股，单边成本 10bp。
- 组合参数只用于 `ENGINEERING_DEMO_ONLY`。

## 两个框架的实际结果

Alphalens Reloaded `{a['engine_version']}` 已实际运行。复合因子的 mean Rank IC 为 `{a['composite']['mean_rank_ic']}`，原始 ICIR 为 `{a['composite']['raw_icir']}`，可用观测 `{a['composite']['observations']}` 条。完整每日 IC、分位数收益、自相关、分位数换手和标准误均在 `alphalens/` 目录。

vectorbt `{v['engine_version']}` 已实际运行。工程组合样例区间 `{v['start']}` 至 `{v['end']}`，总收益 `{v['total_return']:.4%}`，最大回撤 `{v['max_drawdown']:.4%}`，订单 `{v['engine_order_count']}` 笔，成本 `${v['total_fees']:.2f}`。明细在 `vectorbt/` 目录。

这些数字属于已经暴露的开发诊断样本，不能作为独立 OOS、正式回测或收益承诺。

## 阶段验收

| 阶段 | 状态 | 证据 |
|---|---|---|
| T0/T1 来源与配置冻结 | PASS | `candidate_frozen_before_evaluation.json`、`factor_lineage.json` |
| T2 数据字段与哈希 | PASS | `data_coverage.csv`、`input_hashes.csv` |
| T3 因子重新计算与前缀不变 | PASS | `factors/`、`compute_checks.json` |
| Alphalens Reloaded | PASS / DEVELOPMENT_EXPOSED | `alphalens/` 原生表 |
| vectorbt | PASS / ENGINEERING_DIAGNOSTIC | `vectorbt/` 订单、持仓、NAV、基准表 |
| 排名与调仓样例 | PASS / HISTORICAL_DEMO | `latest_selection/` 与两个 Excel |
| 正式 PIT 研究准入 | BLOCKED | 50 只当前快照股票，最低覆盖 500 未满足 |
| 正式发布与实盘 | BLOCKED | 公司行动、真实成本、前瞻及发布证据未完成 |

## 审查顺序

1. 先看 `workflow_summary.json` 和本报告。
2. 打开 `signal_review.xlsx` 与 `execution_review.xlsx` 核对排名、目标权重、股数及阻断项。
3. 查看 `alphalens/summary.csv` 和各因子目录，确认框架明细存在。
4. 查看 `vectorbt/summary.json`、`benchmark_comparison.csv`、`engine_orders.csv` 和 `portfolio_daily.csv`。
5. 用 `manifest.json` 重算文件 SHA-256；用 `credential_scan.json` 确认未落盘凭证。
"""
    (out / "验收报告.md").write_text(text, encoding="utf-8")


def run(output: Path) -> None:
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    _verify_manifest(LIBRARY_ROOT)
    library = json.loads((LIBRARY_ROOT / "project_factor_library.json").read_text(encoding="utf-8"))
    contract = freeze_sample_contract(library, V4_SAMPLE_FACTOR_IDS)
    frozen_path = output / "candidate_frozen_before_evaluation.json"
    _json(frozen_path, contract)
    candidate_hash = hashlib.sha256(frozen_path.read_bytes()).hexdigest()
    ledger = EvidenceLedger(output / "evidence.sqlite")
    ledger.record_trial({"trial_id": "v4-three-alpha-main-config", "kind": "MAIN", "status": "DEVELOPMENT_EXPOSED", "candidate_hash": candidate_hash})
    calendar = xcals.get_calendar("XNYS")
    sessions = pd.DatetimeIndex(calendar.sessions_in_range(contract["data"]["warmup_start"], contract["data"]["end"]))
    universe = pd.read_csv(DATA_ROOT / "universe.csv").head(50).copy()
    symbols = universe["Symbol"].astype(str).tolist()
    universe.rename(columns={"Symbol": "security_id"}).to_csv(output / "universe_50_current_snapshot.csv", index=False)
    sectors = dict(zip(symbols, universe["GICS Sector"].astype(str)))
    frames, fields, coverage, hashes = _load_inputs(symbols, sessions)
    coverage.to_csv(output / "data_coverage.csv", index=False)
    hashes.to_csv(output / "input_hashes.csv", index=False)
    ledger.record_exposure({"start": contract["data"]["evaluation_start"], "end": contract["data"]["end"], "security_scope": symbols, "lineage_scope": list(V4_SAMPLE_FACTOR_IDS), "source": "EXISTING_50_SECURITY_TIINGO_DIAGNOSTIC_SAMPLE"})
    factors = {row["local_factor_id"]: row for row in contract["factors"]}
    _json(output / "factor_lineage.json", {factor_id: factors[factor_id] for factor_id in V4_SAMPLE_FACTOR_IDS})
    computed = compute_registry_factors(library, V4_SAMPLE_FACTOR_IDS, "tiingo_eod", fields, usage="diagnostic")
    prefix_fields = {name: panel.iloc[:-10] for name, panel in fields.items()}
    prefix = compute_registry_factors(library, V4_SAMPLE_FACTOR_IDS, "tiingo_eod", prefix_fields, usage="diagnostic")
    compute_checks = {}
    factor_dir = output / "factors"
    factor_dir.mkdir()
    for factor_id in V4_SAMPLE_FACTOR_IDS:
        pd.testing.assert_frame_equal(prefix[factor_id], computed[factor_id].iloc[:-10])
        computed[factor_id].to_parquet(factor_dir / f"{factor_id}.parquet")
        compute_checks[factor_id] = {"status": "PASS", "prefix_invariant": True, "finite_values": int(np.isfinite(computed[factor_id].to_numpy()).sum())}
    composite, ranked_panels = combine_factor_panels(computed, V4_SAMPLE_FACTOR_IDS)
    composite.to_parquet(factor_dir / "composite_equal_weight_rank.parquet")
    for factor_id, panel in ranked_panels.items():
        panel.to_parquet(factor_dir / f"{factor_id}_cross_sectional_rank.parquet")
    _json(output / "compute_checks.json", compute_checks)
    evaluation_sessions = sessions[(sessions >= pd.Timestamp(contract["data"]["evaluation_start"])) & (sessions <= pd.Timestamp(contract["data"]["end"]))]
    adjusted_open = pd.DataFrame({s: frames[s].loc[evaluation_sessions, "adjOpen"] for s in symbols}, index=evaluation_sessions)
    alpha_root = output / "alphalens"
    alpha_root.mkdir()
    alpha_summaries = {}
    score_panels = {factor_id: computed[factor_id].loc[evaluation_sessions, symbols] for factor_id in V4_SAMPLE_FACTOR_IDS}
    score_panels["composite_equal_weight_rank"] = composite.loc[evaluation_sessions, symbols]
    for factor_id, scores in score_panels.items():
        labels = _build_labels(scores, adjusted_open, contract["diagnostic_horizon_sessions"])
        valid = labels[labels["label_status"] == "AVAILABLE"].copy()
        detail = alphalens_detail_tables(valid, horizon=contract["diagnostic_horizon_sessions"], quantiles=5)
        destination = alpha_root / factor_id
        destination.mkdir()
        labels.to_parquet(destination / "trade_aligned_labels.parquet", index=False)
        detail["factor_data"].to_parquet(destination / "factor_data.parquet")
        for name in ("ic_by_date", "mean_return_by_quantile", "standard_error_by_quantile", "factor_rank_autocorrelation", "quantile_turnover"):
            detail[name].to_csv(destination / f"{name}.csv")
        _json(destination / "summary.json", detail["summary"])
        alpha_summaries[factor_id] = detail["summary"]
    pd.DataFrame([{"factor_id": key, **value} for key, value in alpha_summaries.items()]).to_csv(alpha_root / "summary.csv", index=False)
    _json(alpha_root / "summary.json", alpha_summaries)
    engine, trades, sizes = _simulate(composite.loc[evaluation_sessions, symbols], frames, symbols, evaluation_sessions, contract["portfolio"]["minimum_cash"], contract["portfolio"]["one_way_cost_rate"])
    vector_root = output / "vectorbt"
    vector_root.mkdir()
    daily, benchmark, annual, vector_summary = _portfolio_outputs(engine, trades, frames, symbols, evaluation_sessions)
    daily.to_csv(vector_root / "portfolio_daily.csv")
    benchmark.to_csv(vector_root / "benchmark_comparison.csv")
    annual.to_csv(vector_root / "annual_returns.csv", index=False)
    trades.to_csv(vector_root / "reference_trades.csv", index=False)
    close_times = pd.DatetimeIndex([session_bounds(session)[1] for session in evaluation_sessions])
    positions = engine["positions"].loc[close_times].copy()
    positions.index = evaluation_sessions
    positions.index.name = "session"
    positions.to_parquet(vector_root / "positions_daily.parquet")
    engine_orders = vectorbt_order_records(engine["orders"], symbols, engine["snapshots"].index)
    engine_orders.to_csv(vector_root / "engine_orders.csv", index=False)
    _json(vector_root / "summary.json", vector_summary)
    signal_session = evaluation_sessions[-2]
    execution_session = evaluation_sessions[-1]
    ranking = _rank_frame(composite.loc[signal_session, symbols], score_panels, signal_session, sectors)
    selected = ranking.head(max(1, math.ceil(len(ranking) * contract["portfolio"]["top_fraction"])))
    prices = {s: float(frames[s].at[execution_session, "open"]) for s in selected["security_id"]}
    target_shares, cash_audit = cash_funded_target_shares(selected["security_id"].tolist(), prices, equity=contract["portfolio"]["initial_cash"], minimum_cash=contract["portfolio"]["minimum_cash"], fee_rate=contract["portfolio"]["one_way_cost_rate"])
    ranking["selection_status"] = np.where(ranking["security_id"].isin(selected["security_id"]), "SELECTED", "ELIGIBLE")
    targets = []
    orders = []
    intended_weight = (1 - contract["portfolio"]["minimum_cash"]) / len(selected)
    for row in selected.to_dict("records"):
        security_id = row["security_id"]
        quantity = target_shares[security_id]
        value = quantity * prices[security_id]
        targets.append({"security_id": security_id, "rank": row["rank"], "target_weight": intended_weight, "reference_open": prices[security_id], "model_target_shares": quantity, "rounded_weight": value / contract["portfolio"]["initial_cash"], "current_snapshot_sector": row["current_snapshot_sector"]})
        orders.append({"action": "BUY", "security_id": security_id, "current_shares": 0, "model_target_shares": quantity, "intended_trade_shares": quantity, "approved_trade_shares": 0, "reference_open": prices[security_id], "approx_notional": value, "estimated_fee": value * contract["portfolio"]["one_way_cost_rate"], "order_status": "BLOCKED_HISTORICAL_ENGINEERING_DEMO"})
    targets.append({"security_id": "CASH", "rank": None, "target_weight": contract["portfolio"]["minimum_cash"], "reference_open": None, "model_target_shares": None, "rounded_weight": cash_audit["ending_cash"] / contract["portfolio"]["initial_cash"], "current_snapshot_sector": None})
    target_frame = pd.DataFrame(targets)
    order_frame = pd.DataFrame(orders)
    checks = pd.DataFrame([
        {"check": "ENGINEERING_WORKFLOW", "status": "PASS", "detail": "All stages produced and reconciled"},
        {"check": "FORMAL_MINIMUM_500", "status": "BLOCKED", "detail": "Only 50 current-snapshot securities"},
        {"check": "PIT_UNIVERSE", "status": "BLOCKED", "detail": "Historical PIT membership unavailable"},
        {"check": "PIT_CLASSIFICATION", "status": "BLOCKED", "detail": "Current sector labels display-only"},
        {"check": "CORPORATE_ACTIONS", "status": "PARTIAL", "detail": "Splits and dividend ex-date handled; payment dates and complex actions incomplete"},
        {"check": "EXECUTION_COST", "status": "PARTIAL", "detail": "Fixed 10bp engineering assumption only"},
        {"check": "RELEASE", "status": "BLOCKED", "detail": "No independent holdout/forward release evidence"},
        {"check": "LIVE_ORDERS_SUBMITTED", "status": "PASS", "detail": "0"},
    ])
    write_latest_selection_csvs(output, ranking=ranking, targets=target_frame, orders=order_frame, checks=checks)
    signal = {"signal_id": "v4-three-alpha-20251230", "signal_cutoff": session_bounds(signal_session)[1], "execution_reference_at": session_bounds(execution_session)[0], "release_id": "UNRELEASED_DIAGNOSTIC", "ranking": _records(ranking), "target_weights": _records(target_frame[["security_id", "target_weight"]]), "run_status": "SIGNAL_ONLY_HISTORICAL_DEMO", "evidence_status": "DEVELOPMENT_EXPOSED", "live_orders_submitted": 0}
    signal["signal_hash"] = canonical_hash(signal)
    _json(output / "latest_selection/signal_snapshot.json", signal)
    execution = {"execution_id": "v4-three-alpha-20251231", "signal_id": signal["signal_id"], "signal_hash": signal["signal_hash"], "run_status": "BLOCKED", "evidence_status": "DEVELOPMENT_EXPOSED", "orders": _records(order_frame), "target_portfolio": _records(target_frame), "cash_audit": cash_audit, "live_orders_submitted": 0}
    _json(output / "latest_selection/execution_preview.json", execution)
    _json(output / "evidence_export.json", ledger.export())
    summary = {
        "run_id": contract["contract_version"],
        "engineering_workflow_status": "PASS",
        "factor_library": {"version": "reconstruction-v4", "source_count": 886, "unique_compute_verified_formulas": 135, "selected_factor_count": 3, "selected_source_alpha_associations": sum(len(factors[factor_id]["source_alpha_ids"]) for factor_id in V4_SAMPLE_FACTOR_IDS)},
        "data": {"provider": "Tiingo", "security_count": len(symbols), "benchmark": "SPY", "sessions_with_warmup": len(sessions), "evaluation_sessions": len(evaluation_sessions), "pit_universe": False},
        "alphalens": {"engine": "alphalens-reloaded", "engine_version": next(iter(alpha_summaries.values()))["engine_version"], "composite": alpha_summaries["composite_equal_weight_rank"], "all_factors": alpha_summaries},
        "vectorbt": vector_summary,
        "latest_selection": {"signal_session": signal_session, "execution_reference_session": execution_session, "selected": selected["security_id"].tolist(), "intended_order_count": len(order_frame), "approved_order_count": 0, "live_orders_submitted": 0, "cash_audit": cash_audit},
        "formal_research_status": "BLOCKED",
        "release_status": "BLOCKED",
        "blocking_reasons": ["CURRENT_SNAPSHOT_50_SECURITY_UNIVERSE", "MINIMUM_500_NOT_MET", "NO_HISTORICAL_PIT_MEMBERSHIP", "NO_HISTORICAL_PIT_CLASSIFICATION", "INCOMPLETE_CORPORATE_ACTION_LEDGER", "DEMO_COST_MODEL", "NO_INDEPENDENT_HOLDOUT_OR_FORWARD_EVIDENCE"],
    }
    _json(output / "workflow_summary.json", summary)
    _write_audit_report(output, summary)
    print(json.dumps({"output": str(output), "status": summary["engineering_workflow_status"], "selected": summary["latest_selection"]["selected"], "alphalens_composite": summary["alphalens"]["composite"], "vectorbt": vector_summary}, ensure_ascii=False, default=str))


def finalize(output: Path) -> None:
    if not output.is_dir():
        raise FileNotFoundError(output)
    credential_patterns = ["TIINGO_API_KEY", "APCA_API_KEY_ID", "APCA_API_SECRET_KEY", "WQ_BRAIN_PASSWORD", "WQ_BRAIN_EMAIL"]
    findings = []
    excluded = {"manifest.json", "credential_scan.json"}
    for path in sorted(p for p in output.rglob("*") if p.is_file() and p.name not in excluded and p.suffix.lower() not in {".sqlite", ".parquet", ".xlsx"}):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in credential_patterns:
            if pattern in text:
                findings.append({"file": str(path.relative_to(output)), "pattern": pattern})
    scan = {"status": "PASS" if not findings else "FAIL", "patterns_scanned": credential_patterns, "findings": findings, "note": "Scans credential variable names; secret values are never accepted as pipeline inputs."}
    _json(output / "credential_scan.json", scan)
    if findings:
        raise ValueError("CREDENTIAL_SCAN_FAILED")
    files = {}
    for path in sorted(p for p in output.rglob("*") if p.is_file() and p.name != "manifest.json"):
        files[str(path.relative_to(output))] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
    manifest = {"run_id": json.loads((output / "workflow_summary.json").read_text())["run_id"], "file_count": len(files), "files": files, "credential_scan": "PASS", "manifest_self_hash_excluded": True}
    _json(output / "manifest.json", manifest)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--finalize", action="store_true")
    args = parser.parse_args()
    if args.finalize:
        finalize(args.output)
    else:
        run(args.output)


if __name__ == "__main__":
    main()
