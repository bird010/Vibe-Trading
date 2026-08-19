# StockPred 策略批量回测提速实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**目标：** 建成可恢复的轻量海选与按需完整报告物化流程，在固定快照下保持回测结果一致。

**架构：** 批次协调器是状态文件唯一写入者。海选保存可比较摘要和详情清单、不写逐股 OHLCV；详情物化器仅依据已保存结果和固定快照补写行情。Alpha 海选按评估日共享只读上下文。

**技术栈：** Python 3.11、FastAPI、Pydantic v2、Pandas、Lance、Pytest、React/TypeScript、Vitest。

## 全局约束

- 缓存键至少含数据快照摘要和评估日；不得让评估日读取未来数据。
- 不改变因子、撮合、指标定义或同快照下的回测结果。
- 不新增运行时依赖；Windows 上不得在进程间传递大型 Pandas 面板。
- 保留工作区已有的未完成批次列表改动，并将其接入新的状态模型。
- 初版只消除重复 I/O；进程池最多 2 worker，且只在基准证明安全后启用。

---

### Task 1：批次状态、心跳、停滞和恢复

**文件：**

- 修改：agent/src/stockpred/batch_store.py:20-108
- 修改：agent/src/stockpred/batch_service.py:17-59
- 修改：agent/tests/stockpred/test_batch_store.py
- 修改：agent/tests/stockpred/test_batch_service.py

**接口：**

- 新增 StockPredBatchStore.start_screening(batch_id)、heartbeat(batch_id, current_strategy_id)、mark_stalled(batch_id)、resume_candidates(batch_id)。
- 修改 StockPredStrategyBatchService.execute(batch_id, *, resume: bool = False)。

- [ ] **Step 1：先写失败测试。**

~~~
def test_stalled_batch_only_resumes_nonterminal_reports(tmp_path) -> None:
    store, batch_id = _running_store(tmp_path)
    store.finish_report(batch_id, "alpha101_1", run_id="strategy_ok", status="success", metrics={"sharpe": 1.0})
    store.mark_stalled(batch_id, now="2026-07-25T00:20:00Z")

    assert store.summary(batch_id)["phase"] == "stalled"
    assert store.resume_candidates(batch_id) == ["alpha101_2"]
~~~

- [ ] **Step 2：确认失败。**

运行：pytest agent/tests/stockpred/test_batch_store.py::test_stalled_batch_only_resumes_nonterminal_reports -q

预期：失败，提示 mark_stalled 或 resume_candidates 不存在。

- [ ] **Step 3：实现最小状态模型。**

create() 同时写入兼容的 status 和以下字段：

~~~
{
    "phase": "queued",
    "screening_done": 0,
    "screening_total": len(descriptors),
    "detail_done": 0,
    "detail_total": 0,
    "heartbeat_at": created_at,
    "timings": {},
}
~~~

finish_report() 更新 screening_done、current_strategy_id、heartbeat_at。resume_candidates() 只能返回 queued 或中断项，绝不能返回 success。数据缺失、NaN 比例和参数错误归类为确定性错误；仅暂时 I/O 错误最多重试一次并记录 error_class 与 attempt。

- [ ] **Step 4：让服务按待处理项执行。**

~~~
def execute(self, batch_id: str, *, resume: bool = False) -> str:
    strategy_ids = self.store.resume_candidates(batch_id) if resume else self.store.pending_strategy_ids(batch_id)
    self.store.start_screening(batch_id, total=len(self.store.strategy_ids(batch_id)))
    for strategy_id in strategy_ids:
        self.store.heartbeat(batch_id, current_strategy_id=strategy_id)
        # 保留既有 run_one 和单策略失败隔离
~~~

- [ ] **Step 5：验证并提交。**

运行：pytest agent/tests/stockpred/test_batch_store.py agent/tests/stockpred/test_batch_service.py -q

预期：通过，覆盖成功项不重跑、一次 I/O 重试和正确终态。

~~~
git add agent/src/stockpred/batch_store.py agent/src/stockpred/batch_service.py agent/tests/stockpred/test_batch_store.py agent/tests/stockpred/test_batch_service.py
git commit -m "feat(stockpred): make strategy batches resumable"
~~~

### Task 2：轻量海选产物与完整报告物化

**文件：**

- 修改：agent/backtest/stockpred_strategy/artifacts.py:17-56
- 修改：agent/backtest/stockpred_strategy/runner.py:19-78
- 修改：agent/backtest/stockpred_graph/performance.py:43-57
- 修改：agent/src/stockpred/strategy_execution.py:31-56
- 新建：agent/src/stockpred/strategy_detail.py
- 修改：agent/tests/stockpred/test_strategy_artifacts.py
- 新建：agent/tests/stockpred/test_strategy_detail.py
- 修改：agent/tests/stockpred/test_performance.py

**接口：**

- 新增 write_screening_artifacts(run_dir, result, manifest, config) -> dict[str, object]。
- 新增 materialize_strategy_detail(run_dir: Path, gateway: StockPredDataGateway) -> Path。

- [ ] **Step 1：先写失败测试。**

~~~
def test_screening_then_detail_materialization(tmp_path, gateway) -> None:
    manifest = write_screening_artifacts(tmp_path, _result_with_selected_code(), _manifest(), _config())

    assert not list((tmp_path / "artifacts").glob("ohlcv_*.csv"))
    assert (tmp_path / "detail_manifest.json").is_file()
    materialize_strategy_detail(tmp_path, gateway)
    assert (tmp_path / "detail" / "ohlcv_000001.SZ.csv").is_file()
    assert manifest["codes"] == ["000001.SZ"]
~~~

- [ ] **Step 2：确认失败。**

运行：pytest agent/tests/stockpred/test_strategy_detail.py -q

预期：失败，提示模块或函数不存在。

- [ ] **Step 3：实现海选摘要和清单。**

海选只写 metrics.csv、equity.csv、positions.csv、trades.csv、selected_signals.csv、symbol_metrics.csv、signals.parquet、策略归档和数据快照。detail_manifest.json 最少包含：

~~~
{
    "version": 1,
    "run_id": run_dir.name,
    "comparison_key": config.comparison_key,
    "data_snapshot": manifest.model_dump(mode="json"),
    "codes": sorted(result.selected["ts_code"].astype(str).unique()),
    "market_start": config.start,
    "market_end": end_plus_60_days,
}
~~~

selected 为空时照样写空 codes，物化器生成空 detail 目录。

- [ ] **Step 3a：删除海选执行期的逐代码 DataFrame 扇出。**

在 performance.py 新增 build_symbol_metrics_from_market(trades, market)。它一次规范化 market 的 ts_code，按 ts_code groupby 并向现有 _build_symbol_equity 传入每组价格；保留现有 build_symbol_metrics(trades, dict) 给 Graph 和 UI 调用方。策略 runner 使用新函数、返回空 ohlcv 字典；详情物化器是 OHLCV 文件唯一生产者。

先在 test_performance.py 写入等价测试：对同一 trades 和 market，两个函数的规范化输出相同。这样不会因消除 O(代码数 × 全表行数) 布尔扫描而改变逐股票指标。

- [ ] **Step 4：实现幂等且原子化的物化器。**

读取清单，严格比较 gateway.manifest.model_dump(mode="json") 与清单快照，批量读取价格和复权因子、调用 apply_qfq，再以 ts_code 分组写入 detail/.staging；成功后目录替换为 detail。完成的 detail 直接返回，失败时不破坏海选摘要。

- [ ] **Step 5：让 StrategyReportExecutor 默认写海选产物并验证。**

运行：pytest agent/tests/stockpred/test_strategy_artifacts.py agent/tests/stockpred/test_strategy_detail.py agent/tests/stockpred/test_strategy_runner.py -q

预期：通过；验证摘要不超过 20 文件、清单快照失配被拒绝、物化幂等。

~~~
git add agent/backtest/stockpred_strategy/artifacts.py agent/backtest/stockpred_strategy/runner.py agent/backtest/stockpred_graph/performance.py agent/src/stockpred/strategy_execution.py agent/src/stockpred/strategy_detail.py agent/tests/stockpred/test_strategy_artifacts.py agent/tests/stockpred/test_strategy_detail.py agent/tests/stockpred/test_performance.py
git commit -m "feat(stockpred): split screening and detail artifacts"
~~~

### Task 3：快照隔离的批次数据上下文

**文件：**

- 新建：agent/src/stockpred/batch_data.py
- 修改：agent/src/stockpred/strategies/panel.py:13-66
- 修改：agent/tests/stockpred/strategies/test_panel.py
- 修改：agent/tests/stockpred/test_gateway.py
- 新建：agent/tests/stockpred/test_batch_data.py

**接口：**

- 新增 BatchDataContext(gateway, snapshot_digest)。
- 新增 static_inputs()、panel(eval_date, max_lookback)、panel_for_strategy(eval_date, descriptor)、release_eval_date()。

- [ ] **Step 1：先写读取计数与快照隔离测试。**

~~~
def test_context_caches_static_frames_but_not_across_snapshot() -> None:
    gateway = CountingGateway()
    context = BatchDataContext(gateway, snapshot_digest="a" * 64)

    context.panel_for_strategy("20250103", _descriptor(warmup=2))
    context.panel_for_strategy("20250103", _descriptor(warmup=2))

    assert gateway.stock_dimension_calls == 1
    assert gateway.prices_calls == 1
    assert BatchDataContext(gateway, snapshot_digest="b" * 64).snapshot_digest != context.snapshot_digest
~~~

- [ ] **Step 2：确认失败。**

运行：pytest agent/tests/stockpred/test_batch_data.py -q

预期：失败，提示 BatchDataContext 不存在。

- [ ] **Step 3：实现有界缓存，并让面板构建器可选使用它。**

上下文缓存交易日历和三张静态维表。动态面板键为 (snapshot_digest, eval_date, max_lookback)，一个评估日只能构建一次，release_eval_date() 后释放。给策略的输入保持旧长度：

~~~
def panel_for_strategy(self, eval_date: str, descriptor: StrategyDescriptor) -> dict[str, pd.DataFrame]:
    required = max(self.data_lookback_days, descriptor.min_warmup_bars + 1)
    full = self.panel(eval_date, self.max_lookback)
    return {name: frame.tail(required).copy() for name, frame in full.items()}
~~~

StockPredPanelBuilder 保留 _build_uncached() 以维持单策略调用与现有测试。

- [ ] **Step 4：验证 PIT 和缓存语义并提交。**

运行：pytest agent/tests/stockpred/strategies/test_panel.py agent/tests/stockpred/test_gateway.py agent/tests/stockpred/test_batch_data.py -q

预期：通过；相同快照同日命中缓存、不同快照不命中、最大日期不超过评估日。

~~~
git add agent/src/stockpred/batch_data.py agent/src/stockpred/strategies/panel.py agent/tests/stockpred/test_batch_data.py agent/tests/stockpred/strategies/test_panel.py agent/tests/stockpred/test_gateway.py
git commit -m "feat(stockpred): share immutable batch data"
~~~

### Task 4：按评估日协调 Alpha 海选并做等价验证

**文件：**

- 新建：agent/src/stockpred/batch_screening.py
- 修改：agent/backtest/stockpred_strategy/runner.py:37-78
- 修改：agent/src/stockpred/strategy_execution.py:23-79
- 修改：agent/src/stockpred/batch_service.py
- 新建：agent/tests/stockpred/test_batch_screening.py
- 修改：agent/tests/stockpred/test_strategy_runner.py

**接口：**

- 新增 StrategyScreeningSession.evaluate(eval_date, panel) 和 finalize()。
- 新增 AlphaBatchScreeningCoordinator.run()。

- [ ] **Step 1：先写新旧结果等价测试。**

~~~
def test_shared_date_screening_matches_individual_runner(fixed_gateway) -> None:
    expected = _run_individually(fixed_gateway, ["alpha101_1", "alpha101_2"])
    actual = AlphaBatchScreeningCoordinator(fixed_gateway, _configs()).run()

    assert _canonical(actual["alpha101_1"]) == _canonical(expected["alpha101_1"])
    assert _canonical(actual["alpha101_2"]) == _canonical(expected["alpha101_2"])
~~~

_canonical() 必须固定排序交易、持仓和信号，并以 pytest.approx(abs=1e-12) 比较浮点指标。

- [ ] **Step 2：确认失败。**

运行：pytest agent/tests/stockpred/test_batch_screening.py::test_shared_date_screening_matches_individual_runner -q

预期：失败，提示 AlphaBatchScreeningCoordinator 不存在。

- [ ] **Step 3：提取策略会话并实现协调器。**

会话保存 previous_holdings、有效日期、信号与入选结果，继续使用现有 build_equal_weight_targets 参数。协调器只处理 alpha_zoo：计算共同最大回看窗口，遍历开放交易日，将同日共享面板的切片交给所有会话，再释放该日面板。每个会话完成后继续调用既有 _execute() 和指标计算；图策略仍走独立路径。

- [ ] **Step 4：验证等价并提交。**

运行：pytest agent/tests/stockpred/test_batch_screening.py agent/tests/stockpred/test_strategy_runner.py agent/tests/stockpred/test_batch_service.py -q

预期：通过；共享协调器与旧运行器结果一致。

~~~
git add agent/src/stockpred/batch_screening.py agent/backtest/stockpred_strategy/runner.py agent/src/stockpred/strategy_execution.py agent/src/stockpred/batch_service.py agent/tests/stockpred/test_batch_screening.py agent/tests/stockpred/test_strategy_runner.py
git commit -m "feat(stockpred): screen alpha strategies by evaluation date"
~~~

### Task 5：详情 API、SSE 心跳和启动恢复

**文件：**

- 修改：agent/src/api/stockpred_routes.py:40-173
- 修改：agent/src/stockpred/batch_service.py
- 修改：agent/src/stockpred/batch_store.py
- 修改：agent/tests/stockpred/test_batch_api.py
- 修改：agent/tests/stockpred/test_batch_service.py

**接口：**

- 新增 POST /stockpred/strategy-batches/{batch_id}/detail-reports。
- 批次摘要新增 phase、进度、heartbeat_at、候选和空间估计。

- [ ] **Step 1：先写详情端点和心跳事件失败测试。**

~~~
def test_detail_reports_accept_top_n(api, monkeypatch) -> None:
    response = api.post("/stockpred/strategy-batches/batch_123/detail-reports", json={"top_n": 20, "sort_by": "sharpe"})

    assert response.status_code == 202
    assert response.json()["batch_id"] == "batch_123"
~~~

另写 SSE 测试，验证无策略结束时仍得到带 heartbeat_at 的 progress 事件。

- [ ] **Step 2：确认失败。**

运行：pytest agent/tests/stockpred/test_batch_api.py -q

预期：失败，端点为 404 或服务方法不存在。

- [ ] **Step 3：实现候选选择、物化服务和恢复入口。**

~~~
def materialize_details(self, batch_id: str, *, strategy_ids: tuple[str, ...] = (), top_n: int | None = None, sort_by: str = "sharpe") -> str:
    selected = self.store.select_successful_reports(batch_id, strategy_ids=strategy_ids, top_n=top_n, sort_by=sort_by)
    self.store.start_detail(batch_id, total=len(selected))
    # 父服务逐项调用物化器并写 detail_done/detail_total
~~~

请求模型要求 strategy_ids 与 top_n 二选一，top_n 最大 20。注册路由后的启动钩子只扫描过期心跳并标记 stalled；GET 和 SSE 不得触发执行。保留已有 GET /stockpred/strategy-batches 未完成批次列表。

- [ ] **Step 4：验证并提交。**

运行：pytest agent/tests/stockpred/test_batch_api.py agent/tests/stockpred/test_batch_service.py agent/tests/stockpred/test_batch_store.py -q

预期：通过；详情只接受成功海选项、过期批次会停滞、恢复不重跑成功项。

~~~
git add agent/src/api/stockpred_routes.py agent/src/stockpred/batch_service.py agent/src/stockpred/batch_store.py agent/tests/stockpred/test_batch_api.py agent/tests/stockpred/test_batch_service.py agent/tests/stockpred/test_batch_store.py
git commit -m "feat(stockpred): expose resumable detail reports"
~~~

### Task 6：前端、操作文档、阶段指标与发布基准

**文件：**

- 修改：frontend/src/lib/api.ts
- 修改：frontend/src/pages/StockPred.tsx
- 修改：frontend/src/pages/__tests__/StockPred.test.tsx
- 新建：agent/src/stockpred/batch_metrics.py
- 修改：agent/src/stockpred/strategy_execution.py
- 新建：agent/tests/stockpred/test_batch_metrics.py
- 修改：docs/stockpred-strategy-batch-operations.md

**接口：**

- 新增 api.materializeStrategyDetails(batchId, body)。
- 新增 PhaseTimer 与每策略 timings、读取/缓存/产物统计。

- [ ] **Step 1：先写前端和指标失败测试。**

~~~
it("offers detail materialization only after screening completes", async () => {
  apiMock.getStrategyBatch.mockResolvedValue(screeningCompletedBatch);
  renderStockPred();

  expect(await screen.findByRole("button", { name: "生成前 20 名完整报告" })).toBeEnabled();
  await userEvent.setup().click(screen.getByRole("button", { name: "生成前 20 名完整报告" }));
  expect(apiMock.materializeStrategyDetails).toHaveBeenCalledWith("batch_123", { top_n: 20, sort_by: "sharpe" });
})
~~~

~~~
def test_screening_records_timings_and_artifact_budget(tmp_path) -> None:
    state = run_fixture_strategy(tmp_path)
    assert set(state["timings"]) >= {"data_load", "panel_build", "factor_compute", "execution", "artifact_write"}
    assert len(list((tmp_path / "artifacts").iterdir())) <= 20
~~~

- [ ] **Step 2：确认失败。**

运行：npm --prefix frontend test -- --run src/pages/__tests__/StockPred.test.tsx

运行：pytest agent/tests/stockpred/test_batch_metrics.py -q

预期：分别因客户端方法/按钮和 PhaseTimer 缺失失败。

- [ ] **Step 3：实现页面和无依赖计时器。**

在页面仅 phase 为 screening_completed 且存在候选时显示生成前 20 名完整报告按钮，展示候选数、空间预估、detail_done/detail_total、stalled 状态；页面加载和轮询不得自动物化。

~~~
@dataclass
class PhaseTimer:
    values: dict[str, float] = field(default_factory=dict)

    @contextmanager
    def measure(self, name: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self.values[name] = self.values.get(name, 0.0) + time.perf_counter() - started
~~~

在 data_load、panel_build、factor_compute、execution、artifact_write 处使用它，并持久化读取行数、缓存命中/未命中、文件数和字节数。文档说明两阶段流程、默认前 20 名、空间预估和恢复语义。

- [ ] **Step 4：建立固定快照的 10 策略基准。**

使用现有 StockPred fixture，禁止网络访问。写出 benchmark.json，含阶段耗时、缓存统计、输出文件数/字节数与等价结果。CI 只验证 JSON 结构和产物预算；发布验收在同一台机器上证明海选总墙钟较旧流程降低至少 50%，之后才评估 2 worker 物化并发。

- [ ] **Step 5：全量验证并提交。**

运行：pytest agent/tests/stockpred -q

运行：ruff check agent/src/stockpred agent/backtest/stockpred_strategy agent/tests/stockpred

运行：npm --prefix frontend test -- --run src/pages/__tests__/StockPred.test.tsx

预期：全部通过。

~~~
git add frontend/src/lib/api.ts frontend/src/pages/StockPred.tsx frontend/src/pages/__tests__/StockPred.test.tsx agent/src/stockpred/batch_metrics.py agent/src/stockpred/strategy_execution.py agent/tests/stockpred/test_batch_metrics.py docs/stockpred-strategy-batch-operations.md
git commit -m "test(stockpred): measure two-stage batch performance"
~~~

## 计划自检

- Task 1 覆盖恢复和重试；Task 2 覆盖两阶段产物；Task 3-4 覆盖共享数据与等价性；Task 5 覆盖 API/SSE；Task 6 覆盖用户操作、指标和性能验收。
- 未改变因子、撮合或指标公式；进程并发被限制为基准后才启用。
- detail_manifest 是详情物化唯一输入，协调器是状态唯一写入者，恢复不得重复成功项。
