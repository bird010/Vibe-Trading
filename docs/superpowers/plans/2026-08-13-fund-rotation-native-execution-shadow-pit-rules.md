# Fund Rotation Native Execution, Shadow, and PIT Rules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to implement this plan task-by-task, with a fresh implementer and review checkpoint for every task.

**Goal:** Make the fund-rotation Runner, production Shadow path, and market-rule resolution use explicit shared v2 contracts and real PIT rule facts.

**Architecture:** First create an injectable PIT rule source and make `MarketRuleResolver` select a unique version by valid time, knowledge time, snapshot, and revision. Then extract the legacy execution behavior behind a new `FundRotationExecutionEngine` that directly owns v2 ledger facts; the legacy function remains only as a compatibility wrapper. Finally wire Shadow to a formal strategy-session provider, the shared execution engine, and the shared attribution/accounting primitive.

**Tech Stack:** Python 3, pandas, pytest, dataclasses, existing fund-rotation strategy contracts, `ExecutionLedger`, `DAILY_ACCOUNTING_EVENT_ORDER`, and `compute_accounting_day`.

## Global Constraints

- Formal execution, Shadow execution, and forward validation must share strategy, rule, cost, ledger, and accounting semantics.
- Decision evaluation must not read execution-day or future prices.
- PIT queries must explicitly carry trade date, knowledge cutoff, snapshot version, and query mode.
- Missing, conflicting, or unverifiable PIT rules fail closed; no implicit static-rule fallback.
- The legacy loop may remain only as an explicit parity/compatibility path and must not produce formal ledger, Shadow state, or formal diagnostics.
- Every implementation task follows TDD: add a focused failing test, run it and observe the expected failure, implement the minimum behavior, run the focused and relevant regression tests, then commit.
- Do not reset, checkout, or overwrite existing user changes. Only modify files named by the current task and direct test fixtures required by that task.

---

### Task 1: Replace the static MarketRuleResolver with a PIT rule source

**Files:**
- Create: `agent/backtest/fund_rotation/market_rules.py`
- Modify: `agent/backtest/fund_rotation/execution_ledger_v2.py:1073-1180`
- Modify: `agent/backtest/fund_rotation/pit_universe.py` only if the selected instrument needs to expose rule-source identity
- Test: `agent/tests/fund_rotation/test_market_rules_pit.py`
- Regression tests: `agent/tests/fund_rotation/test_execution_ledger_v2.py`, `agent/tests/fund_rotation/test_pit_universe.py`

**Interfaces:**
- `PITMarketRuleSource.resolve(*, ts_code, instrument_type, trade_date, knowledge_cutoff, snapshot_version, mode) -> MarketRuleRecord`
- `MarketRuleRecord` contains normalized settlement, lot size, tick size, price-limit rule, short permission, currency, valid/known intervals, revision/source IDs, and rule version.
- `MarketRuleResolver(source).resolve(instrument, trade_date, knowledge_cutoff, snapshot_version, mode) -> MarketRules` remains import-compatible for existing callers while requiring the explicit PIT source.

- [ ] **Step 1: Write the failing PIT selection tests**

Add tests that use an in-memory source backed by record mappings and assert:

```python
def test_resolver_selects_rule_known_before_cutoff_and_valid_on_trade_date():
    resolver = MarketRuleResolver(InMemoryPITMarketRuleSource([
        {"ts_code": "510300.SH", "instrument_type": "domestic_equity_etf",
         "valid_from": "20240101", "valid_to": None,
         "known_from": "20240102T00:00:00", "snapshot_version": 7,
         "revision_id": "r1", "settlement": "T+1", "lot_size": 100,
         "tick_size": 0.001, "price_limit_pct": 0.10,
         "short_allowed": False, "currency": "CNY", "rule_version": "pit-r1"}
    ]))
    rules = resolver.resolve(
        FundInstrumentVersion("510300.SH", "domestic_equity_etf", "unused"),
        trade_date="20240103", knowledge_cutoff="20240103T15:00:00",
        snapshot_version=7, mode=PITQueryMode.AS_WAS_KNOWN,
    )
    assert rules.rule_version == "pit-r1"
    assert rules.source_record_id == "r1"
```

Also cover: future-known rows excluded in `AS_WAS_KNOWN`, latest revision selected only in `LATEST_RESTATED`, snapshot mismatch, overlapping valid rows with no deterministic revision order, missing rule, and unknown instrument type. Each failure must use `UnknownExecutionRule` or an explicit PIT-invalid exception and must never return domestic ETF defaults.

- [ ] **Step 2: Run the focused tests and verify they fail for the missing source contract**

Run from `E:\code\stock\Vibe-Trading\agent`:

```powershell
pytest -q tests/fund_rotation/test_market_rules_pit.py
```

Expected: collection or assertion failures because `PITMarketRuleSource`, the source-backed resolver, and the additional PIT fields do not yet exist.

- [ ] **Step 3: Implement the explicit source and deterministic PIT selection**

Implement the source protocol and an in-memory source for tests/research fixtures. Filter records by exact `ts_code`, instrument type, snapshot, `valid_from <= trade_date < valid_to`, and `known_from <= knowledge_cutoff` for `AS_WAS_KNOWN`; use the fixed snapshot’s latest restated revision for `LATEST_RESTATED`. Reject missing/blank time fields, ambiguous overlapping revisions, and missing records. Add `snapshot_version`, `source_record_id`, and `currency` to `MarketRules`; preserve the existing `MarketRuleResolver` import path as a thin source-backed facade.

- [ ] **Step 4: Run focused and regression tests**

```powershell
pytest -q tests/fund_rotation/test_market_rules_pit.py tests/fund_rotation/test_execution_ledger_v2.py tests/fund_rotation/test_pit_universe.py
```

Expected: all focused tests pass; existing tests that instantiate the resolver must be updated only to pass an explicit fixture source.

- [ ] **Step 5: Commit**

```powershell
git add agent/backtest/fund_rotation/market_rules.py agent/backtest/fund_rotation/execution_ledger_v2.py agent/backtest/fund_rotation/pit_universe.py agent/tests/fund_rotation/test_market_rules_pit.py agent/tests/fund_rotation/test_execution_ledger_v2.py agent/tests/fund_rotation/test_pit_universe.py
git commit -m "feat: resolve fund execution rules from PIT source"
```

### Task 2: Add a native v2 execution engine

**Files:**
- Create: `agent/backtest/fund_rotation/native_execution.py`
- Modify: `agent/backtest/fund_rotation/execution.py` only for shared low-level helpers that are moved behind the new engine
- Modify: `agent/backtest/fund_rotation/execution_ledger_v2.py` only if a missing ledger-construction helper is needed
- Test: `agent/tests/fund_rotation/test_native_execution.py`
- Regression tests: `agent/tests/fund_rotation/test_execution_module.py`, `agent/tests/fund_rotation/test_execution_review_fixes.py`, `agent/tests/fund_rotation/test_executor.py`

**Interfaces:**
- `NativeExecutionRequest` contains target schedule, evaluation dates, market data/adjustment indexes, initial account state, execution policy, explicit PIT rule resolver, knowledge cutoff, snapshot version, and run identity.
- `NativeExecutionResult` contains `ledger: ExecutionLedger`, `executed_equity: pd.Series`, `trade_events: list[dict]`, `orders: list[dict]`, `positions_history: list[dict]`, and ending account state.
- `FundRotationExecutionEngine.execute(request, should_cancel=None) -> NativeExecutionResult` is the only formal Runner execution entry point.

- [ ] **Step 1: Write failing engine contract tests**

Add a small synthetic two-symbol test that asserts `execute` creates parent orders, attempts, and trades directly, and that every trade references a known attempt and parent. Add a spy/patch test proving the engine does not call `build_execution_ledger_from_pipeline_result` or `run_execution_loop`. Add tests for sell-before-buy, residual carry, corporate-action replacement lineage, and cash/NAV conservation.

- [ ] **Step 2: Run the focused tests and verify failure**

```powershell
pytest -q tests/fund_rotation/test_native_execution.py
```

Expected: import/API failures because the native engine does not exist.

- [ ] **Step 3: Extract the execution behavior into the native engine**

Move the reusable execution behavior currently embedded in `run_execution_loop` behind `FundRotationExecutionEngine.execute`. Reuse `PortfolioExecutor`, capacity/cost helpers, and corporate-action primitives where their semantics match, but emit `ParentOrderRecord`, `ExecutionAttemptRecord`, `ExecutedTradeRecord`, and `CorporateActionRecord` at the point the facts occur. Validate the resulting `ExecutionLedger` before returning. Do not call the legacy adapter as an intermediate step. Keep `run_execution_loop` as a compatibility wrapper for old pipeline callers only.

- [ ] **Step 4: Run focused and regression tests**

```powershell
pytest -q tests/fund_rotation/test_native_execution.py tests/fund_rotation/test_execution_module.py tests/fund_rotation/test_execution_review_fixes.py tests/fund_rotation/test_executor.py
```

Expected: all tests pass with the old compatibility path unchanged and the new engine producing the same intended execution semantics through v2 facts.

- [ ] **Step 5: Commit**

```powershell
git add agent/backtest/fund_rotation/native_execution.py agent/backtest/fund_rotation/execution.py agent/backtest/fund_rotation/execution_ledger_v2.py agent/tests/fund_rotation/test_native_execution.py agent/tests/fund_rotation/test_execution_module.py agent/tests/fund_rotation/test_execution_review_fixes.py agent/tests/fund_rotation/test_executor.py
git commit -m "feat: add native fund rotation v2 execution engine"
```

### Task 3: Make Runner use the native engine as the formal execution source

**Files:**
- Modify: `agent/backtest/fund_rotation/runner.py`
- Modify: `agent/backtest/fund_rotation/execution_ledger_v2.py` only to consume a native result directly in diagnostics
- Test: `agent/tests/fund_rotation/test_runner_native_execution.py`
- Regression tests: `agent/tests/fund_rotation/test_runner.py`, `agent/tests/fund_rotation/test_runner_contract_integration.py`, `agent/tests/fund_rotation/test_integrated_review_repairs.py`

**Interfaces:**
- `FundRotationBacktestRunner.__init__(..., execution_engine: FundRotationExecutionEngine | None = None, ...)` uses an explicit engine injection for tests and a native engine by default.
- `_formal_execution_diagnostics` accepts an `ExecutionLedger`/`NativeExecutionResult` and calls `compute_execution_diagnostics_v2` directly; it no longer calls `compute_pipeline_execution_diagnostics_v2` for formal metrics.

- [ ] **Step 1: Write failing Runner wiring tests**

Add a spy engine returning a valid native result and assert `FundRotationBacktestRunner.run` calls it once, returns its equity/trade/order/position outputs, and reports v2 diagnostics from its ledger. Add a guard that monkeypatches `runner.run_execution_loop` to raise and proves a successful Runner run does not touch it.

- [ ] **Step 2: Run the focused tests and verify failure**

```powershell
pytest -q tests/fund_rotation/test_runner_native_execution.py
```

Expected: the Runner either lacks the injection point or still calls the legacy loop, so the spy/guard test fails.

- [ ] **Step 3: Switch Runner wiring**

Construct the native request from the already sealed `targets_map`, evaluation dates, market data, execution config, PIT evidence, and snapshot identity. Call the injected/default engine, preserve cancellation and exact evaluation-calendar validation, and populate formal diagnostics from the returned ledger. Keep legacy output only under an explicitly named parity field/helper; do not derive formal diagnostics or account NAV from `PipelineResult`.

- [ ] **Step 4: Run Runner and integration regressions**

```powershell
pytest -q tests/fund_rotation/test_runner_native_execution.py tests/fund_rotation/test_runner.py tests/fund_rotation/test_runner_contract_integration.py tests/fund_rotation/test_integrated_review_repairs.py
```

Expected: native wiring tests pass and existing Runner contract tests remain green.

- [ ] **Step 5: Commit**

```powershell
git add agent/backtest/fund_rotation/runner.py agent/backtest/fund_rotation/execution_ledger_v2.py agent/tests/fund_rotation/test_runner_native_execution.py agent/tests/fund_rotation/test_runner.py agent/tests/fund_rotation/test_runner_contract_integration.py agent/tests/fund_rotation/test_integrated_review_repairs.py
git commit -m "feat: route fund rotation runner through native execution"
```

### Task 4: Wire production Shadow strategy, execution, and accounting adapters

**Files:**
- Create: `agent/src/stockpred/fund_rotation/production_adapters.py`
- Modify: `agent/src/stockpred/fund_rotation/forward_validation.py` only to expose explicit production wiring and preserve fail-closed behavior
- Modify: `agent/backtest/fund_rotation/attribution.py` only if the shared accounting adapter needs a narrowly scoped input conversion helper
- Test: `agent/tests/fund_rotation/test_production_adapters.py`
- Regression tests: `agent/tests/fund_rotation/test_forward_validation.py`, `agent/tests/fund_rotation/test_attribution.py`

**Interfaces:**
- `ProductionFrozenStrategyDecisionProvider(strategy_binding, data_view_factory, calendar_factory)` implements `next_signal(...)` by creating/using the formal strategy session and returning a sealed `ScheduledSignal`; it must not call `store.next_signal`.
- `ProductionShadowExecutionAdapter(engine, request_factory)` implements the existing `ShadowExecutionAdapter` protocol by invoking the native engine and mapping ledger attempts/trades to Shadow attempts/fills while retaining stable IDs.
- `ProductionShadowAccountingAdapter()` implements `ShadowAccountingAdapter` by converting fills and market data into `AccountDayInput`, calling `compute_accounting_day`, and returning a continuous `ShadowAccountState` with the shared contract version and event order.
- `build_production_shadow_execution_service(store, strategy_provider, execution_adapter, accounting_adapter)` rejects missing formal components instead of defaulting to deterministic/store implementations.

- [ ] **Step 1: Write failing production-adapter tests**

Add tests proving: the production provider invokes a formal session factory and never reads precomputed signals; the production execution adapter returns IDs tied to native ledger facts; the accounting adapter uses `compute_accounting_day` and preserves cash/NAV/position continuity; missing production components return the existing structured fail-closed result without persisting fills or account state.

- [ ] **Step 2: Run the focused tests and verify failure**

```powershell
pytest -q tests/fund_rotation/test_production_adapters.py
```

Expected: import/API failures because production adapter classes and wiring do not exist.

- [ ] **Step 3: Implement the formal adapters and wiring**

Use the catalog’s resolved strategy binding and `create_session(...).evaluate(...)` contract for decisions. Use the native engine from Task 2 for execution. Use `compute_accounting_day` from `attribution.py` for accounting; do not reproduce NAV arithmetic in the adapter. Keep deterministic adapters available only when explicitly supplied by unit tests.

- [ ] **Step 4: Run Shadow and accounting regressions**

```powershell
pytest -q tests/fund_rotation/test_production_adapters.py tests/fund_rotation/test_forward_validation.py tests/fund_rotation/test_attribution.py
```

Expected: production adapter tests and all existing forward-validation/accounting contract tests pass.

- [ ] **Step 5: Commit**

```powershell
git add agent/src/stockpred/fund_rotation/production_adapters.py agent/src/stockpred/fund_rotation/forward_validation.py agent/backtest/fund_rotation/attribution.py agent/tests/fund_rotation/test_production_adapters.py agent/tests/fund_rotation/test_forward_validation.py agent/tests/fund_rotation/test_attribution.py
git commit -m "feat: connect shadow to production strategy execution accounting"
```

### Task 5: Remove implicit formal fallbacks and verify the whole branch

**Files:**
- Modify: `agent/backtest/fund_rotation/runner.py`, `agent/backtest/fund_rotation/execution.py`, `agent/backtest/fund_rotation/execution_ledger_v2.py`, and `agent/src/stockpred/fund_rotation/forward_validation.py` only where Task 1–4 review identifies an implicit fallback
- Test: `agent/tests/fund_rotation/test_native_path_contracts.py`
- Documentation: update the design/spec comments only where the implemented public names differ from the plan

- [ ] **Step 1: Write contract tests for forbidden fallbacks**

Assert that the Runner’s formal path contains no call to `run_execution_loop` or `build_execution_ledger_from_pipeline_result`, production Shadow construction never defaults to `StoreScheduledSignalProvider`/deterministic adapters, and `MarketRuleResolver` cannot be constructed without an explicit source.

- [ ] **Step 2: Run the tests and fix only the failing contract boundary**

```powershell
pytest -q tests/fund_rotation/test_native_path_contracts.py
```

- [ ] **Step 3: Run the complete fund-rotation suite and static checks**

```powershell
pytest -q tests/fund_rotation
git diff --check
git status --short
```

Expected: all fund-rotation tests pass, `git diff --check` is clean, and only intended implementation commits/files remain.

- [ ] **Step 4: Commit any final boundary cleanup**

```powershell
git add agent/backtest/fund_rotation agent/src/stockpred/fund_rotation agent/tests/fund_rotation
git commit -m "test: enforce native execution and production shadow boundaries"
```

## Review Gates

- After each task, dispatch a fresh task reviewer with the task brief and diff package. Critical/Important findings enter the five-round fix loop; do not fix review findings in the controller session.
- After Task 5, dispatch one broad whole-branch reviewer against the design document and this plan. Any findings receive one consolidated fix dispatch and one scoped re-review.
- Do not claim completion until the full fund-rotation suite, `git diff --check`, and final review are clean.

