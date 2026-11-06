"""Render a single Alpha performance run as an auditable chart page."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


GROUPS = tuple(f"G{i}" for i in range(1, 11))
SPLIT = pd.Timestamp("2025-01-01", tz="UTC")


def build_decile_return_table(scores: pd.DataFrame, forward_returns: pd.DataFrame) -> pd.DataFrame:
    """Group on signal-only ranks; average the subsequently observed 5D labels."""
    if not scores.index.equals(forward_returns.index) or not scores.columns.equals(forward_returns.columns):
        raise ValueError("DECILE_SCORE_LABEL_ALIGNMENT_MISMATCH")
    rows = []
    for date in scores.index:
        ranking = pd.to_numeric(scores.loc[date], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
        ranking = ranking.sort_index().sort_values(kind="stable")
        row = {"signal_session": date, "eligible_count": len(ranking), "valid_forward_count": 0}
        row.update({group: np.nan for group in GROUPS})
        if len(ranking) >= 10 and ranking.nunique() > 1:
            groups = np.array_split(ranking.index.to_numpy(), 10)
            for group, members in zip(GROUPS, groups, strict=True):
                values = pd.to_numeric(forward_returns.loc[date, members], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
                row[group] = float(values.mean()) if not values.empty else np.nan
                row["valid_forward_count"] += len(values)
        rows.append(row)
    return pd.DataFrame(rows).set_index("signal_session")


def _read_dates(path: Path, date_column: str) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame[date_column] = pd.to_datetime(frame[date_column], utc=True)
    return frame.set_index(date_column).sort_index()


def render_performance_report(run_dir: Path) -> dict[str, str]:
    """Draw only observed series from one run; no metrics or scenarios are invented."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    run_dir = Path(run_dir)
    summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
    group_path = run_dir / "alphalens/decile_5d_returns.csv"
    ic_path = run_dir / "alphalens/ic_by_date.csv"
    nav_path = run_dir / "vectorbt/nav_and_benchmarks.csv"
    missing = [str(path.relative_to(run_dir)) for path in (group_path, ic_path, nav_path) if not path.is_file()]
    group = _read_dates(group_path, "signal_session") if group_path.exists() else None
    ic = _read_dates(ic_path, "date") if ic_path.exists() else None
    nav = _read_dates(nav_path, "session") if nav_path.exists() else None
    fig, axes = plt.subplots(3, 2, figsize=(18, 13))
    fig.suptitle(f"Alpha Performance  |  {summary['factor_id']}", fontsize=19, fontweight="bold")
    colors = plt.get_cmap("RdBu_r")(np.linspace(0.1, 0.9, 10))
    periods = (("Through 2024", lambda idx: idx < SPLIT), ("2025 onward", lambda idx: idx >= SPLIT))
    for column, (period_title, mask) in enumerate(periods):
        ax = axes[0, column]
        ax.set_title(f"Decile cumulative 5D label chain | {period_title}")
        if group is not None:
            values = group.loc[mask(group.index), list(GROUPS)].dropna(how="all")
            if not values.empty:
                curves = (1 + values.fillna(0)).cumprod()
                for number, name in enumerate(GROUPS):
                    ax.plot(curves.index, curves[name], color=colors[number], linewidth=1.3, label=name)
                ax.axhline(1, color="grey", linestyle="--", linewidth=0.7)
                ax.legend(ncol=10, loc="upper center", fontsize=7)
            else:
                ax.text(0.5, 0.5, "No valid group series", transform=ax.transAxes, ha="center")
        else:
            ax.text(0.5, 0.5, "Group time series unavailable", transform=ax.transAxes, ha="center")
        ax.set_ylabel("Cumulative value, rebased to 1")

        ax = axes[1, column]
        ax.set_title(f"5D RankIC | {period_title}")
        if ic is not None and "5D" in ic:
            series = pd.to_numeric(ic.loc[mask(ic.index), "5D"], errors="coerce").dropna()
            if not series.empty:
                ax.plot(series.index, series, color="#9ab9cb", linewidth=0.8, alpha=0.55, label="Weekly RankIC")
                ax.plot(series.index, series.rolling(13, min_periods=5).mean(), color="#087e78", linewidth=1.8, label="13-signal mean")
                ax.legend(fontsize=8)
            else:
                ax.text(0.5, 0.5, "No valid IC series", transform=ax.transAxes, ha="center")
        else:
            ax.text(0.5, 0.5, "IC series unavailable", transform=ax.transAxes, ha="center")
        ax.axhline(0, color="grey", linestyle="--", linewidth=0.7)
        ax.set_ylabel("Spearman correlation")

        ax = axes[2, column]
        ax.set_title(f"Portfolio NAV | {period_title} | actual 10 bp fee run")
        if nav is not None:
            values = nav.loc[mask(nav.index), ["portfolio_index", "spy_index", "universe_equal_weight_index"]].dropna(how="all")
            if not values.empty:
                prior = nav.loc[nav.index < values.index[0]].tail(1)
                base = prior.iloc[0] if not prior.empty else pd.Series(1.0, index=values.columns)
                curves = values / base
                for name, color in (("portfolio_index", "#b12435"), ("spy_index", "#32679c"), ("universe_equal_weight_index", "#be8b3d")):
                    ax.plot(curves.index, curves[name], label=name.replace("_", " "), linewidth=1.5, color=color)
                ax.legend(fontsize=8)
            else:
                ax.text(0.5, 0.5, "No valid NAV series", transform=ax.transAxes, ha="center")
        else:
            ax.text(0.5, 0.5, "NAV series unavailable", transform=ax.transAxes, ha="center")
        ax.axhline(1, color="grey", linestyle="--", linewidth=0.7)
        ax.set_ylabel("Cumulative NAV, rebased to 1")
        for row in range(3):
            axes[row, column].grid(alpha=0.18)
            axes[row, column].tick_params(axis="x", labelrotation=0)
    fig.tight_layout(rect=(0.015, 0.06, 0.985, 0.96), h_pad=2.0, w_pad=2.0)
    fig.text(0.5, 0.018, "DEVELOPMENT EXPOSED | current-snapshot universe | 5D IC only | decile chain is diagnostic, not an executable portfolio", ha="center", fontsize=9, color="#8a2637")
    png = run_dir / "performance_report.png"
    fig.savefig(png, dpi=145, facecolor="white")
    plt.close(fig)
    status = "PARTIAL" if missing else "PASS"
    fee_bp = 10000 * summary.get("vectorbt", {}).get("fee_rate_one_way", 0)
    lines = [
        f"# Alpha Performance: {summary['factor_id']}", "",
        f"公式：`{summary.get('formula', 'UNAVAILABLE')}`。图表：![单因子绩效图](performance_report.png)", "",
        f"状态：**{status}**。可用 IC 标签：**5D RankIC**；实际组合成本：**{fee_bp:g} bp 单边**。",
        "G1 是低分组，G10 是高分组。分组曲线是周度信号的 5D 前瞻收益逐期链接的诊断图；短交易周可能发生标签重叠，不能解释为可执行分组组合净值。",
        "两段曲线各自归一化到 1。该 Yahoo 当前快照样本存在存活偏差，全段均为已暴露开发数据，**不属于独立 OOS**。",
    ]
    metrics = summary.get("vectorbt", {})
    if all(key in metrics for key in ("cagr", "sharpe_zero_rate", "max_drawdown", "modeled_fees", "order_count")):
        lines += [
            "| CAGR | Sharpe（零无风险利率） | 最大回撤 | 模拟费用 | 模拟订单 |",
            "|---:|---:|---:|---:|---:|",
            f"| {metrics['cagr']:.2%} | {metrics['sharpe_zero_rate']:.2f} | {metrics['max_drawdown']:.2%} | ${metrics['modeled_fees']:,.2f} | {metrics['order_count']:,} |",
        ]
    if missing:
        lines.append("缺少输入：" + ", ".join(missing) + "。相应曲线未绘制。")
    (run_dir / "performance_report.md").write_text("\n\n".join(lines) + "\n", encoding="utf-8")
    return {"status": status, "png": str(png), "markdown": str(run_dir / "performance_report.md")}


def main() -> int:
    """Render an existing single-Alpha run without changing factor selection."""
    import argparse

    parser = argparse.ArgumentParser(description="Render one Alpha performance run")
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args()
    result = render_performance_report(args.run_dir)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
