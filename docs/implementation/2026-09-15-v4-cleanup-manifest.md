# V4 项目清理清单

## 口径

- 清理时间：2026-09-15（Asia/Shanghai）。
- 当前权威库：`private/project_factor_library/reconstruction-v4/`。
- 只删除可再生矩阵、缓存、凭证会话、被 V4 取代的阶段脚本与运行结果。
- 保留原始 BRAIN 血缘、最新注册表/字段映射、历史转换决策与暴露记录。
- `private/` 与 `runs/` 继续由 `.gitignore` 管理；供应商原始数据、Alpha 内容和运行交付不强制写入 Git。

## 删除前锚点

- V4 manifest SHA-256：`7b7bff813f407b0e1abba9b46edbac847a53d3521b679307eb0e29be2fa4d0f5`。
- V4 工作流样例 manifest SHA-256：`503ae78a449932d58ace8a651522a6d08d31cd27c960dc349bd8e4a905379fe4`。
- 计划删除总字节：`130714741`。

## 删除项

| 路径 | 删除前字节 | 原因 |
|---|---:|---|
| `../2026-09-09-fifty-alpha-2020-2025-v1` | 12950990 | 被 V4 完整样例交付取代的旧演示/单因子/50 因子产物 |
| `../2026-09-09-fifty-alpha-2020-2025-v1.zip` | 1721484 | 被 V4 完整样例交付取代的旧演示/单因子/50 因子产物 |
| `../2026-09-09-post-t4-demo-v1` | 104004 | 被 V4 完整样例交付取代的旧演示/单因子/50 因子产物 |
| `../2026-09-09-post-t4-demo-v2` | 110152 | 被 V4 完整样例交付取代的旧演示/单因子/50 因子产物 |
| `../2026-09-09-single-alpha-strategy-v1` | 198257 | 被 V4 完整样例交付取代的旧演示/单因子/50 因子产物 |
| `../2026-09-09-single-alpha-strategy-v2` | 204733 | 被 V4 完整样例交付取代的旧演示/单因子/50 因子产物 |
| `../2026-09-11-random-factor-selections-v1` | 36113 | 被 V4 完整样例交付取代的旧演示/单因子/50 因子产物 |
| `../fifty-run.log` | 6600 | 被 V4 完整样例交付取代的旧演示/单因子/50 因子产物 |
| `.artifact_tool_v4_sample` | 10554 | 被 V4 或较新证据覆盖的缓存、会话、合成/阶段运行产物 |
| `.pytest_cache` | 30875 | 被 V4 或较新证据覆盖的缓存、会话、合成/阶段运行产物 |
| `build` | 160205 | 被 V4 或较新证据覆盖的缓存、会话、合成/阶段运行产物 |
| `private/brain_session.json` | 261 | 被 V4 或较新证据覆盖的缓存、会话、合成/阶段运行产物 |
| `private/migrated_library/20260907-v1` | 6234081 | 被 V4 或较新证据覆盖的缓存、会话、合成/阶段运行产物 |
| `private/project_factor_library/reconstruction-v1/factor_values` | 775402 | 旧因子版本的可再生数值矩阵；保留同版本 manifest、转换报告与决策血缘 |
| `private/project_factor_library/reconstruction-v2/factor_values` | 816366 | 旧因子版本的可再生数值矩阵；保留同版本 manifest、转换报告与决策血缘 |
| `private/project_factor_library/reconstruction-v3/factor_values` | 28716793 | 旧因子版本的可再生数值矩阵；保留同版本 manifest、转换报告与决策血缘 |
| `private/project_factor_library/v1/factor_values` | 898968 | 旧因子版本的可再生数值矩阵；保留同版本 manifest、转换报告与决策血缘 |
| `private/project_factor_library/v11-neutralization-engineering/factor_values` | 1135318 | 旧因子版本的可再生数值矩阵；保留同版本 manifest、转换报告与决策血缘 |
| `private/project_factor_library/v11/factor_values` | 70274 | 旧因子版本的可再生数值矩阵；保留同版本 manifest、转换报告与决策血缘 |
| `private/project_factor_library/v12-neutralization-engineering/factor_values` | 1135318 | 旧因子版本的可再生数值矩阵；保留同版本 manifest、转换报告与决策血缘 |
| `private/project_factor_library/v12/factor_values` | 70274 | 旧因子版本的可再生数值矩阵；保留同版本 manifest、转换报告与决策血缘 |
| `private/project_factor_library/v13-neutralization-engineering/factor_values` | 1135318 | 旧因子版本的可再生数值矩阵；保留同版本 manifest、转换报告与决策血缘 |
| `private/project_factor_library/v13/factor_values` | 70274 | 旧因子版本的可再生数值矩阵；保留同版本 manifest、转换报告与决策血缘 |
| `private/project_factor_library/v14-neutralization-engineering/factor_values` | 1174234 | 旧因子版本的可再生数值矩阵；保留同版本 manifest、转换报告与决策血缘 |
| `private/project_factor_library/v14/factor_values` | 70274 | 旧因子版本的可再生数值矩阵；保留同版本 manifest、转换报告与决策血缘 |
| `private/project_factor_library/v2/factor_values` | 1079560 | 旧因子版本的可再生数值矩阵；保留同版本 manifest、转换报告与决策血缘 |
| `private/project_factor_library/v3/factor_values` | 983928 | 旧因子版本的可再生数值矩阵；保留同版本 manifest、转换报告与决策血缘 |
| `private/project_factor_library/v4/factor_values` | 983928 | 旧因子版本的可再生数值矩阵；保留同版本 manifest、转换报告与决策血缘 |
| `private/project_factor_library/v5/factor_values` | 983928 | 旧因子版本的可再生数值矩阵；保留同版本 manifest、转换报告与决策血缘 |
| `private/project_factor_library/v6/factor_values` | 1000096 | 旧因子版本的可再生数值矩阵；保留同版本 manifest、转换报告与决策血缘 |
| `private/project_factor_library/v7/factor_values` | 1000096 | 旧因子版本的可再生数值矩阵；保留同版本 manifest、转换报告与决策血缘 |
| `private/project_factor_library/v8/factor_values` | 59938 | 旧因子版本的可再生数值矩阵；保留同版本 manifest、转换报告与决策血缘 |
| `private/project_factor_library/v9/factor_values` | 59938 | 旧因子版本的可再生数值矩阵；保留同版本 manifest、转换报告与决策血缘 |
| `private/runs/t0-t3-audit-synthetic-v1` | 8110228 | 被 V4 或较新证据覆盖的缓存、会话、合成/阶段运行产物 |
| `private/runs/t0-t3-audit-tiingo-v1` | 8049253 | 被 V4 或较新证据覆盖的缓存、会话、合成/阶段运行产物 |
| `private/runs/t1-import-20260907T051109Z` | 1844694 | 被 V4 或较新证据覆盖的缓存、会话、合成/阶段运行产物 |
| `private/runs/t1-import-20260907T051231Z` | 2555329 | 被 V4 或较新证据覆盖的缓存、会话、合成/阶段运行产物 |
| `private/runs/t1-import-20260907T051404Z` | 2729962 | 被 V4 或较新证据覆盖的缓存、会话、合成/阶段运行产物 |
| `private/runs/t1-live-import-20260907` | 5196631 | 被 V4 或较新证据覆盖的缓存、会话、合成/阶段运行产物 |
| `private/runs/t1-live-import-20260907-v2` | 5927175 | 被 V4 或较新证据覆盖的缓存、会话、合成/阶段运行产物 |
| `private/runs/t1-live-import-20260907-v3` | 5925402 | 被 V4 或较新证据覆盖的缓存、会话、合成/阶段运行产物 |
| `private/runs/t1-live-import-20260907-v4` | 5927646 | 被 V4 或较新证据覆盖的缓存、会话、合成/阶段运行产物 |
| `private/runs/t1-review-fix-20260907T052052Z` | 2833831 | 被 V4 或较新证据覆盖的缓存、会话、合成/阶段运行产物 |
| `private/runs/t2-live-mapping-20260907` | 1033029 | 被 V4 或较新证据覆盖的缓存、会话、合成/阶段运行产物 |
| `private/runs/t2c-neutralization-classification-synthetic-v1` | 5945 | 被 V4 或较新证据覆盖的缓存、会话、合成/阶段运行产物 |
| `private/runs/tiingo-meta-probe-20260908T111139Z` | 16541977 | 被 V4 或较新证据覆盖的缓存、会话、合成/阶段运行产物 |
| `runs` | 641 | 被 V4 或较新证据覆盖的缓存、会话、合成/阶段运行产物 |
| `scripts/package_fifty_delivery.py` | 8367 | 被 V4 完整样例入口取代的阶段性脚本 |
| `scripts/run_fifty_alpha_strategy.py` | 12112 | 被 V4 完整样例入口取代的阶段性脚本 |
| `scripts/run_post_t4_demo.py` | 3799 | 被 V4 完整样例入口取代的阶段性脚本 |
| `scripts/run_random_factor_selections.py` | 10489 | 被 V4 完整样例入口取代的阶段性脚本 |
| `scripts/run_single_alpha_strategy.py` | 9697 | 被 V4 完整样例入口取代的阶段性脚本 |

## 明确保留

- `private/project_factor_library/CURRENT_LOCAL_ALPHA_LIBRARY.json` 与 `reconstruction-v4/` 全部文件。
- `private/runs/brain-sync-20260907-live/`、`t1-live-import-20260907-v5/`、`t2-live-mapping-20260907-v2/`。
- `private/runs/expanded-500-2020-2025-v1/`、两个单 Alpha 双数据源回归输入、V4 三因子完整工作流样例。
- `private/migrated_library/20260907-v2/` 与 `private/project_factor_library/v10/factor_values/`，因为当前跨版本数值回归测试仍依赖它们。
- 核心 `src/`、`tests/`、正式配置、设计/实施文档和 V4 执行脚本。

## 删除后验证

- 清理项复核：清单中的 52 项均不存在；20 个旧因子版本仅保留审计元数据和 `CLEANED.json`。
- 保留矩阵：仅 `reconstruction-v4/factor_values/` 与跨版本回归基准 `v10/factor_values/`。
- V4 manifest：231 项逐文件 SHA-256 通过。
- V4 工作流样例 manifest：83 项逐文件大小与 SHA-256 通过。
- 定向集成测试：`33 passed in 272.00s`。
- 完整回归测试：`332 passed, 504 warnings in 268.31s`；警告来自既有依赖与兼容性路径。
- 正式配置已指向 `reconstruction-v4/project_factor_library.json`，状态为 135 个候选全部样本计算验证、研究准入为零。
- Git 暂存区未包含 `private/`、`runs/`、缓存或编译文件；凭证模式扫描未发现真实 Tiingo/Alpaca key、私钥或持久化授权头。测试哨兵 `Token do-not-persist` 为预期假值。
