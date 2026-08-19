# Fund Rotation Kline Momentum Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将后端 Momentum 策略证据叠加到基金轮动 K 线同一张图中，并保持日期缩放、聚焦和无证据降级行为。

**Architecture:** 扩展现有共享 `CandlestickChart`，增加可选的右侧数值轴和策略折线配置；`TradeMarkersChart` 负责把后端 `strategy_evidence` 的 Momentum 点按日期映射到蜡烛图日期轴。`WeeklyKlineEvidence` 使用合并后的图，不再重复渲染独立策略证据卡片。

**Tech Stack:** React 19、TypeScript、ECharts 6、Vitest、Testing Library。

## Global Constraints

- Momentum 必须使用 `InstrumentChartResponse.strategy_evidence` 的后端点位，禁止前端按收盘价重算。
- 价格使用左轴，Momentum 使用右轴，禁止混用量纲。
- 缺少 Momentum 时保持原有 K 线和交易证据可用。
- 只修改基金轮动 K 线相关组件，不重构其他图表。

---

### Task 1: Extend the shared candlestick chart with an optional overlay

**Files:**
- Modify: `frontend/src/components/charts/CandlestickChart.tsx`
- Test: `frontend/src/components/charts/__tests__/CandlestickChart.stockpred.test.tsx`

**Interfaces:**
- Consumes: existing price data, markers, zoom/focus props, plus optional `overlay?: { name: string; data: Array<{ time: string; value: number }>; color?: string }`.
- Produces: one ECharts option with the existing candlestick series and an optional line series using `yAxisIndex: 1`.

- [ ] **Step 1: Write the failing test**

  Add a test to `frontend/src/components/charts/__tests__/CandlestickChart.stockpred.test.tsx` with two price dates and two overlay points. Assert that the ECharts option passed to `setOption` contains a second y-axis, a line series with `yAxisIndex: 1`, and overlay values aligned to the same date order.

- [ ] **Step 2: Run the focused test and verify it fails**

  Run: `npm run test:run -- --run src/components/charts/__tests__/CandlestickChart.stockpred.test.tsx`

  Expected: FAIL because the chart does not yet accept or render an overlay.

- [ ] **Step 3: Implement the minimal overlay option**

  Normalize overlay points by `time`, map them against the existing price date order, use `null` for missing dates, add a right-side value axis, and preserve all existing zoom, marker, and focus behavior.

- [ ] **Step 4: Run the focused test and verify it passes**

  Run: `npm run test:run -- --run src/components/charts/__tests__/CandlestickChart.stockpred.test.tsx`

  Expected: PASS.

### Task 2: Wire backend Momentum evidence into the fund-rotation Kline

**Files:**
- Modify: `frontend/src/components/stockpred/fund-rotation/TradeMarkersChart.tsx`
- Modify: `frontend/src/components/stockpred/fund-rotation/WeeklyKlineEvidence.tsx`
- Modify: `frontend/src/components/stockpred/fund-rotation/FundRotationStrategyEvidenceChart.tsx`
- Test: `frontend/src/components/stockpred/fund-rotation/__tests__/TradeMarkersChart.test.tsx`
- Test: `frontend/src/components/stockpred/fund-rotation/__tests__/WeeklyKlineEvidence.test.tsx`

**Interfaces:**
- Consumes: `InstrumentChartResponse.strategy_evidence.indicators` and the existing `ohlcv` dates.
- Produces: a single Kline view containing price candles, trade markers, and the selected backend strategy indicator on the right axis; the existing strategy evidence component becomes a compact legend/metadata section or is removed from the per-instrument layout to avoid duplicate charts.

- [ ] **Step 1: Write the failing fund-rotation test**

  Add a Momentum fixture to the existing chart test and assert that `TradeMarkersChart` passes a backend Momentum overlay to `CandlestickChart`; add a no-evidence case asserting the existing chart still renders without an overlay.

- [ ] **Step 2: Run the focused tests and verify the new assertion fails**

  Run: `npm run test:run -- --run src/components/stockpred/fund-rotation/__tests__/TradeMarkersChart.test.tsx src/components/stockpred/fund-rotation/__tests__/WeeklyKlineEvidence.test.tsx`

  Expected: FAIL on the overlay assertion while existing Kline behavior remains covered.

- [ ] **Step 3: Wire the selected Momentum series**

  Select the backend `cluster_momentum`/Momentum series by default, pass its `{ date, value }` points into `TradeMarkersChart`, keep indicator selection callbacks and URL state intact, and remove the duplicate large SVG strategy chart from the yearly Kline layout.

- [ ] **Step 4: Run the focused fund-rotation tests**

  Run: `npm run test:run -- --run src/components/stockpred/fund-rotation/__tests__/TradeMarkersChart.test.tsx src/components/stockpred/fund-rotation/__tests__/WeeklyKlineEvidence.test.tsx src/components/stockpred/fund-rotation/__tests__/FundRotationStrategyEvidenceChart.test.tsx`

  Expected: PASS, with unavailable evidence still showing the explicit fallback.

### Task 3: Full verification

**Files:**
- Verify: `frontend/src/components/stockpred/fund-rotation`
- Verify: `docs/superpowers/specs/2026-08-17-fund-rotation-kline-momentum-overlay-design.md`

- [ ] **Step 1: Run all fund-rotation frontend tests**

  Run: `npm run test:run -- --run src/components/stockpred/fund-rotation`

- [ ] **Step 2: Run the frontend production build**

  Run: `npm run build`

- [ ] **Step 3: Run diff validation**

  Run: `git diff --check`

- [ ] **Step 4: Record the verification result**

  Report exact test/build results and any known pre-existing baseline failures without modifying unrelated chart code.
