# R81 扩展组合实验 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 R81 的固定防御资产 PIT 资格错误，重建可比较的 R81 anchor，并在 `20130329..20220729` 上按单变量 Champion-Challenger 规则测试 R69、role-level R63、R60/R72/R61/R73 及 R21–R30 信号族。

**Architecture:** R81 的经济角色分类、动态代表选择、代表锁定和硬失败重选保持不变。修复只让固定防御资产在当日资格池中时承接现金，否则回退现金；组合实验只接收 R81 当前代表集合，在角色信号、组合权重、风险防御或生命周期层各自做单一改变，不重新引入相关性聚类代表状态机。

**Tech Stack:** Python 3.11、pytest、Pydantic、StockPred fund-rotation batch service、JSON/JSONL experiment ledger。

**Spec:** `docs/superpowers/specs/2026-09-03-fund-rotation-r81-combination-design.md` 及用户本轮目标。

## Global Constraints

- 所有回测 evaluation interval 固定为 `20130329..20220729`，确认区间不参与选择。
- 所有比较共享当前冻结快照、PIT universe、周频日历、执行合同和 `RESEARCH_ONLY` 模式。
- 执行合同固定为初始资金 `1000000.0`、佣金 `0.00025` 且最低 `5.0`、其他费率 `0.0`、参与率 `0.05`、ADV20/10、滑点 `5–30bps`、整手 `100`。
- 每个优化轮次只有一个主要可证伪假设、恰好两个变体；Challenger 只有同时通过 Validation Sharpe、收益、MDD、折叠胜率、PIT、因果、执行和可比性门槛才替换 Champion。
- R81 修复是用户明确要求的前置 bugfix；除该修复外不得修改既有策略默认行为、公共 Runner、PIT/data contract 或执行语义。
- 新组合必须使用独立策略 ID、独立目录、聚焦测试和独立实现哈希；不得直接继承完整 R58/R59/R71 代表选择状态。
- 任何候选若不能在 R81 当前代表上定义清楚，记录 `NO_JUSTIFIED_HYPOTHESIS`，不为填轮次强行回测。

---

### Task 1: 修复 R81 固定防御资产资格处理并重建 anchor

**Files:**
- Modify: `agent/backtest/fund_rotation/strategies/economic_role_rotation/strategy.py:400-405`
- Test: `agent/tests/fund_rotation/test_economic_role_rotation.py`
- Create: `agent/tests/fund_rotation/test_r81_fixed_defense_eligibility.py`
- Modify: `agent/scripts/run_r81_combination_batch.py` only if the repaired anchor requires an explicit preflight record; preserve the requested dates.

**Interfaces:**
- Consumes: existing `signal_eligible` set and `apply_defense_asset()` behavior.
- Produces: a legal R81 decision when `511010.SH` is unavailable, with explicit diagnostics/reason code and cash fallback.

- [ ] Write a failing test proving that an unavailable `511010.SH` does not appear in target weights and preserves cash.
- [ ] Run the focused test and observe failure against the current unconditional R81 defense call.
- [ ] Implement the minimal PIT-aware selection: use `511010.SH` only if present in the current signal-date eligible set; otherwise pass `None` to the defense layer and record unavailable status.
- [ ] Add a present-asset test proving fixed short bond behavior remains unchanged when `511010.SH` is eligible.
- [ ] Run focused R81 tests and the existing economic-role regression tests.
- [ ] Run the R81 anchor and verify both child variants reach terminal success, no contract violation occurs, and the anchor is publishable/comparable.
- [ ] Record the repaired implementation hash, run IDs, snapshot, fold manifest and the precondition deviation in the append-only ledger.

### Task 2: R81 + R69 transition cap 50%

**Files:**
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r86_r81_transition_cap_50/strategy.py`
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r86_r81_transition_cap_50/__init__.py`
- Create: `agent/tests/fund_rotation/test_ai_rotation_r86_r81_transition_cap_50.py`
- Modify: `agent/backtest/fund_rotation/strategies/registry.py` and exact catalog assertions only by appending the new ID.

**Hypothesis:** 在修复后的 R81 下限制单周新增目标暴露为 `50%`，可降低换仓冲击、滑点和回撤，而不改变角色代表、角色排序或生命周期。

- [ ] Write failing behavior tests for positive target additions capped at `0.50` and unchanged reductions/holds.
- [ ] Verify RED, implement a new strategy ID using the existing transition-cap mechanism without modifying R69/R70.
- [ ] Run focused tests, registry tests, and review the strategy pipeline to prove R81 upstream state is unchanged.
- [ ] Run paired R81 anchor vs R86 batch and persist terminal results and gate decision.

### Task 3: role-level R63 rank buffer

**Files:**
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r87_r81_role_rank_buffer/strategy.py`
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r87_r81_role_rank_buffer/__init__.py`
- Create: `agent/tests/fund_rotation/test_ai_rotation_r87_r81_role_rank_buffer.py`
- Modify: registry and exact catalog assertions by appending only the new ID.

**Hypothesis:** 在角色层采用 Top3 入选、Top4 退出的迟滞，可减少角色边界抖动和换手；R81 的角色内代表锁定保持不变。

- [ ] Write failing tests for role-level retention, epoch reset, invalid roles and deterministic ties.
- [ ] Verify RED, implement the new role-level selector without importing cluster IDs or R63’s R59 state.
- [ ] Run focused tests and inspect that representative mapping and R81 lifecycle remain unchanged.
- [ ] Run paired R81 anchor vs R87 batch and record the decision.

### Task 4: role-level R60/R72/R61/R73 signal candidates

**Files:**
- Create new strategy packages and focused tests for R88 (R60 126D positive gate), R89 (R72 126D absolute-momentum exposure gate), R90 (R61 50/50 short-medium score), and R91 (R73 60/120/240D rank score).
- Modify: `agent/backtest/fund_rotation/strategies/registry.py` and exact catalog assertions by appending only new IDs.

**Hypothesis boundary:** each strategy may only transform the current R81 representative set at the role score or role exposure gate; no candidate may re-cluster, choose a new representative, or combine with another signal.

- [ ] For each candidate, write and run a failing behavior test before implementation.
- [ ] Implement each as an independent strategy ID with frozen causal windows, deterministic ties and fail-closed missing data behavior.
- [ ] Run focused tests, registry tests and the existing fund-rotation regression tests after each candidate.
- [ ] Run one paired batch per candidate against the current research Champion, verify terminal state and gates, and consume one ledger round per candidate even when not improving.

### Task 5: role-level R21–R30 signal family screening

**Files:**
- Create independent role-level strategy packages and focused tests for R21, R22, R23, R24, R25, R26, R27, R28, R29 and R30, using IDs R92–R101.
- Modify: registry and exact catalog assertions by appending only the new IDs.

**Hypothesis boundary:** preserve R81’s dynamic representative map and lifecycle; adapt only the selected role representatives or role-member evidence to the exact corresponding historical signal mechanism. If a strategy’s original mechanism requires correlation clusters or member-level state that cannot be separated, record `NO_JUSTIFIED_HYPOTHESIS` instead of silently changing its meaning.

- [ ] For each mechanism, write a design note specifying its original formula, role-level mapping, causal cutoff, missing-data behavior and falsification condition.
- [ ] Have the analyst/reviewer reject candidates whose mapping changes representative selection or introduces post-selection information.
- [ ] Write and run RED tests for every admitted candidate, then implement the smallest independent package.
- [ ] Run focused/regression tests and one paired batch per admitted candidate; record rejected candidates as consumed `NO_JUSTIFIED_HYPOTHESIS` rounds with evidence.

### Task 6: campaign audit, fold analysis and final research status

**Files:**
- Modify/Create: `agent/runs/fund_rotation/experiments/fund_rotation_r81_expanded_combinations_20260903/`
- Create: `docs/superpowers/reports/2026-09-03-r81-expanded-combination-research-report.md`
- Reuse: existing batch/fold/ledger analysis scripts only where their inputs and frozen dates remain exact.

- [ ] Verify every authorized round has a request, terminal batch result, raw metrics, fold evidence, review verdict, and append-only ledger entry.
- [ ] Recompute Champion decisions only from the repaired R81 anchor and comparable child runs.
- [ ] Report the Champion path, all rejected/failed rounds, multiple-testing risk, quality limitations, and whether any candidate reaches only `FROZEN_RESEARCH_CANDIDATE` status.
- [ ] Keep confirmation data excluded and pre-register at least 104 weeks of forward shadow for any surviving research candidate.
- [ ] Run final focused/regression verification and a whole-branch review before claiming completion.

