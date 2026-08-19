# 基金轮动单次回测详情候选池展示实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在单次基金轮动回测详情页补充缺失的任务总览和完整生命周期，并新增按重聚类日期展示代表基金代码、名称、分类和选择原因的“基金候选池”标签页。

**Architecture:** 后端新增 checksum-gated 的候选池读取接口，在服务端按回测 data snapshot 锁定的 `dim_fund` 版本一次性补齐代表基金元数据；前端保留现有详情数据流，新增独立的候选池懒加载状态和标签页。概览只增加当前页面没有的字段，现有内容不迁移、不删除、不重复渲染。

**Tech Stack:** FastAPI/Pydantic、Lance、Python pytest、React 19、TypeScript、Zustand、Vitest、Testing Library、Tailwind CSS。

## Global Constraints

- 只读取已发布且 manifest checksum 校验通过的 v2 回测结果。
- 代表基金名称和分类必须来自本次运行 `data_snapshot.json` 对应的 `dim_fund` 版本。
- 当前维表没有 `asset_class` 或 `instrument_type` 时，分类使用 `fund_type`；缺失值前端显示“—”。
- 不展开全部聚类成员，只展示每次重聚类的 8 个簇摘要和代表选择结果。
- 不删除现有“运行范围”“可复现身份”“策略参数”“核心指标”“收益曲线”“K 线证据”展示。
- 候选池接口加载失败不得清空或阻塞概览、收益曲线和 K 线证据。
- 每个生产代码行为先写一个会失败的测试，确认失败后再实现。

---

### Task 1: 定义候选池后端响应模型

**Files:**
- Modify: `agent/src/stockpred/fund_rotation/api_models.py`
- Test: `agent/tests/fund_rotation/test_backtest_detail_api.py`

**Interfaces:**
- Produces `CandidatePoolRepresentative`, `CandidatePoolRecluster`, and `CandidatePoolResponse` Pydantic models for the new endpoint.
- `CandidatePoolResponse.reclusters` is ordered by `week`; each recluster has exactly the representative rows present in the source artifact, normally 8 rows.

- [ ] **Step 1: Write the failing response-model test**

Add a test payload containing one recluster, two representative rows, gate summary fields, an empty selected code, and a missing metadata value. Assert Pydantic preserves `None` and numeric gate values without coercing missing strings to the text `"None"`.

- [ ] **Step 2: Run the focused backend test and verify it fails**

Run:

```powershell
E:\anaconda3\envs\AlphaFin\python.exe -m pytest agent/tests/fund_rotation/test_backtest_detail_api.py -q
```

Expected: FAIL because the candidate-pool response models do not exist.

- [ ] **Step 3: Implement the minimal Pydantic models**

Add immutable-compatible response models with nullable selected metadata, integer cluster IDs/sizes, boolean lock state, nullable gate numbers, and lists for reclusters/representatives. Do not modify existing `BacktestDetailResponse` fields.

- [ ] **Step 4: Run the focused test and verify it passes**

Run the same command and expect the new model test plus existing API tests to pass.

- [ ] **Step 5: Commit the contract change**

```powershell
git add agent/src/stockpred/fund_rotation/api_models.py agent/tests/fund_rotation/test_backtest_detail_api.py
git commit -m "feat: add candidate pool response models"
```

### Task 2: 实现 checksum-gated 候选池 API

**Files:**
- Modify: `agent/src/api/fund_rotation_routes.py`
- Modify: `agent/tests/fund_rotation/test_backtest_detail_api.py`

**Interfaces:**
- Adds `GET /stockpred/fund-rotation/backtests/{run_id}/candidate-pool`.
- Returns `CandidatePoolResponse` with gate summary and representative metadata.
- Reads `strategy_cluster_history.json`, `strategy_gates.json`, `strategy_representatives.json`, and `data_snapshot.json` only through `_validated_artifact`.

- [ ] **Step 1: Add a failing published-run API test**

Extend the published v2 fixture with the three strategy JSON artifacts and a snapshot. Create a temporary pinned `dim_fund.lance` fixture only when the test environment has Lance support; otherwise test the missing-metadata fallback. Assert:

```python
response = client.get("/stockpred/fund-rotation/backtests/run-detail/candidate-pool")
assert response.status_code == 200
body = response.json()
assert body["reclusters"][0]["week"] == "20240105"
assert body["reclusters"][0]["representatives"][0]["selected_code"] == "510300.SH"
assert body["reclusters"][0]["representatives"][0]["selected_name"] in {"沪深300ETF", None}
```

Add a second failing test that tampers with or omits the published artifact and expects the endpoint to fail closed rather than reading the unvalidated file.

- [ ] **Step 2: Run the focused test and verify the expected failure**

Run:

```powershell
E:\anaconda3\envs\AlphaFin\python.exe -m pytest agent/tests/fund_rotation/test_backtest_detail_api.py -q
```

Expected: FAIL with 404/405 because the endpoint is not implemented.

- [ ] **Step 3: Implement pure parsing/enrichment helpers inside the route registration module**

Add narrowly scoped helpers that:

1. Read JSON list artifacts and normalize malformed/missing values to empty lists.
2. Index gate entries by `week` and extract the `MAX_CLUSTER_SHARE` and `EFFECTIVE_CLUSTER_COUNT` actual values plus their statuses and overall status.
3. Index representative entries by `(week, cluster_id)`.
4. Build one metadata lookup for all non-empty `selected` codes from the pinned `dim_version`. Read only columns available in the dataset schema: `ts_code`, `name`, `fund_type`, `asset_class`, `instrument_type`; use `fund_type` as the returned classification fallback.
5. Return one sorted `CandidatePoolRecluster` per cluster-history entry. Preserve empty representative rows and source exclusion reasons.

If the snapshot or Lance metadata cannot be opened, return the structured rows with `selected_name=None` and `selected_fund_type=None`; do not silently switch to the latest data version.

- [ ] **Step 4: Register the endpoint with the existing auth dependency**

Place the route next to the existing backtest detail and artifact routes. Call `_published_manifest(run_id)` first, use `_validated_artifact` for every source file, and return `CandidatePoolResponse.model_dump(mode="json")`.

- [ ] **Step 5: Run the focused backend tests and verify they pass**

Run:

```powershell
E:\anaconda3\envs\AlphaFin\python.exe -m pytest agent/tests/fund_rotation/test_backtest_detail_api.py -q
```

Expected: all tests pass, including fail-closed behavior.

- [ ] **Step 6: Commit the API implementation**

```powershell
git add agent/src/api/fund_rotation_routes.py agent/tests/fund_rotation/test_backtest_detail_api.py
git commit -m "feat: expose fund rotation candidate pool"
```

### Task 3: Add frontend candidate-pool types, API client, and isolated hook state

**Files:**
- Modify: `frontend/src/components/stockpred/fund-rotation/types.ts`
- Modify: `frontend/src/components/stockpred/fund-rotation/api.ts`
- Modify: `frontend/src/components/stockpred/fund-rotation/useBacktestDetail.ts`
- Test: `frontend/src/components/stockpred/fund-rotation/__tests__/useBacktestDetail.test.ts`
- Test: `frontend/src/components/stockpred/fund-rotation/__tests__/api.test.ts`

**Interfaces:**
- Adds `BacktestDetailTab = "overview" | "equity" | "chart" | "candidate_pool"`.
- Adds `CandidatePoolResponse` TypeScript types matching Task 1.
- Adds `fetchCandidatePool(runId: string, signal?: AbortSignal): Promise<CandidatePoolResponse>`.
- Adds hook state `candidatePool`, `candidatePoolLoading`, `candidatePoolError`, and `loadCandidatePool()`.

- [ ] **Step 1: Write failing API and hook tests**

Add an API test that verifies the URL is `/backtests/run-1/candidate-pool` and the response is returned. Add hook tests that verify:

- `loadCandidatePool()` loads only the selected run;
- changing runs invalidates the older request;
- a candidate-pool error is stored separately while `detail` remains intact;
- `openRun` and `closeRun` clear candidate-pool state.

- [ ] **Step 2: Run the focused frontend tests and verify they fail**

Run:

```powershell
npm --prefix frontend run test:run -- src/components/stockpred/fund-rotation/__tests__/api.test.ts src/components/stockpred/fund-rotation/__tests__/useBacktestDetail.test.ts
```

Expected: FAIL because the API function, tab type, and hook state do not exist.

- [ ] **Step 3: Implement the API types and client**

Add the response types, use `backtestArtifactUrl`-style auth handling, and throw through the existing `responseError` helper on non-2xx responses.

- [ ] **Step 4: Implement request lifecycle state**

Add a candidate-pool request generation counter and AbortController. `loadCandidatePool()` must read `selectedRunId`, clear only candidate-pool loading/error state, and commit the response only when both request generation and selected run still match. Reset the new state in `openRun` and `closeRun`.

- [ ] **Step 5: Run focused frontend tests and verify they pass**

Run the same command and expect all API/hook tests to pass.

- [ ] **Step 6: Commit the data-layer change**

```powershell
git add frontend/src/components/stockpred/fund-rotation/types.ts frontend/src/components/stockpred/fund-rotation/api.ts frontend/src/components/stockpred/fund-rotation/useBacktestDetail.ts frontend/src/components/stockpred/fund-rotation/__tests__/api.test.ts frontend/src/components/stockpred/fund-rotation/__tests__/useBacktestDetail.test.ts
git commit -m "feat: load fund rotation candidate pool"
```

### Task 4: 补充概览页且避免重复已有内容

**Files:**
- Modify: `frontend/src/components/stockpred/fund-rotation/BacktestDetailPanel.tsx`
- Test: `frontend/src/components/stockpred/fund-rotation/__tests__/BacktestDetailPanel.test.tsx`

**Interfaces:**
- The existing overview content remains in place.
- New overview sections render only task-level identity/status fields not already rendered by the existing “运行范围” and “可复现身份” sections.

- [ ] **Step 1: Write failing component tests**

Use a completed detail fixture with batch ID, variant, strategy, mode, quality status, partial/published/comparison flags, and three lifecycle events. Assert:

```tsx
expect(screen.getByText("任务总览")).toBeInTheDocument();
expect(screen.getByText("batch-1")).toBeInTheDocument();
expect(screen.getByText("执行生命周期")).toBeInTheDocument();
expect(screen.getByText("PREPARING_DATA")).toBeInTheDocument();
expect(screen.getAllByText("Run identity")).toHaveLength(1);
```

The last assertion guards against duplicating the existing identity field.

- [ ] **Step 2: Run the focused component test and verify it fails**

Run:

```powershell
npm --prefix frontend run test:run -- src/components/stockpred/fund-rotation/__tests__/BacktestDetailPanel.test.tsx
```

Expected: FAIL because the new sections are absent and successful runs currently hide lifecycle events.

- [ ] **Step 3: Implement task overview and lifecycle sections**

Add a compact task-overview definition list for only missing fields. Render `detail.events` for every published/unpublished state, sorted by numeric `seq`, with timestamp, stage/message, and error. Keep the existing error banner, metric cards, date range, identity, and config table unchanged.

- [ ] **Step 4: Run the focused component tests and verify they pass**

Run the same command and expect the existing chart lifecycle tests plus new overview tests to pass.

- [ ] **Step 5: Commit the overview change**

```powershell
git add frontend/src/components/stockpred/fund-rotation/BacktestDetailPanel.tsx frontend/src/components/stockpred/fund-rotation/__tests__/BacktestDetailPanel.test.tsx
git commit -m "feat: show fund rotation task lifecycle"
```

### Task 5: Add the “基金候选池” tab and representative table

**Files:**
- Modify: `frontend/src/components/stockpred/fund-rotation/BacktestDetailPanel.tsx`
- Test: `frontend/src/components/stockpred/fund-rotation/__tests__/BacktestDetailPanel.test.tsx`

**Interfaces:**
- The tab calls `loadCandidatePool()` once when active and data is absent.
- The tab renders loading, error, empty, and populated states without changing other tabs.

- [ ] **Step 1: Write failing component tests**

Extend the mocked hook state with a candidate-pool response containing one recluster and 8 representative rows. Assert:

```tsx
expect(screen.getByRole("button", { name: "基金候选池" })).toBeInTheDocument();
expect(screen.getByText("510300.SH")).toBeInTheDocument();
expect(screen.getByText("沪深300ETF")).toBeInTheDocument();
expect(screen.getByText("股票型")).toBeInTheDocument();
expect(screen.getByText("REJECT")).toBeInTheDocument();
```

Also assert an empty representative displays “—” instead of the string `null` and that candidate-pool errors show a local error block.

- [ ] **Step 2: Run the focused component test and verify it fails**

Run:

```powershell
npm --prefix frontend run test:run -- src/components/stockpred/fund-rotation/__tests__/BacktestDetailPanel.test.tsx
```

Expected: FAIL because the tab and candidate-pool rendering do not exist.

- [ ] **Step 3: Implement the new tab and lazy loading**

Add a `Layers3`/`ListTree` icon, tab entry, and effect guarded by active tab, selected run, loading state, and empty data. Render one card per recluster with gate summary and one table row per representative. Use existing formatting helpers for values and preserve the existing chart/equity effects.

- [ ] **Step 4: Run the focused component tests and verify they pass**

Run the same command and expect all panel tests to pass.

- [ ] **Step 5: Commit the candidate-pool UI**

```powershell
git add frontend/src/components/stockpred/fund-rotation/BacktestDetailPanel.tsx frontend/src/components/stockpred/fund-rotation/__tests__/BacktestDetailPanel.test.tsx
git commit -m "feat: add fund candidate pool tab"
```

### Task 6: Full verification and integration review

**Files:**
- Modify: only files required by failing verification; do not alter unrelated existing changes.
- Test: existing backend and frontend fund-rotation test suites.

- [ ] **Step 1: Run all focused frontend tests**

```powershell
npm --prefix frontend run test:run -- src/components/stockpred/fund-rotation
```

Expected: exit code 0 with zero failed tests.

- [ ] **Step 2: Run all fund-rotation backend tests**

```powershell
E:\anaconda3\envs\AlphaFin\python.exe -m pytest agent/tests/fund_rotation -q
```

Expected: no new failures; pre-existing environment-specific `tmp_path` failures must be reported separately if reproduced.

- [ ] **Step 3: Run the frontend TypeScript/build verification**

```powershell
npm --prefix frontend run build
```

Expected: TypeScript compilation and Vite build both exit 0.

- [ ] **Step 4: Inspect the final diff**

```powershell
git diff HEAD~6..HEAD --stat
git status --short
```

Confirm existing overview sections and old tabs remain present, the new endpoint is auth-protected and checksum-gated, and no unrelated files were changed.

- [ ] **Step 5: Report verification evidence**

Record exact test commands, pass/fail counts, build result, and any pre-existing environmental failures. Do not claim completion without fresh command output.
