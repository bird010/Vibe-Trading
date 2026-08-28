# Fund Rotation Factor-Basis Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent stale `adj_factor` basis from leaking across Position and native live-order lifetimes while preserving valid corporate-action adjustments.

**Architecture:** Keep the existing economic-share accounting unchanged. Add small lifecycle helpers for Position transitions and native owner cleanup, then integrate them after actual execution/order state transitions in both execution paths. New basis initialization uses the current factor only after a real Position or live Order owner exists; missing factors are not silently replaced with `1.0`.

**Tech Stack:** Python, pytest, pandas, existing fund-rotation execution engine.

## Global Constraints

- Do not modify Lance data, factor generation, `share_adjustment.py`, public execution contracts, or strategy behavior.
- Ordinary execution basis ownership is positive Position only.
- Native basis ownership is positive Position or a live order until Position/Order basis are split in a later task.
- Native cleanup occurs after fills, cancel, supersede, replacement, and completion state updates are stable.
- New basis initialization must not use `setdefault()` or implicit `1.0` fallback.

### Task 1: Add failing lifecycle and ownership tests

**Files:**
- Modify: `agent/tests/fund_rotation/test_execution_module.py`
- Modify: `agent/tests/fund_rotation/test_native_execution.py`

- [x] **Step 1: Add tests for ordinary close/re-entry and native owner cleanup.**
- [x] **Step 2: Add tests for missing-factor new-owner initialization and pending-order preservation.**
- [x] **Step 3: Run the focused tests and confirm they fail for the expected missing behavior.**

### Task 2: Implement lifecycle helpers and ordinary execution integration

**Files:**
- Create: `agent/backtest/fund_rotation/factor_basis.py`
- Modify: `agent/backtest/fund_rotation/execution.py`

- [x] **Step 1: Implement transition classification and ordinary basis synchronization.**
- [x] **Step 2: Process actual filled transitions after `execute_with_capacity()`.**
- [x] **Step 3: Remove ordinary end-of-day `setdefault()` initialization.**
- [x] **Step 4: Run ordinary focused tests.**

### Task 3: Implement native owner-aware lifecycle handling

**Files:**
- Modify: `agent/backtest/fund_rotation/native_execution.py`
- Modify: `agent/tests/fund_rotation/test_native_execution.py`

- [x] **Step 1: Initialize a truly new order only after parent creation succeeds and reject missing-factor fallback.**
- [x] **Step 2: Preserve basis for retry/replacement and pending-order corporate actions.**
- [x] **Step 3: Clean basis only when no positive Position and no live order remain.**
- [x] **Step 4: Validate native ownership before persistence, including the state-hold fast path.**
- [x] **Step 5: Run native focused tests.**

### Task 4: Verify the production regression

**Files:**
- Inspect: `agent/runs/fund_rotation/423186595430/*`

- [x] **Step 1: Run focused regression tests.**
- [ ] **Step 2: Re-run the original 511220 scenario with the same snapshot and execution contract.**
- [ ] **Step 3: Verify no 2023-08-29 share adjustment, quantity remains 16,300, and NAV has no artificial jump.**
