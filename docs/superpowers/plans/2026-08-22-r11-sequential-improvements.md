# R11 Sequential Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four isolated R11-derived fund-rotation strategies and validate each on the same research contract.

**Architecture:** Each candidate lives in its own strategy directory and subclasses the frozen R11 session only to post-process the R11 target decision. The registry appends each strategy ID; focused behavior tests cover the single changed rule. Shared Runner, data access, execution, and historical strategies remain untouched.

**Tech Stack:** Python, pandas, Pydantic strategy contracts, pytest, existing fund-rotation batch/backtest service.

## Global Constraints

- Use strategy IDs `ai_rotation_r31_fast_exit`, `ai_rotation_r32_market_regime`, `ai_rotation_r33_quality_fallback`, and `ai_rotation_r34_staged_reentry`.
- Preserve R11 and all existing strategy IDs/defaults.
- Preserve PIT cutoff, weekly schedule, costs, slippage, capacity, lots, cash accounting, and evaluation policy.
- One principal hypothesis per candidate; no confirmation-interval selection.

### Task 1: Add R31 fast-exit candidate

**Files:**
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r31_fast_exit/__init__.py`
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r31_fast_exit/strategy.py`
- Create: `agent/tests/fund_rotation/test_ai_rotation_r31_fast_exit.py`
- Modify: `agent/backtest/fund_rotation/strategies/registry.py`
- Modify: `agent/tests/fund_rotation/test_strategy_catalog.py`
- Modify: `agent/tests/fund_rotation/test_fund_rotation_catalog_api.py`

**Interfaces:**
- Consumes R11 session decisions and causal weekly returns.
- Produces an R31 descriptor, session, and unchanged R11 config/requirements.

- [ ] Write failing tests for removal of an already-held cluster after a non-positive one-week cluster return, and no removal for a newly selected cluster.
- [ ] Implement a minimal R11 overlay that patches only affected targets, cash, diagnostics, and strategy identity.
- [ ] Run focused tests and catalog tests.

### Task 2: Add R32 market-regime candidate

**Files:**
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r32_market_regime/__init__.py`
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r32_market_regime/strategy.py`
- Create: `agent/tests/fund_rotation/test_ai_rotation_r32_market_regime.py`
- Modify: registry and the two exact catalog lists.

- [ ] Test positive, non-positive, and unavailable `510300.SH` four-week returns.
- [ ] Implement cash-only risk-off overlay without changing R11 when the benchmark is unavailable.
- [ ] Run focused tests and catalog tests.

### Task 3: Add R33 quality fallback candidate

**Files:**
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r33_quality_fallback/__init__.py`
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r33_quality_fallback/strategy.py`
- Create: `agent/tests/fund_rotation/test_ai_rotation_r33_quality_fallback.py`
- Modify: registry and the two exact catalog lists.

- [ ] Test one-slot fallback only under REJECT plus positive benchmark and vacant cash.
- [ ] Implement bounded `1/top_n` fallback to `510300.SH`; preserve cash otherwise.
- [ ] Run focused tests and catalog tests.

### Task 4: Add R34 staged-reentry candidate

**Files:**
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r34_staged_reentry/__init__.py`
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r34_staged_reentry/strategy.py`
- Create: `agent/tests/fund_rotation/test_ai_rotation_r34_staged_reentry.py`
- Modify: registry and the two exact catalog lists.

- [ ] Test half weight for new representatives and full weight for already-held representatives.
- [ ] Implement a fixed 50% new-entry overlay with residual cash.
- [ ] Run focused tests and catalog tests.

### Task 5: Paired research backtests and decision ledger

**Files:**
- Create: `experiments/ai_fund_rotation_r11_improvements_20260822/` artifacts.

- [ ] Register the frozen paired request for each candidate with R11 and preserve the request JSON.
- [ ] Poll each batch to verified terminal state and validate identity/comparability gates.
- [ ] Record fold metrics, Champion gate results, and retain R11 on any failed or non-improving candidate.
- [ ] Summarize the Champion path and remaining research-only limitations.
