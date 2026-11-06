"""One-off, development-exposed V4 single-factor output sample."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt

from us_equity_alpha.alpha_filter_diagnostics import forward_open_returns, weekly_signal_sessions
from us_equity_alpha.factor_library import compute_registry_factors
from us_equity_alpha.performance_visual import build_decile_return_table, render_performance_report
from us_equity_alpha.sample_workflow import alphalens_detail_tables


ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
SOURCE = ROOT / "private/runs/v5-1-yfinance-500-2019-2026-filter-v2/inputs"
FREEZE = ROOT / "config/alpha_library_freeze_v1.json"
FACTOR_ID = "local_reconstruction__23a9d01ddb88a7ae"
START = pd.Timestamp("2020-01-02", tz="UTC")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False, default=str) + "\n", encoding="utf-8")


def main() -> None:
    freeze = json.loads(FREEZE.read_text())
    baseline = freeze["current_usable_library"]
    assert freeze["status"] == "PAUSED"
    assert baseline["factor_count"] == 135
    original = ROOT / baseline["library_path"]
    assert digest(original) == baseline["library_sha256"]
    adapter_manifest = json.loads((SOURCE / "adapted_v4_manifest_yfinance.json").read_text())
    adapted_path = SOURCE / "adapted_v4_library_yfinance.json"
    assert adapter_manifest["project_factor_library.json"] == digest(adapted_path)
    assert adapter_manifest["source_v4_library_sha256"] == digest(original)
    library = json.loads(adapted_path.read_text())
    factor = next(row for row in library["factors"] if row["local_factor_id"] == FACTOR_ID)
    assert factor["expression"] == "rank(-(close / open - 1))"
    assert factor["build_status"] == "COMPUTE_VERIFIED"
    assert factor["field_bindings"] == {"close": "close", "open": "open"}
    universe_path = SOURCE / "universe_500.csv"
    universe = pd.read_csv(universe_path)
    assert len(universe) == 500 and universe.security_id.is_unique
    ids = universe.security_id.astype(str).tolist()
    market = SOURCE / "market_panels"
    inputs = {name: pd.read_parquet(market / f"{name}.parquet") for name in ("open", "close")}
    assert inputs["open"].index.equals(inputs["close"].index)
    assert set(ids).issubset(inputs["open"].columns)
    scores = compute_registry_factors(library, [FACTOR_ID], "yfinance_eod", inputs, usage="diagnostic")[FACTOR_ID]
    prefix = compute_registry_factors(library, [FACTOR_ID], "yfinance_eod", {name: panel.iloc[:-10] for name, panel in inputs.items()}, usage="diagnostic")[FACTOR_ID]
    pd.testing.assert_frame_equal(prefix, scores.iloc[:-10])
    scores = scores.reindex(columns=ids)
    signals = pd.DatetimeIndex(s for s in weekly_signal_sessions(inputs["open"].index) if s >= START)
    labels = forward_open_returns(inputs["open"].reindex(columns=ids), signals, horizon=5)
    ranked = scores.loc[signals].rank(axis=1, pct=True, method="average")
    long = pd.concat([ranked.stack().rename("score"), labels.stack().rename("forward_return")], axis=1).dropna().reset_index()
    long.columns = ["signal_session", "security_id", "score", "forward_return"]
    # Alphalens treats the index as session labels. UTC close timestamps cross
    # DST and its calendar alignment drops summer sessions, so keep date labels.
    long["signal_cutoff"] = long["signal_session"]
    alphalens = alphalens_detail_tables(long, horizon=5, quantiles=5)
    alpha_dir = OUT / "alphalens"
    alpha_dir.mkdir(exist_ok=True)
    build_decile_return_table(ranked, labels).to_csv(alpha_dir / "decile_5d_returns.csv", index_label="signal_session")
    write_json(alpha_dir / "summary.json", alphalens["summary"])
    for name in ("ic_by_date", "mean_return_by_quantile", "factor_rank_autocorrelation", "quantile_turnover"):
        alphalens[name].to_csv(alpha_dir / f"{name}.csv")

    opens = inputs["open"].reindex(columns=ids)
    closes = inputs["close"].reindex(columns=ids)
    targets = pd.DataFrame(np.nan, index=opens.index, columns=ids)
    session_positions = {session: i for i, session in enumerate(opens.index)}
    for signal in signals:
        execution_position = session_positions[signal] + 1
        if execution_position >= len(opens.index):
            continue
        execution = opens.index[execution_position]
        valid = ranked.loc[signal].dropna().index.intersection(opens.columns[opens.loc[execution].gt(0)])
        ordered = ranked.loc[signal, valid].sort_values(ascending=False, kind="stable")
        if ordered.empty:
            continue
        n = max(1, math.ceil(len(ordered) * 0.10))
        targets.loc[execution] = 0.0
        targets.loc[execution, ordered.head(n).index] = 0.95 / n
    clean_open = opens.where(opens.gt(0)).ffill().fillna(1.0)
    clean_close = closes.where(closes.gt(0)).ffill().fillna(1.0)
    portfolio = vbt.Portfolio.from_orders(
        close=clean_close, size=targets, size_type="targetpercent", price=clean_open,
        init_cash=100_000.0, fees=0.001, slippage=0.0,
        cash_sharing=True, group_by=True, call_seq="auto", direction="longonly", freq="1D",
    )
    first_execution = opens.index[session_positions[signals[0]] + 1]
    value = portfolio.value().loc[first_execution:]
    returns = value.pct_change(fill_method=None).fillna(0.0)
    spy_close = inputs["close"]["SPY"].reindex(value.index)
    spy_index = spy_close / inputs["open"]["SPY"].loc[first_execution]
    eligible_ew = closes.loc[value.index].pct_change(fill_method=None).mean(axis=1, skipna=True).fillna(0.0)
    eligible_ew.iloc[0] = (closes.loc[first_execution] / opens.loc[first_execution] - 1).mean(skipna=True)
    ew_index = (1 + eligible_ew).cumprod()
    nav = pd.DataFrame({"portfolio_nav": value, "portfolio_index": value / 100_000.0,
                        "spy_index": spy_index, "universe_equal_weight_index": ew_index})
    nav.index.name = "session"
    vector_dir = OUT / "vectorbt"
    vector_dir.mkdir(exist_ok=True)
    nav.to_csv(vector_dir / "nav_and_benchmarks.csv")
    orders = portfolio.orders.records_readable
    orders.to_csv(vector_dir / "orders.csv", index=False)
    yearly = []
    for year, frame in nav.groupby(nav.index.year):
        first = nav.index.get_loc(frame.index[0])
        previous = nav.iloc[first - 1] if first else pd.Series({"portfolio_index": 1.0, "spy_index": 1.0, "universe_equal_weight_index": 1.0})
        yearly.append({"year": year, **{name + "_return": float(frame[name].iloc[-1] / previous[name] - 1) for name in ("portfolio_index", "spy_index", "universe_equal_weight_index")}})
    pd.DataFrame(yearly).to_csv(vector_dir / "annual_returns.csv", index=False)
    years = (value.index[-1] - value.index[0]).days / 365.25
    volatility = float(returns.std() * np.sqrt(252))
    vector_summary = {
        "engine": "vectorbt", "engine_version": vbt.__version__,
        "start": str(value.index[0].date()), "end": str(value.index[-1].date()),
        "total_return": float(value.iloc[-1] / 100_000.0 - 1),
        "cagr": float((value.iloc[-1] / 100_000.0) ** (1 / years) - 1),
        "max_drawdown": float((value / value.cummax() - 1).min()),
        "sharpe_zero_rate": float(returns.mean() * 252 / volatility) if volatility else None,
        "order_count": int(len(orders)), "modeled_fees": float(orders["Fees"].sum()),
        "spy_total_return": float(spy_index.iloc[-1] - 1),
        "universe_equal_weight_total_return": float(ew_index.iloc[-1] - 1),
        "fill_model": "IDEALIZED_NEXT_SESSION_OPEN_TARGET_PERCENT",
        "fee_rate_one_way": 0.001, "slippage": 0.0,
    }
    write_json(vector_dir / "summary.json", vector_summary)

    latest_signal = signals[-1]
    latest_execution = opens.index[session_positions[latest_signal] + 1]
    latest_scores = ranked.loc[latest_signal].dropna().sort_values(ascending=False, kind="stable")
    ranking = latest_scores.rename("factor_percentile_rank").rename_axis("security_id").reset_index()
    ranking.insert(0, "rank", np.arange(1, len(ranking) + 1))
    ranking["ticker"] = ranking.security_id.map(universe.set_index("security_id").ticker)
    ranking["factor_value"] = ranking.security_id.map(scores.loc[latest_signal])
    n = max(1, math.ceil(len(ranking) * 0.10))
    ranking["selection_status"] = np.where(ranking["rank"] <= n, "SELECTED", "ELIGIBLE")
    ranking.to_csv(OUT / "stock_ranking.csv", index=False)
    target = ranking.head(n)[["rank", "security_id", "ticker", "factor_value", "factor_percentile_rank"]].copy()
    target["target_weight"] = 0.95 / n
    target["signal_close"] = target.security_id.map(closes.loc[latest_signal])
    target["approx_target_value_at_100k"] = target.target_weight * 100_000
    target["model_target_shares_at_signal_close"] = np.floor(target.approx_target_value_at_100k / target.signal_close).astype(int)
    target["execution_status"] = "HISTORICAL_DEMO_NO_ORDERS"
    target.to_csv(OUT / "target_portfolio.csv", index=False)
    write_json(OUT / "run_summary.json", {
        "status": "ENGINEERING_DIAGNOSTIC_COMPLETED", "factor_id": FACTOR_ID,
        "formula": factor["expression"], "factor_delay_sessions": factor["settings"]["delay"],
        "selection_basis": "SIMPLE_TWO_FIELD_FORMULA_NOT_RETURN_SELECTED",
        "factor_library_sha256": digest(original), "data_source": "saved_yfinance_500_current_snapshot",
        "source_evidence": json.loads((SOURCE / "source_evidence.json").read_text()),
        "input_hashes": {str(path.relative_to(ROOT)): digest(path) for path in (adapted_path, universe_path, market / "open.parquet", market / "close.parquet")},
        "signal_frequency": "WEEKLY_REBALANCE_SIGNAL", "forward_label": "T_CLOSE_T1_OPEN_T6_OPEN",
        "alphalens_date_basis": "XNYS_SESSION_LABEL_UTC_MIDNIGHT",
        "signal_session": str(latest_signal.date()), "historical_execution_reference_session": str(latest_execution.date()),
        "selected_count": n, "cash_target": 0.05, "live_orders_submitted": 0,
        "alphalens": alphalens["summary"], "vectorbt": vector_summary,
        "limitations": ["CURRENT_SNAPSHOT_SURVIVORSHIP_BIAS", "NO_HISTORICAL_PIT_UNIVERSE",
                        "DEVELOPMENT_EXPOSED_NO_INDEPENDENT_OOS", "YFINANCE_ADJUSTED_PRICES",
                        "IDEALIZED_OPEN_FILL_ZERO_SLIPPAGE", "NO_CURRENT_ACCOUNT_POSITIONS"],
    })
    top = target.head(10)
    lines = [
        "# V4 单因子回测与选股输出样例", "",
        f"因子：`{FACTOR_ID}`；公式：`{factor['expression']}`；原设置 `delay=1`。",
        "本次从 135 个可计算公式中按字段简单选取，未根据本次收益挑选。", "",
        "## 实际输出", "",
        f"历史数据：500 只当前快照股票，2019-01-02 至 {value.index[-1].date()}；2019 年用于预热，组合从 {value.index[0].date()} 开始。",
        "每周前一交易日收盘生成信号，下一交易日开盘作为理想化成交参考；持有排名前 10%，等权投入 95%，留 5% 现金，单边费用 10bp、滑点 0。", "",
        f"Alphalens：5 个交易日标签共 {alphalens['summary']['sessions']} 个有效周、{alphalens['summary']['observations']:,} 条观测；平均 Rank IC {alphalens['summary']['mean_rank_ic']:.4f}，原始 ICIR {alphalens['summary']['raw_icir']:.4f}。",
        f"vectorbt：组合总收益 {vector_summary['total_return']:.2%}，SPY {vector_summary['spy_total_return']:.2%}，当前快照股票池等权 {vector_summary['universe_equal_weight_total_return']:.2%}；最大回撤 {vector_summary['max_drawdown']:.2%}，模拟订单 {vector_summary['order_count']:,} 笔，累计模拟费用 ${vector_summary['modeled_fees']:,.2f}。", "",
        f"最后一组历史信号为 {latest_signal.date()} 收盘；对应的历史开盘参考日为 {latest_execution.date()}。目标持有 {n} 只，每只目标权重 {0.95 / n:.2%}。", "",
        "| 排名 | 股票 | 因子百分位 | 目标权重 | 10 万美元示例整数股 |", "|---:|---|---:|---:|---:|",
    ]
    for row in top.itertuples(index=False):
        lines.append(f"| {row.rank} | {row.ticker} | {row.factor_percentile_rank:.3f} | {row.target_weight:.2%} | {row.model_target_shares_at_signal_close} |")
    lines += [
        "", "## 文件用途", "",
        "- `stock_ranking.csv`：全部 500 只的最后一期因子排序。",
        "- `target_portfolio.csv`：前 50 只目标权重与按信号日收盘价计算的示例股数；没有账户实际持仓，因此不是调仓指令。",
        "- `alphalens/`：逐周 Rank IC、分位数收益、换手和自相关。",
        "- `vectorbt/`：历史净值、基准、年度收益、模拟订单与汇总。",
        "- `run_summary.json` 与 `manifest.json`：数据和因子血缘、输入及交付文件哈希。", "",
        "## 解释边界", "",
        "这是开发期历史工程样例。股票池是当前快照倒推，存在存活偏差；2019–2026 年数据已用于项目开发，不能称为独立 OOS。价格来自已保存的 Yahoo 调整后数据；开盘目标百分比成交是理想化模型，滑点为零，公司行动和真实执行成本未完成验证。", "",
        "平均 IC 很小，组合在这个样本中的累计收益低于 SPY 和股票池等权基准。结果不能据此证明该因子有效，也不能作为当前交易建议。V4 的 135 因子基线和正式筛选配置未变更。", "",
    ]
    (OUT / "单因子样例报告.md").write_text("\n".join(lines), encoding="utf-8")
    render_performance_report(OUT)
    files = {str(path.relative_to(OUT)): {"sha256": digest(path), "bytes": path.stat().st_size}
             for path in sorted(OUT.rglob("*")) if path.is_file() and path.name != "manifest.json"}
    write_json(OUT / "manifest.json", {"files": files, "file_count": len(files), "manifest_self_hash_excluded": True})


if __name__ == "__main__":
    main()
