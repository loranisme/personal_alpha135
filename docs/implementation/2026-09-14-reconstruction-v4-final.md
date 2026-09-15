# Reconstruction V4 最终本地 Alpha Library

## 最终状态

`reconstruction-v4` 是当前项目保留的最终本地 Alpha Library。它在 V3 的冻结公式和来源血缘上补入同口径 Tiingo SPY 长历史，将原先 24 个样本不足公式全部完成工程计算验证。

| 指标 | V3 | V4 |
|---|---:|---:|
| BRAIN 来源 | 886 | 886 |
| 已重建来源 | 317 | 317 |
| 延期来源 | 569 | 569 |
| 去重公式 | 135 | 135 |
| 计算验证公式 | 111 | 135 |
| 计算验证覆盖来源 | 249 | 317 |
| 可编译但样本不足 | 24 | 0 |
| 工程失败 | 0 | 0 |
| 研究准入/发布 | 0 / 0 | 0 / 0 |

V3 中 24 个 `COMPILED` 公式均在 V4 的 Tiingo 长历史上得到 `PASS`。公式表达式、来源集合、经济语义和延期决定均未因补数据发生变化。

## SPY 证据与隔离口径

- 数据源：Tiingo EOD。
- 日期：2019-10-01 至 2025-12-31。
- 交易日：1572。
- 原始文件 SHA-256：`f8cd66e888cc5b982a949621e3933846c0d66d8bec82a144608c308dac014f2a`。
- SPY 仅生成 `benchmark_returns`，不会进入证券 OHLCV 面板、横截面排名或选股输出。
- Tiingo 工程样本仍为 50 只证券；SPY 是独立基准序列，不计入证券覆盖数。

下载器在进程内隐藏读取 token，只保存 SPY 原始响应、SHA-256 和非敏感证据元数据。产物及 V4 未发现凭证模式字符串。

## 稳定身份合同

V4 修正了 `local_factor_id` 的身份输入：公式、设置、字段定义及 provider 绑定参与哈希；验证样本的日期、行数和覆盖数量不参与。以后增加历史长度或证券数量时，相同本地定义不会仅因样本证据变化而产生新 Alpha ID。

## 工程验证

- 135 个公式全部为 `COMPUTE_VERIFIED`。
- 317 个已重建来源全部至少关联一个计算验证公式。
- T3 普通输入重放：Alpaca 101 个公式通过；Tiingo 长历史 120 个公式通过。
- 101 个 Alpaca 矩阵各含 3 个证券列；120 个 Tiingo 矩阵各含 50 个证券列；全部矩阵无 SPY 列。
- V4 manifest 共 231 项，逐项重新计算哈希通过。
- 仓库完整回归测试：`323 passed`，0 failed；504 条为既有依赖与兼容性警告。
- `conversion_report.json` SHA-256：`a3f59171957f7cc8debce6925dd343d4820d8b70b4ab21411c5aaf13fa3f2e69`。
- `manifest.json` SHA-256：`7b7bff813f407b0e1abba9b46edbac847a53d3521b679307eb0e29be2fa4d0f5`。

## 使用边界

V4 完成的是当前 317 个本地重建来源的工程计算闭环，不是 Alpha 有效性结论。所有 135 个公式继续为 `DIAGNOSTIC_ONLY`；50 只当前股票形成的历史样本存在存活偏差，不满足 PIT、至少 500 只可排名证券、正式 Research Gate 或 Release Gate。

569 个延期来源继续保留在逐来源索引中。它们需要基本面、分析师预期、期权、新闻/社交、关系或专有评分数据，或需要单独确认算子/描述语义。本次没有用价格代理冒充这些缺失机制。

## 权威入口

- 当前库指针：`private/project_factor_library/CURRENT_LOCAL_ALPHA_LIBRARY.json`
- 完整因子库：`private/project_factor_library/reconstruction-v4/project_factor_library.json`
- 来源索引：`private/project_factor_library/reconstruction-v4/source_reconstruction_index.csv`
- 公式清单：`private/project_factor_library/reconstruction-v4/local_formulas.csv`
- 转换报告：`private/project_factor_library/reconstruction-v4/conversion_report.json`
- 完整性清单：`private/project_factor_library/reconstruction-v4/manifest.json`
