# Fund Rotation Shadow P0/P1 Closeout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the two newly identified Shadow P0 execution gaps and six P1 PIT, metric, benchmark, audit, and recovery gaps without weakening the frozen fund-rotation contracts.

**Status:** Completed. All newly identified P0/P1 review items were implemented, regression-tested, and independently approved.

**Architecture:** Keep Shadow decision sealing and execution idempotency separate. Represent a target transition as the union of previous and new targets, with zero-weight orders for removals; represent residual retries as new execution-attempt events against the same parent until terminal state. Make formal PIT paths fail closed on missing per-date knowledge cutoffs, calculate formal metrics from native executable equity, and make benchmark/recovery state explicit and auditable.

**Tech Stack:** Python, pandas, pytest, existing `NativeExecutionState`, `ExecutionLedger`, `MarketRuleResolver`, `FundRotationBacktestRunner`, and `InMemoryForwardValidationStore` contracts.

## Global Constraints

- Formal `AS_WAS_KNOWN` execution must not fall back to a global future knowledge cutoff when a trade-date cutoff is absent.
- Shadow attempts/fills must consume only the current native ledger delta and must preserve parent/residual lineage.
- Formal strategy metrics must describe executable/native account equity; theoretical ideal equity remains separately named.
- PIT universe, market rules, benchmark identity, and recovery data cutoffs must be explicit and fail closed when missing.
- Every behavior change requires a failing regression test before production code and a passing focused test before moving on.

---

### Task 1: Shadow target-transition orders (P0)

**Files:**
- Modify: `agent/src/stockpred/fund_rotation/forward_validation.py:ShadowDecisionService.seal_scheduled_decision`
- Modify: `agent/src/stockpred/fund_rotation/forward_validation.py:ShadowExecutionService._validate_adapter_output`
- Test: `agent/tests/fund_rotation/test_forward_validation.py`
- Test: `agent/tests/fund_rotation/test_production_adapters.py`

**Interfaces:**
- Input: `ShadowDecision.previous_targets` and `ShadowDecision.new_targets`.
- Produces: one `ShadowOrder` per symbol in their union, with `target_weight = new_targets.get(symbol, 0.0)` and the decision execution date.

- [ ] **Step 1: Write the failing test** for A=1.0 transitioning to B=1.0; assert sealed orders contain `(A, 0.0)` and `(B, 1.0)` and that a native SELL A attempt is accepted.
- [ ] **Step 2: Run the focused test** and verify the current implementation fails with `ATTEMPT_ORDER_MISMATCH` or lacks the A order.
- [ ] **Step 3: Implement the union transition** and make order validation compare against the same normalized union.
- [ ] **Step 4: Run the focused tests** and verify the target transition passes without changing idempotency behavior.

### Task 2: Shadow residual retry state machine (P0)

**Files:**
- Modify: `agent/src/stockpred/fund_rotation/forward_validation.py:ShadowExecutionService`
- Modify: `agent/src/stockpred/fund_rotation/production_adapters.py`
- Modify: `agent/backtest/fund_rotation/native_execution.py`
- Test: `agent/tests/fund_rotation/test_forward_validation.py`
- Test: `agent/tests/fund_rotation/test_production_adapters.py`

**Interfaces:**
- Input: `ShadowDecision`, `ShadowAccountState.residual_orders`, native `active_orders`, and the requested `execution_as_of_time`.
- Produces: a distinct execution idempotency key per `(shadow_decision_id, parent_order_id, trade_date)` and new attempt/fill delta facts on later market-data dates; `completed_rebalance_cycles` changes only when the decision target transition completes, not per retry.

- [ ] **Step 1: Write the failing test** with Monday partial fill and Tuesday retry; assert Tuesday creates a new attempt, consumes the Tuesday market data, and does not return Monday’s cached result.
- [ ] **Step 2: Run it** and verify the current `execution:{decision}:{expected_date}` key returns the Monday result instead of retrying.
- [ ] **Step 3: Implement parent/date retry identity** and select the next available execution market date while retaining native state and residual parent lineage.
- [ ] **Step 4: Add terminal-state handling** for FILLED, CANCELED, EXPIRED, and SUPERSEDED so retries stop only at a terminal parent state.
- [ ] **Step 5: Run focused Shadow/native tests** and verify cumulative ledger IDs remain delta-filtered.

### Task 3: PIT knowledge-cutoff fail-fast (P1)

**Files:**
- Modify: `agent/backtest/fund_rotation/native_execution.py:_resolve_execution_rules`
- Modify: `agent/backtest/fund_rotation/runner.py` if formal request construction needs explicit cutoff validation
- Test: `agent/tests/fund_rotation/test_native_execution.py`
- Test: `agent/tests/fund_rotation/test_runner_native_execution.py`

**Interfaces:**
- Input: `NativeExecutionRequest.rule_mode`, `knowledge_cutoffs`, and `trade_date`.
- Produces: `PITInvalidMarketRule` in `AS_WAS_KNOWN` mode when `trade_date` has no specific cutoff; global `knowledge_cutoff` remains valid only for non-PIT/latest-restated mode.

- [ ] **Step 1: Write a failing test** using `AS_WAS_KNOWN` and an empty `knowledge_cutoffs` mapping; assert no resolver call proceeds with the global cutoff.
- [ ] **Step 2: Run it** and verify the current fallback uses `request.knowledge_cutoff`.
- [ ] **Step 3: Implement fail-fast validation** with a stable `PIT_INVALID_EXECUTION_RULE` reason.
- [ ] **Step 4: Run native and runner tests** for both fail-fast PIT and valid date-specific cutoff paths.

### Task 4: Historical PIT coverage denominator (P1)

**Files:**
- Modify: `agent/backtest/fund_rotation/strategies/correlation_all_members/strategy.py:_coverage_eligible_by_week`
- Modify: `agent/backtest/fund_rotation/strategies/correlation_all_members/strategy.py` strategy data requirements/wiring as needed
- Test: `agent/tests/fund_rotation/test_correlation_all_members_strategy.py`
- Test: `agent/tests/fund_rotation/test_pit_universe.py` if resolver behavior is extended

**Interfaces:**
- Input: each historical momentum week, PIT resolver, signal-date knowledge cutoff, and the historical universe source.
- Produces: coverage denominators from the universe actually eligible at each historical week, including instruments no longer present in the latest pool.

- [ ] **Step 1: Write a failing test** where an ETF is eligible in an old week but absent from the current pool; assert the old week denominator includes it through PIT resolution.
- [ ] **Step 2: Run it** and verify current `codes`-based filtering excludes the historical ETF.
- [ ] **Step 3: Implement explicit PIT-universe resolution per week** and fail closed or mark quality invalid when the historical resolver is unavailable in formal mode.
- [ ] **Step 4: Run strategy/PIT regression tests** and verify current-pool behavior remains unchanged in research-only mode.

### Task 5: Formal executable metrics and benchmark policy (P1)

**Files:**
- Modify: `agent/backtest/fund_rotation/runner.py:FundRotationRunResult` and benchmark construction
- Modify: `agent/backtest/fund_rotation/pipeline.py`
- Modify: `agent/backtest/fund_rotation/benchmarks.py` if policy-driven benchmark execution is needed
- Test: `agent/tests/fund_rotation/test_runner.py`
- Test: `agent/tests/fund_rotation/test_pipeline.py`
- Test: `agent/tests/fund_rotation/test_benchmarks.py`

**Interfaces:**
- Input: native `executed_equity`, `BenchmarkPolicy`, PIT benchmark universe, and explicit benchmark instrument/rule mapping.
- Produces: `executable_strategy_metrics`/formal `strategy_metrics` from native executable equity; `ideal_strategy_metrics` only for theoretical equity; benchmark identities and execution semantics come from the policy instead of hard-coded `510300.SH`.

- [ ] **Step 1: Write failing tests** asserting formal strategy metrics equal metrics computed from `run_result.executed_equity`, ideal metrics are separately exposed, and a non-default benchmark policy changes benchmark identity without source edits.
- [ ] **Step 2: Run them** and verify current pipeline uses `run_daily_ideal_account` and hard-coded `510300.SH`.
- [ ] **Step 3: Implement explicit metric fields and policy-driven benchmark selection** while preserving legacy compatibility aliases only where documented.
- [ ] **Step 4: Run runner, pipeline, benchmark, and golden compatibility tests.**

### Task 6: Residual audit and recovery signal cutoff (P1)

**Files:**
- Modify: `agent/backtest/fund_rotation/native_execution.py:_state_from_execution`
- Modify: `agent/src/stockpred/fund_rotation/production_adapters.py:ProductionFrozenStrategyDecisionProvider`
- Modify: `agent/src/stockpred/fund_rotation/forward_validation.py` signal persistence contract if needed
- Test: `agent/tests/fund_rotation/test_native_execution.py`
- Test: `agent/tests/fund_rotation/test_production_adapters.py`

**Interfaces:**
- Input: native active-order state and a scheduled signal date during outage recovery.
- Produces: `active_orders` snapshots with explicit `remaining`; recovered signal data views receive `knowledge_cutoff=signal_date` (or the sealed availability cutoff), never the recovery `as_of_time`.

- [ ] **Step 1: Write failing tests** asserting partial active orders export `remaining=requested-filled`, and a recovered Jan 6 signal factory receives Jan 6 cutoff even when provider `as_of_time` is Jan 20.
- [ ] **Step 2: Run them** and verify the current snapshot omits `remaining` and provider passes Jan 20.
- [ ] **Step 3: Implement explicit remaining quantity and cutoff propagation.**
- [ ] **Step 4: Run focused native/provider tests.**

### Task 7: Full verification and independent review

**Files:**
- Test: all fund-rotation regression modules touched above

- [ ] Run `git diff --check`.
- [ ] Run focused red/green tests for every task.
- [ ] Run the full fund-rotation trusted-chain regression.
- [ ] Dispatch a read-only subagent review of all new P0/P1 items and iterate until `APPROVED`.
- [ ] Commit only after verification and clean worktree checks.
