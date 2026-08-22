# Fund Rotation Chart History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure cluster-interval charts receive OHLCV rows covering the selected backtest period instead of only the most recent 2000 rows.

**Architecture:** Extend the read-only instrument-chart API with optional inclusive date bounds. The frontend passes the backtest evaluation bounds when loading chart evidence, while preserving the existing limit as a safety cap. The backend filters by date before applying the cap and returns the latest rows within the requested range.

**Tech Stack:** FastAPI/Python, pandas/Lance, React/TypeScript, Vitest, pytest.

## Global Constraints

- Preserve existing chart endpoint compatibility when date bounds are omitted.
- Keep the existing `limit` safety cap.
- Do not modify backtest artifacts or strategy implementations.
- Use the existing request/cache/error handling patterns.

---

### Task 1: Lock down the backend date-window behavior

**Files:**
- Modify: `agent/tests/fund_rotation/test_batch_backend_review_regressions.py`
- Modify: `agent/src/api/fund_rotation_routes.py`

**Interfaces:**
- Extend `get_instrument_chart` with optional `start_date` and `end_date` query parameters.
- Keep the response schema unchanged.

- [ ] **Step 1: Write the failing test**

Add a route test with fake Lance rows containing an old row before the requested window and a newer row after it. Request `start_date=20170707&end_date=20180104`, and assert the returned OHLCV contains only rows in that inclusive range, including the old 2017 row.

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `pytest agent/tests/fund_rotation/test_batch_backend_review_regressions.py -k "chart_date_window" -v`

Expected: FAIL because the current route does not accept date bounds and still uses the unbounded tail.

- [ ] **Step 3: Implement the minimal route change**

Parse optional `start_date` and `end_date`, filter the Lance frame by `trade_date` inclusively when present, sort ascending, then apply `tail(limit)`. Preserve current behavior for callers that omit both bounds.

- [ ] **Step 4: Run the focused test and verify it passes**

Run: `pytest agent/tests/fund_rotation/test_batch_backend_review_regressions.py -k "chart_date_window" -v`

Expected: PASS.

### Task 2: Pass backtest period bounds from the frontend

**Files:**
- Modify: `frontend/src/components/stockpred/fund-rotation/api.ts`
- Modify: `frontend/src/components/stockpred/fund-rotation/useBacktestDetail.ts`
- Modify: `frontend/src/components/stockpred/fund-rotation/__tests__/useBacktestDetail.test.ts`

**Interfaces:**
- Add optional `startDate` and `endDate` arguments to `backtestChartUrl` and `fetchInstrumentChart`.
- `loadCharts` passes `detail.period.evaluation_start_date` and `detail.period.evaluation_end_date`.
- Single-instrument chart loading also passes the same period bounds.

- [ ] **Step 1: Write the failing test**

Add a hook test whose detail period is `20170707..20220729`, load chart evidence, and assert `fetchInstrumentChart` receives the two date bounds in addition to the existing run ID, code, limit, and abort signal.

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `npm --prefix frontend test -- --run src/components/stockpred/fund-rotation/__tests__/useBacktestDetail.test.ts -t "passes the backtest period"`

Expected: FAIL because the current chart request only passes run ID, code, limit, and signal.

- [ ] **Step 3: Implement the minimal frontend change**

Add the query parameters only when values are present, preserving existing URL behavior for callers without a period. Thread the period values through both chart-loading paths.

- [ ] **Step 4: Run the focused test and verify it passes**

Run: `npm --prefix frontend test -- --run src/components/stockpred/fund-rotation/__tests__/useBacktestDetail.test.ts -t "passes the backtest period"`

Expected: PASS.

### Task 3: Run regression verification

**Files:**
- No additional files.

- [ ] **Step 1: Run backend fund-rotation tests**

Run: `pytest agent/tests/fund_rotation/test_batch_backend_review_regressions.py agent/tests/fund_rotation/test_backtest_detail_api.py -q`

Expected: PASS with zero failures.

- [ ] **Step 2: Run frontend focused tests**

Run: `npm --prefix frontend test -- --run src/components/stockpred/fund-rotation/__tests__/useBacktestDetail.test.ts src/components/stockpred/fund-rotation/__tests__/ClusterIntervalChart.test.tsx src/components/stockpred/fund-rotation/__tests__/BacktestDetailPanel.test.tsx`

Expected: PASS with zero failures.

- [ ] **Step 3: Verify the live endpoint semantics against run d10bfc78020b**

Request each of `510300.SH`, `511820.SH`, and `518880.SH` with `start_date=20170707&end_date=20180104` and confirm their returned OHLCV date ranges overlap the requested interval.

