# V5.1 PRE2025_POOL 历史 Holdout Replay

## 证据状态

本次运行先冻结既有 14-factor full-history Champion，再使用完全不变的 V5.1
Filter，仅允许 2019-01-02 至 2024-12-31 数据参与筛选。生成并冻结
`PRE2025_POOL` 后，才一次性读取 2025–2026 标签。

本次证据属于 `RETROSPECTIVE_HOLDOUT_REPLAY`，不能记为独立 OOS。原因是
2025–2026 已经在此前 full-history 工程运行中出现过。当前 500-stock universe
来自当前成分股快照，存在 survivorship bias，也没有历史 PIT membership 证明。
Replay 后没有修改 Gate、阈值、公式、方向或 pool membership。

## 冻结身份

| 产物 | 值 |
|---|---|
| Full-history Champion | 14 factors |
| Champion content hash | `28037e743fe419bd4116383d5e93e28608b24b1338ea316bdef34c1bc21450ab` |
| Filter config hash | `f65e5ee4256167e95d6af9fb13d83ad081a428757980c79d8270c6540f0845da` |
| PRE2025_POOL | 15 factors |
| PRE2025_POOL content hash | `d4b6eec5491894387700768ad63ea2e681dc63eea9e43f5a20c5892e950f5387` |
| Champion / PRE2025 overlap | 10 factors |
| Jaccard overlap | 0.5263 |
| Holdout access commit hash | `8153950f5194bb4320941cb1d991262af056cc3336972d7fad7be8884f5620e3` |
| Replay result content hash | `5706af2a62c34d2d6a53964734bcf802e914f1ffbe7e3f46227be9f363e2030f` |

## PRE2025 Filter 漏斗

| 阶段 | 剩余 | 本阶段删除 |
|---|---:|---:|
| Catalog versions | 135 | — |
| Catalog eligible | 97 | 38 |
| Data Gate | 95 | 2 |
| Signal Gate | 73 | 22 |
| Stability Gate | 67 | 6 |
| Usability Gate | 44 | 23 |
| Redundancy + 15-factor cap | 15 | 29 |

最后 29 个删除包括 22 个 redundancy removal 和 7 个 pool-cap removal。

## 固定 Pool Replay 结果

| 指标 | PRE2025 development | 2025–2026 replay |
|---|---:|---:|
| Mean Rank IC 5D | 0.026431 | 0.004170 |
| ICIR 5D | 0.168896 | 0.029281 |
| Positive-year ratio | 1.00 | 0.50 |
| Q5-Q1 5D | 0.002272 | -0.000328 |
| Weekly Top 10% turnover | 0.6397 | 0.6132 |
| Total return | 121.81% | 9.88% |
| Sharpe | 0.833 | 0.171 |
| Maximum drawdown | -20.09% | -22.90% |
| Modeled fees | $45,108.57 | $10,754.01 |

Development 和 replay 的总收益、费用覆盖不同长度的期间，不能当作同期限结果直接
比较。组合诊断沿用 V5.1 原口径：weekly signal、Top 10%、95% invested、
long-only、10 bp fees、zero modeled slippage。

## 时间稳定性

| 期间 | 有效周度标签 | Mean Rank IC 5D |
|---|---:|---:|
| 2025 | 52 | -0.004367 |
| 2026 至 2026-09-14 | 35 | 0.016853 |

Composite replay 共包含 87 个有效 IC 观察。在固定的 15 个因子中，8 个因子的
replay mean IC 为正，8 个因子的 Q5-Q1 spread 为正；同时满足两项的有 6 个。

## 结果解释

相较于已暴露的 PRE2025 development sample，replay 表现明显衰减。Composite IC
仍略为正，但 ICIR 接近零，Q5-Q1 为负，2025 IC 为负；组合 Sharpe 较低，最大回撤
与更长的 development period 接近。因此，正的 portfolio return 本身不能证明稳定的
Alpha selection power。

本结果不会改变 `PRE2025_POOL` 或 full-history Champion，也不授予
`HOLDOUT_PASS`、`FORWARD_PASS`、模拟盘自动下单或实盘授权。下一次设计审查只需决定：
V5.2 Forward Paper 使用 frozen full-history Champion、frozen PRE2025_POOL，还是两者
并行作为 shadow candidates。

## 复现入口

入口脚本为 `scripts/run_v5_1_historical_holdout_replay.py`，必须依次调用：

1. `freeze-champion`
2. `build-pre2025`
3. `replay`

`replay` 会在读取标签前以独占方式写入 `holdout_access_commit.json`；已有该文件时，
第二次 replay 会被拒绝。完整结果由 `delivery_manifest.json` 逐文件绑定哈希。
