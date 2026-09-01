# Economic Role 基金轮动第一阶段实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 严格实现 Economic Role v4.3 的最小可验证链路，完成 G0/G1b/G1/G2 的 2016–2020 同口径回测与结论。

**Architecture:** 保留 correlation 旧策略、legacy pipeline、执行引擎和 R11/R34/R39/R76 原实现不变；新增独立 `economic_role_rotation` 策略包。通过 snapshot/PIT admission 为 Role 策略提供包含合法 QDII 的有效代码池，策略本身只读取 CausalDataView，并输出 Role 专属可审计 artifact。

**Tech Stack:** Python 3.12、pandas、Pydantic、pytest、现有 FundRotationBacktestRunner/BatchService、Lance snapshot。

**Spec:** 用户提供的 `Economic Role 基金轮动实验设计 v4.3` 及其审查意见附件。

## Global Constraints

- `history_quality_lookback_weeks=52`，`min_valid_weeks=20`，评分实际使用连续 5 个周收益和 4 周窗口。
- `warmup_trade_days=264`，Role refresh 每 26 个 weekly evaluation index。
- 流动性复用 G0：20 个交易日、至少 15 个 `amount > 0`，连续 5 个非正值为 hard failure。
- 五类 Role 固定；AMBIGUOUS 不进入任何 Role、不参与评分，并记录排除原因。
- Fixed Representative 按 manifest 顺序；Dynamic Representative 按可用候选过滤后 Tier→ADV→代码确定性 tie-break；普通周 lock，hard failure 只切换执行代表、不重建 frozen members、不重置 refresh clock。
- G1b 的 score subject 是 frozen Role Members；G1/G2 的 score subject 是当前 Fixed Representative；execution subject 始终是当前有效代表。
- downstream 顺序固定为 R34→R39→R76，防御资产 `511010.SH`；不复制这些算法。
- 新策略使用独立 strategy ID；注册表回归断言只能追加 ID，不能删除/放宽既有断言。
- 2016–2020 只在同一 snapshot、calendar、execution contract 和 PIT evidence 下比较；若数据不足或名称 PIT 未验证，不缩短窗口并明确降级为探索性结论。

---

### Task 1: 冻结 G0 基线与新策略契约

**Files:**
- Create: `docs/superpowers/plans/2026-09-01-economic-role-rotation.md`
- Test: `agent/tests/fund_rotation/test_economic_role_rotation.py`

- [ ] 写出分类、评分、生命周期、downstream identity 和 catalog registration 的失败测试。
- [ ] 运行 focused tests，确认因新模块不存在而失败。
- [ ] 保存 G0 当前 catalog/runner 行为证据，作为后续 parity 对照。

### Task 2: Role classifier 与 hash

**Files:**
- Create: `agent/backtest/fund_rotation/strategies/economic_role_rotation/roles.py`
- Create: `agent/tests/fund_rotation/test_economic_role_rotation.py`

- [ ] 实现规范化、五类 include/exclude/tier 规则、MATCHED/UNCLASSIFIED/AMBIGUOUS/EMPTY_NAME 状态。
- [ ] 对冲突、黄金股、海外主题、信用债/转债和“创业板50/创业板”顺序写测试。
- [ ] 实现 canonical JSON SHA-256 `role_rule_hash`。

### Task 3: 独立 config、Momentum 与代表生命周期

**Files:**
- Create: `agent/backtest/fund_rotation/strategies/economic_role_rotation/config.py`
- Create: `agent/backtest/fund_rotation/strategies/economic_role_rotation/strategy.py`
- Modify: `agent/backtest/fund_rotation/strategies/registry.py`

- [ ] 先用 synthetic data 写失败测试，覆盖 Role Return、M0/M1、score metadata、fixed/dynamic selection、lock、hard failure、refresh clock 和 G1b frozen members。
- [ ] 以最小代码实现两种 Role strategy 变体（Fixed/Fixed-Members 与 Dynamic/Representative score），注册为 G1b/G1/G2 可配置变体并保留独立 ID。
- [ ] 仅复用 R11 数学与 R34/R39/R76 函数，artifact scope 改为 `ECONOMIC_ROLE`。

### Task 4: Role-aware PIT admission、identity 与 artifact

**Files:**
- Modify: `agent/src/stockpred/fund_rotation/data_snapshot.py`
- Modify: `agent/backtest/fund_rotation/runner.py`
- Modify: `agent/src/stockpred/fund_rotation/batch_child_runtime.py`
- Add tests under `agent/tests/fund_rotation/`

- [ ] snapshot 同时保存旧 ETF pool 与包含 QDII 的 Role pool；根据策略 ID 选择，不改变 G0。
- [ ] 在运行开始前冻结每个 signal date 的 effective universe/role assignment evidence，并写入 resolved spec/artifact；身份 hash 不得只在回测后生成。
- [ ] 补齐 `history_quality_lookback_weeks == correlation_lookback_weeks == 52` 断言与 evidence level/name history status。

### Task 5: downstream/G0 parity 与完整回归

**Files:**
- Modify only files required by tests/artifact publication.

- [ ] 验证相同 base targets 的 R34/R39/R76 输出、cash、reason codes 一致。
- [ ] 验证 G0 旧 strategy 的 decisions/orders/fills/equity/metrics 不变，允许 framework hash 变化。
- [ ] 验证完整运行 deterministic restart，且 artifact 无 `cluster_id/clusters` 伪造语义。

### Task 6: 2016–2020 同口径 Phase A 回测

**Files:**
- Create: `experiments/economic_role_rotation/2016_2020/experiment_spec.json`
- Create: `experiments/economic_role_rotation/2016_2020/analysis_spec.json`
- Create: `experiments/economic_role_rotation/2016_2020/report.md`

- [ ] 预冻结 snapshot、PIT/名称质量、calendar、execution、warmup 和 comparison contract。
- [ ] 通过正式 StrategyBatch 路径运行 G0/G1b/G1/G2；HTTP 202/进程启动不视为完成，必须读取 terminal child state 和 manifest。
- [ ] 记录 annual return、total return、Sharpe、MDD、volatility、turnover、slot weeks、invested ratio、Role availability、selection/switch/contribution，并按年/阶段比较。
- [ ] 应用 G0/G1b/G1/G2 比较语义；若名称/PIT 不达标，结论只标记 `LEVEL_1_EXPLORATORY`。

### Task 7: Review 与完成审计

- [ ] 运行 focused、fund-rotation regression 和 2016–2020 batch verification。
- [ ] 复查 P0/P1：PIT/look-ahead、execution、identity、comparability、空样本、NaN/inf、tie/determinism、旧策略隔离。
- [ ] 只有全部必需证据齐全时，输出中文分析结论并冻结候选；否则输出具体 data-gap/blocked report。

