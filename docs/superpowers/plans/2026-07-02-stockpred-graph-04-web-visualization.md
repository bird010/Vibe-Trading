# StockPred Graph Web 与蜡烛图诊断 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Vibe 现有页面体系中增加 StockPred Graph 配置与启动页，并在 RunDetail 中显示相关证券蜡烛图、交易标记和独立 Graph 信号诊断。

**Architecture:** `/stockpred` 页面调用专用 API 并通过 SSE 跟踪持久 run；完成后跳转现有 `/runs/:runId`。RunDetail 只对 `strategy_type=stockpred_graph` 条件增加 Graph 标签，现有蜡烛图、净值和非 Graph run 保持不变。

**Tech Stack:** React 19、TypeScript 5.7、React Router 7、ECharts 6、Tailwind、i18next、Vitest、Testing Library

## Global Constraints

- 必须先完成 `2026-07-02-stockpred-graph-03-backtest-cli-api.md`。
- 新页面复用现有 card、border、muted、primary、Skeleton 和错误样式，不增加第二套视觉系统。
- parity mode 下 Top-N=50、eval step=5 等兼容参数只读；research mode 才可编辑。
- 浏览器不加载全市场 signals/行情，只按选择证券加载 K 线与 Graph 信号轨。
- Graph score 使用独立比例尺，不叠加到价格 Y 轴。
- 非 Graph RunDetail 不显示 Graph 标签，API 兼容字段缺失时正常工作。
- 中文和英文 i18n key 同步增加，不在组件中硬编码用户可见文案。
- 设计依据：`docs/superpowers/specs/2026-07-02-stockpred-graph-vibe-integration-design.md`。

---

## File Structure

- Modify `frontend/src/lib/api.ts`：StockPred API 类型和 client 方法。
- Create `frontend/src/pages/StockPred.tsx`：数据状态、配置、进度和最近运行。
- Create `frontend/src/pages/__tests__/StockPred.test.tsx`。
- Modify `frontend/src/router.tsx`：懒加载 `/stockpred`。
- Modify `frontend/src/components/layout/Layout.tsx`：侧栏导航。
- Modify `frontend/src/i18n/locales/zh-CN.json`、`en.json`。
- Create `frontend/src/components/charts/GraphSignalPanel.tsx`：独立信号折线和当前点摘要。
- Create `frontend/src/components/charts/__tests__/GraphSignalPanel.test.tsx`。
- Modify `frontend/src/pages/RunDetail.tsx`：条件 Graph 标签、按需缓存信号。
- Modify `frontend/src/components/charts/CandlestickChart.tsx`：按执行状态区分交易标记。
- Create `frontend/src/pages/__tests__/RunDetail.graph.test.tsx`。

### Task 1: 增加前端 API 契约

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Create: `frontend/src/lib/__tests__/stockpredApi.test.ts`

**Interfaces:**
- Produces: `StockPredStatus`、`GraphBacktestDefaults`、`GraphBacktestRequest`、`GraphBacktestCreated`、`GraphRunSummary`。
- Produces: `GraphSignalPoint` 和 `RunData.graph_signal_series`。
- Produces: `api.getStockPredStatus()`、`getGraphBacktestDefaults()`、`listGraphBacktests()`、`createGraphBacktest()`、`graphBacktestStreamUrl()`。

- [ ] **Step 1: 写请求路径和类型行为测试**

```typescript
it("creates a graph backtest through the dedicated endpoint", async () => {
  fetchMock.mockResolvedValueOnce(jsonResponse({
    run_id: "graph_123",
    events_url: "/stockpred/graph/backtests/graph_123/events",
  }));
  const result = await api.createGraphBacktest({
    start: "2025-01-01",
    end: "2025-03-31",
    mode: "parity",
  });
  expect(fetchMock).toHaveBeenCalledWith(
    expect.stringContaining("/stockpred/graph/backtests"),
    expect.objectContaining({ method: "POST" }),
  );
  expect(result.run_id).toBe("graph_123");
});
```

- [ ] **Step 2: 运行并确认失败**

Run: `npm run test:run -- src/lib/__tests__/stockpredApi.test.ts`

Working directory: `frontend`

Expected: FAIL，client 方法不存在。

- [ ] **Step 3: 增加精确类型和 client 方法**

```typescript
export interface StockPredStatus {
  ready: boolean;
  contract: "stockpred-data/v1";
  root?: string;
  as_of?: string;
  tables: Array<{ name: string; version?: number; max_date?: string; status: string }>;
  error_code?: string;
  message?: string;
}

export interface GraphBacktestRequest {
  start: string;
  end: string;
  mode: "parity" | "research";
  top_n?: number;
  eval_step?: number;
}

export interface GraphBacktestDefaults {
  mode: "parity";
  benchmark_code: "000300.SH";
  top_n: 50;
  eval_step: 5;
  forward_days: 5;
  locked_fields: string[];
}

export interface GraphBacktestCreated {
  run_id: string;
  events_url: string;
}

export interface GraphSignalPoint {
  time: string;
  code: string;
  score: number;
  rank: number;
  direction: string;
  stage: string;
  action: string;
  risk_adjustment?: number;
}

export interface GraphRunSummary {
  run_id: string;
  status: string;
  phase?: string;
  created_at: string;
  start: string;
  end: string;
  mode: "parity" | "research";
}
```

在 `RunData` 增加 `graph_signal_series?: Record<string, GraphSignalPoint[]>`；在 `TradeMarker` 增加 `status?: "FILLED" | "PARTIAL" | "REJECTED"` 和 `exit_delay_days?: number`。SSE URL 使用现有 `withAuthQuery()`，确保远程认证行为一致。`listGraphBacktests()` 调用 `GET /stockpred/graph/backtests?limit=20`。

- [ ] **Step 4: 运行测试和 TypeScript 编译**

Run: `npm run test:run -- src/lib/__tests__/stockpredApi.test.ts`

Expected: PASS。

Run: `npx tsc -b --pretty false`

Expected: 退出码 0。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/lib/api.ts frontend/src/lib/__tests__/stockpredApi.test.ts
git commit -m "feat(stockpred): add frontend API contract"
```

### Task 2: 实现独立 StockPred 配置与启动页

**Files:**
- Create: `frontend/src/pages/StockPred.tsx`
- Create: `frontend/src/pages/__tests__/StockPred.test.tsx`

**Interfaces:**
- Consumes: Task 1 API client。
- Produces: `StockPred` 页面。
- 行为：加载 status/defaults；创建 run；消费 `progress/done/error`；完成后显示或跳转 RunDetail。

- [ ] **Step 1: 写 readiness、锁定字段和启动流程测试**

```typescript
it("disables start when the StockPred contract is not ready", async () => {
  vi.mocked(api.getStockPredStatus).mockResolvedValue({
    ready: false, contract: "stockpred-data/v1", root: "", tables: [], error_code: "STOCKPRED_ROOT_MISSING",
  });
  renderStockPred();
  expect(await screen.findByRole("button", { name: /启动回测/i })).toBeDisabled();
});


it("locks parity fields and navigates to the completed run", async () => {
  seedReadyApi();
  const user = userEvent.setup();
  renderStockPred();
  expect(await screen.findByLabelText(/Top N/i)).toBeDisabled();
  await user.click(screen.getByRole("button", { name: /启动回测/i }));
  emitSse("done", { run_id: "graph_123" });
  expect(navigate).toHaveBeenCalledWith("/runs/graph_123");
});
```

- [ ] **Step 2: 运行并确认失败**

Run: `npm run test:run -- src/pages/__tests__/StockPred.test.tsx`

Working directory: `frontend`

Expected: FAIL，页面不存在。

- [ ] **Step 3: 实现页面状态机**

```typescript
type RunUiState =
  | { kind: "idle" }
  | { kind: "starting" }
  | { kind: "running"; runId: string; done: number; total: number; evalDate?: string }
  | { kind: "failed"; runId?: string; message: string };

async function startBacktest() {
  setRunState({ kind: "starting" });
  const created = await api.createGraphBacktest(toRequest(form));
  setRunState({ kind: "running", runId: created.run_id, done: 0, total: 0 });
  const source = new EventSource(api.graphBacktestStreamUrl(created.run_id));
  source.addEventListener("progress", (event) => setRunState(parseProgress(created.run_id, event)));
  source.addEventListener("done", () => {
    source.close();
    navigate(`/runs/${created.run_id}`);
  });
  source.addEventListener("error", (event) => {
    source.close();
    setRunState({ kind: "failed", runId: created.run_id, message: parseSseError(event) });
  });
}
```

页面分为数据状态、回测配置、运行进度、最近 Graph runs 四张现有风格卡片。组件卸载时关闭 EventSource。research mode 打开后才启用 Top-N 和评价步长输入。
首屏并行调用 `getStockPredStatus()`、`getGraphBacktestDefaults()` 和 `listGraphBacktests()`；最近运行按后端顺序展示，点击行跳转 `/runs/${runId}`，不在前端扫描通用 runs。

- [ ] **Step 4: 运行页面测试**

Run: `npm run test:run -- src/pages/__tests__/StockPred.test.tsx`

Expected: PASS，包括 POST 失败、SSE 错误和组件卸载关闭连接。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/pages/StockPred.tsx frontend/src/pages/__tests__/StockPred.test.tsx
git commit -m "feat(stockpred): add graph backtest page"
```

### Task 3: 接入路由、侧栏与国际化

**Files:**
- Modify: `frontend/src/router.tsx`
- Modify: `frontend/src/components/layout/Layout.tsx`
- Modify: `frontend/src/i18n/locales/zh-CN.json`
- Modify: `frontend/src/i18n/locales/en.json`
- Create: `frontend/src/components/layout/__tests__/StockPredNavigation.test.tsx`

**Interfaces:**
- Produces: `/stockpred` 懒加载路由。
- Produces: `layout.stockPred` 和 `stockPred.*` 双语 key。

- [ ] **Step 1: 写导航激活测试**

```typescript
it("renders and activates the StockPred sidebar item", () => {
  renderLayoutAt("/stockpred");
  const link = screen.getByRole("link", { name: /StockPred/i });
  expect(link).toHaveAttribute("href", "/stockpred");
  expect(link.className).toContain("text-primary");
});
```

- [ ] **Step 2: 运行并确认失败**

Run: `npm run test:run -- src/components/layout/__tests__/StockPredNavigation.test.tsx`

Expected: FAIL，导航项不存在。

- [ ] **Step 3: 最小接入现有体系**

```typescript
const StockPred = lazy(() =>
  import("@/pages/StockPred").then((m) => ({ default: m.StockPred })),
);
// routes: { path: "/stockpred", element: wrap(StockPred) }
// NAV: { to: "/stockpred", icon: Network, label: t("layout.stockPred") }
```

中文 key 使用“StockPred”，说明文案使用“Graph 回测”；英文使用“StockPred”和“Graph backtest”。保持两个 JSON 的 key 集合一致。

- [ ] **Step 4: 运行导航与 i18n 回归**

Run: `npm run test:run -- src/components/layout/__tests__/StockPredNavigation.test.tsx src/components/layout/__tests__/ConnectionBanner.test.tsx`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/router.tsx frontend/src/components/layout/Layout.tsx frontend/src/i18n/locales/zh-CN.json frontend/src/i18n/locales/en.json frontend/src/components/layout/__tests__/StockPredNavigation.test.tsx
git commit -m "feat(stockpred): add navigation and translations"
```

### Task 4: 实现独立 Graph 信号诊断图

**Files:**
- Create: `frontend/src/components/charts/GraphSignalPanel.tsx`
- Create: `frontend/src/components/charts/__tests__/GraphSignalPanel.test.tsx`

**Interfaces:**
- Consumes: `GraphSignalPoint[]`。
- Produces: score/risk_adjustment 独立折线、rank/direction/stage/action 当前摘要。

- [ ] **Step 1: 写空状态与独立坐标测试**

```typescript
it("renders score diagnostics without a price axis", () => {
  render(<GraphSignalPanel symbol="000001.SZ" points={POINTS} />);
  expect(screen.getByText(/rank 7/i)).toBeInTheDocument();
  expect(screen.getByText(/expansion/i)).toBeInTheDocument();
  expect(echartsSetOption).toHaveBeenCalledWith(expect.objectContaining({
    yAxis: expect.objectContaining({ name: "Score" }),
  }));
  expect(JSON.stringify(echartsSetOption.mock.calls[0][0])).not.toContain("candlestick");
});
```

- [ ] **Step 2: 运行并确认失败**

Run: `npm run test:run -- src/components/charts/__tests__/GraphSignalPanel.test.tsx`

Expected: FAIL，组件不存在。

- [ ] **Step 3: 使用现有 ECharts 初始化模式实现**

```typescript
const option: EChartsOption = {
  tooltip: { trigger: "axis" },
  xAxis: { type: "category", data: points.map((p) => p.time) },
  yAxis: { type: "value", name: "Score", scale: true },
  series: [
    { name: "score", type: "line", showSymbol: true, data: points.map((p) => p.score) },
    ...(points.some((p) => p.risk_adjustment != null)
      ? [{ name: "risk adjustment", type: "line" as const, data: points.map((p) => p.risk_adjustment ?? null) }]
      : []),
  ],
};
```

复用 `frontend/src/lib/echarts.ts` 和 chart theme；组件负责 dispose/resize cleanup，空数组显示本地化空状态。

- [ ] **Step 4: 运行组件测试**

Run: `npm run test:run -- src/components/charts/__tests__/GraphSignalPanel.test.tsx`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/charts/GraphSignalPanel.tsx frontend/src/components/charts/__tests__/GraphSignalPanel.test.tsx
git commit -m "feat(stockpred): add graph signal diagnostics chart"
```

### Task 5: 条件扩展 RunDetail

**Files:**
- Modify: `frontend/src/pages/RunDetail.tsx`
- Modify: `frontend/src/components/charts/CandlestickChart.tsx`
- Create: `frontend/src/components/charts/__tests__/CandlestickChart.stockpred.test.tsx`
- Create: `frontend/src/pages/__tests__/RunDetail.graph.test.tsx`
- Modify: `frontend/src/i18n/locales/zh-CN.json`
- Modify: `frontend/src/i18n/locales/en.json`

**Interfaces:**
- Extends: `Tab` 为 `"graph"`。
- Extends: chart symbol cache，保存对应 `graph_signal_series`。
- 条件：`run.run_context?.strategy_type === "stockpred_graph"`。

- [ ] **Step 1: 写 Graph 条件标签和按证券加载测试**

```typescript
it("shows Graph diagnostics only for stockpred_graph runs", async () => {
  vi.mocked(api.getRun).mockResolvedValue(graphRun({ strategy_type: "stockpred_graph" }));
  renderRunDetail("graph_123");
  expect(await screen.findByRole("button", { name: /Graph 诊断/i })).toBeInTheDocument();
});


it("keeps the Graph tab hidden for normal runs", async () => {
  vi.mocked(api.getRun).mockResolvedValue(normalRun());
  renderRunDetail("run_123");
  await screen.findByText("run_123");
  expect(screen.queryByRole("button", { name: /Graph/i })).not.toBeInTheDocument();
});


it("renders rejected and delayed trade markers with distinct labels", () => {
  render(<CandlestickChart data={BARS} markers={EXECUTION_MARKERS} />);
  const option = echartsSetOption.mock.calls.at(-1)?.[0];
  expect(JSON.stringify(option)).toContain("X");
  expect(JSON.stringify(option)).toContain("D");
});
```

- [ ] **Step 2: 运行并确认失败**

Run: `npm run test:run -- src/pages/__tests__/RunDetail.graph.test.tsx src/components/charts/__tests__/CandlestickChart.stockpred.test.tsx`

Expected: FAIL，Graph 标签不存在。

- [ ] **Step 3: 条件增加标签和诊断组件**

```typescript
type Tab = "chart" | "trades" | "runCard" | "code" | "validation" | "graph";
const isGraphRun = run?.run_context?.strategy_type === "stockpred_graph";
const TABS = [
  // existing tabs unchanged
  { id: "graph" as const, label: i18n.t("runDetail.graphDiagnostics"), icon: Network, hidden: !isGraphRun },
];
```

`cacheFromRun()` 和 `loadChartSymbol()` 合并 `graph_signal_series`；Graph tab 使用当前 `selectedSymbol` 的点渲染 `GraphSignalPanel`。蜡烛图继续使用现有 `trade_markers`，不把 score 塞入 `indicator_series`。

`CandlestickChart` 的 markPoint 映射固定为：成功 BUY=`B` 绿色、成功 SELL=`S` 红色、PARTIAL=`P` 橙色、REJECTED=`X` 灰色、`exit_delay_days>0`=`D` 紫色；tooltip 显示 `status/reason`。没有 `status` 的旧 marker 保持原 B/S 行为。

- [ ] **Step 4: 运行前端完整验证**

Run: `npm run test:run`

Working directory: `frontend`

Expected: 全部 Vitest PASS。

Run: `npm run build`

Expected: TypeScript 和 Vite 构建成功。

- [ ] **Step 5: 浏览器验收并提交**

启动本地 API 和前端后验证：

1. `/stockpred` readiness 和表水位正确；
2. parity 字段锁定，research 字段可编辑；
3. 启动后进度持续更新，完成跳转 `/runs/:runId`；
4. 蜡烛图仅按选中证券加载并显示交易标记；
5. Graph 诊断与证券联动；
6. 普通 run 没有 Graph 标签。

```bash
git add frontend/src/pages/RunDetail.tsx frontend/src/pages/__tests__/RunDetail.graph.test.tsx frontend/src/components/charts/CandlestickChart.tsx frontend/src/components/charts/__tests__/CandlestickChart.stockpred.test.tsx frontend/src/i18n/locales/zh-CN.json frontend/src/i18n/locales/en.json
git commit -m "feat(stockpred): integrate graph diagnostics into run detail"
```

### Task 6: 全链路回归与切换验收

**Files:**
- Modify: `docs/superpowers/specs/2026-07-02-stockpred-graph-vibe-integration-design.md`（仅追加实际验收结果）
- Create: `docs/stockpred-graph-operations.md`（运维命令和错误码）

**Interfaces:**
- Produces: 可重复执行的验收记录和运维入口。

- [ ] **Step 1: 运行后端完整测试**

Run: `python -m pytest agent/tests/stockpred -q`

Expected: PASS。

Run: `python -m pytest agent/tests/test_stockpred_loader.py agent/tests/test_run_card.py agent/tests/test_alpha_compare_api.py -q`

Expected: PASS。

- [ ] **Step 2: 运行前端完整测试与构建**

Run: `npm run test:run`

Run: `npm run build`

Working directory: `frontend`

Expected: 均成功。

- [ ] **Step 3: 运行三个 Golden 窗口差分**

Run: `vibe-trading stockpred graph-backtest --start 2025-01-02 --end 2025-03-31 --mode parity --parity-golden tmp/golden/normal --json`

Run: `vibe-trading stockpred graph-backtest --start 2024-03-01 --end 2024-05-31 --mode parity --parity-golden tmp/golden/pit-boundary --json`

Run: `vibe-trading stockpred graph-backtest --start 2024-09-02 --end 2024-11-29 --mode parity --parity-golden tmp/golden/execution-edge --json`

Expected：normal、PIT 公告边界、交易限制三个窗口的 `parity.json` 均为 `passed=true`，keys/排序/选股/成交事件无差异，数值满足设计容差。

- [ ] **Step 4: 写实际运维文档和验收证据**

`docs/stockpred-graph-operations.md` 必须记录：`STOCKPRED_DATA_ROOT`、status 命令、Graph 回测命令、API 路径、run 工件位置、稳定错误码、golden 对账命令、失败排查顺序。设计文档只追加实际提交号、测试命令、三个 run_id 和 parity 摘要，不改已确认决策。

- [ ] **Step 5: 提交验收文档**

```bash
git add docs/stockpred-graph-operations.md docs/superpowers/specs/2026-07-02-stockpred-graph-vibe-integration-design.md
git commit -m "docs(stockpred): record graph migration verification"
```
