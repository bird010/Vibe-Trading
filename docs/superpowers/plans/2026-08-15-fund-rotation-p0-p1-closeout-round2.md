# Fund Rotation Shadow P0/P1 收口实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复最新 review 指出的真实 partial-fill residual retry 契约阻断，以及 historical PIT universe 过度扩大当前 reclustering / returns 可见范围的问题。

**Architecture:** Shadow 执行仍由 native execution engine 产生实际账户状态；validator 只在没有 residual 时要求实际 cash weight 等于目标 cash weight，有 residual 时记录实际现金并允许 execution-failure cash。Residual retry 通过显式 execution mode 传入 request factory，retry 使用空 targets 和恢复的 native state。PIT 方面将当前 signal-date PIT universe 作为 clustering/selection 候选，将历史 PIT universe 仅用于逐周 coverage denominator；默认 `CausalDataView.returns()` 恢复为当前 signal-date universe，并为 coverage 提供显式历史 returns 查询。

**Tech Stack:** Python, pytest, pandas, native fund-rotation execution ledger, CausalDataView.

## Global Constraints

- 不改变已冻结的策略 target、Parent、Attempt、Trade、NAV、P&L 语义。
- 不允许 partial fill 因实际现金偏离目标现金而被误判为契约违规。
- 不允许历史已退出 ETF 影响当前 signal date 的 reclustering。
- 所有行为变更必须先有会失败的回归测试，再写生产代码。
- 只修改与本次 P0/P1 直接相关的文件；完成后运行 fund-rotation 可信链针对性测试。

---

### Task 1: Shadow partial-fill accounting contract and explicit retry mode

**Files:**
- Modify: `agent/src/stockpred/fund_rotation/forward_validation.py`
- Modify: `agent/src/stockpred/fund_rotation/production_adapters.py`
- Test: `agent/tests/fund_rotation/test_forward_validation.py`
- Test: `agent/tests/fund_rotation/test_production_adapters.py`

**Interfaces:**
- `ShadowExecutionAdapter.execute_formal(..., execution_mode: str = "NEW_TARGET")`
- `ProductionShadowExecutionAdapter.execute_formal(..., execution_mode: str = "NEW_TARGET")`
- `request_factory(..., execution_mode=...)`
- `ShadowAccountState` keeps actual `cash`/`cash_weight`; residual state is allowed to differ from `decision.new_cash_weight`.

- [x] **Step 1: Write failing tests**
  - Add a production-accounting regression with native state cash 700 and NAV 1000 after a 300-filled buy toward 100% ETF target; assert the Shadow service executes, persists cash weight 0.7, and retains residual orders.
  - Add a request-factory regression asserting first call receives `NEW_TARGET`, the next residual call receives `RESIDUAL_RETRY` and empty target orders.
  - Extend the existing residual retry fixture so its accounting state exposes actual cash 0.7 on the first partial fill and 0.0 after completion.

- [x] **Step 2: Run the new tests and confirm the expected failure**
  - Run the two focused test nodes with `PYTHONPATH=agent python -m pytest ... -q`.
  - Expected failures are `ACCOUNT_CASH_WEIGHT_MISMATCH`, missing request mode, or equivalent behavior gap.

- [x] **Step 3: Implement the minimal contract change**
  - Pass `execution_mode = "RESIDUAL_RETRY"` from `ShadowExecutionService` when `retrying_residual` is true.
  - Extend formal adapter/request signatures compatibly with the default `"NEW_TARGET"`.
  - Ensure production request factory receives the mode; for retry, pass no new target signal/orders while retaining the restored native state.
  - Change cash-weight validation to require equality to target only when no residual orders remain; when residuals remain, validate actual cash backing and persist it.
  - Preserve cycle increment, idempotency, and native accounting consistency.

- [x] **Step 4: Run focused tests and refactor only if green**
  - Run `test_forward_validation.py` and `test_production_adapters.py` focused nodes, then the full two files.

- [x] **Step 5: Commit**
  - Commit as `fix fund rotation residual cash contract`.

---

### Task 2: Separate current clustering candidates from historical coverage candidates

**Files:**
- Modify: `agent/backtest/fund_rotation/causal_data.py`
- Modify: `agent/backtest/fund_rotation/strategies/correlation_all_members/strategy.py`
- Test: `agent/tests/fund_rotation/test_causal_data.py`
- Test: `agent/tests/fund_rotation/test_correlation_all_members_strategy.py`

**Interfaces:**
- `CausalDataView.returns()` returns only current `_universe_codes`.
- Add explicit `CausalDataView.historical_returns(..., candidate_codes=...)` for coverage/clustering support when needed.
- Current reclustering uses current signal-date PIT candidates; historical PIT lookup remains only in `historical_signal_date_eligible()` / coverage denominator logic.

- [x] **Step 1: Write failing tests**
  - Change the causal-data regression to assert `returns()` excludes an exited historical symbol while `historical_returns(candidate_codes=...)` can retrieve it.
  - Change the clustering regression so a historical-only symbol is available in raw data but is absent from the current signal-date candidate set; assert it is not passed to the reclustering distance matrix, while historical PIT eligibility is still consulted for coverage.

- [x] **Step 2: Run the new tests and confirm the expected failure**
  - Run the two focused nodes and confirm current implementation exposes/uses the historical-only symbol.

- [x] **Step 3: Implement the minimal data-boundary change**
  - Refactor adjusted-close/returns column filtering through a private candidate-code helper.
  - Keep public `returns()` and `adjusted_closes()` on current signal universe.
  - Add explicit historical query only if the coverage implementation needs it; do not broaden the default strategy surface.
  - In `CorrelationAllMembersSession.evaluate()`, compute clustering `valid_codes` from current signal-date PIT eligible candidates and the returns columns; use historical PIT sets only to compute weekly coverage eligibility.
  - Retain current-date selection filtering and existing historical denominator semantics.

- [x] **Step 4: Run focused tests and refactor only if green**
  - Run the two test files plus pipeline/runner PIT integration tests.

- [x] **Step 5: Commit**
  - Commit as `fix fund rotation PIT clustering boundary`.

---

### Task 3: Integrated verification and independent review

**Files:**
- No production changes unless Task 1/2 review finds a concrete defect.
- Test: trusted fund-rotation chain.

- [x] **Step 1: Run targeted trusted-chain tests**
  - Run execution, production adapters, causal data, correlation strategy, pipeline, runner contract, signal risk, data snapshot, and forward validation tests with a workspace-local pytest basetemp.

- [x] **Step 2: Run static checks**
  - Run `git diff --check` and inspect `git status`.

- [x] **Step 3: Dispatch a subagent review**
  - Ask a fresh subagent to review only the P0/P1 requirements against the implementation and tests, without modifying files or running the test suite.

- [x] **Step 4: Iterate if review finds a concrete gap**
  - Add a reproducer first, fix minimally, rerun the affected and trusted-chain tests, then repeat the review.

- [x] **Step 5: Commit final integrated change**
  - Commit the final validated changes and report exact test evidence.
