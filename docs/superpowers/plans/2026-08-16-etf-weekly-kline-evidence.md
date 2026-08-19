# ETF 周度 K 线证据宏观查看 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将基金轮动回测详情的“K 线证据”改为按交易周纵向展示多个 ETF 的统一时间范围 K 线，并在右侧汇总周度目标权重、成交与阻断记录。

**Architecture:** 新增无副作用的周度聚合模块，将各 ETF chart 响应转换为排序稳定的周区块；详情 hook 在进入 K 线页签时并发加载所有标的并以 run/request 代次隔离迟到响应；`WeeklyKlineEvidence` 负责周卡片布局，继续复用 `TradeMarkersChart` 渲染单标的 K 线。后端接口和响应 schema 不变。

**Tech Stack:** React 19, TypeScript, Zustand, Tailwind CSS, ECharts adapter, Vitest, Testing Library。

## Global Constraints

- 使用中文 UI 文案。
- 不新增第三方依赖或后端接口。
- 不把不同 ETF 的原始价格叠加到同一价格轴；每个标的独立小倍图，共享周时间范围。
- 保留现有概览、收益曲线、单标的交易标记语义和数据快照信息。
- 不修改工作区已有的 `agent/backtest/fund_rotation/market_rules.py` 与 `agent/tests/fund_rotation/test_market_rules.py`。

---

### Task 1: 实现周度证据聚合纯函数

**Files:**
- Create: `frontend/src/components/stockpred/fund-rotation/weeklyEvidence.ts`
- Create: `frontend/src/components/stockpred/fund-rotation/__tests__/weeklyEvidence.test.ts`
- Reference: `frontend/src/components/stockpred/fund-rotation/types.ts`

**Interfaces:**
- Produces `normalizeEvidenceDate(value: unknown): string | null`。
- Produces `weekKeyForDate(date: string): string`，返回 ISO 周一日期 `YYYYMMDD`。
- Produces `groupWeeklyEvidence(charts: InstrumentChartResponse[]): WeeklyEvidenceWeek[]`。
- `WeeklyEvidenceWeek` 至少包含 `weekStart`, `weekEnd`, `instruments`, `events`；每个 instrument 包含 `tsCode`, `chart`, `coreDates`。
- 周事件包含 `kind: "signal" | "trade" | "blocked"`、日期、标的和用于表格展示的原始记录。

- [ ] **Step 1: Write the failing tests**

```ts
const baseChart = (tsCode: string): InstrumentChartResponse => ({
  run_id: "run-1", ts_code: tsCode, signals: [], trades: [], ohlcv: [],
  positions: [], orders: [], ohlcv_source: { available: true }, mode: "RESEARCH_ONLY",
});
const signal = (date: string, target_weight: number): InstrumentSignal => ({ date, target_weight });
const trade = (trade_date: string, action: "BUY" | "SELL", extra = {}): InstrumentTrade => ({
  trade_date, action, filled: action === "BUY" ? 100 : 50, price: 3.5, ...extra,
});

it("normalizes mixed date formats and groups them into the same ISO week", () => {
  expect(normalizeEvidenceDate("2024-01-08")).toBe("20240108");
  expect(normalizeEvidenceDate(20240108.0)).toBe("20240108");
  expect(weekKeyForDate("20240108")).toBe("20240108");
});

it("groups multiple ETF signals and trades by week in stable order", () => {
  const weeks = groupWeeklyEvidence([
    { ...baseChart("159915.SZ"), signals: [signal("20240109", 0.2)], trades: [trade("20240110", "BUY")] },
    { ...baseChart("510300.SH"), signals: [signal("2024-01-08", 0.8)], trades: [trade("20240108", "SELL")] },
  ]);
  expect(weeks.map((week) => week.weekStart)).toEqual(["20240108"]);
  expect(weeks[0].instruments.map((item) => item.tsCode)).toEqual(["159915.SZ", "510300.SH"]);
  expect(weeks[0].events.map((event) => `${event.date}:${event.tsCode}`)).toEqual([
    "20240108:510300.SH",
    "20240109:159915.SZ",
    "20240110:159915.SZ",
  ]);
});

it("marks blocked trades separately from filled and partial trades", () => {
  const weeks = groupWeeklyEvidence([
    { ...baseChart("510300.SH"), trades: [trade("20240108", "BUY", { blocked_reason: "no adv", filled: 0, status: "BLOCKED" })] },
  ]);
  expect(weeks[0].events[0]).toMatchObject({ kind: "blocked", blockedReason: "no adv" });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm --prefix frontend run test:run -- src/components/stockpred/fund-rotation/__tests__/weeklyEvidence.test.ts`

Expected: FAIL because `weeklyEvidence.ts` and the exported aggregation functions do not exist.

- [ ] **Step 3: Write the minimal implementation**

Implement date normalization by stripping non-digits and retaining the first eight digits; use UTC date arithmetic to calculate Monday and Sunday; create signal events from `date`/`week_ending`, trade events from `trade_date`, and classify trades with `blocked_reason`, blocked/rejected status, or non-positive filled quantity as `blocked`. Sort weeks by `weekStart`, instruments by `tsCode`, and events by date then `tsCode` then event kind. For each instrument, retain the original chart and calculate the core week date range from the week’s event dates.

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm --prefix frontend run test:run -- src/components/stockpred/fund-rotation/__tests__/weeklyEvidence.test.ts`

Expected: PASS with all date, grouping, ordering, and blocked classification assertions passing.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/stockpred/fund-rotation/weeklyEvidence.ts frontend/src/components/stockpred/fund-rotation/__tests__/weeklyEvidence.test.ts
git commit -m "feat: aggregate ETF evidence by trading week"
```

### Task 2: 扩展回测详情 hook 为多 ETF 并发加载

**Files:**
- Modify: `frontend/src/components/stockpred/fund-rotation/useBacktestDetail.ts`
- Modify: `frontend/src/components/stockpred/fund-rotation/types.ts`
- Modify: `frontend/src/components/stockpred/fund-rotation/__tests__/useBacktestDetail.test.ts`

**Interfaces:**
- `BacktestDetailState` gains `charts: Record<string, InstrumentChartResponse>`, `chartLoading`, `chartErrors: Record<string, string>` and `loadCharts(): Promise<void>`.
- Existing `chart`, `selectedInstrument`, and `selectInstrument` remain temporarily compatible for existing tests/components until Task 3 removes their only page consumer.
- `loadCharts` fetches every `detail.instruments[].ts_code` with limit `2000` and an abort signal.

- [ ] **Step 1: Write the failing tests**

```ts
const instrument = (tsCode: string): BacktestInstrument => ({
  ts_code: tsCode, has_signal: true, has_order: true, has_trade: true, has_position: true,
});
const chartResponse = (runId: string, tsCode: string): InstrumentChartResponse => ({
  run_id: runId, ts_code: tsCode, signals: [], trades: [], ohlcv: [], positions: [],
  orders: [], ohlcv_source: { available: true }, mode: "RESEARCH_ONLY",
});

it("loads chart evidence for every instrument in parallel", async () => {
  const runDetail = detail("run-1");
  runDetail.instruments = [instrument("510300.SH"), instrument("159915.SZ")];
  api.fetchBacktestDetail.mockResolvedValue(runDetail);
  api.fetchInstrumentChart.mockImplementation((_runId: string, tsCode: string) =>
    Promise.resolve(chartResponse("run-1", tsCode)),
  );

  await useBacktestDetail.getState().openRun("variant-1", "run-1");
  await useBacktestDetail.getState().loadCharts();

  expect(api.fetchInstrumentChart).toHaveBeenCalledTimes(2);
  expect(Object.keys(useBacktestDetail.getState().charts)).toEqual(["510300.SH", "159915.SZ"]);
});

it("keeps successful instrument charts when another instrument fails", async () => {
  api.fetchBacktestDetail.mockResolvedValue({
    ...detail("run-1"), instruments: [instrument("510300.SH"), instrument("159915.SZ")],
  });
  api.fetchInstrumentChart
    .mockResolvedValueOnce(chartResponse("run-1", "510300.SH"))
    .mockRejectedValueOnce(new Error("failed to load 159915.SZ"));
  await useBacktestDetail.getState().openRun("variant-1", "run-1");
  await useBacktestDetail.getState().loadCharts();
  expect(useBacktestDetail.getState().charts["510300.SH"]).toBeDefined();
  expect(useBacktestDetail.getState().chartErrors["159915.SZ"]).toContain("failed");
});

it("ignores chart responses from a previous run", async () => {
  let resolveRunA: ((value: InstrumentChartResponse) => void) | undefined;
  api.fetchBacktestDetail
    .mockResolvedValueOnce({ ...detail("run-a"), instruments: [instrument("510300.SH")] })
    .mockResolvedValueOnce({ ...detail("run-b"), instruments: [instrument("159915.SZ")] });
  api.fetchInstrumentChart
    .mockImplementationOnce(() => new Promise((resolve) => { resolveRunA = resolve; }))
    .mockResolvedValueOnce(chartResponse("run-b", "159915.SZ"));
  await useBacktestDetail.getState().openRun("variant-a", "run-a");
  const runACharts = useBacktestDetail.getState().loadCharts();
  await useBacktestDetail.getState().openRun("variant-b", "run-b");
  await useBacktestDetail.getState().loadCharts();
  resolveRunA?.(chartResponse("run-a", "510300.SH"));
  await runACharts;
  expect(useBacktestDetail.getState().selectedRunId).toBe("run-b");
  expect(useBacktestDetail.getState().charts["510300.SH"]).toBeUndefined();
  expect(useBacktestDetail.getState().charts["159915.SZ"]).toBeDefined();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm --prefix frontend run test:run -- src/components/stockpred/fund-rotation/__tests__/useBacktestDetail.test.ts`

Expected: FAIL because the state does not expose `charts`, `chartErrors`, or `loadCharts`.

- [ ] **Step 3: Write the minimal implementation**

Add a chart request generation counter and abort controller alongside the existing detail request lifecycle. `loadCharts` should clear chart state, set loading, call `Promise.allSettled` for the current detail instruments, retain fulfilled responses by `ts_code`, retain rejected non-abort errors by instrument code, and only commit the result if both request generation and selected run still match. Reset all chart-map state in `openRun` and `closeRun`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm --prefix frontend run test:run -- src/components/stockpred/fund-rotation/__tests__/useBacktestDetail.test.ts`

Expected: PASS, including existing single-instrument selection and stale-response tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/stockpred/fund-rotation/useBacktestDetail.ts frontend/src/components/stockpred/fund-rotation/types.ts frontend/src/components/stockpred/fund-rotation/__tests__/useBacktestDetail.test.ts
git commit -m "feat: load fund rotation charts by instrument"
```

### Task 3: 构建周度 K 线证据布局与事件表

**Files:**
- Create: `frontend/src/components/stockpred/fund-rotation/WeeklyKlineEvidence.tsx`
- Create: `frontend/src/components/stockpred/fund-rotation/__tests__/WeeklyKlineEvidence.test.tsx`
- Modify: `frontend/src/components/stockpred/fund-rotation/TradeMarkersChart.tsx`
- Modify: `frontend/src/components/stockpred/fund-rotation/BacktestDetailPanel.tsx`

**Interfaces:**
- `WeeklyKlineEvidence` accepts `{ weeks: WeeklyEvidenceWeek[]; charts: Record<string, InstrumentChartResponse>; chartErrors: Record<string, string>; loading: boolean; onRetry: () => void }`.
- Each week renders a two-column layout: left `TradeMarkersChart` instances keyed by ETF code, right a table containing target weight, trade status, filled quantity/price, and blocked reason.
- `TradeMarkersChart` accepts optional `dateRange?: { start: string; end: string }` and `height?: number`; when supplied, filters/sorts OHLCV and markers to the requested chart range.

- [ ] **Step 1: Write the failing component tests**

```tsx
const propsForTwoWeeks = (): WeeklyKlineEvidenceProps => ({
  weeks: [week("20240108", "510300.SH"), week("20240115", "159915.SZ")],
  charts: { "510300.SH": chartResponse("run-1", "510300.SH"), "159915.SZ": chartResponse("run-1", "159915.SZ") },
  chartErrors: {}, loading: false, onRetry: vi.fn(),
});

it("renders weekly cards in chronological order with charts on the left and records on the right", () => {
  render(<WeeklyKlineEvidence {...propsForTwoWeeks()} />);
  const headings = screen.getAllByRole("heading", { level: 4 });
  expect(headings[0]).toHaveTextContent("2024-01-08");
  expect(headings[1]).toHaveTextContent("2024-01-15");
  expect(screen.getByText("510300.SH")).toBeInTheDocument();
  expect(screen.getByText("目标权重")).toBeInTheDocument();
  expect(screen.getByText("阻断")).toBeInTheDocument();
});

it("shows one instrument error without hiding the other instrument chart", () => {
  render(<WeeklyKlineEvidence {...propsForTwoWeeks()} chartErrors={{ "159915.SZ": "failed to load 159915.SZ" }} />);
  expect(screen.getByText(/159915.SZ.*加载失败/)).toBeInTheDocument();
  expect(screen.getByTestId("shared-candlestick-chart")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm --prefix frontend run test:run -- src/components/stockpred/fund-rotation/__tests__/WeeklyKlineEvidence.test.tsx`

Expected: FAIL because the weekly component and its two-column event table do not exist.

- [ ] **Step 3: Implement the minimal component**

Render a page-level loading state, all-empty state, or retryable all-failed state before mapping weeks. For each week, render the week header and a responsive `lg:grid-cols-[minmax(0,1.6fr)_minmax(20rem,1fr)]` body. On the left, render one compact chart per instrument with the week’s context range. On the right, render a table with Chinese labels and explicit status badges for signal/成交/阻断; use the original raw values for details and show `—` for absent optional fields. Keep the K-line source version beside each chart or in its instrument header.

- [ ] **Step 4: Add date-range filtering to `TradeMarkersChart`**

Filter chart bars and markers to inclusive `dateRange` boundaries before building ECharts props, keep the existing default behavior when no range is passed, and set the weekly chart height to a compact value such as `300` from the new component. Add an assertion to `TradeMarkersChart.test.tsx` that a supplied range excludes out-of-range bars and markers.

- [ ] **Step 5: Replace the single-instrument selector in `BacktestDetailPanel`**

When `activeTab === "chart"`, call `loadCharts()` once after detail is ready, derive `weeks` with `groupWeeklyEvidence(Object.values(charts))`, and render `WeeklyKlineEvidence`. Remove the ETF `<select>` and the single-chart loading branch from this tab. Preserve the existing detail-level empty state and chart source/error semantics through the new component.

- [ ] **Step 6: Run component tests to verify they pass**

Run: `npm --prefix frontend run test:run -- src/components/stockpred/fund-rotation/__tests__/WeeklyKlineEvidence.test.tsx src/components/stockpred/fund-rotation/__tests__/TradeMarkersChart.test.tsx`

Expected: PASS with chronological weekly layout, left/right content, per-instrument error isolation, and date-range filtering verified.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/stockpred/fund-rotation/WeeklyKlineEvidence.tsx frontend/src/components/stockpred/fund-rotation/__tests__/WeeklyKlineEvidence.test.tsx frontend/src/components/stockpred/fund-rotation/TradeMarkersChart.tsx frontend/src/components/stockpred/fund-rotation/BacktestDetailPanel.tsx frontend/src/components/stockpred/fund-rotation/__tests__/TradeMarkersChart.test.tsx
git commit -m "feat: render weekly ETF kline evidence"
```

### Task 4: 回归验证与页面级接线测试

**Files:**
- Modify: `frontend/src/components/stockpred/fund-rotation/__tests__/BacktestDetailPanel.test.tsx` (create if absent)
- Modify: `frontend/src/pages/__tests__/StockPred.test.tsx` only if existing tab integration needs an assertion

- [ ] **Step 1: Add the page-level regression test**

Render the detail panel with a completed run containing two instruments and resolve both chart requests with events in two different weeks. Assert that the two week headings render in chronological order and that no ETF selector is present.

- [ ] **Step 2: Run the focused regression suite**

Run: `npm --prefix frontend run test:run -- src/components/stockpred/fund-rotation src/pages/__tests__/StockPred.test.tsx`

Expected: PASS with no failures in fund-rotation or StockPred tests.

- [ ] **Step 3: Run type-check and production build**

Run: `npm --prefix frontend run build`

Expected: TypeScript compilation and Vite production build exit with code 0.

- [ ] **Step 4: Review the diff and verify scope**

Run: `git diff --check; git status --short`

Expected: no whitespace errors; only the planned frontend files and any user-owned pre-existing modifications are present.

- [ ] **Step 5: Commit the regression coverage**

```bash
git add frontend/src/components/stockpred/fund-rotation/__tests__/BacktestDetailPanel.test.tsx frontend/src/pages/__tests__/StockPred.test.tsx
git commit -m "test: cover weekly ETF evidence integration"
```
