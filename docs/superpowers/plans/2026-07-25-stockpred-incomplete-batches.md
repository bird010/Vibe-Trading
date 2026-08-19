# StockPred 未完成批次展示 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 StockPred 页面初始化时显示所有未完成策略批次及其已持久化报告。

**Architecture:** 后端批次存储新增只读未完成批次摘要列表，路由公开该列表。前端初始化时请求列表并在批量回测区域逐批渲染，不改写任务状态也不触发续跑。

**Tech Stack:** Python/FastAPI/Pytest；React/TypeScript/Vitest。

## Global Constraints

- 仅返回状态为 `queued` 或 `running` 的批次。
- 不恢复、续跑、取消或改写任何既有批次。
- 保持现有单策略报告链接与排序行为。
- 不触碰本工作区已有的文档修改。

---

### Task 1: 后端未完成批次列表

**Files:**
- Modify: `agent/src/stockpred/batch_store.py`
- Modify: `agent/src/api/stockpred_routes.py`
- Test: `agent/tests/stockpred/test_batch_store.py`

- [ ] **Step 1: 写失败测试**

```python
def test_batch_store_lists_only_unfinished_summaries(tmp_path) -> None:
    store = StockPredBatchStore(tmp_path)
    queued = store.create(...)
    running = store.create(...)
    store.finish_report(running, "alpha101_1", run_id="strategy_ok", status="success")
    completed = store.create(...)
    store.complete(completed)

    assert [row["batch_id"] for row in store.list_unfinished()] == [running, queued]
```

- [ ] **Step 2: 验证测试失败**

运行：`python -m pytest tests/stockpred/test_batch_store.py::test_batch_store_lists_only_unfinished_summaries -q -p no:cacheprovider`

- [ ] **Step 3: 最小实现**

新增 `StockPredBatchStore.list_unfinished()`：遍历批次目录，读取状态，只对 `queued`、`running` 调用 `summary()`，按更新时间倒序返回；跳过损坏或不完整目录。

- [ ] **Step 4: 验证后端测试通过**

运行：`python -m pytest tests/stockpred/test_batch_store.py -q -p no:cacheprovider`

### Task 2: 前端初始加载与渲染

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/pages/StockPred.tsx`
- Test: `frontend/src/pages/__tests__/StockPred.test.tsx`

- [ ] **Step 1: 写失败测试**

```tsx
apiMock.listUnfinishedStrategyBatches.mockResolvedValueOnce([unfinishedBatch]);
render(<StockPred />);
expect(await screen.findByText("未完成批次")).toBeInTheDocument();
expect(screen.getByText("alpha101_1")).toBeInTheDocument();
```

- [ ] **Step 2: 验证测试失败**

运行：`npm test -- --run src/pages/__tests__/StockPred.test.tsx`

- [ ] **Step 3: 最小实现**

新增 `listUnfinishedStrategyBatches()` API 与 `unfinishedStrategyBatches` 页面状态；初始加载时请求该列表；在批量回测区域渲染每个批次的状态、计数与报告行。

- [ ] **Step 4: 验证前端测试通过**

运行：`npm test -- --run src/pages/__tests__/StockPred.test.tsx`

### Task 3: 全量验证

- [ ] **Step 1: 运行后端相关测试**

运行：`python -m pytest tests/stockpred/test_batch_store.py tests/stockpred/test_batch_service.py -q -p no:cacheprovider`

- [ ] **Step 2: 运行前端测试与静态检查**

运行：`npm test -- --run src/pages/__tests__/StockPred.test.tsx`，再运行 `npm run lint`。

- [ ] **Step 3: 核对改动范围**

运行：`git diff --check` 与 `git status --short`，确认未包含已有文档修改。
