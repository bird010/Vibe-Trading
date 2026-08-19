# 基金轮动 K 线证据交互稳定性实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让基金轮动 K 线证据只在用户操作或明确进入页面时加载，并使右侧点击与手动缩放遵循最后操作优先且不被父组件重渲染破坏。

**Architecture:** 暂停基金轮动页无关的批测轮询；以 `WeeklyKlineEvidence` 的绝对日期范围作为共享视口状态；图表更新时按该范围恢复各自本地索引，不再依赖默认百分比范围。稳定 `TradeMarkersChart` 的派生数组，避免普通父级重渲染触发 ECharts 重设。

**Tech Stack:** React 18, Zustand, ECharts, Vitest, Testing Library, TypeScript, Vite

## Global Constraints

- 基金轮动页不执行策略批测状态轮询。
- 共享视口使用绝对日期 `{ start: string; end: string }`，不同标的按各自数据索引映射。
- 右侧点击、左侧标记点击、手动缩放/平移都视为用户操作，最后一次操作拥有视口控制权。
- 用户点击当前可见日期不得触发 `dataZoom` 平移动作；点击范围外日期时保持当前跨度并居中。
- 必须保留现有右侧选中行高亮、滚动定位和跨标的缩放同步行为。

---

### Task 1: 停止基金轮动页的无关批测轮询

**Files:**
- Modify: `frontend/src/pages/StockPred.tsx`
- Test: `frontend/src/pages/__tests__/StockPred.test.tsx`（如现有文件不存在，则在已有 StockPred 测试目录中添加最小测试）

**Interfaces:**
- `StockPred` 的 `activeTab` 决定批测轮询 effect 的生命周期。
- 轮询函数仍用于策略批测页，不改变批测页已有刷新行为。

- [ ] **Step 1: Write the failing test**

断言渲染基金轮动标签页时不创建 `setInterval`，切换到策略批测标签页时才创建，并在离开时清理。

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend run test:run -- src/pages/__tests__/StockPred.test.tsx`

Expected: FAIL because the current effect creates `setInterval` regardless of `activeTab`.

- [ ] **Step 3: Write minimal implementation**

将轮询 effect 依赖加入 `activeTab`，仅当 `activeTab === "batch"` 时调用 `pollRunningBatches()`、创建定时器；cleanup 中继续清理已有定时器。

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix frontend run test:run -- src/pages/__tests__/StockPred.test.tsx`

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/StockPred.tsx frontend/src/pages/__tests__/StockPred.test.tsx
git commit -m "fix: pause batch polling outside batch tab"
```

### Task 2: 稳定基金轮动图表派生数据并保存共享视口

**Files:**
- Modify: `frontend/src/components/stockpred/fund-rotation/TradeMarkersChart.tsx`
- Modify: `frontend/src/components/charts/CandlestickChart.tsx`
- Test: `frontend/src/components/charts/__tests__/CandlestickChart.stockpred.test.tsx`
- Test: `frontend/src/components/stockpred/fund-rotation/__tests__/TradeMarkersChart.test.tsx`（如现有文件不存在则创建）

**Interfaces:**
- `CandlestickChart` 继续接收 `sharedZoomRange`, `onZoomRangeChange`, `focusTime`。
- `TradeMarkersChart` 负责用 `useMemo` 稳定 `priceBars` 和 `chartMarkers`。

- [ ] **Step 1: Write the failing tests**

增加以下行为测试：

1. 图表更新 effect 已有共享日期范围时，`setOption` 使用该范围对应的本地图表起止索引/百分比，不回到默认全量范围。
2. `TradeMarkersChart` 在相同输入引用下重新渲染时，传给 `CandlestickChart` 的 `data` 和 `markers` 引用保持稳定。

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm --prefix frontend run test:run -- src/components/charts/__tests__/CandlestickChart.stockpred.test.tsx src/components/stockpred/fund-rotation/__tests__/TradeMarkersChart.test.tsx`

Expected: FAIL because chart option updates currently only preserve current percentage values and adapter arrays are recreated on every render.

- [ ] **Step 3: Write minimal implementation**

在 `CandlestickChart` 中增加绝对日期到本地索引的转换，并在图表 option 更新时优先使用 `sharedZoomRange` 映射出的起止百分比；当共享范围为空时保留当前图表范围或默认范围。保持 `datazoom` 回调把用户手动操作转换回绝对日期。

在 `TradeMarkersChart` 中用 `useMemo` 包裹价格柱和交易标记转换，依赖仅包含原始输入、日期范围和标的代码。

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm --prefix frontend run test:run -- src/components/charts/__tests__/CandlestickChart.stockpred.test.tsx src/components/stockpred/fund-rotation/__tests__/TradeMarkersChart.test.tsx`

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/charts/CandlestickChart.tsx frontend/src/components/charts/__tests__/CandlestickChart.stockpred.test.tsx frontend/src/components/stockpred/fund-rotation/TradeMarkersChart.tsx frontend/src/components/stockpred/fund-rotation/__tests__/TradeMarkersChart.test.tsx
git commit -m "fix: preserve shared fund rotation chart viewport"
```

### Task 3: 覆盖最后操作优先的右侧交互

**Files:**
- Modify: `frontend/src/components/stockpred/fund-rotation/WeeklyKlineEvidence.tsx`
- Modify: `frontend/src/components/charts/CandlestickChart.tsx`（如 Task 2 已覆盖则仅补测试所需最小调整）
- Test: `frontend/src/components/stockpred/fund-rotation/__tests__/WeeklyKlineEvidence.test.tsx`
- Test: `frontend/src/components/charts/__tests__/CandlestickChart.stockpred.test.tsx`

**Interfaces:**
- `WeeklyKlineEvidence` 维护 `selectedEvidence` 与 `sharedZoomRange`，二者分别表示选择高亮和当前视口。
- 右侧行点击和左侧标记点击都调用同一套选择回调；手动 `onZoomRangeChange` 更新共享视口。

- [ ] **Step 1: Write the failing tests**

覆盖以下序列：

1. 右侧点击范围外记录后产生居中视口。
2. 随后模拟手动缩放，新的共享日期范围覆盖右侧点击产生的范围。
3. 组件重渲染/数据引用更新后，不重新执行旧的 `focusTime` 聚焦。
4. 再次点击新的范围外记录时，按这次点击重新平移；范围内记录不平移。

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm --prefix frontend run test:run -- src/components/stockpred/fund-rotation/__tests__/WeeklyKlineEvidence.test.tsx src/components/charts/__tests__/CandlestickChart.stockpred.test.tsx`

Expected: FAIL on at least one last-operation sequence before the state guards are completed.

- [ ] **Step 3: Write minimal implementation**

确保 `focusTime` 只由最新用户选择触发一次；手动缩放回调更新 `sharedZoomRange`，但不清除右侧高亮；图表重渲染只恢复最新共享范围，不重新执行过期聚焦请求。

- [ ] **Step 4: Run focused and complete tests**

Run: `npm --prefix frontend run test:run -- src/components/stockpred/fund-rotation src/components/charts/__tests__/CandlestickChart.stockpred.test.tsx`

Expected: all relevant test files pass。

- [ ] **Step 5: Run production verification**

Run: `npm --prefix frontend run build`

Expected: TypeScript compilation and Vite build succeed。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/stockpred/fund-rotation/WeeklyKlineEvidence.tsx frontend/src/components/stockpred/fund-rotation/__tests__/WeeklyKlineEvidence.test.tsx frontend/src/components/charts/CandlestickChart.tsx frontend/src/components/charts/__tests__/CandlestickChart.stockpred.test.tsx
git commit -m "fix: prioritize latest fund rotation chart interaction"
```
