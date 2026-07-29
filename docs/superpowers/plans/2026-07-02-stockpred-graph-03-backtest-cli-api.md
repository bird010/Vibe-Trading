# StockPred Graph 回测服务、CLI 与 API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Vibe 中完成 Graph 历史回测、可审计工件、持久状态、专用 CLI 和异步 API，并让现有 `/runs/{run_id}` 能读取结果。

**Architecture:** `GraphBacktestRunner` 负责确定性历史循环，`GraphBacktestService` 负责 run 生命周期和原子工件发布。CLI 与 API 只做输入输出适配，均调用该 service；API 后台线程通过持久 `state.json` 发布 SSE 进度。

**Tech Stack:** Python 3.11、pandas、numpy、FastAPI、Pydantic 2、sse-starlette、pytest

## Global Constraints

- 必须先完成数据 Gateway 和 Graph Core 两份计划。
- 默认 parity mode：Top-50、每 5 个交易日评价、5 日持有、沪深 300、1000 万 CNY、最大参与率 5%。
- parity mode 禁止覆盖兼容参数；research mode 才允许 `top_n/eval_step` 自定义。
- 买入为下一交易日复权开盘价；停牌/涨停不买；停牌/跌停卖出顺延。
- 滑点 `clip(5 + 200 × participation_rate, 5, 30)` bps；佣金 15 bps；卖出印花税 10 bps。
- 状态必须落盘，不能只存在进程内存；失败保留 snapshot、state 和日志。
- CLI/API/Web 共用 `GraphBacktestService`，禁止复制回测流程。
- 设计依据：`docs/superpowers/specs/2026-07-02-stockpred-graph-vibe-integration-design.md`。

---

## File Structure

- Create `agent/src/stockpred/graph/backtest_config.py`：parity/research 配置校验。
- Create `agent/backtest/stockpred_graph/__init__.py`。
- Create `agent/backtest/stockpred_graph/execution.py`：可交易规则、容量和费用。
- Create `agent/backtest/stockpred_graph/runner.py`：历史评价日循环和组合记账。
- Create `agent/backtest/stockpred_graph/artifacts.py`：标准/审计工件写入。
- Create `agent/src/stockpred/run_store.py`：原子 JSON 和持久状态。
- Create `agent/src/stockpred/backtest_service.py`：统一应用服务。
- Create `agent/src/stockpred/cli_handlers.py`：专用 CLI。
- Create `agent/src/api/stockpred_routes.py`：status/defaults/backtests/SSE。
- Modify `agent/cli/_legacy.py`：仅注册与分发 StockPred handler。
- Modify `agent/api_server.py`：注册路由、扩展 Graph 信号响应。
- Modify `agent/src/ui_services.py`：读取 Graph 信号轨。
- Create `agent/tests/stockpred/test_execution.py`、`test_runner.py`、`test_artifacts.py`、`test_backtest_service.py`、`test_cli.py`、`test_api.py`。

### Task 1: 固定回测配置和评价日循环

**Files:**
- Create: `agent/src/stockpred/graph/backtest_config.py`
- Create: `agent/backtest/stockpred_graph/__init__.py`
- Create: `agent/backtest/stockpred_graph/runner.py`
- Create: `agent/tests/stockpred/test_runner.py`

**Interfaces:**
- Produces: `GraphBacktestConfig` Pydantic model。
- Produces: `GraphBacktestResult(eval_dates, signals, selected, trades, positions, equity, metrics)`。
- Produces: `GraphBacktestRunner.run(config, on_progress=None) -> GraphBacktestResult`。
- Produces: `ProgressCallback = Callable[[int, int, str], None]`，参数为 done、total、eval_date。

- [ ] **Step 1: 写 parity 锁定和评价日测试**

```python
def test_parity_mode_rejects_execution_override() -> None:
    with pytest.raises(ValidationError):
        GraphBacktestConfig(start="2025-01-01", end="2025-03-31", mode="parity", top_n=20)


def test_runner_evaluates_every_fifth_open_day(fake_gateway, fake_signal_service) -> None:
    result = GraphBacktestRunner(fake_gateway, fake_signal_service).run(
        GraphBacktestConfig(start="2025-01-01", end="2025-01-31")
    )
    assert result.eval_dates == fake_gateway.trade_dates("20250101", "20250131")[::5]
```

- [ ] **Step 2: 运行并确认失败**

Run: `python -m pytest agent/tests/stockpred/test_runner.py -q`

Expected: FAIL，配置和 runner 不存在。

- [ ] **Step 3: 实现配置校验和无撮合历史循环**

```python
class GraphBacktestConfig(BaseModel):
    start: str
    end: str
    mode: Literal["parity", "research"] = "parity"
    lookback_days: int = 120
    data_lookback_days: int = 180
    forward_days: int = 5
    top_n: int = 50
    eval_step: int = 5
    benchmark_code: str = "000300.SH"
    min_listed_trade_days: int = 60
    min_adj_coverage: float = 0.98
    min_valid_eval_ratio: float = 0.90
    buffer_retain_rank: int = 15
    portfolio_capital: float = 10_000_000.0
    max_participation: float = 0.05
    parity_reference: str | None = None

    @model_validator(mode="after")
    def lock_parity(self) -> "GraphBacktestConfig":
        locked = {"top_n": 50, "eval_step": 5, "forward_days": 5, "benchmark_code": "000300.SH"}
        if self.mode == "parity" and any(getattr(self, key) != value for key, value in locked.items()):
            raise ValueError("parity mode parameters are locked")
        return self
```

runner 先只收集逐评价日 signals/selected，并调用 `on_progress(done, total, eval_date)`；交易和净值在 Task 2 接入。

- [ ] **Step 4: 运行测试**

Run: `python -m pytest agent/tests/stockpred/test_runner.py -q`

Expected: PASS，且有效评价日比例不足 90% 时返回 `STOCKPRED_VALID_EVAL_RATIO`。

- [ ] **Step 5: 提交**

```bash
git add agent/src/stockpred/graph/backtest_config.py agent/backtest/stockpred_graph agent/tests/stockpred/test_runner.py
git commit -m "feat(stockpred): add graph backtest loop"
```

### Task 2: 复现执行、容量和成本语义

**Files:**
- Create: `agent/backtest/stockpred_graph/execution.py`
- Create: `agent/tests/stockpred/test_execution.py`
- Modify: `agent/backtest/stockpred_graph/runner.py`

**Interfaces:**
- Produces: `estimate_one_way_cost_bps()`、`apply_capacity_limit()`。
- Produces: `execute_target_portfolio(market, targets, *, signal_date, holding_days, capital, max_participation) -> pd.DataFrame`。
- Runner 输出逐事件 `trades`、逐日 `positions/equity`。

- [ ] **Step 1: 写异常交易日和费用测试**

```python
def test_limit_up_blocks_next_open_entry() -> None:
    trades = execute_target_portfolio(LIMIT_UP_MARKET, TARGETS, signal_date="20250102", holding_days=5, capital=1_000_000, max_participation=0.05)
    row = trades.iloc[0]
    assert row["status"] == "REJECTED"
    assert row["reason"] == "limit_up"


def test_limit_down_delays_exit() -> None:
    trades = execute_target_portfolio(LIMIT_DOWN_MARKET, TARGETS, signal_date="20250102", holding_days=5, capital=1_000_000, max_participation=0.05)
    sell = trades[trades["side"] == "SELL"].iloc[0]
    assert sell["exit_delay_days"] == 1


def test_cost_formula_includes_sell_stamp_duty() -> None:
    assert estimate_one_way_cost_bps(trade_value=50_000, daily_amount_cny=1_000_000, side="sell") == 40.0
```

- [ ] **Step 2: 运行并确认失败**

Run: `python -m pytest agent/tests/stockpred/test_execution.py -q`

Expected: FAIL。

- [ ] **Step 3: 迁移规则并规范事件 schema**

从 StockPred `graph/execution.py` 迁移 `simulate_trades()`、容量和费用公式；输出统一列：

```python
TRADE_COLUMNS = [
    "timestamp", "code", "side", "requested_value", "executed_value", "qty",
    "price", "cost_bps", "status", "reason", "signal_date", "exit_delay_days",
]
```

金额换算使用 StockPred 的 `amount * 1000` CNY 口径。部分成交写 `PARTIAL`；完全无法成交写 `REJECTED`；未成交现金不再分配给其他证券。

- [ ] **Step 4: 运行执行和 runner 测试**

Run: `python -m pytest agent/tests/stockpred/test_execution.py agent/tests/stockpred/test_runner.py -q`

Expected: PASS；交易事件、现金、持仓和 NAV 守恒。

- [ ] **Step 5: 提交**

```bash
git add agent/backtest/stockpred_graph/execution.py agent/backtest/stockpred_graph/runner.py agent/tests/stockpred/test_execution.py agent/tests/stockpred/test_runner.py
git commit -m "feat(stockpred): reproduce graph execution semantics"
```

### Task 3: 写标准工件和持久 run 状态

**Files:**
- Create: `agent/backtest/stockpred_graph/artifacts.py`
- Create: `agent/src/stockpred/run_store.py`
- Create: `agent/src/stockpred/backtest_service.py`
- Create: `agent/tests/stockpred/test_artifacts.py`
- Create: `agent/tests/stockpred/test_backtest_service.py`

**Interfaces:**
- Produces: `StockPredRunStore.create()`、`require()`、`load_config()`、`transition()`、`read()`、`fail()`。
- Produces: `write_graph_artifacts(staging_dir, result, manifest, config, parity_report=None)`。
- Produces: `write_parity_report(path: Path, report: ParityReport) -> None`。
- Produces: `GraphBacktestService.reserve(config) -> str`、`execute(run_id, on_progress=None) -> str`、`run(config, on_progress=None) -> str`。
- Produces: `state.status` 使用 `queued/running/success/failed` 兼容现有 Run API；`state.phase` 使用 `QUEUED/VALIDATING/RUNNING/FINALIZING/SUCCEEDED/FAILED`。

- [ ] **Step 1: 写原子发布和失败保留测试**

```python
def test_service_publishes_complete_run_atomically(tmp_path, fake_runner) -> None:
    run_id = GraphBacktestService(tmp_path, fake_runner, SNAPSHOT_FACTORY).run(CONFIG)
    run_dir = tmp_path / run_id
    assert json.loads((run_dir / "state.json").read_text())["status"] == "success"
    assert (run_dir / "artifacts" / "metrics.csv").is_file()
    model_manifest = json.loads((run_dir / "model_manifest.json").read_text())
    assert model_manifest["id"] == "stockpred-graph"
    assert len(model_manifest["config_sha256"]) == 64
    assert not list(tmp_path.glob(f".{run_id}.staging"))


def test_failed_run_keeps_snapshot_and_error_code(tmp_path, failing_runner) -> None:
    run_id = GraphBacktestService(tmp_path, failing_runner, SNAPSHOT_FACTORY).run(CONFIG)
    state = json.loads((tmp_path / run_id / "state.json").read_text())
    assert state["status"] == "failed"
    assert state["error_code"] == "STOCKPRED_ADJUSTMENT_COVERAGE"
    assert (tmp_path / run_id / "data_snapshot.json").is_file()


def test_failed_parity_writes_report_before_marking_failed(tmp_path, fake_runner, failing_golden) -> None:
    config = CONFIG.model_copy(update={"parity_reference": str(failing_golden)})
    run_id = GraphBacktestService(tmp_path, fake_runner, SNAPSHOT_FACTORY).run(config)
    run_dir = tmp_path / run_id
    assert json.loads((run_dir / "state.json").read_text())["error_code"] == "STOCKPRED_PARITY_FAILED"
    assert json.loads((run_dir / "parity.json").read_text())["passed"] is False
```

- [ ] **Step 2: 运行并确认失败**

Run: `python -m pytest agent/tests/stockpred/test_artifacts.py agent/tests/stockpred/test_backtest_service.py -q`

Expected: FAIL。

- [ ] **Step 3: 实现原子 JSON、工件和 service**

```python
def atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def atomic_csv(path: Path, frame: pd.DataFrame) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    frame.to_csv(tmp, index=False)
    tmp.replace(path)


class GraphBacktestService:
    def reserve(self, config: GraphBacktestConfig) -> str:
        return self.store.create(config).name

    def execute(self, run_id: str, on_progress: ProgressCallback | None = None) -> str:
        run_dir = self.store.require(run_id)
        config = self.store.load_config(run_dir)
        return self._execute(run_dir, config, on_progress)

    def run(self, config: GraphBacktestConfig, on_progress: ProgressCallback | None = None) -> str:
        run_id = self.reserve(config)
        return self.execute(run_id, on_progress)

    def _execute(self, run_dir: Path, config: GraphBacktestConfig, on_progress: ProgressCallback | None) -> str:
        try:
            self.store.transition(run_dir, "VALIDATING")
            manifest = self.snapshot_factory(config)
            atomic_json(run_dir / "data_snapshot.json", manifest.model_dump(mode="json"))
            self.store.transition(run_dir, "RUNNING")
            result = self.runner.run(config, on_progress=on_progress)
            parity_report = (
                compare_backtest_bundle(Path(config.parity_reference), result)
                if config.parity_reference else None
            )
            self.store.transition(run_dir, "FINALIZING")
            write_graph_artifacts(run_dir, result, manifest, config, parity_report=parity_report)
            if parity_report is not None and not parity_report.passed:
                raise StockPredDataError("STOCKPRED_PARITY_FAILED", parity_report.summary)
            self.store.transition(run_dir, "SUCCEEDED")
        except StockPredDataError as exc:
            self.store.fail(run_dir, error_code=exc.code, reason=str(exc))
        except Exception:
            logger.exception("StockPred graph backtest crashed (run=%s)", run_dir.name)
            self.store.fail(run_dir, error_code="STOCKPRED_INTERNAL_ERROR", reason="internal error; see server logs")
        return run_dir.name
```

工件必须包含 `config.json`、`req.json`、`run_card.json`、`data_snapshot.json`、`model_manifest.json`、`artifacts/{metrics,equity,positions,trades}.csv`、`artifacts/signals.parquet`、`artifacts/selected_signals.csv`。传入 `parity_reference` 时还必须写根目录 `parity.json`。只为实际持仓或尝试成交证券写 `artifacts/ohlcv_{code}.csv`。
`config_sha256` 对排除 `parity_reference` 后、按 key 排序且无空白的 `GraphBacktestConfig` JSON 计算 SHA-256，避免本机 golden 路径改变模型身份。
`req.json` 固定写入 `context.strategy_type="stockpred_graph"`、日期、模式和基准；`state.json` 从创建开始写入 `created_at`，供最近运行列表和 RunDetail 识别。parity 失败时先保留完整实际工件与 `parity.json`，再把 run 标记为失败。

- [ ] **Step 4: 运行工件测试和现有 RunDetail 后端测试**

Run: `python -m pytest agent/tests/stockpred/test_artifacts.py agent/tests/stockpred/test_backtest_service.py agent/tests/test_run_card.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add agent/backtest/stockpred_graph/artifacts.py agent/src/stockpred/run_store.py agent/src/stockpred/backtest_service.py agent/tests/stockpred/test_artifacts.py agent/tests/stockpred/test_backtest_service.py
git commit -m "feat(stockpred): persist graph runs and artifacts"
```

### Task 4: 增加专用 CLI

**Files:**
- Create: `agent/src/stockpred/cli_handlers.py`
- Modify: `agent/cli/_legacy.py`
- Create: `agent/tests/stockpred/test_cli.py`

**Interfaces:**
- Produces: `add_subparser(subparsers)`、`dispatch(args) -> int`。
- Produces: `vibe-trading stockpred status`。
- Produces: `vibe-trading stockpred graph-backtest --start --end [--mode parity|research] [--json]`。

- [ ] **Step 1: 写 parser、JSON 输出和锁定参数测试**

```python
def test_graph_backtest_json_calls_shared_service(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli_handlers, "build_service", lambda: FakeService(run_id="graph_123"))
    args = parse_stockpred(["graph-backtest", "--start", "2025-01-01", "--end", "2025-03-31", "--json"])
    assert cli_handlers.dispatch(args) == 0
    assert json.loads(capsys.readouterr().out)["run_id"] == "graph_123"


def test_parity_cli_rejects_top_n_override(capsys) -> None:
    args = parse_stockpred(["graph-backtest", "--start", "2025-01-01", "--end", "2025-03-31", "--top-n", "20"])
    assert cli_handlers.dispatch(args) == 2


def test_graph_backtest_returns_nonzero_when_persisted_run_failed(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli_handlers, "build_service", lambda: FakeService(run_id="graph_failed", status="failed"))
    args = parse_stockpred(["graph-backtest", "--start", "2025-01-01", "--end", "2025-03-31", "--json"])
    assert cli_handlers.dispatch(args) == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == "STOCKPRED_PARITY_FAILED"
```

- [ ] **Step 2: 运行并确认失败**

Run: `python -m pytest agent/tests/stockpred/test_cli.py -q`

Expected: FAIL。

- [ ] **Step 3: 实现 handler 并最小修改 legacy 注册点**

```python
def add_subparser(subparsers: Any) -> argparse.ArgumentParser:
    parser = subparsers.add_parser("stockpred", help="StockPred data and Graph backtests")
    sub = parser.add_subparsers(dest="stockpred_command")
    sub.add_parser("status", help="Validate StockPred data contract").add_argument("--json", action="store_true")
    backtest = sub.add_parser("graph-backtest", help="Run StockPred Graph backtest")
    backtest.add_argument("--start", required=True)
    backtest.add_argument("--end", required=True)
    backtest.add_argument("--mode", choices=("parity", "research"), default="parity")
    backtest.add_argument("--top-n", type=int, default=None)
    backtest.add_argument("--eval-step", type=int, default=None)
    backtest.add_argument("--parity-golden", default=None, help="Frozen StockPred golden directory")
    backtest.add_argument("--json", action="store_true")
    return parser
```

`_legacy.py` 只增加 `add_subparser()` 调用和 `if args.command == "stockpred": return dispatch(args)`，不加入业务逻辑。
`cmd_graph_backtest()` 将 `--parity-golden` 解析为绝对路径并写入 `GraphBacktestConfig.parity_reference`；路径不存在时返回退出码 2。Web/API 请求模型不暴露本地 golden 路径。
service 返回 `run_id` 后，CLI 必须读取持久 `state.json`：`success` 返回 0，`failed` 输出 `error_code/reason/run_id` 并返回 1；不能只因 service 调用未抛异常就报告成功。

- [ ] **Step 4: 运行 CLI 回归**

Run: `python -m pytest agent/tests/stockpred/test_cli.py agent/tests/test_cli_init.py agent/tests/test_cli_version.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add agent/src/stockpred/cli_handlers.py agent/cli/_legacy.py agent/tests/stockpred/test_cli.py
git commit -m "feat(stockpred): add graph backtest CLI"
```

### Task 5: 增加持久后台 API 与 SSE

**Files:**
- Create: `agent/src/api/stockpred_routes.py`
- Modify: `agent/api_server.py`
- Create: `agent/tests/stockpred/test_api.py`

**Interfaces:**
- Produces: `register_stockpred_routes(app, *, runs_dir, require_auth, require_event_stream_auth)`。
- Produces: `GET /stockpred/status`、`GET /stockpred/graph/defaults`。
- Produces: `GET /stockpred/graph/backtests?limit=20`、`POST /stockpred/graph/backtests`、`GET /stockpred/graph/backtests/{run_id}/events`。

- [ ] **Step 1: 写请求校验、202 和持久 SSE 测试**

```python
def test_status_reports_contract_failure_without_starting_job(monkeypatch) -> None:
    monkeypatch.setattr(stockpred_routes, "probe_stockpred_status", lambda: {
        "ready": False, "contract": "stockpred-data/v1", "tables": [],
        "error_code": "STOCKPRED_ROOT_MISSING", "message": "data root is not configured",
    })
    response = client.get("/stockpred/status")
    assert response.status_code == 200
    assert response.json()["ready"] is False


def test_defaults_lock_parity_fields() -> None:
    body = client.get("/stockpred/graph/defaults").json()
    assert body["top_n"] == 50
    assert set(body["locked_fields"]) >= {"top_n", "eval_step", "forward_days", "benchmark_code"}


def test_create_backtest_returns_run_and_event_url(monkeypatch) -> None:
    monkeypatch.setattr(stockpred_routes, "build_service", lambda *_: FakeService("graph_123"))
    response = client.post("/stockpred/graph/backtests", json={"start": "2025-01-01", "end": "2025-03-31", "mode": "parity"})
    assert response.status_code == 202
    assert response.json() == {
        "run_id": "graph_123",
        "events_url": "/stockpred/graph/backtests/graph_123/events",
    }


def test_event_stream_reads_terminal_state_from_disk(tmp_runs) -> None:
    seed_state(tmp_runs / "graph_123", status="success", progress={"done": 12, "total": 12})
    response = client.get("/stockpred/graph/backtests/graph_123/events")
    assert "event: progress" in response.text
    assert "event: done" in response.text


def test_list_backtests_returns_only_graph_runs(tmp_runs) -> None:
    seed_request(tmp_runs / "graph_123", strategy_type="stockpred_graph")
    seed_request(tmp_runs / "normal_123", strategy_type="generated_strategy")
    response = client.get("/stockpred/graph/backtests?limit=20")
    assert [row["run_id"] for row in response.json()] == ["graph_123"]
```

- [ ] **Step 2: 运行并确认失败**

Run: `python -m pytest agent/tests/stockpred/test_api.py -q`

Expected: FAIL，路由不存在。

- [ ] **Step 3: 实现路由和后台线程**

```python
class GraphBacktestRequest(BaseModel):
    start: date
    end: date
    mode: Literal["parity", "research"] = "parity"
    top_n: int | None = Field(None, ge=1, le=500)
    eval_step: int | None = Field(None, ge=1, le=60)

    def to_config(self) -> GraphBacktestConfig:
        overrides = {key: value for key, value in {
            "top_n": self.top_n, "eval_step": self.eval_step,
        }.items() if value is not None}
        return GraphBacktestConfig(
            start=self.start.isoformat(), end=self.end.isoformat(), mode=self.mode, **overrides,
        )


class GraphRunSummary(BaseModel):
    run_id: str
    status: str
    phase: str | None = None
    created_at: str
    start: str
    end: str
    mode: Literal["parity", "research"]


@router.get("/stockpred/status")
def get_status() -> dict[str, object]:
    return probe_stockpred_status()


@router.get("/stockpred/graph/defaults")
def get_defaults() -> dict[str, object]:
    config = GraphBacktestConfig(start="2000-01-01", end="2000-01-02")
    return {
        "mode": "parity", "benchmark_code": config.benchmark_code,
        "top_n": config.top_n, "eval_step": config.eval_step,
        "forward_days": config.forward_days,
        "locked_fields": ["top_n", "eval_step", "forward_days", "benchmark_code"],
    }


@router.post("/stockpred/graph/backtests", status_code=202)
async def create_backtest(body: GraphBacktestRequest) -> dict[str, str]:
    service = build_service(runs_dir)
    run_id = service.reserve(body.to_config())
    task = asyncio.create_task(asyncio.to_thread(service.execute, run_id))
    _RUNNING_TASKS.add(task)
    task.add_done_callback(_RUNNING_TASKS.discard)
    return {"run_id": run_id, "events_url": f"/stockpred/graph/backtests/{run_id}/events"}


@router.get("/stockpred/graph/backtests")
def list_backtests(limit: int = Query(20, ge=1, le=100)) -> list[GraphRunSummary]:
    return list_graph_run_summaries(runs_dir, limit=limit)
```

`list_graph_run_summaries()` 按 run 目录修改时间倒序读取 `req.json`，只保留 `context.strategy_type == "stockpred_graph"`，并与 `state.json` 合并。SSE 每 500ms 读取 run 目录的 `state.json`，状态变化发送 `progress`；`success` 发送 `done`，`failed` 发送 `error`。run_id 使用现有安全正则校验。API 进程重启后仍可从磁盘恢复终态。

- [ ] **Step 4: 运行 API 与安全回归**

Run: `python -m pytest agent/tests/stockpred/test_api.py agent/tests/test_security_auth_api.py agent/tests/test_alpha_compare_api.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add agent/src/api/stockpred_routes.py agent/api_server.py agent/tests/stockpred/test_api.py
git commit -m "feat(stockpred): expose graph backtest API"
```

### Task 6: 让现有 Run API 按需返回 Graph 信号轨

**Files:**
- Modify: `agent/src/ui_services.py`
- Modify: `agent/api_server.py`
- Create: `agent/tests/stockpred/test_run_analysis.py`

**Interfaces:**
- Produces: `load_graph_signal_series(run_dir, symbols=None) -> dict[str, list[dict]]`。
- Extends: `RunResponse.graph_signal_series`。
- 约定：`chart_payload=summary` 不返回信号行；`chart_symbol=X` 只返回 X。

- [ ] **Step 1: 写按证券过滤和非 Graph 兼容测试**

```python
def test_graph_signal_series_filters_selected_symbol(graph_run_dir) -> None:
    result = load_graph_signal_series(graph_run_dir, symbols=["000001.SZ"])
    assert set(result) == {"000001.SZ"}
    assert result["000001.SZ"][0]["time"] == "2025-01-03"
    assert result["000001.SZ"][0]["rank"] == 7


def test_normal_run_has_no_graph_signal_series(normal_run_dir) -> None:
    assert load_graph_signal_series(normal_run_dir) == {}


def test_run_context_exposes_strategy_type(graph_run_dir) -> None:
    assert load_run_context(graph_run_dir)["strategy_type"] == "stockpred_graph"


def test_trade_marker_preserves_execution_status() -> None:
    marker = build_trade_markers([{
        "timestamp": "2025-01-06", "code": "000001.SZ", "side": "BUY",
        "price": "10.0", "qty": "0", "status": "REJECTED", "reason": "limit_up",
    }])[0]
    assert marker["status"] == "REJECTED"
```

- [ ] **Step 2: 运行并确认失败**

Run: `python -m pytest agent/tests/stockpred/test_run_analysis.py -q`

Expected: FAIL。

- [ ] **Step 3: 读取 `selected_signals.csv` 并扩展响应**

```python
def load_graph_signal_series(run_dir: Path, symbols: Sequence[str] | None = None) -> dict[str, list[dict[str, Any]]]:
    rows = load_csv_records(run_dir / "artifacts" / "selected_signals.csv")
    allowed = set(symbols or [])
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        code = str(row.get("ts_code") or row.get("code") or "")
        if not code or (allowed and code not in allowed):
            continue
        grouped.setdefault(code, []).append({
            **row,
            "time": format_run_date(row.get("eval_date") or row.get("trade_date")) or "",
            "code": code,
            "score": _safe_float(row.get("score")),
            "rank": int(float(row.get("rank") or 0)),
            "risk_adjustment": (
                None if row.get("risk_adjustment") in (None, "")
                else _safe_float(row.get("risk_adjustment"))
            ),
        })
    return grouped
```

`load_run_context()` 在现有返回值中增加 `strategy_type=context.get("strategy_type")`。`RunResponse` 新字段为 `Optional[Dict[str, List[Dict[str, Any]]]]`。现有字段及默认响应保持兼容。`build_trade_markers()` 同时透传 `status` 和 `exit_delay_days`，时间兼容读取 `timestamp` 或 `time`，供蜡烛图区分成交、部分成交、拒绝和顺延。

- [ ] **Step 4: 运行 Run API 回归和后端完整测试**

Run: `python -m pytest agent/tests/stockpred agent/tests/test_api_live_runtime.py agent/tests/test_run_card.py -q`

Expected: PASS。

Run: `python -m ruff check agent/src/stockpred agent/src/api/stockpred_routes.py agent/backtest/stockpred_graph agent/tests/stockpred`

Expected: 无错误。

- [ ] **Step 5: 提交**

```bash
git add agent/src/ui_services.py agent/api_server.py agent/tests/stockpred/test_run_analysis.py
git commit -m "feat(stockpred): expose graph signal diagnostics"
```
