# V5.1 Lite Library-First 使用说明

V5.1 将个人 Alpha Library 保持为项目核心。Alpha Filter 只在需要重新审查
Active Pool 时运行，不进入每日选股路径。V4 基础库保持只读，后续新因子进入
只追加的 Expansion Registry。

## 1. 登记扩展因子

扩展定义必须包含经济假设、机制、字段、provider、设置、方向和血缘。公式发生
变化时必须提高 `factor_version`，同一版本不能覆盖。

```bash
PYTHONPATH=src .venv/bin/python -m us_equity_alpha.cli register-expansion \
  --definition private/expansion_candidates/candidate.json \
  --registry private/expansion_registry
```

登记不会自动赋予 `COMPUTE_VERIFIED`，也不会运行筛选或改变 Active Pool。

## 2. 构建统一 Catalog

先使用现有 `build-alpha-catalog` 生成 V4 Catalog，再把它与 Expansion Registry
合并。Catalog 会保留所有版本，包括尚未验证、机制未分类或 provider 不兼容的
版本，并在 `catalog_exclusion_reasons` 中记录原因。

```bash
PYTHONPATH=src .venv/bin/python -m us_equity_alpha.cli build-unified-catalog \
  --library private/project_factor_library/reconstruction-v4/project_factor_library.json \
  --base-catalog private/runs/v5-real-20260915/catalog/alpha_catalog.csv \
  --expansion-registry private/expansion_registry \
  --provider tiingo_eod \
  --output private/runs/v5-1-catalog-tiingo
```

## 3. 按需运行 Alpha Filter

市场数据目录使用宽表 Parquet，每个字段一个文件，例如 `open.parquet`、
`close.parquet` 和 `volume.parquet`。列为 `security_id`，索引为交易日。`SPY`
只可作为 benchmark 列，不得进入 universe CSV。

```bash
PYTHONPATH=src .venv/bin/python -m us_equity_alpha.cli run-alpha-filter \
  --unified-catalog private/runs/v5-1-catalog-tiingo/unified_alpha_catalog.parquet \
  --library private/project_factor_library/reconstruction-v4/project_factor_library.json \
  --v4-manifest private/project_factor_library/reconstruction-v4/manifest.json \
  --expansion-registry private/expansion_registry \
  --universe private/runs/current-universe/eligible_universe.csv \
  --market-data private/runs/tiingo-history/panels \
  --provider tiingo_eod \
  --policy config/v5_1_alpha_filter.json \
  --output private/runs/v5-1-filter-tiingo
```

筛选使用周频 signal session，唯一主标签为：

```text
t close freeze signal -> t+1 open entry -> t+6 open exit
```

输出中的历史结论固定标记为 `DEVELOPMENT_EXPOSED`、
`historical_pit_verified=false` 和 `survivorship_bias=true`。若不足五个因子通过，
运行仍会完整输出诊断，但 proposal 标记
`COMPOSITE_BLOCKED_INSUFFICIENT_ALPHA`，不会降低阈值凑数。

## 4. 生成人工审查工作簿

```bash
node scripts/build_v5_1_alpha_filter_workbook.mjs \
  private/runs/v5-1-filter-tiingo \
  private/runs/v5-1-filter-tiingo/alpha_filter_review.xlsx
```

工作簿只包含 `Summary`、`Alpha Diagnostics`、`Selected Pool`、
`Composite Comparison` 和 `Audit`。在 `Selected Pool` 中填写 `APPROVE` 或
`REJECT` 及备注，再导出为具有以下列的 CSV：

```text
factor_id,factor_version,decision,comment
```

`Composite Comparison` 初始为 `NOT_RUN`。只有冻结筛选决策，并确认新旧组合的
provider、universe、signal sessions 和 forward label 完全一致后，才运行可选的
Alphalens/vectorbt 比较。组合收益、Sharpe 和回撤不参与因子准入。

## 5. 冻结 Active Pool

```bash
PYTHONPATH=src .venv/bin/python -m us_equity_alpha.cli freeze-filtered-pool \
  --proposal private/runs/v5-1-filter-tiingo/proposed_active_pool.json \
  --decisions private/runs/v5-1-filter-tiingo/review_decisions.csv \
  --output private/active_pools/v5-1-pool-001
```

冻结只接受 proposal 内的因子，批准后必须保留 5–12 个。权重固定为等权，文件
不可覆盖，并记录 V4 manifest、Expansion Registry、Catalog、universe 和 policy
哈希。

## 6. 每日选股

每日路径保持不变，只加载一个冻结 Active Pool，不重新评估全库：

```bash
PYTHONPATH=src .venv/bin/python -m us_equity_alpha.cli daily-select \
  --library private/project_factor_library/reconstruction-v4/project_factor_library.json \
  --expansion-registry private/expansion_registry \
  --active-pool private/active_pools/v5-1-pool-001/active_pool.json \
  --universe private/runs/current-universe/eligible_universe.csv \
  --market-data private/runs/current-panels \
  --policy config/personal_selection_policy.json \
  --as-of 2026-09-16 \
  --output private/runs/daily-2026-09-16 \
  --paper-log private/forward_paper.sqlite
```

`--execution-helper` 是可选的手工调仓辅助。所有核心命令固定
`live_orders_submitted=0`，不会调用 broker 下单、撤单或改单接口。私有行情、
凭证、Expansion Registry 和运行产物继续保存在 Git 忽略目录。
