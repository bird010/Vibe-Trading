# One-Phase Strategy Worker 架构重构

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 消除 eval → 主进程聚合 → finalize 三段式架构中的跨进程 DataFrame 序列化，从根源解决 OOM。

**Architecture:** 每个 worker 进程独立完成一个策略全流程。eval 和 finalize 在同一进程内通过 live DataFrame 通信（无需序列化）。主进程仅收几百字节的 metrics dict。

**Tech Stack:** Python 3.12, ProcessPoolExecutor, LanceDB, pandas

## 核心洞察

三段式的问题**不是** eval 和 finalize 分开了——分开本身不坏。问题是两个阶段之间**跨了进程边界**，迫使 GB 级 DataFrame 被序列化/反序列化。

解法：**消除进程边界，而非消除阶段边界。** eval → finalize 在同一个进程内就是纯内存操作，不需要序列化。LanceDB 在同一进程内读两次（eval 读片断，finalize 读全量）没有额外的序列化成本。

## File Structure

| 文件 | 改动类型 | 职责 |
|------|---------|------|
| `agent/src/stockpred/batch_screening.py` | **重写 Coordinator** | `_strategy_worker()` 一段式 worker；`AlphaBatchScreeningCoordinator` 简化为单 pool |
| `agent/src/stockpred/strategy_execution.py` | 小改 | artifact_write 逻辑移入 worker |
| `agent/backtest/stockpred_strategy/runner.py` | **不改** | 现有 live DataFrame 传递无需改动 |

## Global Constraints

- eval + finalize 同一进程，live DataFrame 连接，零序列化
- 主进程只持有 futures 和几百字节的 metrics dicts
- 每个 worker 独立创建 LanceDB handle
- 不引入 parquet 中间文件
- 不改 runner.py

---

### Task 1: 新增 `_strategy_worker()` + 简化 `AlphaBatchScreeningCoordinator`

**Files:**
- Modify: `agent/src/stockpred/batch_screening.py`

**Interfaces:**
- Consumes: `{config_json, gateway_root, gateway_manifest_json, run_dir}`
- Produces: `{strategy_id, metrics: dict | null, error: str | null}` — 几百字节

- [ ] **Step 1: 删除旧函数，新增 `_strategy_worker`**

删除: `_partition_dates`, `_eval_dates_worker`, `_finalize_worker`, `_finalize_thread`, `_PicklableAdapter`

新增:

```python
def _strategy_worker(task: dict[str, object]) -> dict[str, object]:
    """Run one strategy end-to-end in a worker process.

    Opens its own LanceDB handles, creates a Registry, evaluates every
    scheduled date, calls session.finalize() (live DataFrame — no
    serialisation), writes artifacts, and returns only metrics.

    The main process never sees intermediate DataFrames.
    """
    from src.factors.registry import Registry

    try:
        config = StrategyBacktestConfig.model_validate_json(str(task["config_json"]))
        gateway_root = Path(str(task["gateway_root"]))
        gateway_manifest = DataSnapshotManifest.model_validate(
            json.loads(str(task["gateway_manifest_json"]))
        )
        run_dir = Path(str(task["run_dir"]))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {"strategy_id": str(task.get("strategy_id", "?")), "metrics": None, "error": f"invalid task: {exc}"}

    strategy_id = config.strategy_snapshot.descriptor.id
    phase_timer = PhaseTimer()

    try:
        # ── setup ──
        gateway = StockPredDataGateway(gateway_root, gateway_manifest)
        gateway.set_phase_timer(phase_timer)
        registry = Registry()

        descriptor = config.strategy_snapshot.descriptor
        strategy = AlphaZooStrategyAdapter(registry, None, descriptor)
        runner = StockPredStrategyBacktestRunner(gateway, strategy)
        trade_dates = gateway.trade_dates(config.start, config.end)
        scheduled_dates = trade_dates[::config.eval_step]
        session = StrategyScreeningSession(
            runner, config, scheduled_dates=scheduled_dates, phase_timer=phase_timer,
        )

        # ── evaluate: for each date, build panel → compute factor -> build targets ──
        panel_builder = StockPredPanelBuilder(gateway, data_lookback_days=config.data_lookback_days)
        for eval_date in scheduled_dates:
            try:
                with phase_timer.phase("data_load"):
                    panel = panel_builder.build(eval_date, descriptor)
                with phase_timer.phase("factor_compute"):
                    session.evaluate(eval_date, panel)
            except Exception:
                pass  # skip this eval_date for this strategy

        # ── execution: simulate trades (live DataFrame from session, no serialisation) ──
        with phase_timer.phase("execution"):
            result = session.finalize()

        # ── artifact_write ──
        with phase_timer.phase("artifact_write"):
            write_screening_artifacts(run_dir, result, gateway_manifest, config)
            write_phase_metrics(run_dir, phase_timer, read_metrics=gateway.read_metrics())

        return {"strategy_id": strategy_id, "metrics": result.metrics, "error": None}

    except Exception as exc:
        # Best-effort: write phase metrics even on failure
        try:
            write_phase_metrics(run_dir, phase_timer, read_metrics={})
        except Exception:
            pass
        return {"strategy_id": strategy_id, "metrics": None, "error": str(exc)}
```

- [ ] **Step 2: 简化 `AlphaBatchScreeningCoordinator.run()`**

替换现有的三段式 `run()` 方法为单段式：

```python
def run(self) -> dict[str, StrategyBacktestResult | Exception]:
    # Build serialisable tasks — one per strategy
    gateway_manifest_dict = (
        self.gateway.manifest.model_dump(mode="json")
        if hasattr(self.gateway.manifest, "model_dump")
        else dict(self.gateway.manifest)
    )
    gateway_manifest_json_str = json.dumps(gateway_manifest_dict, ensure_ascii=False, default=str)

    tasks: list[dict[str, object]] = []
    for config in self.configs:
        strategy_id = config.strategy_snapshot.descriptor.id
        run_dir = Path(str(self.run_id_by_strategy.get(strategy_id, "")))
        tasks.append({
            "strategy_id": strategy_id,
            "config_json": config.model_dump_json(),
            "gateway_root": str(self.gateway.root),
            "gateway_manifest_json": gateway_manifest_json_str,
            "run_dir": str(run_dir),
        })

    # Submit all to single ProcessPoolExecutor
    n_workers = min(_EVAL_PROCESS_WORKERS, len(tasks)) if tasks else 1
    results: dict[str, StrategyBacktestResult | Exception] = {}
    dates_total = len({d for c in self.configs for d in
        self.context.static_inputs().trade_dates if c.start <= d <= c.end}[::c.eval_step])

    with ProcessPoolExecutor(max_workers=n_workers) as executor:
        futures = {executor.submit(_strategy_worker, task): task for task in tasks}
        for future in as_completed(futures):
            task = futures[future]
            strategy_id = str(task["strategy_id"])
            arena = future.result()
            if arena.get("error"):
                results[strategy_id] = Exception(arena["error"])
                if self.on_strategy_done:
                    self.on_strategy_done(strategy_id, Exception(arena["error"]),
                                          self.run_id_by_strategy.get(strategy_id))
            else:
                # Reconstruct a minimal result for callers that expect StrategyBacktestResult
                metrics = arena.get("metrics") or {}
                results[strategy_id] = _bare_result(strategy_id, metrics)
                if self.on_strategy_done:
                    self.on_strategy_done(strategy_id, metrics,
                                          self.run_id_by_strategy.get(strategy_id))
            if self.on_eval_done:
                done = sum(1 for v in results.values() if not isinstance(v, Exception)
                           or str(v) != "")
                self.on_eval_done(done, len(self.configs), strategy_id)

    return results
```

- [ ] **Step 3: 新增 `_bare_result()` 辅助函数**

为保持返回类型兼容 `StrategyBacktestResult`，用一个轻量 wrapper：

```python
def _bare_result(strategy_id: str, metrics: dict[str, float]) -> StrategyBacktestResult:
    """Return a minimal StrategyBacktestResult carrying only metrics."""
    return StrategyBacktestResult(
        strategy_id=strategy_id,
        eval_dates=[],
        signals=pd.DataFrame(),
        selected=pd.DataFrame(),
        trades=pd.DataFrame(),
        positions=pd.DataFrame(),
        equity=pd.DataFrame(),
        metrics=metrics,
    )
```

- [ ] **Step 4: 更新 imports 和常量**

新增 import：
```python
from src.stockpred.strategies.panel import StockPredPanelBuilder
```

移除不再需要的 imports（`BatchDataContext` 保留 — `__init__` 中 `self.context` 仍用于取 `static_inputs().trade_dates` 和 `snapshot_digest`）。

常量更新 — 移除 `_EVAL_THREAD_WORKERS`：
```python
_EVAL_PROCESS_WORKERS = min(8, (os.cpu_count() or 4))
```

- [ ] **Step 5: 更新类 docstring**

```python
class AlphaBatchScreeningCoordinator:
    """Run Alpha strategies each in a dedicated worker process.

    Each worker runs a strategy end-to-end: LanceDB reads, panel build,
    factor computation, trade simulation, artifact write.  The main
    process collects only per-strategy metrics dicts (hundreds of bytes).
    """
```

---

### Task 2: 更新 `strategy_execution.py` — artifact_write 逻辑已移入 worker

**Files:**
- Modify: `agent/src/stockpred/strategy_execution.py:141-171`

**Interfaces:**
- Consumes: `_strategy_worker` 输出的 `{strategy_id, metrics, error}`

- [ ] **Step 1: 简化 `run_alpha_batch()` 的结果处理**

worker 已经完成 artifact_write，主进程只需处理报告：

```python
# strategy_execution.py:141-171 替换为:
for config, run_dir in prepared:
    strategy_id = config.strategy_snapshot.descriptor.id
    outcome = outcomes[strategy_id]
    if isinstance(outcome, Exception):
        code = outcome.code if isinstance(outcome, StockPredDataError) else "STOCKPRED_INTERNAL_ERROR"
        self.runs.fail(run_dir, error_code=code, reason=str(outcome))
        results[strategy_id] = outcome
        continue
    # Worker already wrote artifacts + phase_metrics; just transition state
    self.runs.transition(run_dir, "SUCCEEDED")
    results[strategy_id] = (run_dir.name, outcome.metrics)
```

- [ ] **Step 2: 清理 `run_alpha_batch` 中不再需要的变量**

`batch_timer` 不再需要传递给 coordinator（每个 worker 自己计时）。可以保留用于其他统计。

---

### Task 3: 验证

- [ ] **Step 1: 语法检查**

```bash
python -c "import ast; ast.parse(open('agent/src/stockpred/batch_screening.py', encoding='utf-8').read()); print('OK')"
python -c "import ast; ast.parse(open('agent/src/stockpred/strategy_execution.py', encoding='utf-8').read()); print('OK')"
```

- [ ] **Step 2: 单元测试**

```bash
pytest agent/tests/stockpred/test_batch_screening.py -v --tb=short
```

- [ ] **Step 3: 16 策略 benchmark**

```bash
python bench_16.py
```

检查：
- 进程中只有 8 个 worker + 主进程（没有 finalize pool）
- 主进程内存 < 200MB
- 16 个策略全部成功（不再 OOM）
- phase_metrics.json 包含所有阶段耗时
