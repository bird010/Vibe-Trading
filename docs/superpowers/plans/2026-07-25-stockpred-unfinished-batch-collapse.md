# StockPred 未完成批次折叠 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 StockPred 页面中的每个未完成策略批次默认折叠，并允许用户独立展开或收起策略报告明细。

**Architecture:** 在 `StockPred` 组件维护一个已展开批次 ID 的 `Set<string>`。未完成批次标题行渲染独立的折叠按钮；仅当对应 ID 在集合内时渲染报告列表。该状态仅存在于组件内，不修改 API 或后端批次数据。

**Tech Stack:** React、TypeScript、Vitest、Testing Library。

## Global Constraints

- 默认所有未完成批次折叠。
- 标题行保留批次摘要与既有“查看并生成详情”操作。
- 展开状态按 batch ID 独立保存，不持久化到浏览器或后端。
- 只修改前端页面与其测试；不改 API、批次状态或详情物化逻辑。

---

### Task 1: 未完成批次的独立折叠控制

**Files:**

- Modify: `frontend/src/pages/StockPred.tsx:64-287`
- Test: `frontend/src/pages/__tests__/StockPred.test.tsx`

**Interfaces:**

- Consumes: `unfinishedStrategyBatches: StrategyBatchSummary[]`，每项包含 `batch_id` 和 `reports`。
- Produces: 每个未完成批次标题行的“展开”或“收起”按钮；按钮的 `aria-expanded` 与对应报告列表可见性同步。

- [ ] **Step 1: 写默认折叠的失败测试**

在 `StockPred.test.tsx` 中添加一个含两个未完成批次的 mock，并测试报告名不会在初始渲染时出现：

```tsx
it("keeps unfinished batch reports collapsed by default", async () => {
  apiMock.listUnfinishedStrategyBatches.mockResolvedValue([
    { ...unfinishedBatch, batch_id: "batch_a", reports: [{ ...unfinishedBatch.reports[0], strategy_name: "Batch A report" }] },
    { ...unfinishedBatch, batch_id: "batch_b", reports: [{ ...unfinishedBatch.reports[0], strategy_name: "Batch B report" }] },
  ]);
  renderStockPred();

  expect(await screen.findByText("未完成批次")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "展开 batch_a" })).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByText("Batch A report")).not.toBeInTheDocument();
  expect(screen.queryByText("Batch B report")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
npm --prefix frontend test -- --run src/pages/__tests__/StockPred.test.tsx
```

Expected: FAIL，因为当前页面初始渲染每个未完成批次的报告列表，且没有“展开 batch_a”按钮。

- [ ] **Step 3: 实现最小折叠状态与按钮**

在现有 state 区域加入：

```tsx
const [expandedUnfinishedBatchIds, setExpandedUnfinishedBatchIds] = useState<Set<string>>(() => new Set());

function toggleUnfinishedBatch(batchId: string) {
  setExpandedUnfinishedBatchIds((current) => {
    const next = new Set(current);
    if (next.has(batchId)) next.delete(batchId);
    else next.add(batchId);
    return next;
  });
}
```

将未完成批次标题行中的报告列表替换为以下控制逻辑：

```tsx
const expanded = expandedUnfinishedBatchIds.has(batch.batch_id);

<button
  type="button"
  aria-expanded={expanded}
  aria-controls={`unfinished-batch-${batch.batch_id}`}
  onClick={() => toggleUnfinishedBatch(batch.batch_id)}
  className="rounded border px-2 py-1"
>
  {expanded ? "收起" : "展开"} {batch.batch_id}
</button>
{expanded ? (
  <div id={`unfinished-batch-${batch.batch_id}`} className="divide-y">
    {batch.reports.map(/* 保持既有报告 Link 内容 */)}
  </div>
) : null}
```

保留标题行内原有的批次摘要和“查看并生成详情”按钮；不要把该按钮嵌套到折叠按钮中。

- [ ] **Step 4: 写展开、收起和独立状态测试**

追加测试：

```tsx
it("expands and collapses unfinished batches independently", async () => {
  apiMock.listUnfinishedStrategyBatches.mockResolvedValue([batchA, batchB]);
  const user = userEvent.setup();
  renderStockPred();

  await user.click(await screen.findByRole("button", { name: "展开 batch_a" }));
  expect(screen.getByText("Batch A report")).toBeInTheDocument();
  expect(screen.queryByText("Batch B report")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "收起 batch_a" })).toHaveAttribute("aria-expanded", "true");

  await user.click(screen.getByRole("button", { name: "收起 batch_a" }));
  expect(screen.queryByText("Batch A report")).not.toBeInTheDocument();
});
```

- [ ] **Step 5: 运行测试确认通过**

Run:

```powershell
npm --prefix frontend test -- --run src/pages/__tests__/StockPred.test.tsx
```

Expected: PASS，包含默认折叠、展开、收起和批次间独立性。

- [ ] **Step 6: 提交**

```powershell
git add frontend/src/pages/StockPred.tsx frontend/src/pages/__tests__/StockPred.test.tsx
git commit -m "feat(stockpred): collapse unfinished batch reports"
```

## Self-review

- 设计的默认折叠、独立状态、标题摘要保留和不持久化均由 Task 1 覆盖。
- 计划不修改 API 或后端状态，符合范围限制。
- 所有代码步骤包含具体 state、按钮属性、测试断言与执行命令；无占位任务。
