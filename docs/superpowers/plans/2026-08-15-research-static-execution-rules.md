# RESEARCH_ONLY 基金轮动静态执行规则降级实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让缺少正式 PIT 执行规则的 `RESEARCH_ONLY` 基金轮动 Batch 通过显式 `RESEARCH_STATIC_RULES` 上下文完成 Native Execution，同时保留 Runner 的 fail-fast 契约。

**Architecture:** 在 `market_rules.py` 集中构造研究静态规则记录、Resolver 和 instrument mapping；BatchService 只负责根据模式和可用上下文选择真实 PIT 或研究静态上下文并注入 Runner。NativeExecutionRequest、FundRotationExecutionEngine、Formal/OOS/Shadow 路径不改。

**Tech Stack:** Python 3.12, pandas, pytest, 现有 `MarketRuleResolver`、`InMemoryPITMarketRuleSource`、`FundRotationBacktestRunner`、`FundRotationExecutionEngine`。

## Global Constraints

- 仅 `StrategyBatchRequest.mode == "RESEARCH_ONLY"` 允许 Research Static fallback。
- 不删除 Runner 的 `EXECUTION_RULES_UNAVAILABLE` fail-fast 校验。
- 不修改 `NativeExecutionRequest` 为可选 resolver，不恢复 Legacy execution。
- fallback provenance 必须为 `source="RESEARCH_STATIC_RULES"`、`pit_verified=false`、`rule_version="research-cn-etf-v1"`。
- 真实 PIT resolver 和 mapping 同时存在时优先使用真实 PIT。
- 未识别 instrument type 不得静默映射为 `domestic_equity_etf`。

---

### Task 1: Research Static Rule Context

**Files:**
- Modify: `agent/backtest/fund_rotation/market_rules.py`
- Test: `agent/tests/fund_rotation/test_market_rules.py`

**Interfaces:**
- Produces `ResearchExecutionRuleContext` with `resolver`, `instruments`, `rule_version`, `source_id`, and `pit_verified`.
- Produces `build_research_static_execution_rule_context(dim_fund, universe_codes, evaluation_start_date, evaluation_end_date, snapshot_version)`.

- [ ] **Step 1: Write failing tests** for domestic ETF mapping, exact static rule values, provenance, and unknown instrument rejection.
- [ ] **Step 2: Run `pytest -q agent/tests/fund_rotation/test_market_rules.py`** and confirm the new imports/functions fail.
- [ ] **Step 3: Implement the immutable context, type classification from `dim_fund`, static records through `InMemoryPITMarketRuleSource`, and mapping builder.
- [ ] **Step 4: Run the focused market-rule tests** and confirm they pass.

### Task 2: Batch Wiring and Diagnostics

**Files:**
- Modify: `agent/src/stockpred/fund_rotation/batch_service.py`
- Modify: `agent/backtest/fund_rotation/runner.py` only if diagnostics cannot be attached at BatchService boundary
- Test: `agent/tests/fund_rotation/test_batch_service.py`

**Interfaces:**
- Batch execution constructs the context before Runner creation and passes `market_rule_resolver` and `market_rule_instruments`.
- The resulting `execution_diagnostics["execution_rule_evidence"]` contains source, PIT flag, and rule version.

- [ ] **Step 1: Add a Batch regression test** with no PIT context that asserts successful Native execution, non-empty decisions/orders/positions/equity, and Research Static evidence.
- [ ] **Step 2: Run the focused batch test** and confirm it fails with the current `EXECUTION_RULES_UNAVAILABLE` behavior.
- [ ] **Step 3: Implement explicit Research Static context selection for `RESEARCH_ONLY`; keep the existing runner fail-fast and pass the context into the Runner.
- [ ] **Step 4: Add/propagate rule evidence without changing native execution semantics.
- [ ] **Step 5: Run focused Batch tests and confirm they pass.

### Task 3: PIT Priority and Boundary Regressions

**Files:**
- Modify: `agent/tests/fund_rotation/test_batch_service.py`
- Modify: `agent/tests/fund_rotation/test_runner_native_execution.py` only if coverage is missing

- [ ] **Step 1: Add a test that supplies real PIT resolver/instruments and asserts the fallback builder is not called.
- [ ] **Step 2: Add/retain a test that directly constructs Runner without rule inputs and still receives `EXECUTION_RULES_UNAVAILABLE`.
- [ ] **Step 3: Add a test that an unsupported instrument type fails explicitly rather than using domestic ETF rules.
- [ ] **Step 4: Run the focused boundary tests and confirm they pass.

### Task 4: Full Verification

**Files:**
- Test only: existing fund-rotation test suite

- [ ] **Step 1: Run the complete fund-rotation pytest suite.
- [ ] **Step 2: Run native execution invariant tests covering lot rounding, residual retry, settlement, price limits, sell-before-buy, corporate action, and state restore.
- [ ] **Step 3: Inspect the diff for unintended mode, engine, or Formal/Shadow changes.
- [ ] **Step 4: Report verification results and remaining limitations; do not claim the historical run is rerun unless the configured data service is available.
