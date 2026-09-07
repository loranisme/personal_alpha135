> **2026-09-07 audit correction:** Phase 1 is PARTIAL. Five definitions executed on synthetic data only; zero real-data or BRAIN-equivalent Alpha conversions are verified. T2 now includes settings-derived sector: 203 dependencies (202 expression fields), with 8 direct candidates, 25 proxies, 109 specialist requirements and 61 unknown. Previous counts below are historical and superseded. Full BRAIN settings are now rejected by the local evaluator; explicit local variants must record exclusions. See [current audit](../../2026-09-07-alpha-conversion-audit.md) for the current findings.

# Phase 1 implementation — T0 to T3 only

Implementation status as of 2026-09-07: T0 complete; T1 current-library sync
complete for the API-declared 886 records, while search-history completeness
remains unknown; T2 mapping complete but Alpaca/Tiingo market samples are
`NOT_RUN_AUTH_REQUIRED`; T3 minimal direct-candidate subset complete for five
Alpha definitions with synthetic execution only and numeric parity unverified.

Authority: ../2026-09-06-us-equity-alpha-implementation-plan.md (v1.1). User requests real Alpha mapping and source selection first.

Global constraints: Read-only BRAIN metadata; no simulation, submission, deletion, trading, PnL fetching or performance-driven factor selection. Credentials stay user-controlled and never printed/logged/committed. All private Alpha expressions, IDs, original snapshots, field catalogs and account data stay under private/ or runs/ (Git ignored). Synthetic fixtures prove engineering only. Missing definitions or data remain UNKNOWN/PARTIAL; no provider can be declared complete from field names alone. Historical PIT, revisions, universe, corporate actions and execution quote coverage must be distinguished. No T4-T8 strategy validation or protected holdout access this phase. Python 3.12, local venv, pandas/NumPy/SciPy/requests/pytest/Parquet/openpyxl plus Alphalens Reloaded/vectorbt compatibility probe. First write behavioral failing tests then minimal implementation.

Rulings: This is a new isolated project on implementation/phase1; no existing repository is modified. T0 release validation rejects missing values but T1 metadata import and T2 mapping work without unchosen strategy parameters. No actual data credentials should block synthetic engineering. Later-phase CLI contracts must reject as NOT_IMPLEMENTED. Phase completion reports engineering and actual data coverage separately.

## Task 1: T0 —建立输入契约、依赖锁与可运行骨架

**文件：** `pyproject.toml`、`dependency-lock.txt`、`src/us_equity_alpha/contracts.py`、`src/us_equity_alpha/cli.py`、`tests/test_contracts.py`、`.gitignore`。

**输入/输出：** 本文第 3、12 节 → 可校验配置、错误码和 CLI；不需要真实账号即可执行。

- [ ] 将阶段必填字段写入 `validate_config`；缺少配置返回 `BLOCKED_CONFIG` 并列出字段，禁止用示例值兜底。
- [ ] 添加下方测试后运行 `python -m pytest tests/test_contracts.py -q`，确认缺失实现时测试失败。
- [ ] 在隔离环境验证 pandas/NumPy、Alphalens、vectorbt、Parquet 和 Excel 的最小导入/运行兼容性，记录实际版本并生成锁文件；不沿用未验证的旧版本组合。
- [ ] 实现 CLI 参数解析和阶段阻断，检查私有目录忽略规则。
- [ ] 重跑测试，保存 `environment_check.json`；仓库可用时提交本任务代码与合成测试。

```python
from us_equity_alpha.contracts import validate_config

def test_missing_release_config_blocks():
    errors = validate_config({}, stage="release")
    assert "BLOCKED_CONFIG" in errors
    assert "MISSING_N" in errors
    assert "MISSING_EVALUATION_PROTOCOL" in errors
```

**验收：** 缺输入被准确阻断；锁文件可复建最小环境；未发出任何真实平台或交易请求。


## Task 2: T1 —读取/导入 BRAIN 库，建立完整性与暴露登记

**文件：** `brain_sync.py`、`registry.py`、`tests/test_brain_sync.py`、`tests/fixtures/brain_export.json`。

**输入/输出：** 用户认证或实际导出 → 原始快照、`alpha_registry`、`exposure_ledger`、同步完整性报告。当前账号权限若未解决，可先实现文件入口和合成网络测试。

- [ ] 在账号内核实接口文档和允许的读取范围，记录分页和响应合同；复用现有工具前确认没有模拟/提交副作用。
- [ ] 建立内部标准化导入 envelope：`schema_version=1, records, sync_scope_complete, search_history_complete, source`。records 保留原始对象，任何归一化失败不得丢掉原记录。
- [ ] 合成导出夹具包含 3 条记录：A 一次、A 重复一次、B 一次；B 的平台检查是 FAIL，两个 completeness 字段均为 false。只在计数中去重 A，B 必须保留。
- [ ] 测试分页重复、第二页限流后恢复、页失败、未知总数、账号验证中断和导入 settings 缺失；认证日志断言不含凭证。
- [ ] 实现限次重试、断点、原始快照与归一化；只有被证实的范围取完才设置 `sync_scope_complete=true`。
- [ ] 执行一次真实只读同步或真实文件导入，输出实际计数/缺项；若失败，保存 `PARTIAL`，不换成合成结果。

```python
import json
from pathlib import Path
from us_equity_alpha.registry import import_alpha_files

def test_failed_alpha_is_kept_and_history_is_not_invented(tmp_path):
    source = Path("tests/fixtures/brain_export.json")
    result = import_alpha_files([source], tmp_path)
    assert result["unique_alpha_count"] == 2
    assert result["duplicate_record_count"] == 1
    assert result["platform_failed_count"] == 1
    assert result["search_history_complete"] is False
```

**运行：** `python -m pytest tests/test_brain_sync.py -q`。

**验收：** 每条可访问实验有去向；不隐藏失败；能给出未知搜索历史；终端认证由用户控制。


## Task 3: T2 —完成字段映射、数据探针与历史时点校验

**文件：** `market_data.py`、`universe.py`、`actions.py`、`tests/test_market_data.py`、`config/resolved_config.json`。

**输入/输出：** T1 因子依赖 → `field_mapping.csv`、`data_source_decision.md`、证券/行业/事件数据、覆盖报告。

- [ ] 按第 5 节逐字段列出定义、来源候选、可用时点、代理差异和费用依据；仅对必需来源做最小样本探针。
- [ ] 单独调查执行 bid/ask、feed 权限、时间戳、报价精度和历史窗口覆盖；决定可认证历史执行还是只做诊断/前瞻，不能仅凭有日线价格认定全部数据就绪。
- [ ] 建立修订、上市/退市、改代码、早收市、跨夏令时、缺失行情和拆股合成夹具；测试先运行失败。
- [ ] 实现 as-of 版本选择和计算池/交易池；未知行业、新股不足窗口和缺失量价按合同输出原因。
- [ ] 决定并冻结首批供应商和可用因子范围；所有代理使用新本地因子 ID。
- [ ] 用真实小样本核查单位、公司行动前后和财报披露；只产出数据/语义结果，不提前浏览 Validation/Holdout 的策略绩效。
- [ ] 保存原始快照、覆盖、映射证据与哈希，运行全部数据测试。

```python
import pandas as pd
from us_equity_alpha.market_data import load_asof

def test_future_revision_is_not_visible(tmp_path):
    path = tmp_path / "revisions.parquet"
    pd.DataFrame({
        "security_id": ["S1", "S1"],
        "field": ["revenue", "revenue"],
        "value": [100.0, 120.0],
        "event_time": pd.to_datetime(["2025-03-31", "2025-03-31"], utc=True),
        "available_at": pd.to_datetime(["2025-05-01", "2025-08-01"], utc=True),
        "revision_id": ["v1", "v2"],
        "source": ["synthetic", "synthetic"],
        "unit": ["USD", "USD"],
        "adjustment": ["none", "none"],
        "fetched_at": pd.to_datetime(["2026-09-06", "2026-09-06"], utc=True),
    }).to_parquet(path)
    result = load_asof(path, pd.Timestamp("2025-06-01", tz="UTC"), ["revenue"])
    assert result["value"].tolist() == [100.0]
```

**运行：** `python -m pytest tests/test_market_data.py -q`。

**验收：** 字段可用性与局限可追溯；没有未来修订、复权或股票池泄漏；不可满足的因子被明确暂缓。


## Task 4: T3 —实现最小算子、设置和等权信号

**文件：** `operators.py`、`factors.py`、`signals.py`、`tests/test_operators.py`、`tests/test_signals.py`。

**输入/输出：** T1/T2 审核后的因子和数据 → `factor_values` 与固定尺度综合分数；保留每因子有效性。

- [ ] 为真实入选操作符建立短序列手算值，先覆盖价格差/收益率、并列排名、NaN、窗口边界、分组和 Decay。
- [ ] 定义 `price_delta(price: DataFrame, window: int) -> DataFrame`，并只实现真实依赖的其他算子；每项有文档定义及对应测试。
- [ ] 以有序配置实现表达式和 settings；单独验证 Delay 日期表，不重复延迟或重复中性化。
- [ ] 实现固定 K 合成与覆盖检查；登记经济假设决定的临时方向、家族与代表；依赖 Development 收益证据的最终选择在 T6 中执行并保存依据。
- [ ] 做前缀不变性检查：给定同一历史 as-of 快照，添加未来数据后，过去算分必须不变；未来公司行动夹具亦必须通过。
- [ ] 运行算子和信号测试，输出语义匹配报告；平台数值未比对时保持 UNVERIFIED。

```python
import pandas as pd
from us_equity_alpha.operators import price_delta

def test_price_difference_is_not_percentage_return():
    prices = pd.DataFrame({"A": [100.0, 110.0], "B": [5.0, 6.0]})
    delta = price_delta(prices, window=1).iloc[-1]
    returns = prices.iloc[-1] / prices.iloc[0] - 1
    assert delta["A"] == 10.0 and delta["B"] == 1.0
    assert returns["A"] < returns["B"]
```

**运行：** `python -m pytest tests/test_operators.py tests/test_signals.py -q`。

**验收：** 本地函数是已登记的公式与设置；未知定义不被猜测；缺任一合成因子不产生伪完整分数。
