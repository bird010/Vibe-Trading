# 基金轮动研究有效性路线实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 严格按研究有效性路线完成 Batch 0–6 的可执行实验、证据产物和晋级判断，并冻结/启动 R40 Shadow A 的真实前瞻记录。

**Architecture:** 复用现有 `execution_ledger_v2.py`、PIT `UniverseResolver`、fund-rotation Runner、策略 contract、artifact publisher、comparison 和 forward-validation 生命周期。每个新增机制通过独立纯函数/策略目录/实验 manifest 接入；R39、旧运行产物和共享会计语义保持不变。每个任务结束后依次完成 focused tests、相关回归、实验产物核验和 5.6 Luna 高推理 review。

**Tech Stack:** Python 3、pandas/numpy、Pydantic、pytest、现有 fund-rotation backtest Runner、JSON/CSV artifacts、Git。

## Global Constraints

- 文档中的旧选择区间与 `1a8eb8560998` 全区间均为已观察数据，不得标记为新 OOS。
- 一个策略 ID 只对应一个机制变化；R39 只作为控制组，不修改其行为。
- 只做必要的增量修改；禁止平台级重构、无关格式化、重写公共 Runner、另造平行账本或删除既有失败产物。
- 所有时间敏感数据必须按 `event_time`、`available_time`、`knowledge_cutoff` 和 `revision_policy` 校验；无法证明可得时 fail-closed。
- 所有新策略和实验必须使用确定性排序、严格 JSON、稳定 hash 和独立 manifest。
- 每个实验至少提供三折配对、正常成本/2×成本、T+1/T+2 延迟、参数邻域、fold contribution、switch count、持有期、阻塞和现金占比证据；固定单候选时在 policy 中明确 `NOT_APPLICABLE`。
- 每个任务在进入下一任务前必须有 Luna 5.6 高推理 review，review 结论明确无 P0/P1；P0/P1 未关闭时停止推进。
- R40 Shadow 的硬资格门槛为至少 26 周真实前瞻观察与 6 个完整 rebalance cycles；104 周是建议观察长度。当前工作只能冻结版本、启动 deployment、写入首期事件并报告证据不足。

---

### Task 1: Batch 0 证据冻结与 summary 指标合同修复

**Files:**
- Modify: `agent/src/stockpred/fund_rotation/batch_child_runtime.py`
- Modify: `agent/backtest/fund_rotation/execution_ledger_v2.py` only if a missing v2 field prevents summary projection
- Modify: `agent/src/stockpred/fund_rotation/comparison.py` only if legacy summary is currently ranked as v2
- Create: `agent/tests/fund_rotation/test_batch_child_runtime.py`
- Modify: `agent/tests/fund_rotation/test_execution_ledger_v2.py`
- Modify: `agent/tests/fund_rotation/test_batch_comparison.py`
- Create: `experiments/fund_rotation_research_validity/batch_0_summary_repair.py`
- Create: `experiments/fund_rotation_research_validity/batch_0_report.md`

**Interfaces:**
- Consume `build_execution_ledger_from_pipeline_result()` and `compute_execution_diagnostics_v2()`.
- Produce summary fields sourced from `execution_diagnostics_v2`, including `one_way_turnover`, `annualized_one_way_turnover`, `blocked_attempt_rate`, explicit costs, opportunity cost and `metric_contract_version`.
- Preserve the existing `orders.csv`, `positions.csv`, `equity.csv`, `trade_events.csv` and their checksums.

- [ ] Write tests that load a synthetic pipeline result and assert summary turnover equals v2 half-gross turnover, missing values remain `None`/`unavailable`, and legacy summaries are excluded from formal ranking.
- [ ] Run `E:\anaconda3\envs\AlphaFin\python.exe -m pytest agent/tests/fund_rotation/test_batch_child_runtime.py agent/tests/fund_rotation/test_execution_ledger_v2.py agent/tests/fund_rotation/test_batch_comparison.py -q` and observe the new assertions fail for the old zero-default projection.
- [ ] Implement only the summary projection and repair script; never recompute strategy decisions or rewrite source run artifacts.
- [ ] Run the focused tests and then `E:\anaconda3\envs\AlphaFin\python.exe -m pytest agent/tests/fund_rotation -q`.
- [ ] Execute `batch_0_summary_repair.py --run-dir agent/runs/fund_rotation/1a8eb8560998 --output-dir experiments/fund_rotation_research_validity/batch_0` and write before/after checksums plus R39 impact analysis in the Chinese report.
- [ ] Review `git diff --stat`, changed-file list, artifact checksums and report; dispatch the required Luna review and do not continue until it reports no P0/P1.

### Task 2: PIT Master schema、U0/U1 identity snapshot and diagnostics

**Files:**
- Modify: `agent/backtest/fund_rotation/pit_universe.py`
- Modify: `agent/backtest/fund_rotation/universe.py` only for compatible identity-policy wiring
- Modify: `agent/backtest/fund_rotation/runner.py` only for snapshot fingerprint/diagnostic propagation
- Modify: `agent/tests/fund_rotation/test_pit_universe.py`
- Modify: `agent/tests/fund_rotation/test_runner_contract_integration.py`
- Create: `agent/tests/fund_rotation/test_pit_identity_layers.py`
- Create: `experiments/fund_rotation_research_validity/pit_identity.py`
- Create: `experiments/fund_rotation_research_validity/batch_1_report.md`

**Interfaces:**
- Extend existing `FundInstrumentVersion`, `UniverseResolution` and `FundRotationPITUniverseAdapter` compatibly; do not introduce a second resolver.
- Produce immutable `U0` and `U1` snapshots with per-date membership reasons, identity mapping, `identity_hash`, `snapshot_fingerprint` and coverage diagnostics.
- U1 identity key is based on underlying index, asset class, region, currency, leveraged/inverse flag and share-class/feeder relationship.

- [ ] Add failing tests for known-before-cutoff inclusion, future-known exclusion, listing/delisting boundaries, same-index de-duplication, missing identity fail-closed and deterministic snapshot hashes.
- [ ] Run the focused PIT tests and confirm failures arise from missing identity-layer behavior rather than test setup.
- [ ] Implement the smallest compatible schema/resolver extension using only decision-date information; preserve existing `AS_WAS_KNOWN` behavior and legacy diagnostics.
- [ ] Run focused tests, then `E:\anaconda3\envs\AlphaFin\python.exe -m pytest agent/tests/fund_rotation/test_pit_universe.py agent/tests/fund_rotation/test_runner_contract_integration.py agent/tests/fund_rotation/test_universe.py -q`.
- [ ] Generate U0/U1 snapshots and read-only diagnostics for every R39 rebalance date: available count, duplicate identity ratio, momentum coverage, max cluster share, effective cluster count and tradable representative ratio.
- [ ] Run the same R39 manifest against U0 and U1 with three folds, normal/2× costs and T+1/T+2 delay; record data-gate and strategy-gate conclusions without using U1 as a tuned signal.
- [ ] Perform the diff/artifact review and Luna review; unresolved P0/P1 blocks Task 3.

### Task 3: Capacity-aware representative unlock and fallback challenger

**Files:**
- Create: `agent/backtest/fund_rotation/capacity.py`
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r71_r39_capacity_aware_representative/__init__.py`
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r71_r39_capacity_aware_representative/strategy.py`
- Modify: `agent/backtest/fund_rotation/strategies/registry.py`
- Create: `agent/tests/fund_rotation/test_capacity.py`
- Create: `agent/tests/fund_rotation/test_ai_rotation_r71_r39_capacity_aware_representative.py`
- Create: `experiments/fund_rotation_research_validity/batch_2_capacity_repair.py`
- Create: `experiments/fund_rotation_research_validity/batch_2_report.md`

**Interfaces:**
- `estimate_capacity(adv, max_participation, execution_horizon, lot_size, tradable_state) -> CapacityEstimate`.
- `select_capacity_aware_representative(candidates, target_quantity, market_observation, prior_representative) -> RepresentativeSelection`.
- Candidate selection uses only same-cluster/same-identity instruments visible at the decision cutoff and deterministic tie-breaks.

- [ ] Add failing unit tests for capacity-sufficient carry, zero-capacity unlock, first fallback blocked then next fallback, all candidates unavailable to cash, future-volume exclusion, lot-size rounding and multi-period anti-flap state.
- [ ] Run `E:\anaconda3\envs\AlphaFin\python.exe -m pytest agent/tests/fund_rotation/test_capacity.py agent/tests/fund_rotation/test_ai_rotation_r71_r39_capacity_aware_representative.py -q` and observe failures for the new API.
- [ ] Implement the pure capacity selector and R39 overlay without changing R39, public Runner or execution ledger semantics.
- [ ] Run focused tests and the existing R39/R40/runner regression suites.
- [ ] Run the historical counterfactual on frozen U1 and compare blocked attempts, parent fill ratio, capacity-zero count, opportunity cost, turnover and target-deviation duration under 1×/2×/3× cost/capacity stress.
- [ ] Run the formal U1 paired backtest using strategy ID `ai_rotation_r71_r39_capacity_aware_representative`, save manifest/hash and Chinese report, then perform Luna review with explicit P0/P1 result.

### Task 4: Freeze R40 and start Shadow A

**Files:**
- Modify: `agent/src/stockpred/fund_rotation/forward_validation.py` only for missing evidence/manifest fields
- Modify: `agent/src/stockpred/fund_rotation/production_adapters.py` only for wiring existing Shadow services
- Modify: `agent/tests/fund_rotation/test_forward_validation.py`
- Create: `agent/tests/fund_rotation/test_r40_shadow_start.py`
- Create: `experiments/fund_rotation_research_validity/start_r40_shadow.py`
- Create: `experiments/fund_rotation_research_validity/shadow_a_report.md`

**Interfaces:**
- Reuse `FrozenStrategyVersion`, `ShadowDecisionService`, `ShadowExecutionService`, `ShadowRunScheduler` and current `shadow_*` artifact contracts.
- Freeze strategy ID `ai_rotation_r40_single_name_ceiling` with ceiling `0.5`; use separate decision and execution idempotency keys.
- Produce `frozen_strategy_manifest.json`, `qualification_policy.json`, initial evidence/assessment, Shadow deployment manifest and first append-only decision/account artifacts.

- [ ] Add failing tests for immutable frozen R40 config, pre-execution decision sealing, delayed execution, distinct idempotency keys, continuous account state, ideal/executable NAV separation and `INSUFFICIENT_FORWARD_EVIDENCE` status.
- [ ] Run the focused forward-validation tests and confirm the new evidence/start assertions fail before implementation.
- [ ] Implement only the missing manifest/deployment wiring; preserve the 50% threshold and existing forward-validation state machine.
- [ ] Run `E:\anaconda3\envs\AlphaFin\python.exe -m pytest agent/tests/fund_rotation/test_forward_validation.py agent/tests/fund_rotation/test_r40_shadow_start.py -q`.
- [ ] Freeze R40 from U1 and R39 control identities, start the first real shadow cycle, and record that the 26-week/6-cycle hard-gate evidence is not yet available (104 weeks remains a recommended observation length).
- [ ] Dispatch Luna review; this gate must explicitly distinguish “shadow started” from “shadow qualified”.

### Task 5: Batch 3A R39 plus absolute momentum

**Files:**
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r72_r39_absolute_momentum/__init__.py`
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r72_r39_absolute_momentum/strategy.py`
- Modify: `agent/backtest/fund_rotation/strategies/registry.py`
- Create: `agent/tests/fund_rotation/test_ai_rotation_r72_r39_absolute_momentum.py`
- Create: `experiments/fund_rotation_research_validity/batch_3a_absolute_momentum.py`
- Create: `experiments/fund_rotation_research_validity/batch_3a_report.md`

**Interfaces:**
- Preserve R39 selection, clustering, carry, staging, execution and U1 universe.
- Add only `R126d > 0`; failed candidates go to cash, with separate missing-window and negative-trend reason codes.

- [ ] Write and run red tests for positive/negative/insufficient 126-day return, boundary date, T+1/T+2 cutoff and unchanged R39 target when the gate passes.
- [ ] Implement the independent strategy overlay and registry entry.
- [ ] Run focused plus R39/R40/runner regression suites.
- [ ] First compute historical negative-trend frequency and forward outcomes; then run the three-fold paired experiment with required stress scenarios and tail-risk metrics.
- [ ] Write the report and obtain Luna review with no P0/P1 before Task 6.

### Task 6: Batch 3B multi-horizon relative momentum

**Files:**
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r73_r39_multi_horizon_rank/__init__.py`
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r73_r39_multi_horizon_rank/strategy.py`
- Modify: `agent/backtest/fund_rotation/strategies/registry.py`
- Create: `agent/tests/fund_rotation/test_ai_rotation_r73_r39_multi_horizon_rank.py`
- Create: `experiments/fund_rotation_research_validity/batch_3b_multi_horizon.py`
- Create: `experiments/fund_rotation_research_validity/batch_3b_report.md`

**Interfaces:**
- Replace only the R39 ranking score with equal-weight `rank(R60) + rank(R120) + rank(R240)`; do not add R20 or other optimized weights.
- Preserve all R39 clustering, carry, top-K, execution and cost rules.

- [ ] Write red tests for exact rank aggregation, missing windows, ties, deterministic ordering and absence of short-horizon R20.
- [ ] Implement the score-only challenger with diagnostics for rank flips and score coverage.
- [ ] Run focused and regression tests.
- [ ] Diagnose period ranking correlations and flip rates before the three-fold paired experiment; include holding period, switch count, turnover and fold contributions.
- [ ] Obtain Luna review and stop if any P0/P1 remains.

### Task 7: Batch 3C volatility-adjusted score

**Files:**
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r74_r39_vol_adjusted_score/__init__.py`
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r74_r39_vol_adjusted_score/strategy.py`
- Modify: `agent/backtest/fund_rotation/strategies/registry.py`
- Create: `agent/tests/fund_rotation/test_ai_rotation_r74_r39_vol_adjusted_score.py`
- Create: `experiments/fund_rotation_research_validity/batch_3c_vol_adjusted.py`
- Create: `experiments/fund_rotation_research_validity/batch_3c_report.md`

**Interfaces:**
- Replace only ranking with `momentum / volatility_60`; volatility missing, non-finite or below the declared epsilon is fail-closed.
- Do not change selected-position allocation to inverse volatility in this task.

- [ ] Write red tests for finite volatility, missing/near-zero fail-closed behavior, causal 60-day window and unchanged target sizing.
- [ ] Implement the independent score-only challenger.
- [ ] Run focused and regression tests.
- [ ] Run the required fold/cost/delay/neighbor experiment and compare concentration, turnover, Sharpe/Calmar and tail risk.
- [ ] Obtain Luna review before Batch 4.

### Task 8: Batch 4 Momentum/Cluster/Carry ablation

**Files:**
- Modify: `agent/backtest/fund_rotation/strategies/registry.py` only to register explicit ablation adapters if required by the current runner
- Create: `agent/backtest/fund_rotation/strategies/ablation.py`
- Create: `agent/tests/fund_rotation/test_ablation.py`
- Create: `experiments/fund_rotation_research_validity/batch_4_ablation.py`
- Create: `experiments/fund_rotation_research_validity/batch_4_report.md`

**Interfaces:**
- Run fixed U1 three-arm comparison: M0 Momentum only; M1 Momentum+Cluster; M2 Momentum+Cluster+R39 carry.
- Use the same data snapshot, calendar, execution contract, costs and delays for all arms.

- [ ] Write red tests proving each arm disables exactly one mechanism and that identity de-duplication is not silently removed.
- [ ] Implement the smallest adapter around existing scoring/selection/carry components; do not implement direct-correlation migration.
- [ ] Run focused and runner regression tests.
- [ ] Execute three-fold ablation and quantify duplicate underlying exposure, carry marginal contribution and cluster marginal contribution.
- [ ] Only if M0/M1 proves clustering has no value, record a separately approved direct-correlation diagnostic; do not optimize R64/R66 returns.
- [ ] Obtain Luna review with explicit architecture decision.

### Task 9: Batch 5 risk layer and defense assets

**Files:**
- Create: `agent/backtest/fund_rotation/risk_layers.py`
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r75_r39_vol_target/__init__.py`
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r75_r39_vol_target/strategy.py`
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r76_cash_defense_baseline/__init__.py`
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r76_cash_defense_baseline/strategy.py`
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r77_defense_relative_momentum/__init__.py`
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r77_defense_relative_momentum/strategy.py`
- Modify: `agent/backtest/fund_rotation/strategies/registry.py`
- Create: `agent/tests/fund_rotation/test_risk_layers.py`
- Create: `experiments/fund_rotation_research_validity/batch_5_risk_layers.py`
- Create: `experiments/fund_rotation_research_validity/batch_5_report.md`

**Interfaces:**
- Volatility target uses one predeclared target and `exposure=min(1, sigma_target/sigma_portfolio)` without leverage.
- Defense comparison has three arms: cash baseline, fixed short-duration bond, and simple defense relative momentum; no historical best-asset backfill.
- Breadth, if tested, counts independent U1 identities rather than raw fund count.

- [ ] Write red tests for no leverage, missing/zero volatility, cash baseline, fixed defense and identity-counted breadth.
- [ ] Implement only after Task 8 review passes and at least one trend mechanism has passed the predeclared stability gate.
- [ ] Run focused/regression tests and the three-fold stress experiment.
- [ ] Report Calmar, MDD, cash occupancy, defense turnover and fold contribution; do not combine risk target and absolute momentum in the first introduction.
- [ ] Obtain Luna review before Task 10.

### Task 10: Batch 6 survivor combination and final evidence matrix

**Files:**
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r78_survivor_combo/__init__.py`
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r78_survivor_combo/strategy.py`
- Modify: `agent/backtest/fund_rotation/strategies/registry.py`
- Create: `agent/tests/fund_rotation/test_r78_survivor_combo.py`
- Create: `experiments/fund_rotation_research_validity/batch_6_survivor_combo.py`
- Create: `experiments/fund_rotation_research_validity/batch_6_report.md`
- Create: `experiments/fund_rotation_research_validity/acceptance_matrix.md`

**Interfaces:**
- Combine only mechanisms that passed their own single-variable gate; preserve each mechanism's frozen ID and provenance.
- Re-run ablation after combination and record marginal contribution for every included layer.

- [ ] Write red tests for composition order, no duplicate mechanism, provenance/hash propagation and fail-closed survivor selection.
- [ ] Implement the minimum composition adapter; do not introduce new tunable parameters.
- [ ] Run focused, full fund-rotation regression and all formal comparison tests.
- [ ] Run the final three-fold/normal-2×/T+1-T+2/neighbor experiment and produce Chinese reports for winners and stopped directions.
- [ ] Fill the acceptance matrix requirement-by-requirement, including the explicit unresolved 26-week/6-cycle Shadow hard gates and the recommended 104-week observation length.
- [ ] Dispatch final Luna high-reasoning whole-branch review; resolve all P0/P1 findings before any completion claim.

## Per-task review protocol

For every task, record in the task ledger:

```text
base_commit
head_commit
changed_files
focused_test_command_and_result
regression_test_command_and_result
experiment_manifest_and_hashes
luna_model = gpt-5.6-luna
luna_reasoning = high
luna_review_result
p0_count
p1_count
decision = proceed | fix_and_rereview | stop
```

The coordinator must independently inspect the diff and command exit codes after each agent report. A subagent report alone is not evidence of completion. Shadow evidence remains an active append-only process after this plan finishes; its current status must never be presented as completed 26-week/6-cycle qualification (or as completed 104-week recommended observation).
