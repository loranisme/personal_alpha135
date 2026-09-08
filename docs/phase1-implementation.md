# Phase 1 — T0–T3 与假设转换器 T2b

统一依据：[原工程设计方案 v1.2](../../2026-09-06-us-equity-alpha-implementation-plan.md)（2026-09-08）。本文件任务内容摘自该主方案；后续冲突以主方案为准。

当前证据：T1 当前库 886 个唯一 ID；T2b 已处理全部来源。T2c 已实现设置级 MARKET/SECTOR/INDUSTRY/SUBINDUSTRY 中性化、PIT 分类输入和当前 Tiingo 分类适配器。93 个仅缺中性化的来源全部通过合成工程回放；真实数据口径仅新增放行 1 个 MARKET 中性化来源，v14 共 7 个来源计算通过、879 个暂缓。其余 92 个仍缺真实分类数据。所有候选仍为 `DIAGNOSTIC_ONLY`，`RESEARCH_ELIGIBLE=0`。

新版顺序：T0 → T1 → T2 数据能力 → T2b 假设/模板/项目候选库 → T3 原矩阵及合成接口。原 T4–T8 验证、组合、执行和日报保持不变。每条来源已有去向，唯一目标与源 ID 分别统计；不能合理表达的假设没有被强行改造成量价公式。

所有任务清单是目标验收，不代表全部未开始或全部已通过；实际完成状态必须由运行报告逐项证明。设计工作已授权，工程实现不得用合成数据冒充真实验证；不访问收益/留出标签，不产生下单副作用。

### T0：建立输入契约、依赖锁与可运行骨架

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

### T1：读取/导入 BRAIN 库，建立完整性与暴露登记

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

### T2：完成字段映射、数据探针与历史时点校验

**文件：** `market_data.py`、`universe.py`、`actions.py`、`tests/test_market_data.py`、`config/resolved_config.json`。

**输入/输出：** T1 因子依赖 + 现有两源样本 → `field_mapping.csv`、`data_capabilities.json`、`data_source_decision.md`、证券/行业/事件数据及覆盖报告；为 T2b 提供可用能力，不承诺补齐原字段。

- [ ] 按第 5 节逐字段列出定义、来源候选、可用时点、代理差异和费用依据；仅对必需来源做最小样本探针。
- [ ] 单独调查执行 bid/ask、feed 权限、时间戳、报价精度和历史窗口覆盖；决定可认证历史执行还是只做诊断/前瞻，不能仅凭有日线价格认定全部数据就绪。
- [ ] 建立修订、上市/退市、改代码、早收市、跨夏令时、缺失行情和拆股合成夹具；测试先运行失败。
- [ ] 实现 as-of 版本选择和计算池/交易池；未知行业、新股不足窗口和缺失量价按合同输出原因。
- [ ] 按第 6.4 节建立能力分层、依赖 DAG 和证据范围；冻结首批来源/口径。原字段缺失保留原映射状态，交给 T2b 判断经济机制，不在字段表内伪装成定义匹配。
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

**验收：** 字段可用性与局限可追溯；未来修订/复权/股票池缺口如实阻断；原字段不能直接对应时进入 T2b，不再默认永久排除。

### T2b：构建假设驱动转换器与项目候选因子库（v1.2 新增）

**计划文件：** `proxy_converter.py`、`factor_library.py`、`tests/test_proxy_converter.py`、`tests/test_factor_library.py`；私有卡片、模板实例和输出按第 6.7 节保存。

**输入/输出：** T1 去绩效投影视图 + T2 能力目录 → 假设卡片、直接/Proxy 决策、唯一目标定义、来源血缘、待处理清单及覆盖报告。当前有效实现位于 `private/project_factor_library/v14/`；v7 已被代码审查否决，不得用于下游。

- [x] 对 886 个来源建立完整去向清单；原 5 个直接本地定义兼容导入，其余来源按证据纳入 Proxy 或暂缓。
- [x] 以确定性规则生成 886 张假设卡片，区分原描述与公式推断；缺字段、缺算子、缺分类、未知机制和注释/公式冲突均保留。卡片是机器分类，尚未冒充人工语义批准。
- [x] 建立 5 个当前可用家族和有限模板；相同公式、完整定义 settings、绑定与计算池才可归并，多条来源血缘分别保留。
- [x] 实现实际候选需要的最小派生字段与算子；原数据适配层保持兼容。
- [x] 为 QA37–48 建立回归测试，并实现安全编译、状态闸门、ID/版本/血缘与归并逻辑。
- [x] 在保存的 Alpaca SIP/Tiingo EOD 真实快照上批量计算并做前缀不变性检查；所有候选仍为 `DIAGNOSTIC_ONLY`。
- [x] 构造 T3 registry 适配器；旧 5 个直接因子在相同数据上的 `factor_values` 逐值一致。
- [x] 输出分层计数、失败原因、输入哈希和制品 manifest；未浏览策略收益，未自动发布。

**验收：** 每个来源有可追踪去向；每个目标可独立解释与计算；同一代理不因多个来源重复计权；无合理代理也能正常返回待处理。未知覆盖数不得用解析数量或来源 ID 数替代。

### T2c：补齐设置级中性化

- [x] 中性化在表达式、delay 和 decay 后按当日分组去均值；缺分类保持 NaN，单成员组得到零残差。
- [x] 分类记录同时受 `effective_at` 与 `available_at` 约束；未来分类不回填历史。
- [x] MARKET 由项目当日计算池生成；SECTOR/INDUSTRY/SUBINDUSTRY 必须由带 manifest 的分类 bundle 提供。
- [x] 93 个仅缺中性化来源全部通过 220 会话合成工程回放，未读取收益标签。
- [ ] 接入真实分类：65 个 SUBINDUSTRY、26 个 INDUSTRY、1 个 SECTOR 仍阻塞。Tiingo 当前 metadata 可作 live SIC proxy，但不能证明历史 PIT 或 BRAIN 分类等价。

### T3：实现最小算子、设置和等权信号

**文件：** `operators.py`、`factors.py`、`signals.py`、`tests/test_operators.py`、`tests/test_signals.py`。

**输入/输出：** T2b 项目候选因子库的审核视图 + T2 数据 → 原有 `factor_values` 与固定尺度综合分数；保持每因子有效性、固定 K 和后续接口。仅 DIAGNOSTIC_ONLY 的记录不能伪装为正式策略输入。

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
