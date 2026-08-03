# StockPred 统一策略回测 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Graph 与全部 Alpha Zoo 因子使用同一 StockPred 回测与报告框架，支持批次多选/全选、独立报告、版本可追溯与严格横向比较。

**Architecture:** 新建策略目录、适配器、冻结快照和批次域；策略仅输出每个评价日的截面分数，公共引擎负责股票池、执行、净值、指标和标准工件。Graph 通过内建适配器进入该框架；保留 Graph 旧接口，并在内部适配成单策略批次。批次冻结一次数据快照和执行配置，以 `comparison_key` 汇总独立子报告并排名。

**Tech Stack:** Python 3、Pydantic、pandas、FastAPI/SSE、pytest、React、TypeScript、Vitest、现有 StockPred Lance 数据网关。

## Global Constraints

- 全部策略必须使用同一份 StockPred 固定数据快照、点时可见股票池、复权价格、组合执行、费用、容量和绩效算法。
- 策略评分只能使用评价日当时可见的数据；字段缺失、空信号或低有效评价日比例必须有稳定错误码，禁止静默填零或使用未来数据。
- 每个子报告必须保存 `strategy_snapshot.json`、源码归档、`strategy_version`、Git/环境信息和执行配置哈希；历史报告以其中的冻结内容为准。
- 严格排行榜只包含同一 `comparison_key` 的成功报告，默认按费用后年化夏普（`sharpe`）降序；失败或跳过项保留原因但不参与数值排名。
- 保持 `/stockpred/graph/*`、`stockpred graph-backtest`、Graph run id 和 Graph 诊断行为向后兼容。
- 只改与本功能直接相关的文件；不得修改用户当前未提交的 `docs/stockpred-graph-operations.md` 与 `docs/superpowers/plans/2026-07-02-stockpred-graph-01-data-gateway.md`。

---

## 文件结构

| 路径 | 职责 |
| --- | --- |
| `agent/src/stockpred/strategies/contracts.py` | 统一策略、评分、版本与批次配置的不可变 Pydantic/数据类契约。 |
| `agent/src/stockpred/strategies/catalog.py` | 将内建 Graph 与 Alpha Zoo `Registry` 映射为统一可选策略目录。 |
| `agent/src/stockpred/strategies/snapshot.py` | 生成源码/环境/Git 的不可变策略快照及 `strategy_version`。 |
| `agent/src/stockpred/strategies/panel.py` | 用固定网关与 PIT 股票池构造 Alpha Zoo 评分面板。 |
| `agent/src/stockpred/strategies/adapters.py` | Graph 与 Alpha Zoo 的统一评分适配器。 |
| `agent/backtest/stockpred_strategy/runner.py` | 不依赖策略来源的历史评分、选股、执行和绩效循环。 |
| `agent/backtest/stockpred_strategy/artifacts.py` | 子报告标准工件和策略快照的原子发布。 |
| `agent/src/stockpred/strategy_run_store.py` | 通用策略报告的运行状态与持久化。 |
| `agent/src/stockpred/batch_store.py` | 批次状态、子报告索引和排名摘要持久化。 |
| `agent/src/stockpred/batch_service.py` | 固定共享快照、受限并发执行子报告、失败隔离。 |
| `agent/src/stockpred/cli_handlers.py` | Graph 旧 CLI 适配到统一单策略批次，保持输出契约。 |
| `agent/src/api/stockpred_routes.py` | 策略目录、批次创建/查询/SSE 和 Graph 兼容路由。 |
| `frontend/src/lib/api.ts` | 批次与策略目录的 HTTP 类型和调用。 |
| `frontend/src/pages/StockPred.tsx` | 多选/全选、批次进度、报告摘要排序与跳转。 |
| `frontend/src/pages/RunDetail.tsx` | 显示策略快照及保持 Graph 专属诊断。 |

## Task 1: 建立统一策略、批次和版本契约

**Files:**
- Create: `agent/src/stockpred/strategies/__init__.py`
- Create: `agent/src/stockpred/strategies/contracts.py`
- Test: `agent/tests/stockpred/strategies/test_contracts.py`

**Interfaces:**
- Produces: `StrategyDescriptor`, `StrategySnapshot`, `StrategyScore`, `StrategyBacktestConfig`, `StrategyBatchRequest`。
- Produces: `metric_sort_value(row, field)`；只接受 `sharpe`、`annual_return`、`max_drawdown`、`win_rate`、`turnover`、`strategy_name` 与 `status`。
- Consumed by: catalog、snapshot、adapters、runner、batch service、API 和前端契约。

- [ ] **Step 1: 写失败的策略/批次契约测试**

```python
from src.stockpred.strategies.contracts import StrategyBatchRequest, StrategyDescriptor


def test_batch_request_requires_selection_and_deduplicates_ids() -> None:
    request = StrategyBatchRequest(
        start="2025-01-01", end="2025-03-31",
        strategy_ids=("alpha101_1", "alpha101_1", "stockpred_graph"),
    )
    assert request.strategy_ids == ("alpha101_1", "stockpred_graph")
    assert request.select_all is False


def test_descriptor_version_is_required_for_a_report() -> None:
    descriptor = StrategyDescriptor(
        id="stockpred_graph", name="StockPred Graph", kind="graph", zoo=None,
        columns_required=(), min_warmup_bars=120,
    )
    assert descriptor.id == "stockpred_graph"
```

- [ ] **Step 2: 运行测试并确认因模块缺失而失败**

Run: `python -m pytest agent/tests/stockpred/strategies/test_contracts.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'src.stockpred.strategies'`.

- [ ] **Step 3: 实现最小不可变契约**

```python
# agent/src/stockpred/strategies/contracts.py
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrategyDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    name: str
    kind: Literal["graph", "alpha_zoo"]
    zoo: str | None
    columns_required: tuple[str, ...] = ()
    min_warmup_bars: int = Field(ge=0)
    metadata: dict[str, object] = Field(default_factory=dict)


class StrategyBatchRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    start: str
    end: str
    strategy_ids: tuple[str, ...] = ()
    select_all: bool = False
    top_n: int = Field(default=50, ge=1, le=500)
    eval_step: int = Field(default=5, ge=1, le=60)

    @model_validator(mode="after")
    def validate_selection(self) -> "StrategyBatchRequest":
        ids = tuple(dict.fromkeys(self.strategy_ids))
        if self.select_all == bool(ids):
            raise ValueError("select exactly one of strategy_ids or select_all")
        object.__setattr__(self, "strategy_ids", ids)
        return self
```

在同一文件中定义 `StrategySnapshot`（描述符、文件哈希、Git、Python、依赖、`strategy_version`）、`StrategyScore`（`scores: pd.DataFrame` 与诊断）和 `StrategyBacktestConfig`（日期、执行参数、`comparison_key`、`strategy_snapshot`）。`metric_sort_value()` 对非有限值返回 `None`，使失败行不会混入数字排序。

- [ ] **Step 4: 运行契约测试并确认通过**

Run: `python -m pytest agent/tests/stockpred/strategies/test_contracts.py -q`

Expected: PASS.

- [ ] **Step 5: 提交契约**

```bash
git add agent/src/stockpred/strategies/__init__.py agent/src/stockpred/strategies/contracts.py agent/tests/stockpred/strategies/test_contracts.py
git commit -m "feat(stockpred): add unified strategy contracts"
```

## Task 2: 构建统一策略目录和不可变源码快照

**Files:**
- Create: `agent/src/stockpred/strategies/catalog.py`
- Create: `agent/src/stockpred/strategies/snapshot.py`
- Test: `agent/tests/stockpred/strategies/test_catalog.py`
- Test: `agent/tests/stockpred/strategies/test_snapshot.py`

**Interfaces:**
- Consumes: `src.factors.registry.Registry`、Task 1 契约。
- Produces: `StrategyCatalog.list() -> list[StrategyDescriptor]`、`StrategyCatalog.require(strategy_id) -> StrategyDescriptor`。
- Produces: `snapshot_strategy(descriptor, *, repository_root: Path) -> StrategySnapshot`、`write_strategy_archive(run_dir, snapshot) -> Path`。
- Consumed by: batch service 和报告工件写入。

- [ ] **Step 1: 写目录和版本快照的失败测试**

```python
from src.stockpred.strategies.catalog import StrategyCatalog
from src.stockpred.strategies.snapshot import snapshot_strategy


def test_catalog_exposes_graph_and_registered_alpha(fake_registry, tmp_path) -> None:
    catalog = StrategyCatalog(registry=fake_registry)
    assert [item.id for item in catalog.list()] == ["alpha101_1", "stockpred_graph"]
    assert catalog.require("alpha101_1").kind == "alpha_zoo"


def test_snapshot_version_changes_when_source_content_changes(tmp_path) -> None:
    source = tmp_path / "alpha.py"
    source.write_text("def compute(panel): return panel['close']\n", encoding="utf-8")
    first = snapshot_strategy(_descriptor(source), repository_root=tmp_path)
    source.write_text("def compute(panel): return -panel['close']\n", encoding="utf-8")
    second = snapshot_strategy(_descriptor(source), repository_root=tmp_path)
    assert first.strategy_version != second.strategy_version
```

- [ ] **Step 2: 运行测试并确认缺少目录/快照实现**

Run: `python -m pytest agent/tests/stockpred/strategies/test_catalog.py agent/tests/stockpred/strategies/test_snapshot.py -q`

Expected: FAIL with import errors for `catalog` and `snapshot`.

- [ ] **Step 3: 实现目录和冻结快照**

```python
# agent/src/stockpred/strategies/catalog.py
class StrategyCatalog:
    def __init__(self, registry: Registry | None = None) -> None:
        self._registry = registry or Registry()

    def list(self) -> list[StrategyDescriptor]:
        graph = StrategyDescriptor(
            id="stockpred_graph", name="StockPred Graph", kind="graph", zoo=None,
            columns_required=("open", "high", "low", "close", "volume", "amount"),
            min_warmup_bars=120,
        )
        alphas = [self._alpha_descriptor(alpha_id) for alpha_id in self._registry.list(universe="equity_cn")]
        return sorted([graph, *alphas], key=lambda item: item.id)
```

`_alpha_descriptor()` 必须从 `Registry.get(alpha_id).meta` 复制 `columns_required`、`min_warmup_bars`、公式和主题，绝不导入或执行因子。`snapshot_strategy()` 收集策略模块、`src/factors/base.py`、`src/factors/registry.py`、Graph 模块或公共执行模块的实际字节，按相对路径排序、逐个 SHA-256；以规范 JSON 的描述符和文件清单计算 `strategy_version`。将源码写入 `strategy_source.zip`，把完整元数据写入 `strategy_snapshot.json`；无法读取、路径逃逸或哈希不符时抛出 `StockPredDataError("STOCKPRED_STRATEGY_SNAPSHOT", ...)`。

- [ ] **Step 4: 运行目录与快照测试并确认通过**

Run: `python -m pytest agent/tests/stockpred/strategies/test_catalog.py agent/tests/stockpred/strategies/test_snapshot.py -q`

Expected: PASS.

- [ ] **Step 5: 提交目录和快照**

```bash
git add agent/src/stockpred/strategies/catalog.py agent/src/stockpred/strategies/snapshot.py agent/tests/stockpred/strategies/test_catalog.py agent/tests/stockpred/strategies/test_snapshot.py
git commit -m "feat(stockpred): catalog and freeze strategies"
```

## Task 3: 从固定 StockPred 快照构造 Alpha Zoo 面板并实现策略适配器

**Files:**
- Create: `agent/src/stockpred/strategies/panel.py`
- Create: `agent/src/stockpred/strategies/adapters.py`
- Test: `agent/tests/stockpred/strategies/test_panel.py`
- Test: `agent/tests/stockpred/strategies/test_adapters.py`

**Interfaces:**
- Consumes: `StockPredDataGateway`、`build_pit_universe()`、`apply_qfq()`、`GraphSignalService`、`Registry`。
- Produces: `StockPredPanelBuilder.build(eval_date, descriptor) -> dict[str, pd.DataFrame]`。
- Produces: `StrategyAdapter.evaluate(eval_date, config) -> StrategyScore`; `GraphStrategyAdapter` 和 `AlphaZooStrategyAdapter`。
- Consumed by: Task 4 的统一 runner。

- [ ] **Step 1: 写 PIT 面板和适配器的失败测试**

```python
def test_panel_only_contains_observations_visible_on_eval_date(gateway) -> None:
    panel = StockPredPanelBuilder(gateway).build("20250110", _alpha_descriptor())
    assert panel["close"].index.max() <= pd.Timestamp("2025-01-10")
    assert set(panel) >= {"open", "high", "low", "close", "volume", "amount", "vwap"}


def test_alpha_adapter_reports_missing_field_as_typed_skip() -> None:
    adapter = AlphaZooStrategyAdapter(_registry_requiring("sector"), _descriptor_requiring("sector"))
    with pytest.raises(StockPredDataError, match="STOCKPRED_STRATEGY_INPUT_MISSING"):
        adapter.evaluate("20250110", _config_without_sector())


def test_graph_adapter_returns_score_frame_with_trade_date(graph_service) -> None:
    score = GraphStrategyAdapter(graph_service).evaluate("20250110", _config())
    assert {"ts_code", "score", "trade_date"}.issubset(score.scores.columns)
```

- [ ] **Step 2: 运行测试并确认缺少面板/适配器**

Run: `python -m pytest agent/tests/stockpred/strategies/test_panel.py agent/tests/stockpred/strategies/test_adapters.py -q`

Expected: FAIL with import errors for `panel` and `adapters`.

- [ ] **Step 3: 实现面板和适配器**

```python
# agent/src/stockpred/strategies/adapters.py
class AlphaZooStrategyAdapter:
    def evaluate(self, eval_date: str, config: StrategyBacktestConfig) -> StrategyScore:
        panel = self._panel_builder.build(eval_date, self.descriptor)
        try:
            values = self._registry.compute(self.descriptor.id, panel)
        except SkipAlpha as exc:
            raise StockPredDataError("STOCKPRED_STRATEGY_INPUT_MISSING", str(exc)) from exc
        scores = values.iloc[-1].rename_axis("ts_code").rename("score").dropna().reset_index()
        scores["trade_date"] = eval_date
        return StrategyScore(scores=scores, diagnostics={"kind": "alpha_zoo"})
```

`StockPredPanelBuilder` 必须先使用 PIT 股票池过滤可交易证券，再从固定 gateway 读取足够的历史日线、复权并 pivot 为日期索引/证券列；`volume` 映射为 StockPred `vol`，`vwap` 由 `amount * 1000 / (vol * 100 + 1)` 计算。只保留截至 `eval_date` 的行，按日期和证券排序。Graph 适配器调用既有 `GraphSignalService.evaluate()` 并将其结果规范为 `ts_code/score/trade_date`，同时在诊断中保留 Graph 原信号列。

- [ ] **Step 4: 运行面板与适配器测试并确认通过**

Run: `python -m pytest agent/tests/stockpred/strategies/test_panel.py agent/tests/stockpred/strategies/test_adapters.py -q`

Expected: PASS.

- [ ] **Step 5: 提交面板和策略适配器**

```bash
git add agent/src/stockpred/strategies/panel.py agent/src/stockpred/strategies/adapters.py agent/tests/stockpred/strategies/test_panel.py agent/tests/stockpred/strategies/test_adapters.py
git commit -m "feat(stockpred): adapt graph and alpha zoo strategies"
```

## Task 4: 提取统一策略回测引擎并保持 Graph 结果兼容

**Files:**
- Create: `agent/backtest/stockpred_strategy/__init__.py`
- Create: `agent/backtest/stockpred_strategy/runner.py`
- Modify: `agent/backtest/stockpred_graph/runner.py`
- Test: `agent/tests/stockpred/test_strategy_runner.py`
- Modify: `agent/tests/stockpred/test_runner.py`

**Interfaces:**
- Consumes: Task 1 `StrategyBacktestConfig`、Task 3 `StrategyAdapter` 和现有 execution/performance helpers。
- Produces: `StrategyBacktestResult` 和 `StockPredStrategyBacktestRunner.run(config, on_progress=None)`。
- Produces: 原有 `GraphBacktestRunner.run(GraphBacktestConfig)` 的输出字段和 parity 行为不变。
- Consumed by: 报告工件和批次服务。

- [ ] **Step 1: 写统一 runner 和 Graph 包装兼容的失败测试**

```python
def test_runner_executes_any_score_adapter_with_shared_execution(gateway) -> None:
    result = StockPredStrategyBacktestRunner(gateway, _constant_score_adapter()).run(_strategy_config())
    assert result.metrics["scheduled_evaluations"] == 4.0
    assert not result.trades.empty
    assert result.strategy_id == "alpha101_1"


def test_graph_runner_delegates_without_changing_existing_contract(gateway, graph_service) -> None:
    result = GraphBacktestRunner(gateway, graph_service).run(GraphBacktestConfig(start="2025-01-01", end="2025-01-31"))
    assert hasattr(result, "parity_signals")
    assert {"ts_code", "score"}.issubset(result.signals.columns)
```

- [ ] **Step 2: 运行测试并确认统一 runner 不存在**

Run: `python -m pytest agent/tests/stockpred/test_strategy_runner.py agent/tests/stockpred/test_runner.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'backtest.stockpred_strategy'`.

- [ ] **Step 3: 实现公共历史循环并把 Graph 包装为策略**

```python
# agent/backtest/stockpred_strategy/runner.py
class StockPredStrategyBacktestRunner:
    def run(self, config: StrategyBacktestConfig, on_progress: ProgressCallback | None = None) -> StrategyBacktestResult:
        open_dates = self.gateway.trade_dates(config.start, config.end)
        for done, eval_date in enumerate(open_dates[::config.eval_step], start=1):
            score = self.strategy.evaluate(eval_date, config)
            selected = build_equal_weight_targets(score.scores, top_n=config.top_n, previous_holdings=previous, retain_rank=config.buffer_retain_rank)
            ...
        return StrategyBacktestResult(strategy_id=config.strategy_snapshot.descriptor.id, signals=signals, selected=selected, trades=trades, positions=positions, equity=equity, metrics=metrics, ohlcv=ohlcv, symbol_metrics=symbol_metrics, diagnostics=diagnostics)
```

将现有 `GraphBacktestRunner` 改为薄包装：构造 `GraphStrategyAdapter` 与共享 runner，映射结果至原 `GraphBacktestResult`；parity 模式仍在包装层通过既有 `build_oracle_parity_view()` 完成，因此 parity CSV/JSON 的字段与测试固定值不变。公共 runner 必须直接复用 `execute_target_portfolio()`、`build_daily_ledger()`、`calculate_performance_metrics()` 和 `build_symbol_metrics()`，而不是复制交易或指标公式。

- [ ] **Step 4: 运行统一 runner、Graph runner 和 parity 测试**

Run: `python -m pytest agent/tests/stockpred/test_strategy_runner.py agent/tests/stockpred/test_runner.py agent/tests/stockpred/test_parity.py agent/tests/stockpred/test_oracle_parity.py -q`

Expected: PASS.

- [ ] **Step 5: 提交统一回测引擎**

```bash
git add agent/backtest/stockpred_strategy agent/backtest/stockpred_graph/runner.py agent/tests/stockpred/test_strategy_runner.py agent/tests/stockpred/test_runner.py
git commit -m "feat(stockpred): unify strategy backtest execution"
```

## Task 5: 通用报告工件、运行存储和策略快照展示数据

**Files:**
- Create: `agent/src/stockpred/strategy_run_store.py`
- Create: `agent/backtest/stockpred_strategy/artifacts.py`
- Modify: `agent/src/ui_services.py`
- Test: `agent/tests/stockpred/test_strategy_artifacts.py`
- Test: `agent/tests/stockpred/test_run_analysis.py`

**Interfaces:**
- Consumes: Task 1 config/snapshot、Task 4 result、现有 `atomic_json` 和 `write_run_card()`。
- Produces: `StrategyRunStore.create/load_config/transition/fail/progress` 和 `write_strategy_artifacts()`。
- Extends: run context 中的 `strategy`、`batch_id`、`comparison_key`；run response 中的 `strategy_snapshot`。
- Consumed by: batch service、RunDetail 和批次摘要。

- [ ] **Step 1: 写子报告工件与运行详情的失败测试**

```python
def test_strategy_artifacts_write_snapshot_and_standard_report(tmp_path, result, manifest, config) -> None:
    write_strategy_artifacts(tmp_path, result, manifest, config)
    assert (tmp_path / "strategy_snapshot.json").is_file()
    assert (tmp_path / "strategy_source.zip").is_file()
    assert (tmp_path / "artifacts" / "metrics.csv").is_file()


def test_run_analysis_exposes_strategy_snapshot(strategy_run_dir) -> None:
    payload = build_run_analysis(strategy_run_dir)
    assert payload["run_context"]["strategy_id"] == "alpha101_1"
    assert payload["strategy_snapshot"]["strategy_version"] == "a" * 64
```

- [ ] **Step 2: 运行测试并确认缺少通用工件**

Run: `python -m pytest agent/tests/stockpred/test_strategy_artifacts.py agent/tests/stockpred/test_run_analysis.py -q`

Expected: FAIL with import/key errors for `write_strategy_artifacts` and `strategy_snapshot`.

- [ ] **Step 3: 实现通用报告写入与读取**

```python
# agent/backtest/stockpred_strategy/artifacts.py
def write_strategy_artifacts(run_dir: Path, result: StrategyBacktestResult, manifest: DataSnapshotManifest, config: StrategyBacktestConfig) -> None:
    atomic_json(run_dir / "strategy_snapshot.json", config.strategy_snapshot.model_dump(mode="json"))
    write_strategy_archive(run_dir, config.strategy_snapshot)
    _publish_standard_artifacts(run_dir, result)
    write_run_card(run_dir, {**config.model_dump(mode="json"), "engine": "stockpred_strategy"}, result.metrics, data_sources=["stockpred"])
```

`StrategyRunStore.create()` 生成 `strategy_YYYY...` run id，`req.json.context` 固定写入 `strategy_type="stockpred_strategy"`、`strategy_id`、`strategy_version`、`batch_id`、`comparison_key`、日期与执行参数。`ui_services.build_run_analysis()` 在 run 目录有 `strategy_snapshot.json` 时读取并返回它；Graph 读取路径保持原 `graph_signal_series` 行为。

- [ ] **Step 4: 运行工件、运行详情和既有 Graph 工件测试**

Run: `python -m pytest agent/tests/stockpred/test_strategy_artifacts.py agent/tests/stockpred/test_run_analysis.py agent/tests/stockpred/test_artifacts.py agent/tests/stockpred/test_run_response.py -q`

Expected: PASS.

- [ ] **Step 5: 提交通用报告工件**

```bash
git add agent/src/stockpred/strategy_run_store.py agent/backtest/stockpred_strategy/artifacts.py agent/src/ui_services.py agent/tests/stockpred/test_strategy_artifacts.py agent/tests/stockpred/test_run_analysis.py
git commit -m "feat(stockpred): persist versioned strategy reports"
```

## Task 6: 实现批次存储、失败隔离和受限并发服务

**Files:**
- Create: `agent/src/stockpred/batch_store.py`
- Create: `agent/src/stockpred/batch_service.py`
- Test: `agent/tests/stockpred/test_batch_store.py`
- Test: `agent/tests/stockpred/test_batch_service.py`

**Interfaces:**
- Consumes: Task 1 request/config、Task 2 catalog/snapshot、Task 4 runner、Task 5 run store/artifacts。
- Produces: `StockPredBatchStore`、`StockPredStrategyBatchService.reserve/execute/run`。
- Produces: `list_batch_report_summaries(batch_id, sort_by="sharpe", descending=True)`。
- Consumed by: API、Graph compatibility builder 和主页面。

- [ ] **Step 1: 写共享快照、失败隔离与排序的失败测试**

```python
def test_batch_uses_one_manifest_and_keeps_other_reports_after_failure(tmp_path) -> None:
    service = _service(tmp_path, adapters={"a": _ok(), "b": _fails("bad input")})
    batch_id = service.run(_request("a", "b"))
    rows = service.store.list_reports(batch_id, sort_by="sharpe")
    assert [row["strategy_id"] for row in rows] == ["a", "b"]
    assert rows[0]["status"] == "success"
    assert rows[1]["status"] == "failed"
    assert rows[0]["data_snapshot_sha256"] == rows[1]["data_snapshot_sha256"]


def test_batch_summary_defaults_to_descending_sharpe(tmp_path) -> None:
    store = StockPredBatchStore(tmp_path)
    batch_id = _seed_batch(store, [{"strategy_id": "low", "status": "success", "sharpe": 0.2}, {"strategy_id": "high", "status": "success", "sharpe": 1.2}])
    assert [row["strategy_id"] for row in store.list_reports(batch_id)] == ["high", "low"]
```

- [ ] **Step 2: 运行测试并确认批次服务不存在**

Run: `python -m pytest agent/tests/stockpred/test_batch_store.py agent/tests/stockpred/test_batch_service.py -q`

Expected: FAIL with import errors for `batch_store` and `batch_service`.

- [ ] **Step 3: 实现批次状态机和服务**

```python
# agent/src/stockpred/batch_service.py
class StockPredStrategyBatchService:
    max_workers = 2

    def execute(self, batch_id: str, on_progress: BatchProgressCallback | None = None) -> str:
        manifest = self.snapshot_factory(self.store.load_request(batch_id))
        self.store.attach_manifest(batch_id, manifest)
        for descriptor in self.store.pending_descriptors(batch_id):
            try:
                self._run_one(batch_id, descriptor, manifest)
            except StockPredDataError as exc:
                self.store.finish_report(batch_id, descriptor.id, status="failed", error_code=exc.code, reason=str(exc))
            finally:
                self.store.advance(batch_id, descriptor.id)
                if on_progress is not None:
                    on_progress(self.store.progress(batch_id))
        return batch_id
```

实现时可用 `ThreadPoolExecutor(max_workers=2)` 提高吞吐，但所有状态更新必须由 `StockPredBatchStore` 原子写入，且每个 worker 只写自己的 run 目录。`comparison_key` 由规范化的共享快照、日期和执行字段计算；`list_reports()` 把成功且有限的数值行按 `sharpe` 排在前面，失败/跳过行按策略 ID 置后。子策略必须先冻结快照、再创建报告 run，因而任一失败仍有可追溯状态。

- [ ] **Step 4: 运行批次测试与相关报告测试**

Run: `python -m pytest agent/tests/stockpred/test_batch_store.py agent/tests/stockpred/test_batch_service.py agent/tests/stockpred/test_strategy_artifacts.py -q`

Expected: PASS.

- [ ] **Step 5: 提交批次服务**

```bash
git add agent/src/stockpred/batch_store.py agent/src/stockpred/batch_service.py agent/tests/stockpred/test_batch_store.py agent/tests/stockpred/test_batch_service.py
git commit -m "feat(stockpred): run comparable strategy batches"
```

## Task 7: 将 Graph 旧服务/CLI 适配到单策略批次

**Files:**
- Modify: `agent/src/stockpred/backtest_service.py`
- Modify: `agent/src/stockpred/cli_handlers.py`
- Modify: `agent/src/stockpred/run_store.py`
- Modify: `agent/tests/stockpred/test_backtest_service.py`
- Modify: `agent/tests/stockpred/test_cli.py`

**Interfaces:**
- Consumes: Task 6 `StockPredStrategyBatchService`。
- Produces: `GraphBacktestService` 仍有 `reserve/execute/run`，仍返回 `graph_*` run id。
- Produces: 既有 `graph-backtest --json` 输出字段和退出码不变。
- Consumed by: 原 Graph API 路由和外部调用者。

- [ ] **Step 1: 扩展 Graph 兼容失败测试**

```python
def test_graph_service_creates_a_single_graph_strategy_batch(tmp_path) -> None:
    service = _compat_service(tmp_path)
    run_id = service.run(GraphBacktestConfig(start="2025-01-01", end="2025-01-31"))
    request = json.loads((tmp_path / run_id / "req.json").read_text())
    assert request["context"]["strategy_id"] == "stockpred_graph"
    assert run_id.startswith("graph_")


def test_graph_cli_json_contract_is_unchanged(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli_handlers, "build_service", lambda: _fake_graph_service("graph_123"))
    assert cli_handlers.dispatch(_parse(["graph-backtest", "--start", "2025-01-01", "--end", "2025-01-31", "--json"])) == 0
    assert json.loads(capsys.readouterr().out)["run_id"] == "graph_123"
```

- [ ] **Step 2: 运行兼容测试并确认目前上下文不含统一策略字段**

Run: `python -m pytest agent/tests/stockpred/test_backtest_service.py agent/tests/stockpred/test_cli.py -q`

Expected: FAIL on the new `strategy_id` assertion.

- [ ] **Step 3: 实现 Graph 外观层**

```python
# agent/src/stockpred/backtest_service.py
class GraphBacktestService:
    def run(self, config: GraphBacktestConfig, on_progress: ProgressCallback | None = None) -> str:
        return self._compat_batch_service.run_graph(config, on_progress=on_progress)
```

`run_graph()` 创建仅选择 `stockpred_graph` 的统一请求，保留 parity 的锁定参数和 golden 对比；将生成的子报告目录命名为原 `graph_*` 格式，确保 `stockpred_routes._RUN_ID`、历史链接和 CLI 不变。不要让 Graph CLI 暴露 Alpha Zoo 的批次字段。

- [ ] **Step 4: 运行 Graph 服务、CLI、API 兼容套件**

Run: `python -m pytest agent/tests/stockpred/test_backtest_service.py agent/tests/stockpred/test_cli.py agent/tests/stockpred/test_api.py agent/tests/stockpred/test_artifacts.py -q`

Expected: PASS.

- [ ] **Step 5: 提交 Graph 兼容适配**

```bash
git add agent/src/stockpred/backtest_service.py agent/src/stockpred/cli_handlers.py agent/src/stockpred/run_store.py agent/tests/stockpred/test_backtest_service.py agent/tests/stockpred/test_cli.py
git commit -m "refactor(stockpred): run graph through strategy batches"
```

## Task 8: 提供策略目录、批次、摘要和 SSE API

**Files:**
- Modify: `agent/src/api/stockpred_routes.py`
- Modify: `agent/tests/stockpred/test_api.py`
- Create: `agent/tests/stockpred/test_batch_api.py`

**Interfaces:**
- Produces: `GET /stockpred/strategies`、`POST /stockpred/strategy-batches`、`GET /stockpred/strategy-batches/{batch_id}`、`GET /stockpred/strategy-batches/{batch_id}/events`。
- Preserves: `/stockpred/graph/defaults`、`/stockpred/graph/backtests` 与 Graph SSE 形状。
- Consumed by: `frontend/src/lib/api.ts`。

- [ ] **Step 1: 写路由和 SSE 的失败测试**

```python
def test_strategy_catalog_lists_graph_and_alpha(api, monkeypatch) -> None:
    monkeypatch.setattr(stockpred_routes, "build_catalog", lambda: _catalog("stockpred_graph", "alpha101_1"))
    response = api.get("/stockpred/strategies")
    assert [item["id"] for item in response.json()["strategies"]] == ["alpha101_1", "stockpred_graph"]


def test_create_batch_returns_summary_and_events_url(api, monkeypatch) -> None:
    monkeypatch.setattr(stockpred_routes, "build_batch_service", lambda *_: _batch_service("batch_123"))
    response = api.post("/stockpred/strategy-batches", json={"start": "2025-01-01", "end": "2025-03-31", "strategy_ids": ["alpha101_1", "stockpred_graph"]})
    assert response.json() == {"batch_id": "batch_123", "events_url": "/stockpred/strategy-batches/batch_123/events"}


def test_batch_summary_defaults_to_sharpe_descending(api, seeded_batch) -> None:
    rows = api.get(f"/stockpred/strategy-batches/{seeded_batch}").json()["reports"]
    assert [row["strategy_id"] for row in rows] == ["high", "low"]
```

- [ ] **Step 2: 运行 API 测试并确认新端点为 404**

Run: `python -m pytest agent/tests/stockpred/test_batch_api.py -q`

Expected: FAIL with 404 responses for the three new endpoints.

- [ ] **Step 3: 增加请求模型、路由和 SSE**

```python
@router.post("/stockpred/strategy-batches", status_code=202)
async def create_strategy_batch(body: StrategyBatchRequest) -> dict[str, str]:
    service = build_batch_service(root)
    batch_id = service.reserve(body)
    task = asyncio.create_task(asyncio.to_thread(service.execute, batch_id))
    _RUNNING_TASKS.add(task)
    task.add_done_callback(_RUNNING_TASKS.discard)
    return {"batch_id": batch_id, "events_url": f"/stockpred/strategy-batches/{batch_id}/events"}
```

目录响应必须返回 `id/name/kind/zoo/theme/columns_required/min_warmup_bars`，不得返回因子源代码。摘要端点允许 `sort_by` 和 `descending`，但只接受 Task 1 白名单；非法值返回 422。SSE 每 500ms 读取批次状态，发送 `progress`、终态 `done` 或 `error`，并在 API 重启后从磁盘恢复终态。

- [ ] **Step 4: 运行新旧 API 测试**

Run: `python -m pytest agent/tests/stockpred/test_batch_api.py agent/tests/stockpred/test_api.py -q`

Expected: PASS.

- [ ] **Step 5: 提交批次 API**

```bash
git add agent/src/api/stockpred_routes.py agent/tests/stockpred/test_api.py agent/tests/stockpred/test_batch_api.py
git commit -m "feat(stockpred): expose strategy batch API"
```

## Task 9: 更新前端 API 契约和 StockPred 主页面

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/__tests__/stockpredApi.test.ts`
- Modify: `frontend/src/pages/StockPred.tsx`
- Create: `frontend/src/pages/__tests__/StockPred.test.tsx`

**Interfaces:**
- Consumes: Task 8 HTTP 契约。
- Produces: `api.listStockPredStrategies()`、`api.createStrategyBatch()`、`api.getStrategyBatch()`、`api.strategyBatchStreamUrl()`。
- Produces: 策略搜索、zoo 筛选、多选/全选、批次进度和可排序报告表。
- Consumed by: 用户和 Task 10 的报告跳转。

- [ ] **Step 1: 写前端 API 与页面的失败测试**

```tsx
it("submits all selected strategies and sorts reports by sharpe by default", async () => {
  vi.mocked(api.listStockPredStrategies).mockResolvedValue([graph, alpha]);
  vi.mocked(api.getStrategyBatch).mockResolvedValue(batchWithHighSharpeAlpha);
  render(<StockPred />);
  await userEvent.click(await screen.findByRole("checkbox", { name: /select all/i }));
  await userEvent.click(screen.getByRole("button", { name: /start batch/i }));
  expect(api.createStrategyBatch).toHaveBeenCalledWith(expect.objectContaining({ select_all: true }));
  expect(screen.getAllByRole("link", { name: /view report/i })[0]).toHaveAttribute("href", "/runs/strategy_high");
});


it("changes report ordering when the annual return header is clicked", async () => {
  render(<StockPred />);
  await userEvent.click(await screen.findByRole("button", { name: /annual return/i }));
  expect(screen.getAllByTestId("strategy-report-row")[0]).toHaveTextContent("highest annual return");
});
```

- [ ] **Step 2: 运行前端测试并确认缺少批次 API/UI**

Run: `npm --prefix frontend test -- StockPred.test.tsx stockpredApi.test.ts --run`

Expected: FAIL because `listStockPredStrategies` and batch controls do not exist.

- [ ] **Step 3: 实现 TypeScript 契约和主页面**

```ts
export interface StrategyBatchReportSummary {
  run_id: string; strategy_id: string; strategy_name: string; strategy_version: string;
  status: "queued" | "running" | "success" | "failed" | "skipped";
  sharpe?: number; annual_return?: number; max_drawdown?: number; win_rate?: number; turnover?: number;
  reason?: string;
}

export const api = {
  ...api,
  listStockPredStrategies: () => request<StrategyDescriptor[]>("/stockpred/strategies"),
  createStrategyBatch: (body: StrategyBatchRequest) => request<StrategyBatchCreated>("/stockpred/strategy-batches", { method: "POST", body: JSON.stringify(body) }),
  getStrategyBatch: (batchId: string, sortBy = "sharpe") => request<StrategyBatchSummary>(`/stockpred/strategy-batches/${encodeURIComponent(batchId)}?sort_by=${sortBy}`),
};
```

在 `StockPred.tsx` 的现有 Graph 卡片旁增加策略批次卡片和摘要表，不删除现有数据状态。目录搜索仅在浏览器内过滤已加载列表；全选切换 `select_all=true` 并清空显式选择；提交前要求有选择。摘要表初始 `sortBy="sharpe"`、降序；每个表头明确升降序；失败/跳过行显示原因，报告行使用 `<Link to={`/runs/${row.run_id}`}>`。SSE 的 progress/done/error 更新批次状态，终态后刷新摘要而不是自动跳走。

- [ ] **Step 4: 运行前端单元测试和类型检查**

Run: `npm --prefix frontend test -- StockPred.test.tsx stockpredApi.test.ts --run && npm --prefix frontend run build`

Expected: tests PASS and Vite build exits 0.

- [ ] **Step 5: 提交主页面批次能力**

```bash
git add frontend/src/lib/api.ts frontend/src/lib/__tests__/stockpredApi.test.ts frontend/src/pages/StockPred.tsx frontend/src/pages/__tests__/StockPred.test.tsx
git commit -m "feat(frontend): compare stockpred strategy reports"
```

## Task 10: 在报告详情页显示策略快照与个股信息入口

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/pages/RunDetail.tsx`
- Modify: `frontend/src/pages/__tests__/RunDetail.test.tsx`
- Modify: `frontend/src/pages/__tests__/RunDetail.graph.test.tsx`

**Interfaces:**
- Consumes: Task 5 的 `RunData.strategy_snapshot` 和现有总体/个股数据。
- Produces: 通用策略快照卡片；Graph run 保持 Graph 信号标签，Alpha Zoo run 不显示空 Graph 标签。
- Consumed by: 用户从主页面打开的每份子报告。

- [ ] **Step 1: 写详情页的失败测试**

```tsx
it("shows immutable strategy identity and version for an alpha report", async () => {
  mockRunData({ run_context: { strategy_id: "alpha101_1" }, strategy_snapshot: { descriptor: { name: "Alpha101 #1", kind: "alpha_zoo", zoo: "alpha101" }, strategy_version: "abcdef012345" } });
  render(<RunDetail />);
  expect(await screen.findByText("Alpha101 #1")).toBeInTheDocument();
  expect(screen.getByText(/abcdef012345/)).toBeInTheDocument();
  expect(screen.queryByRole("tab", { name: /graph/i })).not.toBeInTheDocument();
});


it("keeps graph diagnostics available for graph reports", async () => {
  mockRunData({ run_context: { strategy_id: "stockpred_graph" }, graph_signal_series: { "000001.SZ": [] } });
  render(<RunDetail />);
  expect(await screen.findByRole("tab", { name: /graph/i })).toBeInTheDocument();
});
```

- [ ] **Step 2: 运行详情页测试并确认策略快照未渲染**

Run: `npm --prefix frontend test -- RunDetail.test.tsx RunDetail.graph.test.tsx --run`

Expected: FAIL because `RunData` and `RunDetail` do not expose `strategy_snapshot`.

- [ ] **Step 3: 实现策略快照卡片和条件化 Graph 标签**

```tsx
const strategy = run?.strategy_snapshot;
const isGraph = strategy?.descriptor?.kind === "graph" || run?.run_context?.strategy_id === "stockpred_graph";

{strategy ? <StrategySnapshotCard snapshot={strategy} /> : null}
{isGraph ? <TabButton value="graph" label="Graph" /> : null}
```

扩展 `RunData` 的 TypeScript 类型为 `strategy_snapshot?: { descriptor: { id: string; name: string; kind: "graph" | "alpha_zoo"; zoo?: string | null }; strategy_version: string; git?: { commit?: string; dirty?: boolean }; source_archive?: string }`。卡片显示策略名、ID、类型、zoo、完整版本哈希、Git 版本/dirty、数据快照和执行配置；复用现有 metrics、symbol metrics、K 线和交易标签，不复制个股视图。Graph 标签仅在 `isGraph` 为真时渲染。

- [ ] **Step 4: 运行详情页测试和前端构建**

Run: `npm --prefix frontend test -- RunDetail.test.tsx RunDetail.graph.test.tsx --run && npm --prefix frontend run build`

Expected: tests PASS and build exits 0.

- [ ] **Step 5: 提交报告详情增强**

```bash
git add frontend/src/lib/api.ts frontend/src/pages/RunDetail.tsx frontend/src/pages/__tests__/RunDetail.test.tsx frontend/src/pages/__tests__/RunDetail.graph.test.tsx
git commit -m "feat(frontend): show stockpred strategy provenance"
```

## Task 11: 端到端回归验证与操作文档

**Files:**
- Modify: `docs/stockpred-graph-operations.md`（仅在该文件没有用户未提交改动或已获用户明确同意时）
- Create: `agent/tests/stockpred/test_strategy_batch_integration.py`
- Modify: `agent/tests/stockpred/test_contracts.py`

**Interfaces:**
- Consumes: Tasks 1–10。
- Produces: 受控假数据上的 Graph + 两个 Alpha Zoo 策略批次回归测试和操作说明。
- Verifies: 数据快照一致、策略版本可变更、排名、报告链接和 Graph 兼容。

- [ ] **Step 1: 写端到端失败测试**

```python
def test_graph_and_two_alphas_create_versioned_comparable_reports(stockpred_root, tmp_path) -> None:
    service = build_batch_service(tmp_path, stockpred_root=stockpred_root, registry=_three_strategy_registry())
    batch_id = service.run(StrategyBatchRequest(start="2025-01-01", end="2025-03-31", strategy_ids=("stockpred_graph", "alpha101_1", "alpha101_2")))
    summary = service.store.summary(batch_id)
    assert summary["comparison_key"]
    assert len(summary["reports"]) == 3
    for row in summary["reports"]:
        run_dir = tmp_path / row["run_id"]
        assert (run_dir / "strategy_snapshot.json").is_file()
        assert (run_dir / "strategy_source.zip").is_file()
```

- [ ] **Step 2: 运行端到端测试并确认未实现的集成点失败**

Run: `python -m pytest agent/tests/stockpred/test_strategy_batch_integration.py -q`

Expected: FAIL until the complete batch path is wired.

- [ ] **Step 3: 补齐仅为测试暴露出的连接缺口并写操作说明**

操作说明必须记录：策略目录端点、批次提交请求、如何解释 `comparison_key`、策略版本/源码归档位置、失败/跳过语义、Graph 兼容入口。若 `docs/stockpred-graph-operations.md` 仍有用户未提交修改，则不要修改它；改为在本次变更中创建 `docs/stockpred-strategy-batch-operations.md` 并只写本功能内容。

- [ ] **Step 4: 运行完整后端与前端验证**

Run: `python -m pytest agent/tests/stockpred -q && npm --prefix frontend test -- StockPred.test.tsx RunDetail.test.tsx RunDetail.graph.test.tsx stockpredApi.test.ts --run && npm --prefix frontend run build`

Expected: all pytest tests PASS, all selected Vitest tests PASS, and Vite build exits 0.

- [ ] **Step 5: 提交集成验证与操作文档**

```bash
git add agent/tests/stockpred/test_strategy_batch_integration.py agent/tests/stockpred/test_contracts.py docs/stockpred-strategy-batch-operations.md
git commit -m "test(stockpred): verify unified strategy batches"
```

## 计划自检

- 规格覆盖：Task 1–4 统一策略与 Graph 适配；Task 2 与 5 实现不可变版本快照；Task 5–6 实现独立报告、批次、隔离与比较；Task 7 保证旧 Graph 行为；Task 8–10 实现 API、主页面、排序、报告与个股入口；Task 11 做端到端验证和操作记录。
- 占位符检查：所有代码任务包含明确文件、接口、失败测试、命令、最小实现方向、通过验证和提交命令；没有未决实现标记。
- 类型一致性：批次入口使用 `StrategyBatchRequest`，子策略运行使用 `StrategyBacktestConfig`，评分使用 `StrategyScore`，结果使用 `StrategyBacktestResult`，报告使用 `StrategySnapshot`，前端对应 `StrategyBatchReportSummary`。
