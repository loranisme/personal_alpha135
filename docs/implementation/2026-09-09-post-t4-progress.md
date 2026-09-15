# T5–T8 工程实施记录（2026-09-09）

主依据：outputs/2026-09-06-us-equity-alpha-implementation-plan.md。正式组合参数按用户决定保持待定；示例参数仅用于合成测试。

| 部分 | 本次交付 | 验收边界 |
|---|---|---|
| T4 防过拟合前置 | SQLite 试验账本、试验预算、标签成熟期 purge、留出读取前原子占用、失败消耗留出、重放审计 | 不能改变 provider/lineage 名称重开相同证券日期标签；不认证历史从未看过 |
| T5/T5a | 固定目标权重、整数股意向、报价/账户/时点/限价/现金/未结订单检查、成交去重 | 不自动下单；成交导入不会刷新券商账户时间 |
| T6 | 实际 Alphalens 诊断、vectorbt 订单及事件回测适配器 | 事件支持初始持仓、整数拆股、分红应收/到账、退市终值、外部现金流 TWR；不支持碎股现金补偿、税、并购等 |
| T7 | 三表 signal 与四表 execution，JSON/CSV/XLSX、哈希和不可变目录 | 被阻断时批准数量归零，清除假设成交后的现金值，原始意图单独审计 |
| T8 | 合成候选冻结、文件完整性检查、重放比较、前瞻事件账本 | 真实发布认证及 OBSERVED 实时采集仍未实现；正式 PAPER/LIVE 不可启用 |
| CLI | validate/freeze/signal/execution-preview/reconcile 文件编排 | 正式验证在读取标签前阻断；仅显式合成流程可运行 |

## 实测结果

`PYTHONPATH=src .venv/bin/python -m pytest -q --disable-warnings`：**283 passed**，504 条依赖弃用/数值诊断警告，9.30 秒。

`PYTHONPATH=src .venv/bin/python scripts/run_post_t4_demo.py --output ../2026-09-09-post-t4-demo-v2`：**PASS**。

交付目录：`outputs/2026-09-09-post-t4-demo-v2`。实际调用 Alphalens 处理 170 个合成标签；vectorbt 回调核对拆股与分红期间权益保持 1100，主动订单数为 0。执行报告状态 BLOCKED、正式协议状态 BLOCKED_CONFIG 均为预期。CLI 端到端测试另覆盖 freeze→signal→execution-preview 与文件哈希、CSV/XLSX 一致性。

## 尚未完成的正式能力

这不是 T0–T8 全部验收。真实历史评价编排/认证、真实候选发布器和可核验 OBSERVED 采集仍需实现；需要正式参数、足够历史、PIT/历史股票池及可匹配执行策略的数据才能验证。当前不会把 2024 小样本、Proxy 编译成功或 BRAIN 原指标当作收益证据。前端因子库口径和后续防过拟合设计保持不变。

`backtest.py` 的轻量订单接口仍主动拒绝企业事件；企业事件须显式使用 `event_backtest.run_event_backtest`，返回回调快照作为现金/持仓/NAV 权威结果，不使用 vectorbt 原生仅订单重建的净值。
