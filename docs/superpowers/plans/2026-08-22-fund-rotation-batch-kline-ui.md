# 基金轮动批次与 K 线证据 UI 调整实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 调整基金轮动批次布局，并让目标权重未变化的交易在 K 线中使用浅色 B/S 标记。

**Architecture:** 批次页继续由 `FundRotationTab` 管理批次状态，只将历史批次列表从配置栏移动到原进度栏，并用 flex 收缩约束保护策略编辑器控件。K 线证据由 `TradeMarkersChart` 计算领域含义的 `muted` 标志，共享 `CandlestickChart` 只负责通用视觉渲染。

**Tech Stack:** React 18、TypeScript、Tailwind CSS、Vitest、Testing Library、ECharts。

## Global Constraints

- 不修改批次 API、SSE、历史批次数据结构或后台任务行为。
- 不改变目标权重计算、成交执行和 K 线数据范围。
- 首次出现、目标权重变化、缺少可比权重时保持当前 B/S 样式。
- 只有存在上一条目标权重且与当前相等时使用浅色 B/S 样式。
- 遵循 TDD：每个行为变更先写会失败的测试，再写最小实现。

---

## 文件映射

- Modify: `frontend/src/components/stockpred/fund-rotation/FundRotationTab.tsx` — 移动历史批次列表并替换中栏内容。
- Modify: `frontend/src/components/stockpred/fund-rotation/StrategyVariantsEditor.tsx` — 约束长策略名称和变体标签的 flex 行为。
- Test: `frontend/src/components/stockpred/__tests__/FundRotationTab.test.tsx` — 验证新栏标题、空态和批次行。
- Test: `frontend/src/components/stockpred/fund-rotation/__tests__/StrategyVariantsEditor.test.tsx` — 验证布局结构 class。
- Modify: `frontend/src/lib/api.ts` — 给通用交易标记增加可选 `muted` 属性。
- Modify: `frontend/src/components/stockpred/fund-rotation/TradeMarkersChart.tsx` — 计算每笔交易是否目标权重未变。
- Modify: `frontend/src/components/charts/CandlestickChart.tsx` — 根据 `muted` 渲染浅色正常 B/S 标记。
- Test: `frontend/src/components/stockpred/fund-rotation/__tests__/TradeMarkersChart.test.tsx` — 验证权重比较输出。
- Test: `frontend/src/components/charts/__tests__/CandlestickChart.test.tsx`（若目录已有测试则追加到现有文件）— 验证标记视觉配置。

## Task 1: 批次栏改为历史批次栏

**Files:**
- Modify: `frontend/src/components/stockpred/fund-rotation/FundRotationTab.tsx`
- Modify: `frontend/src/components/stockpred/__tests__/FundRotationTab.test.tsx`

**Interfaces:**
- Consumes: 现有 `batches`, `activeBatchId`, `selectBatch`, `BATCH_STAGE_LABELS`。
- Produces: 中栏标题“历史批次”；批次按钮调用 `selectBatch(batch.batch_id)`；空列表显示“暂无批次”。

- [ ] **Step 1: 修改测试夹具并写失败测试**

在 `FundRotationTab.test.tsx` 增加一个含历史批次的测试状态，断言：

```tsx
it("moves batch history into the former progress column", () => {
  mockState.batches = [{ batch_id: "batch-history", status: "SUCCEEDED" }];
  render(<FundRotationTab />);

  expect(screen.getByText("历史批次")).toBeInTheDocument();
  expect(screen.getByText("batch-history…")).toBeInTheDocument();
  expect(screen.queryByText("批次进度")).toBeNull();
});
```

同时将原“renders progress section”测试改为无批次时断言“历史批次”和“暂无批次”，并确保测试状态在 `beforeEach` 恢复为空列表，避免测试间共享状态。

- [ ] **Step 2: 运行测试确认按预期失败**

Run: `npm --prefix frontend test -- --run src/components/stockpred/__tests__/FundRotationTab.test.tsx`

Expected: 新测试因当前仍渲染“批次进度”且历史批次仍在配置栏而失败；若先出现共享 mock 状态污染，先在测试的 `beforeEach` 恢复 `mockState.batches = []`，不要修改生产代码。

- [ ] **Step 3: 实现最小布局移动**

在 `FundRotationTab.tsx`：

1. 删除策略配置栏底部 `batches.length > 0` 的“历史批次”块。
2. 将第二栏标题改为 `历史批次`。
3. 删除第二栏的 `currentStage` 终态提示、取消按钮和 `events` 事件流。
4. 将历史批次按钮列表放入第二栏；无批次时显示 `暂无批次`。
5. 每行继续显示截断后的 batch id 和 `BATCH_STAGE_LABELS[batch.status] ?? batch.status`，当前批次继续使用 `activeBatchId` 高亮。
6. 保留第三栏策略比较不变。

示意结构：

```tsx
<div className="space-y-4 rounded-lg border p-4">
  <h3 className="font-semibold text-sm">历史批次</h3>
  {batches.length > 0 ? (
    <div className="max-h-64 overflow-y-auto space-y-0.5">
      {batches.map((batch) => (
        <button key={batch.batch_id} onClick={() => void selectBatch(batch.batch_id)}>
          <span>{batch.batch_id.slice(0, 12)}…</span>
          <span>{BATCH_STAGE_LABELS[batch.status] ?? batch.status}</span>
        </button>
      ))}
    </div>
  ) : <div className="text-xs text-muted-foreground">暂无批次</div>}
</div>
```

- [ ] **Step 4: 运行测试确认通过**

Run: `npm --prefix frontend test -- --run src/components/stockpred/__tests__/FundRotationTab.test.tsx`

Expected: 该文件全部 PASS，且没有“批次进度”文本。

- [ ] **Step 5: 提交该独立变更**

Run:

```bash
git add frontend/src/components/stockpred/fund-rotation/FundRotationTab.tsx frontend/src/components/stockpred/__tests__/FundRotationTab.test.tsx
git commit -m "feat: move fund rotation batch history panel"
```

## Task 2: 保护策略选择框与变体标签布局

**Files:**
- Modify: `frontend/src/components/stockpred/fund-rotation/StrategyVariantsEditor.tsx`
- Test: `frontend/src/components/stockpred/fund-rotation/__tests__/StrategyVariantsEditor.test.tsx`

**Interfaces:**
- Consumes: 现有 `VariantDraft` 和策略列表。
- Produces: 不改变 `onChange` 数据，只改变编辑器首行的可收缩布局。

- [ ] **Step 1: 写失败测试**

在现有编辑器测试中渲染一个超长策略名和一个变体标签，断言首行包含收缩约束：策略选择框拥有 `min-w-0`，标签输入拥有 `min-w-0` 且固定宽度，外层仍包含 `shrink-0` 的操作按钮。

```tsx
expect(screen.getByRole("combobox", { name: "策略" })).toHaveClass("min-w-0");
expect(screen.getByPlaceholderText("变体标签（可选）")).toHaveClass("min-w-0");
expect(screen.getByTitle("复制变体")).toHaveClass("shrink-0");
```

若当前测试没有给策略选择框 accessible name，则先给该 `select` 增加 `aria-label="策略"`，再让测试和实现保持一致。

- [ ] **Step 2: 运行测试确认失败**

Run: `npm --prefix frontend test -- --run src/components/stockpred/fund-rotation/__tests__/StrategyVariantsEditor.test.tsx`

Expected: 至少一个 `toHaveClass` 断言失败，原因是当前 class 未包含对应收缩约束。

- [ ] **Step 3: 写最小布局实现**

将首行调整为：

```tsx
<div className="flex min-w-0 items-center gap-2">
  <GripVertical className="h-4 w-4 shrink-0 text-muted-foreground" />
  <select aria-label="策略" className="min-w-0 flex-1 truncate ..." />
  <input className="min-w-0 w-32 shrink ..." />
  <button className="shrink-0 ..." />
  <button className="shrink-0 ..." />
</div>
```

保留已有选择、复制、删除和变体数据逻辑。

- [ ] **Step 4: 运行测试确认通过**

Run: `npm --prefix frontend test -- --run src/components/stockpred/fund-rotation/__tests__/StrategyVariantsEditor.test.tsx src/components/stockpred/__tests__/FundRotationTab.test.tsx`

Expected: 两个测试文件全部 PASS。

- [ ] **Step 5: 提交该独立变更**

Run:

```bash
git add frontend/src/components/stockpred/fund-rotation/StrategyVariantsEditor.tsx frontend/src/components/stockpred/fund-rotation/__tests__/StrategyVariantsEditor.test.tsx
git commit -m "fix: prevent long strategy labels from crowding batch layout"
```

## Task 3: 计算权重未变的交易标记

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/components/stockpred/fund-rotation/TradeMarkersChart.tsx`
- Modify: `frontend/src/components/stockpred/fund-rotation/__tests__/TradeMarkersChart.test.tsx`

**Interfaces:**
- Consumes: `SignalMarker[]`, `TradeMarker[]`，按日期排序后的 `filteredSignals` 和 `filteredTrades`。
- Produces: `SharedTradeMarker[]`，其中正常交易可带 `muted: true`。

- [ ] **Step 1: 写失败测试**

扩展测试输入为两笔交易和信号：

```tsx
const trades = [
  { trade_date: "20250106", action: "BUY" as const, filled: 10, price: 11, target_weight: 0.5 },
  { trade_date: "20250110", action: "SELL" as const, filled: 2, price: 10.8, target_weight: 0.5 },
];
const signals = [{ date: "20250106", target_weight: 0.5 }];

render(<TradeMarkersChart ohlcv={...} trades={trades} signals={signals} tsCode="159712.SZ" />);
expect(chartMock.props?.markers).toEqual(expect.arrayContaining([
  expect.objectContaining({ time: "20250110", muted: true }),
]));
```

再加入一个权重变化和一个无目标权重的交易，断言它们没有 `muted: true`；首笔交易也没有浅色标志。

- [ ] **Step 2: 运行测试确认失败**

Run: `npm --prefix frontend test -- --run src/components/stockpred/fund-rotation/__tests__/TradeMarkersChart.test.tsx`

Expected: `muted` 属性不存在，新增权重未变断言失败。

- [ ] **Step 3: 扩展共享类型并实现最小比较逻辑**

在 `frontend/src/lib/api.ts` 的 `TradeMarker` 增加：

```ts
muted?: boolean;
```

在 `TradeMarkersChart` 中：

1. 规范化 signals/trades 日期后，建立按日期排序的事件数组。
2. 每笔交易的当前权重只取交易自身 `target_weight`；没有当前权重时不设置 `muted`。
3. 维护 `previousTargetWeight: number | null`，扫描事件时先比较当前事件与上一可用目标权重，再更新上一值。signal 事件只提供上一目标权重，不生成 marker；trade 事件生成 marker。
4. 只有 `previousTargetWeight !== null && currentTargetWeight === previousTargetWeight` 时返回 `muted: true`。
5. 保留现有 blocked/status/price/reason 逻辑和 `filteredTrades` 的范围过滤。

伪代码：

```ts
let previousTargetWeight: number | null = null;
for (const event of eventsByDate) {
  const current = event.kind === "signal"
    ? finite(event.record.target_weight)
    : finite(event.record.target_weight);
  const unchanged = event.kind === "trade"
    && current !== null
    && previousTargetWeight !== null
    && current === previousTargetWeight;
  if (event.kind === "trade") marker.muted = unchanged || undefined;
  if (current !== null) previousTargetWeight = current;
}
```

对于同一日期，排序时让 signal 先于 trade，使交易可以和当日信号目标权重比较；之后才更新上一值。

- [ ] **Step 4: 运行测试确认通过**

Run: `npm --prefix frontend test -- --run src/components/stockpred/fund-rotation/__tests__/TradeMarkersChart.test.tsx`

Expected: 新增的权重未变、权重变化、首次交易、缺少权重用例全部 PASS，原有稳定性和 strategy score 用例也 PASS。

- [ ] **Step 5: 提交该独立变更**

Run:

```bash
git add frontend/src/lib/api.ts frontend/src/components/stockpred/fund-rotation/TradeMarkersChart.tsx frontend/src/components/stockpred/fund-rotation/__tests__/TradeMarkersChart.test.tsx
git commit -m "feat: mark unchanged fund rotation weights on kline"
```

## Task 4: 渲染浅色 B/S 标记

**Files:**
- Modify: `frontend/src/components/charts/CandlestickChart.tsx`
- Test: `frontend/src/components/charts/__tests__/CandlestickChart.test.tsx`（若不存在则创建）

**Interfaces:**
- Consumes: `TradeMarker.muted`，以及现有 status/side。
- Produces: 正常且 muted 的 B/S markPoint 使用浅色；拒绝、部分成交、延迟状态优先级不变。

- [ ] **Step 1: 写失败测试**

如果已有图表单元测试，沿用其 ECharts mock；否则创建最小 mock，让 `echarts.init().setOption` 捕获 option。输入一个正常 BUY muted marker、一个正常 SELL marker、一个 REJECTED marker，断言：

```tsx
expect(markPoints[0].itemStyle.color).not.toBe(getChartTheme().upColor);
expect(markPoints[1].itemStyle.color).toBe(getChartTheme().downColor);
expect(markPoints[2].itemStyle.color).toBe(getChartTheme().textColor);
```

测试只验证最终 markPoint 配置，不验证 ECharts 内部绘图。

- [ ] **Step 2: 运行测试确认失败**

Run: `npm --prefix frontend test -- --run src/components/charts/__tests__/CandlestickChart.test.tsx`

Expected: muted BUY 当前仍使用普通 `t.upColor`，浅色断言失败；若不存在测试文件，先确认 mock 初始化错误并修正测试基础设施。

- [ ] **Step 3: 实现最小视觉分支**

在 `CandlestickChart` 的 trade marker 映射中，仅对正常 B/S 分支选择浅色：

```ts
const color = delayed
  ? "#8b5cf6"
  : status === "REJECTED"
    ? t.textColor
    : status === "PARTIAL"
      ? t.warningColor
      : m.muted
        ? (m.side === "BUY" ? `${t.upColor}99` : `${t.downColor}99`)
        : (m.side === "BUY" ? t.upColor : t.downColor);
```

如果当前主题颜色不是可安全追加 alpha 的 hex，使用与主题一致的预定义浅色映射，避免生成非法 CSS 颜色。不要改变 label、value、tooltip 或非正常交易状态。

- [ ] **Step 4: 运行测试确认通过**

Run: `npm --prefix frontend test -- --run src/components/charts/__tests__/CandlestickChart.test.tsx src/components/stockpred/fund-rotation/__tests__/TradeMarkersChart.test.tsx`

Expected: 图表视觉测试和适配器测试全部 PASS。

- [ ] **Step 5: 提交该独立变更**

Run:

```bash
git add frontend/src/components/charts/CandlestickChart.tsx frontend/src/components/charts/__tests__/CandlestickChart.test.tsx
git commit -m "feat: render unchanged weight trades with muted markers"
```

## Task 5: 全量验证与交付检查

**Files:**
- No source changes expected; inspect all files from Tasks 1–4.

- [ ] **Step 1: 检查变更范围**

Run: `git diff --check HEAD~4..HEAD` and `git status --short`

Expected: 本次提交只包含设计文档和四个实现任务涉及的前端文件；工作区中用户既有的策略代码改动不得被覆盖或提交。

- [ ] **Step 2: 运行相关回归测试**

Run:

```bash
npm --prefix frontend test -- --run \
  src/components/stockpred/__tests__/FundRotationTab.test.tsx \
  src/components/stockpred/fund-rotation/__tests__/StrategyVariantsEditor.test.tsx \
  src/components/stockpred/fund-rotation/__tests__/TradeMarkersChart.test.tsx \
  src/components/charts/__tests__/CandlestickChart.test.tsx
```

Expected: 所有指定测试 PASS，若图表测试文件不存在则使用实际存在的对应测试文件，并在交付说明中记录。

- [ ] **Step 3: 运行前端类型检查与构建**

Run: `npm --prefix frontend run build`

Expected: Vite 构建退出码为 0，无 TypeScript 错误。

- [ ] **Step 4: 按验收清单人工检查**

确认：

1. 策略名称很长时，选择框显示截断，不覆盖变体标签、复制和删除按钮。
2. 中栏标题是“历史批次”，配置栏不再重复显示历史批次。
3. 中栏无批次时显示“暂无批次”；有批次时可点击切换并显示状态。
4. 权重未变的正常 B/S 标记为浅色；权重变化、首次交易、缺少权重和拒绝/部分成交标记保持原样。

- [ ] **Step 5: 交付前运行最终验证**

Run: `git diff --check` and repeat the relevant test/build commands after any final edit. 只有命令退出码为 0 且输出确认无失败时，才报告完成。
