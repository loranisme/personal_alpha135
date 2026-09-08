# US Equity Alpha — Phase 1 / T2b

> **2026-09-08 T2b implementation:** The performance-blind converter processed
> all 886 source Alpha IDs. It admitted 6 source definitions into 6 unique
> local factors (5 direct and 1 explicit raw-close-return proxy); all 6 passed
> computation on saved real Alpaca/Tiingo samples. The other 880 sources remain
> deferred with explicit blockers, including 879 whose non-NONE BRAIN
> neutralization cannot be reproduced from the current data. No
> factor is research-eligible or automatically released. Current private
> artifact: `private/project_factor_library/v10/`.

当前设计：**v1.2（2026-09-08）**，以 BRAIN 的经济假设为来源，使用已有 Alpaca / Tiingo 能力构造项目自己的直接因子与 Proxy Alpha。主方案见 [原工程设计方案](../2026-09-06-us-equity-alpha-implementation-plan.md)，重点为第 6.3–6.8 节、T2b 和 QA37–48。

## 实际实现状态

- BRAIN 当前同步范围：886 个唯一 Alpha ID，885 个表达式可安全解析；历史全部搜索尝试仍未证实完整。
- 依赖映射：203 项（含 settings 分类依赖）；8 个直接候选、25 个派生/代理候选、109 个专门数据需求、61 个未知。字段分类不等于已经实现 Proxy。
- T2b 已为 886 个来源生成假设卡片和逐项决策：6 个来源纳入 6 个唯一候选，其中 5 个为直接定义、1 个为原始收盘价一日收益 Proxy；880 个来源保留为 `DEFERRED`。
- 6 个唯一因子均已在保存的 Alpaca SIP / Tiingo EOD 真实 4 标的、124 会话快照上计算并通过前缀不变性检查，工程失败数为 0。
- BRAIN 数值等价认证为 0；历史 PIT/真实完整股票池未验证；工程可计算不代表研究或实盘准入。
- 所有 6 个候选均为 `DIAGNOSTIC_ONLY`，没有自动进入冻结 release；转换过程未读取或继承 BRAIN Sharpe、Fitness、收益等绩效标签。

[T2b 实施报告](../2026-09-08-t2b-proxy-factor-library.md) · [首批迁移证据](../2026-09-07-batch-alpha-migration.md) · [双数据源验证](../2026-09-07-dual-provider-validation.md) · [T0–T3 实施任务](docs/phase1-implementation.md)

## 转换设计与数据边界

```text
原始 BRAIN 库 + 已验证数据能力
  → 假设卡片 → 直接/Proxy/暂缓决策 → 有限模板 → 工程验证
  → 去重后的项目候选因子库 → 原 T3–T8 工作流
```

原始公式与 settings 永久保留；目标 Proxy 使用新本地定义，明确机制、信息损失、暴露、设置和来源血缘。多个原 ID 可归并为同一本地因子。不为提高数量硬凑代理，不继承平台 Sharpe/Fitness，不自动进入冻结 release。组合、Alphalens/vectorbt、signal/execution 输出及手动交易边界不变。

当前真实样本证明了两源 OHLCV 的局部可计算性；其他衍生能力、VWAP/公司行动处理、历史基本面和分类按能力目录逐项审核。Tiingo Fundamentals、SEC 或其他数据源不是已完成接入的能力。转换器设计不会放宽原 T4–T8 对 PIT、行业约束和执行报价的要求。

当前运行配置已指向 v10 诊断候选库；模板、动态能力目录、逐项 settings 审计、状态闸门和来源血缘均已生成。v7 因批量移除中性化缺少经济依据而被审查否决，仅作为历史制品保留。该配置不构成研究准入或实盘发布，后续仍须完成 T4–T8 的 PIT、验证、组合和执行门槛。

## Commands

Create the environment from `dependency-lock.txt`, then install the local wheel
without build isolation so pip uses the already locked build dependency:

```bash
.venv/bin/python -m pip install --no-build-isolation --no-deps .
```

Run the installed CLI directly:

```bash
.venv/bin/us-equity-alpha environment-check \
  --output runs/environment/environment_check.json

.venv/bin/us-equity-alpha login-brain \
  --session-file private/brain_session.json

.venv/bin/us-equity-alpha sync-brain \
  --session-file private/brain_session.json \
  --output private/runs/brain-sync

.venv/bin/us-equity-alpha map-library \
  --registry private/runs/t1-live-import/alpha_registry.json \
  --field-catalog private/local_snapshots/wq_usa_top3000_delay1_data_fields.json \
  --output private/runs/t2-live-mapping

PYTHONPATH=src .venv/bin/python -m pytest -q
```

Credentials, account data, Alpha IDs, formulas, raw pages, and generated
registries remain under ignored `private/` or `runs/` paths.
