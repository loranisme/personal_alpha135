# Reconstruction V3 实施与审计结果

> 历史版本说明：同日后续的 `reconstruction-v4` 已补齐 Tiingo SPY 长历史，将 24 个 `COMPILED` 公式全部提升为 `COMPUTE_VERIFIED`。当前权威库见 `2026-09-14-reconstruction-v4-final.md`。

## 结论

V3 已在冻结的 886 个 BRAIN 来源上完成不读取绩效的确定性重建。当前 Alpaca/Tiingo 已验证行情字段以及经审计的 OHLCV/SPY 派生字段所能支持的自动转换范围已经处理完毕。

这仍是 `DIAGNOSTIC_ONLY` 因子库。工程计算通过不代表 Alpha 有效、研究准入、发布准入或可用于实盘。

## V2 与 V3 对比

| 指标 | V2 | V3 | 变化 |
|---|---:|---:|---:|
| 来源卡片/决策 | 886 | 886 | 0 |
| 已关联本地公式的来源 | 246 | 317 | +71 |
| 延期来源 | 640 | 569 | -71 |
| 去重本地公式 | 96 | 135 | +39 |
| 结构家族 | 80 | 99 | +19 |
| 计算验证公式 | 84 | 111 | +27 |
| 计算验证所覆盖来源 | 191 | 249 | +58 |
| 可编译但样本不足公式 | 12 | 24 | +12 |
| 工程失败公式 | 0 | 0 | 0 |
| 研究准入/发布 | 0 / 0 | 0 / 0 | 不变 |

来源守恒为 `317 + 569 = 886`。每个已重建来源只归属一个去重公式；135 个 `local_factor_id` 和 135 个表达式哈希均唯一。

## 新增 71 个来源的互斥归类

| 转换依据 | 来源数 | 口径 |
|---|---:|---|
| OHLCV 派生的 ATR/成交量字段 | 43 | 使用真实波幅、14/20 日滚动均值及原始成交量；明确不声称与 BRAIN 同名字段数值一致 |
| SPY 滚动风险字段 | 20 | 使用本地收益、SPY 收益、滚动协方差/相关性定义 beta、系统性与残差风险 |
| 转换器语法/语义纠正 | 7 | 修正翻译后 AST 的实质性判断、变长 `add` 和缺失值语义登记等保守规则 |
| 安全拆出的显式加法分量 | 1 | 只保留源公式中明确、非负加权且可独立解释的分量；其余信息登记为损失 |

新增来源全部为 `PARTIAL_INTENT`，其中 53 个来源对应的公式已计算验证，18 个来源目前对应长历史基准不足的可编译公式。

## 工程验证范围

- Tiingo 长历史覆盖 50 只证券、1572 个交易日，区间为 2019-10-01 至 2025-12-31。
- 每个原始 JSON 均校验 `.sha256`，再对齐为 OHLCV 面板。
- Tiingo T3 重放通过 94 个公式；证据路径标记为 `HASHED_LONG_HISTORY_BARS`。
- Alpaca 已保存样本 T3 重放通过 101 个公式。
- 所有 111 个 `COMPUTE_VERIFIED` 公式均通过前缀不变性检查。
- 长历史样本是当前成分股形成的开发诊断样本，存在存活偏差，不构成 PIT 或 R2 覆盖证据。

## 剩余 569 个来源

### 立即可实施，但不增加已重建来源数

24 个公式已经编译，关联 68 个已重建来源；它们全部需要长历史 `SPY`。当前 Tiingo 长历史快照没有 SPY，而含 SPY 的 Alpaca 样本只有 124 日，低于公式所需的 153–845 日。

下一步只需用同一 Tiingo 定义下载覆盖 2019-10-01 至 2025-12-31 的 SPY 原始 JSON 和 `.sha256`，然后通过 `--tiingo-benchmark-json` 重建新版本。若数据完整，这 24 个公式可从 `COMPILED` 提升为 `COMPUTE_VERIFIED`；来源仍属于现有 317 个，不应重复计数。

### 不缺字段但仍延期的 2 个来源

| 来源 ID | 阻塞 | 处理 |
|---|---|---|
| `78jQx8Jb` | `ts_regression(..., rettype=2)` 语义未经权威确认 | 取得账号内官方算子合同，增加黄金样例后再实现；不得凭经验猜测返回项 |
| `A17dlqzw` | 描述与公式冲突，且含未支持源节点 | 人工分别确认“描述假设”和“可测公式假设”；后者只能作为 `RELATED_NEW_HYPOTHESIS` 新版本 |

### 必须新增数据的 567 个来源

延期来源经常同时缺多类字段，所以下列数字是重叠覆盖数，不可相加：

| 数据包 | 涉及延期来源数 | 代表字段 |
|---|---:|---|
| PIT 基本面与股本 | 321 | assets、equity、cash flow、operating income、sales、shares outstanding、market cap |
| 分析师预期与公司指引 | 186 | EPS/EBITDA/Capex consensus、revision、guidance |
| 期权 | 150 | implied volatility、put/call OI/volume、breakeven |
| 新闻与社交 | 114 | sentiment、buzz、event novelty/impact、social value/volume |
| 专有评分 | 15 | rank derivative、composite/fscore 等不可从字段名复刻的模型输出 |
| 关系与内部人 | 12 | customer/partner relationship、insider scores |

实施顺序固定为：先建立带 `effective_at/as_of/reported_at` 的 PIT 字段合同和覆盖审计，再重新运行字段匹配；只对完整表达所需机制的数据包开放转换。不得使用价格动量替代盈利预期、期权、情绪、关系或专有评分。

新增数据的优先顺序建议为：

1. 补 Tiingo 同口径 SPY 长历史，完成现有 317 个来源的工程验证闭环。
2. 接入 PIT 基本面与拆分调整股本，优先处理覆盖面最大的基本面/市值族。
3. 接入分析师预期与指引，并冻结公告可用时点和修订历史。
4. 期权、新闻/社交、关系与专有评分分别作为独立数据项目；未取得可回溯历史和授权前保持 `DEFERRED`。

每次新增数据或算子语义都会生成新的不可变重建版本，不修改 V3。

## 产物与证据

- `private/project_factor_library/reconstruction-v3/conversion_report.json`：权威计数和验证范围。
- `private/project_factor_library/reconstruction-v3/source_reconstruction_index.csv`：886 个来源逐项结果。
- `private/project_factor_library/reconstruction-v3/local_formulas.csv`：135 个去重公式。
- `private/project_factor_library/reconstruction-v3/project_factor_library.json`：完整血缘、语义损失与验证状态。
- `private/project_factor_library/reconstruction-v3/manifest.json`：205 个产物文件的内容哈希。

V2 的 164 项 manifest 均通过重新计算，V3 的 205 项 manifest 也全部通过；V3 目录未发现凭证模式字符串。
