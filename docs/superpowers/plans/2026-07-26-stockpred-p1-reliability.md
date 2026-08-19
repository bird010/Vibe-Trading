# StockPred P1 可靠性修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 防止不完整 OHLCV 污染详情标的列表，并确保一次用户启动意图最多执行一个策略批次。

**Architecture:** 完成 marker 是分文件 OHLCV 的统一读取门闩；未完成时摘要使用 manifest 的完整 codes。批次 POST 通过客户端 UUID 和服务端原子映射去重，前端用活动 batch 状态阻止误操作。

**Tech Stack:** Python 3.12、FastAPI、Pydantic、pytest、React、TypeScript、Vitest。

## Global Constraints

- 不兼容或迁移已删除的旧版报告。
- idempotency_key 不参与 comparison_key，也不进入 StrategyBatchRequest。
- 无 detail_manifest.json 的运行保持原行为。
- 仅使用 Python 标准库文件原子操作，不增加运行时依赖。

---

### Task 1: 统一完成校验并保护详情标的列表

**Files:**

- Modify: agent/src/stockpred/strategy_detail.py
- Modify: agent/src/ui_services.py
- Modify: agent/tests/stockpred/test_strategy_detail.py
- Modify: agent/tests/stockpred/test_run_analysis.py

**Interfaces:**

- Produces: detail_publish_complete(run_dir: Path) -> bool。
- Consumes: detail_manifest.json、detail_complete.json 与 artifacts/ohlcv_<code>.csv。

- [ ] **Step 1: 写失败测试：部分 CSV 不得缩短 summary 标的列表**

在 test_run_analysis.py 新增：

```python
def test_load_chart_symbols_uses_manifest_codes_while_publish_incomplete(tmp_path: Path) -> None:
    run_dir = tmp_path / "strategy_test"
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True)
    (run_dir / "detail_manifest.json").write_text(
        json.dumps({"version": 1, "codes": ["A", "B"]}), encoding="utf-8"
    )
    (artifacts / "ohlcv_A.csv").write_text(
        "trade_date,close\n20250102,10\n", encoding="utf-8"
    )

    assert load_chart_symbols(run_dir, {"codes": ["A", "B"]}) == ["A", "B"]
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python -B -m pytest agent/tests/stockpred/test_run_analysis.py::test_load_chart_symbols_uses_manifest_codes_while_publish_incomplete -q -p no:cacheprovider`

Expected: FAIL；当前实现只返回 A。

- [ ] **Step 3: 写失败测试：无效 marker 不得允许读取 CSV**

新增两个样例：

```python
assert load_price_series(run_dir) == []
assert load_chart_symbols(run_dir, {"codes": ["A", "B"]}) == ["A", "B"]
```

一个样例使 marker 的 detail_manifest_sha256 错误；另一个样例只创建 ohlcv_A.csv，缺少 ohlcv_B.csv。

- [ ] **Step 4: 最小实现共享校验**

在 strategy_detail.py 定义：

```python
def detail_publish_complete(run_dir: Path) -> bool:
    root = Path(run_dir)
    manifest_path = root / "detail_manifest.json"
    if not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        codes = [str(code) for code in manifest["codes"]]
    except (KeyError, OSError, TypeError, json.JSONDecodeError):
        return False
    return _completion_marker_valid(root, codes, manifest)
```

删除 ui_services.py 内重复的 _detail_publish_complete。导入新函数。load_chart_symbols 在 manifest 存在且函数返回 False 时读取 manifest["codes"] 并排序返回；只有完成或非分阶段运行才扫描 CSV。

- [ ] **Step 5: 运行详情读取测试**

Run: `python -B -m pytest agent/tests/stockpred/test_strategy_detail.py agent/tests/stockpred/test_run_analysis.py -q -p no:cacheprovider`

Expected: PASS；部分 CSV 不出现在价格结果，也不会缩短 symbol list。

- [ ] **Step 6: Commit**

```powershell
git add agent/src/stockpred/strategy_detail.py agent/src/ui_services.py agent/tests/stockpred/test_strategy_detail.py agent/tests/stockpred/test_run_analysis.py
git commit -m "fix: gate strategy detail reads on completion marker"
```

### Task 2: 隔离详情发布临时路径

**Files:**

- Modify: agent/src/stockpred/strategy_detail.py
- Modify: agent/tests/stockpred/test_strategy_detail.py

**Interfaces:**

- Produces: 每次 materialize_strategy_detail 调用独占的 staging 与 marker temporary path。

- [ ] **Step 1: 写失败测试：临时路径必须调用级唯一**

通过 monkeypatch uuid.uuid4 依次返回两个 UUID，替换 _write_ohlcv 记录 staging 参数，替换 marker 写入记录临时路径。连续两次物化后断言两组路径不相同，且第二次清理不删除第一次记录的路径。

- [ ] **Step 2: 运行测试，确认失败**

Run: `python -B -m pytest agent/tests/stockpred/test_strategy_detail.py::test_detail_materialization_uses_unique_temporary_paths -q -p no:cacheprovider`

Expected: FAIL；当前 PID staging 或固定 marker tmp 重复。

- [ ] **Step 3: 最小实现**

```python
token = uuid.uuid4().hex
staging = root / f".detail.staging.{token}"
temp_path = root / f".detail_complete.{uuid.uuid4().hex}.tmp"
```

finally 仅删除当前调用保存的 staging；完成 marker 仍通过 Path.replace() 原子发布。

- [ ] **Step 4: 运行测试**

Run: `python -B -m pytest agent/tests/stockpred/test_strategy_detail.py -q -p no:cacheprovider`

Expected: PASS。

- [ ] **Step 5: Commit**

```powershell
git add agent/src/stockpred/strategy_detail.py agent/tests/stockpred/test_strategy_detail.py
git commit -m "fix: isolate concurrent strategy detail staging"
```

### Task 3: 实现服务端批次幂等预留

**Files:**

- Modify: agent/src/api/stockpred_routes.py
- Modify: agent/src/stockpred/batch_store.py
- Modify: agent/src/stockpred/batch_service.py
- Modify: agent/tests/stockpred/test_batch_api.py
- Modify: agent/tests/stockpred/test_batch_service.py

**Interfaces:**

- Produces: IdempotentStrategyBatchRequest，包含 idempotency_key: UUID 与 StrategyBatchRequest 字段。
- Produces: StrategyBatchService.reserve_idempotent(body) -> tuple[str, bool]。
- Produces: StockPredBatchStore 的 .idempotency/<key>.json 映射。

- [ ] **Step 1: 写失败测试：同 key 仅预留一次**

```python
first_id, first_created = service.reserve_idempotent(body)
second_id, second_created = service.reserve_idempotent(body)
assert first_created is True
assert second_created is False
assert second_id == first_id
```

- [ ] **Step 2: 写失败测试：不同 key 代表独立意图**

对相同回测参数使用两个 UUID：

```python
assert first_created is True
assert second_created is True
assert first_id != second_id
```

- [ ] **Step 3: 运行测试，确认失败**

Run: `python -B -m pytest agent/tests/stockpred/test_batch_service.py -q -p no:cacheprovider`

Expected: FAIL；reserve_idempotent 未定义。

- [ ] **Step 4: 加入传输模型与原子映射**

在路由模块用 Pydantic 模型从扁平 body 提取 idempotency_key，再将剩余字段交给 StrategyBatchRequest.model_validate。key 不得传给 StrategyBatchRequest。

在 batch_store.py：

```python
mapping = self.root / ".idempotency" / f"{key}.json"
try:
    with mapping.open("x", encoding="utf-8") as handle:
        json.dump({"batch_id": candidate_batch_id}, handle)
except FileExistsError:
    return json.loads(mapping.read_text(encoding="utf-8"))["batch_id"]
return None
```

候选 batch 必须先完整创建；FileExistsError 路径只删除本调用新建且未映射的候选目录。

- [ ] **Step 5: 路由只在首次创建时调度**

```python
batch_id, created = service.reserve_idempotent(body)
if created:
    task = asyncio.create_task(asyncio.to_thread(service.execute, batch_id))
    _RUNNING_TASKS.add(task)
    task.add_done_callback(_RUNNING_TASKS.discard)
return {"batch_id": batch_id, "events_url": f"/stockpred/strategy-batches/{batch_id}/events"}
```

新增 API 测试：相同 key 发两次 POST，响应 batch_id 相同，mock execute 只被调度一次。

- [ ] **Step 6: 运行后端测试**

Run: `python -B -m pytest agent/tests/stockpred/test_batch_api.py agent/tests/stockpred/test_batch_service.py -q -p no:cacheprovider`

Expected: PASS。

- [ ] **Step 7: Commit**

```powershell
git add agent/src/api/stockpred_routes.py agent/src/stockpred/batch_store.py agent/src/stockpred/batch_service.py agent/tests/stockpred/test_batch_api.py agent/tests/stockpred/test_batch_service.py
git commit -m "fix: make strategy batch creation idempotent"
```

### Task 4: 前端复用幂等键并阻止活动批次重复启动

**Files:**

- Modify: frontend/src/lib/api.ts
- Modify: frontend/src/pages/StockPred.tsx
- Modify: frontend/src/pages/__tests__/StockPred.test.tsx

**Interfaces:**

- Consumes: POST body 的 idempotency_key: string。
- Produces: activeBatchId 与 activeIdempotencyKeyRef。

- [ ] **Step 1: 写失败测试：POST 成功、GET 连续失败时禁止再次提交**

mock createStrategyBatch 成功、getStrategyBatch 连续 reject。点击启动并推进重试计时器后，断言启动按钮 disabled；再次点击后 createStrategyBatch 调用数仍为 1。

- [ ] **Step 2: 写失败测试：终态后使用新 key**

mock 两次 POST 成功。触发第一个 batch 的 done 事件，再次点击；断言两次请求都有 idempotency_key，且值不同。

- [ ] **Step 3: 运行测试，确认失败**

Run: `npm --prefix frontend test -- --run src/pages/__tests__/StockPred.test.tsx`

Expected: FAIL；请求不含 key，或活动 batch 期间仍可启动。

- [ ] **Step 4: 最小实现**

在 API 类型将 idempotency_key 声明为必填。组件首次启动时生成并保存 crypto.randomUUID()；POST、重试与渲染复用该 key。canStart 增加“没有 queued/running/stalled active batch”。在 done、batch_error 或 GET 读到终态时清空 active batch 和 key。

- [ ] **Step 5: 运行前端测试**

Run: `npm --prefix frontend test -- --run src/pages/__tests__/StockPred.test.tsx`

Expected: PASS。

- [ ] **Step 6: Commit**

```powershell
git add frontend/src/lib/api.ts frontend/src/pages/StockPred.tsx frontend/src/pages/__tests__/StockPred.test.tsx
git commit -m "fix: prevent duplicate strategy batch submissions"
```

### Task 5: 跨层验证

**Files:**

- Modify: 无

- [ ] **Step 1: 运行 Python 回归集**

Run: `python -B -m pytest agent/tests/stockpred/test_strategy_detail.py agent/tests/stockpred/test_run_analysis.py agent/tests/stockpred/test_batch_api.py agent/tests/stockpred/test_batch_service.py -q -p no:cacheprovider`

Expected: PASS。

- [ ] **Step 2: 运行前端构建与测试**

Run: `npm --prefix frontend run build`

Expected: exit code 0。

Run: `npm --prefix frontend test -- --run src/pages/__tests__/StockPred.test.tsx`

Expected: PASS。

- [ ] **Step 3: 检查补丁**

Run: `git diff --check HEAD~4 HEAD`

Expected: 无输出，exit code 0。

## 自审

- Tasks 1-2 覆盖 OHLCV P1；Tasks 3-4 覆盖重复批次 P1。
- 每个任务按失败测试、最小实现、通过测试、提交拆分。
- 未包含旧报告迁移、回测参数改造或第三方依赖。

