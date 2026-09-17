# V5.1 Lite Yahoo Finance 长历史工程验收

## 本次运行目的

本次运行只验证 V5.1 Lite 从 135 因子目录、统一计算、顺序 Gate、冗余删除、
Composite 构建到同口径比较的完整工作流。数据来自已经保存在本地的 Yahoo
Finance 500-stock sample，日期为 2019-01-02 至 2026-09-14，共 1935 个交易日。

这不是 Alpha 有效性结论。该股票池是当前成分股快照，存在 survivorship bias，
没有 point-in-time membership 证据；样本也已经用于筛选。因此统一记录为：

- `selection_evidence=DEVELOPMENT_EXPOSED`
- `historical_pit_verified=false`
- `independent_oos_claim_allowed=false`
- `alpha_effectiveness_claim_allowed=false`
- `live_orders_submitted=0`

此前 Alpaca 2024-12 至 2026-09 的运行只属于
`SHORT_HISTORY_ENGINEERING_ACCEPTANCE_ONLY`，也不得用于判断 Alpha 有效性。

## 冻结口径

- Active Pool 数量：`8–15`
- 频率：`WEEKLY_REBALANCE_SIGNAL`
- 标签：`t close -> t+1 open -> t+6 open`
- 组合：通过因子横截面 rank 后等权合成
- 对照：旧 8-factor baseline
- 比较口径：同 provider adapter、同 universe、同 signal dates、同 forward label
- 组合诊断：Top 10%、95% invested、10bp fees、zero slippage、long-only
- 组合结果不参与因子 Gate 准入
- 观察结果后没有修改筛选阈值

Yahoo Finance 没有提供本次运行所需的交易所 VWAP；工作流明确绑定
`(high + low + close) / 3` 为 VWAP proxy，不能把它描述为真实 VWAP。

## Gate 漏斗

| 阶段 | 剩余 | 本阶段删除 | 状态 |
|---|---:|---:|---|
| Catalog versions | 135 | — | — |
| Catalog eligible | 97 | 38 | `CATALOG_INELIGIBLE` |
| Data gate passed | 97 | 0 | `REJECT_DATA` |
| Signal gate passed | 63 | 34 | `REJECT_SIGNAL` |
| Stability gate passed | 46 | 17 | `WATCHLIST_UNSTABLE` |
| Usability gate passed | 29 | 17 | `WATCHLIST_HIGH_TURNOVER` |
| Final selected | 14 | 15 | redundancy; pool-cap removal = 0 |

15 个冗余删除由已冻结规则产生：

- `SCORE_CORRELATION`: 7
- `SAME_FAMILY|SCORE_CORRELATION`: 4
- `SAME_FAMILY`: 2
- `SCORE_CORRELATION|TOP10_OVERLAP`: 2

`same mechanism` 仍然只是 warning；它没有被恢复成 hard gate。

## 同口径 Composite 比较

| 指标 | V5.1 proposed（14） | 旧 baseline（8） |
|---|---:|---:|
| Mean Rank IC 5D | 0.024626 | 0.006664 |
| ICIR 5D | 0.167586 | 0.045128 |
| Positive year ratio | 1.0000 | 0.7500 |
| Q5-Q1 5D | 0.002622 | 0.000293 |
| Weekly Top 10% turnover | 0.589871 | 0.868539 |
| Total return | 2.027229 | 0.553750 |
| Sharpe | 0.852716 | 0.358988 |
| Maximum drawdown | -0.217010 | -0.465723 |
| Modeled fees | 71,701.13 | 86,788.24 |

这些数值证明相同输入下比较管线可运行，并说明本次已暴露样本内的观察结果。
它们不能证明未来有效、独立样本外有效、PIT 可复现或能够直接实盘。

## 主要交付物

- `outputs/v5-1-yfinance-500-2019-2026-20260916/V5_1_Lite_YFinance_500_Long_History_Filter_Review.xlsx`
- `outputs/v5-1-yfinance-500-2019-2026-20260916/evidence/gate_funnel.csv`
- `outputs/v5-1-yfinance-500-2019-2026-20260916/evidence/filter_decisions.csv`
- `outputs/v5-1-yfinance-500-2019-2026-20260916/evidence/final_selected_factors.csv`
- `outputs/v5-1-yfinance-500-2019-2026-20260916/evidence/redundancy_deletions.csv`
- `outputs/v5-1-yfinance-500-2019-2026-20260916/evidence/composite_comparison.csv`
- `outputs/v5-1-yfinance-500-2019-2026-20260916/evidence/filter_manifest.json`

复现入口为 `scripts/run_v5_1_yfinance_long_history.py`；工作簿构建入口为
`scripts/build_v5_1_long_history_filter_workbook.mjs`。
