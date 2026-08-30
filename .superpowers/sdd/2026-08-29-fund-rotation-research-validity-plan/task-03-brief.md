# Task 3 实现简报：容量感知代表解锁与 fallback challenger

## 目标

严格执行计划 Task 3：在不修改 R39、公共 Runner 或 execution ledger 语义的前提下，新增一个可审计、确定性的容量感知代表选择纯函数，以及 R39 overlay strategy `ai_rotation_r71_r39_capacity_aware_representative`。

## 允许修改范围

- 新增 `agent/backtest/fund_rotation/capacity.py`
- 新增 `agent/backtest/fund_rotation/strategies/ai_rotation_r71_r39_capacity_aware_representative/__init__.py`
- 新增该目录下的 `strategy.py`
- 仅在 `agent/backtest/fund_rotation/strategies/registry.py` 增加新 strategy 的显式 import/whitelist 项
- 新增对应 focused tests
- 新增 `experiments/fund_rotation_research_validity/batch_2_capacity_repair.py` 和中文报告

不得改写既有 R39、Runner、公共 execution ledger 或平台级架构；不得引入动态扫描、隐式全局状态或未来信息。

## 必须实现的接口与行为

- `estimate_capacity(adv, max_participation, execution_horizon, lot_size, tradable_state) -> CapacityEstimate`
- `select_capacity_aware_representative(candidates, target_quantity, market_observation, prior_representative) -> RepresentativeSelection`
- 候选只能来自决策 cutoff 可见的同 cluster/同 identity 数据；所有排序、tie-break、fallback 必须确定性。
- 覆盖：容量足够时继续持有、零容量解锁、首个 fallback 被阻塞后选择下一候选、全不可用转现金、未来成交量排除、lot-size rounding、多期 anti-flap。
- 缺失/非法/未来信息必须 fail closed；不得把未知容量当成无限容量。
- 保持 R39 的默认行为可复现；overlay 只在容量证据明确允许时改变代表，否则精确回退 R39。
- diagnostics 必须能解释 carry/解锁/fallback/现金结果及容量原因。

## 实施顺序

1. 先写 failing unit tests，并运行计划指定的 focused 命令确认 RED。
2. 实现最小纯容量模型和 overlay，补齐 focused tests。
3. 运行 focused、R39/R40/Runner 回归。
4. 执行历史 counterfactual；若输入数据不可用，必须输出 `unavailable`，不得填零或虚构结论。
5. 执行正式 U1 paired backtest；记录 manifest/hash/中文报告及环境限制。
6. 交付前报告改动文件、测试证据、最小改动自评；等待主 agent 派发独立 gpt-5.6-luna/high review。

## 最小改动检查

每个新增字段和分支都必须能映射到容量估计、代表 fallback、anti-flap、诊断或实验可复现性要求。不要顺手重构相邻策略、Runner 或数据层。
