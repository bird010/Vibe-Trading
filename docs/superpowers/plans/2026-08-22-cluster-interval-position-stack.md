# 聚类区间仓位堆叠图 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在聚类区间页面增加基金/现金 100% 仓位堆叠面积图，并实现上下价格图与仓位图按基金双向高亮联动。

**Architecture:** 扩展现有 `ClusterIntervalChart` 模型，在选中聚类区间内按行情日期构造代表基金实际仓位和现金补足序列；ECharts 使用上下两个 grid 共享 category 轴，价格系列放在上图，仓位/现金系列放在下图。两张图通过同一 ECharts 实例的 `mouseover`/`mouseout` 事件和 `logicalId` 建立联动样式，图例按逻辑基金控制对应价格与仓位系列。

**Tech Stack:** React, TypeScript, Apache ECharts, Vitest, Testing Library.

## Global Constraints

- 仅显示当前下拉菜单选中的聚类区间及其代表基金。
- Y 轴固定显示 0%～100%，现金区域代表未投资资金。
- 基金仓位颜色必须复用上方对应基金价格曲线颜色。
- 实际仓位在记录之间前向延续，卖出后为 0；缺失数据不能伪造为 0。
- 上方价格曲线与下方仓位区域必须双向高亮；现金高亮不误高亮基金。
- 保留现有组合收益曲线、B/S/D 标记、tooltip 和深浅颜色逻辑。
- 只提交本功能相关文件；不得覆盖或提交其他工作区已有改动。

---

### Task 1: 扩展聚类区间模型与上下堆叠图

**Files:**
- Modify: `frontend/src/components/stockpred/fund-rotation/ClusterIntervalChart.tsx`
- Modify: `frontend/src/components/stockpred/fund-rotation/__tests__/ClusterIntervalChart.test.tsx`
- Modify: `frontend/src/components/stockpred/fund-rotation/types.ts`（仅保留本功能确需的类型字段）

**Interfaces:**
- Consumes: `InstrumentChartResponse.positions[].actual_weight`, 当前区间代表基金集合、现有 `ClusterIntervalSeries` 和 `ClusterIntervalMarkPoint`。
- Produces: 扩展后的 `ClusterIntervalChartModel`，以及包含价格、仓位、现金 series 的 ECharts option；series 需带可用于联动的逻辑基金标识。

- [ ] **Step 1: Write failing model tests**

在现有 `ClusterIntervalChart.test.tsx` 增加测试，覆盖：

```ts
it("builds actual fund weights and cash to a 100% stack", () => {
  const model = buildClusterIntervalChartModel(inputWithTwoRepresentativesAndPositions);
  expect(model.positionSeries).toEqual([
    expect.objectContaining({ logicalId: "fund:AAA", stack: "positions" }),
    expect.objectContaining({ logicalId: "fund:BBB", stack: "positions" }),
    expect.objectContaining({ logicalId: "cash", stack: "positions" }),
  ]);
  expect(model.positionSeries.find((series) => series.logicalId === "cash")?.data).toEqual([
    ["20250102", 0.2],
    ["20250103", 0.4],
  ]);
});

it("forwards actual weights, sets sold funds to zero, and preserves gaps", () => {
  const model = buildClusterIntervalChartModel(inputWithPositionsAndGap);
  expect(seriesData(model, "fund:AAA")).toEqual([
    ["20250102", 0.5],
    ["20250103", 0],
  ]);
  expect(seriesData(model, "fund:BBB")).toEqual([
    ["20250102", null],
    ["20250103", 0.25],
  ]);
});
```

使用与现有模型测试相同的日期、候选池和行情 fixture；测试应先失败，原因是模型尚无仓位 series。

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```powershell
npm --prefix frontend test -- --run src/components/stockpred/fund-rotation/__tests__/ClusterIntervalChart.test.tsx
```

Expected: 新增仓位模型断言失败，既有交易标签测试不因本任务回归。

- [ ] **Step 3: Implement the position model**

扩展模型类型，至少包含 `logicalId`, `name`, `color`, `kind: "position"`, `instrument?`, `data`, `stack` 和缺失值支持。对每个当前区间代表基金：

1. 按日期读取 `positions[].actual_weight`。
2. 在有效持仓记录之间前向延续上一条值。
3. 对已完成卖出后的日期置为 0。
4. 无可靠记录的日期保留 `null`，不要转换成 0。
5. 现金只在该日期基金仓位数据整体可靠时计算 `max(0, 1 - sum(weights))`，否则保留 `null`。
6. cash series 使用固定 `logicalId: "cash"`、中性颜色和名称“现金”。

确保现有价格/收益 model 和交易 marker 结果不变，并让上下图可以使用同一个日期轴。

- [ ] **Step 4: Implement the two-grid stacked area option**

在现有 ECharts option 中增加下图 grid、下方 x/y axis 和仓位 series：

```ts
grid: [
  { left: 12, right: 12, top: 42, height: "52%", containLabel: true },
  { left: 12, right: 12, top: "63%", height: "27%", containLabel: true },
],
yAxis: [
  existingPriceAxis,
  { gridIndex: 1, min: 0, max: 1, axisLabel: { formatter: percentageLabel } },
],
```

基金仓位 series 使用 `type: "line"`, `stack: "positions"`, `step: "end"`, `areaStyle`；现金作为最后一层。仓位 series 绑定 `xAxisIndex: 1`, `yAxisIndex: 1`, 基金价格 series 绑定上图 grid。两张图的日期数组必须完全一致。

- [ ] **Step 5: Implement bidirectional highlight and shared pointer**

注册 ECharts 事件：

- `mouseover` 价格基金 series 或仓位基金 series 时，根据 `logicalId` 设置 active logical id。
- active 时通过 `setOption` 或 `dispatchAction` 调整两组对应 series 的透明度和线宽/边界；其他基金降低透明度。
- `logicalId === "cash"` 只强调现金。
- `mouseout` 离开联动区域后恢复默认样式。
- 使用 `axisPointer`/`showTip` 或等价机制保持上下图共享日期指示。
- 图例事件按逻辑基金同步控制上方价格 series 和下方仓位 series，现金单独控制。

事件解绑和 chart dispose 必须放在现有 effect 清理逻辑中，避免区间切换后重复监听。

- [ ] **Step 6: Add component option and interaction assertions**

在测试中读取 mock chart 的最终 option，验证：

```ts
expect(option.grid).toHaveLength(2);
expect(option.yAxis[1]).toMatchObject({ min: 0, max: 1 });
expect(positionSeries(option)).toEqual(expect.arrayContaining([
  expect.objectContaining({ stack: "positions", yAxisIndex: 1, xAxisIndex: 1 }),
]));
```

触发 mock 的 `mouseover`/`mouseout` 回调，验证价格基金和仓位基金被同时调整，现金事件不调整任何基金；保留现有 markPoint tooltip `trigger: "item"` 测试。

- [ ] **Step 7: Run focused tests and build**

Run:

```powershell
npm --prefix frontend test -- --run src/components/stockpred/fund-rotation/__tests__/ClusterIntervalChart.test.tsx
npm --prefix frontend run build
git diff --check
```

Expected: 聚类区间测试全部通过，前端构建退出码为 0，diff 无空白错误。

- [ ] **Step 8: Commit only the feature files**

先确认 staged 文件只包含本任务涉及的前端文件：

```powershell
git add -- frontend/src/components/stockpred/fund-rotation/ClusterIntervalChart.tsx frontend/src/components/stockpred/fund-rotation/__tests__/ClusterIntervalChart.test.tsx frontend/src/components/stockpred/fund-rotation/types.ts
git diff --cached --name-only
git commit -m "feat: add cluster interval position stack chart"
```
