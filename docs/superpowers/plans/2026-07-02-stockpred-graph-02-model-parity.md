# StockPred Graph Core 与差分校验 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 StockPred 当前 Graph 预测核心迁入 Vibe，并在固定快照上证明逐日特征、预测、排序和 Top-50 决策等价。

**Architecture:** 先原样迁移生产路径中的纯计算模块，只替换包导入和数据入口，不同时重构算法。`GraphSignalService` 从 Gateway 获取数据并编排单个评价日；`parity.py` 负责与冻结 golden 工件逐层对账。

**Tech Stack:** Python 3.11、pandas、numpy、scipy、scikit-learn、pytest、Parquet

## Global Constraints

- 必须先完成 `2026-07-02-stockpred-graph-01-data-gateway.md`。
- 生产代码不得导入 `stockpred_ai`；只有 `tools/migration` 下的离线 golden 导出器可以加载冻结 StockPred oracle。
- 首次迁移以行为等价为目标，不顺手优化、拆分或重写 1,000 行级数值模块。
- 模型浮点比较使用 `rtol=1e-8`、`atol=1e-10`；排序、Top-50 和 action 必须完全一致。
- 同分时使用 `ts_code` 升序作为次级排序键。
- 默认 `top_n=50`、`eval_step=5`；`buffer_retain_rank=15` 当前对纯 Top-50 无实际影响，必须保留这一行为。
- 设计依据：`docs/superpowers/specs/2026-07-02-stockpred-graph-vibe-integration-design.md`。

---

## File Structure

- Create `agent/src/stockpred/graph/schema.py`：Graph 节点、边、预测领域类型。
- Create `agent/src/stockpred/graph/config.py`：纯配置 dataclass，不含文件系统路径。
- Create `agent/src/stockpred/graph/builder.py`：每日图构建。
- Create `agent/src/stockpred/graph/features.py`：Graph 特征；迁移期保持单文件以减少算法改动。
- Create `agent/src/stockpred/graph/predictor.py`：批量/向量化预测。
- Create `agent/src/stockpred/graph/advisor.py`：action、证据与风险解释。
- Create `agent/src/stockpred/graph/market_regime.py`：市场阶段。
- Create `agent/src/stockpred/graph/local_risk_features.py`：本地风险特征纯计算入口。
- Create `agent/src/stockpred/graph/pattern_exposure.py`：风险暴露聚合。
- Create `agent/src/stockpred/graph/portfolio.py`：确定性排序和目标组合。
- Create `agent/src/stockpred/graph/service.py`：单评价日信号服务。
- Create `agent/src/stockpred/parity.py`：差分报告。
- Create `tools/migration/export_stockpred_graph_golden.py`：离线 oracle 导出器，不进入发行包。
- Create `agent/tests/stockpred/graph/` 下对应测试与 `fixtures/golden/manifest.json`。

### Task 1: 冻结 StockPred Golden 工件

**Files:**
- Create: `tools/migration/export_stockpred_graph_golden.py`
- Create: `agent/tests/stockpred/fixtures/golden/README.md`
- Create: `agent/tests/stockpred/test_golden_export_contract.py`

**Interfaces:**
- Produces: `manifest.json`、`details.parquet`、`selected.csv`、`trades.csv`、`equity.csv`、`metrics.json`。
- Produces: CLI `python tools/migration/export_stockpred_graph_golden.py --stockpred-root ../StockPred --start 2025-01-02 --end 2025-03-31 --out tmp/golden/normal`。

- [ ] **Step 1: 写导出工件契约测试**

```python
def test_golden_manifest_records_oracle_and_snapshot(tmp_path: Path) -> None:
    write_golden_bundle(
        tmp_path,
        oracle_commit="abc123",
        config={"top_n": 50, "eval_step": 5},
        details=pd.DataFrame({"trade_date": ["20260105"], "ts_code": ["000001.SZ"], "score": [0.8]}),
        trades=pd.DataFrame({"timestamp": ["2026-01-06"], "code": ["000001.SZ"], "side": ["BUY"], "status": ["FILLED"]}),
        equity=pd.DataFrame({"time": ["2026-01-06"], "equity": [10_000_000.0]}),
        metrics={"total_return": 0.0},
    )
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["oracle_commit"] == "abc123"
    assert manifest["config"]["top_n"] == 50
    assert (tmp_path / "details.parquet").is_file()
    assert (tmp_path / "trades.csv").is_file()
    assert (tmp_path / "equity.csv").is_file()
```

- [ ] **Step 2: 运行并确认失败**

Run: `python -m pytest agent/tests/stockpred/test_golden_export_contract.py -q`

Expected: FAIL，导出模块不存在。

- [ ] **Step 3: 实现离线导出器**

```python
def write_golden_bundle(
    out: Path, *, oracle_commit: str, config: dict[str, object], details: pd.DataFrame,
    trades: pd.DataFrame, equity: pd.DataFrame, metrics: dict[str, object],
) -> None:
    out.mkdir(parents=True, exist_ok=False)
    ordered = details.sort_values(["trade_date", "score", "ts_code"], ascending=[True, False, True])
    ordered.to_parquet(out / "details.parquet", index=False)
    eligible = ordered.groupby("trade_date")["ts_code"].transform("size") >= int(config["top_n"])
    selected = ordered.loc[eligible].groupby("trade_date", sort=True).head(int(config["top_n"]))
    selected.to_csv(out / "selected.csv", index=False)
    trades.to_csv(out / "trades.csv", index=False)
    equity.to_csv(out / "equity.csv", index=False)
    (out / "metrics.json").write_text(json.dumps(metrics, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (out / "manifest.json").write_text(
        json.dumps({"oracle_commit": oracle_commit, "config": config}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
```

命令入口使用显式 `--stockpred-root` 将传入目录下的 `src` 临时加入 `sys.path`，调用冻结版本 `stockpred_ai.graph.backtest.run_backtest()`。原生接口没有 `start/end`，因此导出器先从 `dim_trade_cal` 读取不晚于 `end` 的 SSE 开市日，将 `[start, end]` 内交易日数映射为 `lookback_days`，并仅在当前导出进程内把 `backtest.get_recent_trade_dates` 替换为该固定日历窗口；Graph 计算和回测主体不修改。配置固定 `n_workers=1`，运行后断言全部 `result.eval_dates` 都位于请求区间。`details_df` 中的入场/退出字段规范化为 `trades.csv`；收益曲线日期只取候选数不少于 `top_n` 的评价日，与 `cumulative_returns` 对齐后写入 `equity.csv`；dataclass 汇总字段写入 `metrics.json`。脚本启动后在传入目录执行 `git rev-parse HEAD` 并写入 manifest；检测工作树中 Graph 文件有未提交修改时拒绝导出。

- [ ] **Step 4: 生成三个短窗口并验证格式**

Run: `python tools/migration/export_stockpred_graph_golden.py --stockpred-root ../StockPred --start 2025-01-02 --end 2025-03-31 --out tmp/golden/normal`

Run: `python tools/migration/export_stockpred_graph_golden.py --stockpred-root ../StockPred --start 2024-03-01 --end 2024-05-31 --out tmp/golden/pit-boundary`

Run: `python tools/migration/export_stockpred_graph_golden.py --stockpred-root ../StockPred --start 2024-09-02 --end 2024-11-29 --out tmp/golden/execution-edge`

Expected: 每个目录包含六个文件；重复导出文件 SHA-256 一致。大体量 golden 数据保留在 `tmp/`，测试仓库只提交裁剪后的最小夹具和 manifest。

- [ ] **Step 5: 提交**

```bash
git add tools/migration/export_stockpred_graph_golden.py agent/tests/stockpred/test_golden_export_contract.py agent/tests/stockpred/fixtures/golden/README.md
git commit -m "test(stockpred): add frozen graph oracle exporter"
```

### Task 2: 迁移 Graph Schema、配置、图构建和特征

**Files:**
- Create: `agent/src/stockpred/graph/schema.py`
- Create: `agent/src/stockpred/graph/config.py`
- Create: `agent/src/stockpred/graph/builder.py`
- Create: `agent/src/stockpred/graph/features.py`
- Create: `agent/tests/stockpred/graph/test_builder.py`
- Create: `agent/tests/stockpred/graph/test_features.py`

**Interfaces:**
- Produces: 与 StockPred 当前同名的 `StockNode`、`IndustryNode`、`Edge`、`TrendPrediction`。
- Produces: `GraphConfig`、`PredictionConfig`、`HorizonConfig` 及默认实例。
- Produces: `build_daily_graph()`、`compute_all_graph_features()`、`compute_industry_momentum()`。

- [ ] **Step 1: 先迁移最小行为测试**

从 StockPred 当前测试中选择并复制以下行为到 Vibe，导入路径改为 `src.stockpred.graph`：

```python
def test_features_do_not_use_rows_after_eval_date() -> None:
    baseline = compute_all_graph_features(graph_at("20260105"), prices_up_to("20260105"))
    with_future = compute_all_graph_features(graph_at("20260105"), prices_with_future_spike("20260106"))
    pd.testing.assert_frame_equal(baseline, with_future)


def test_vectorized_feature_output_has_stable_stock_order() -> None:
    result = compute_all_graph_features(GRAPH, SHUFFLED_PRICES)
    assert result["ts_code"].tolist() == sorted(result["ts_code"].tolist())
```

同时覆盖行业动量、相对强弱、量价相关、拥挤度和缺失值语义。

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest agent/tests/stockpred/graph/test_builder.py agent/tests/stockpred/graph/test_features.py -q`

Expected: FAIL，模块或函数不存在。

- [ ] **Step 3: 原样迁移生产实现并只做边界改造**

源文件映射：

```text
StockPred/src/stockpred_ai/graph/schema.py   -> agent/src/stockpred/graph/schema.py
StockPred/src/stockpred_ai/graph/config.py   -> agent/src/stockpred/graph/config.py
StockPred/src/stockpred_ai/graph/builder.py  -> agent/src/stockpred/graph/builder.py
StockPred/src/stockpred_ai/graph/features.py -> agent/src/stockpred/graph/features.py
```

所有导入前缀 `stockpred_ai.graph` 改为 `src.stockpred.graph`。从 `config.py` 删除 `PROJECT_ROOT`、`LANCE_MARKET_CORE`、`LANCE_SOURCE_RAW`、`LANCE_GRAPH`；任何函数若依赖这些路径，移动到 Gateway 或显式接收 DataFrame。除导入和 I/O 注入外，不改变公式、默认值或缺失值处理。

- [ ] **Step 4: 运行迁移测试和数值 smoke test**

Run: `python -m pytest agent/tests/stockpred/graph/test_builder.py agent/tests/stockpred/graph/test_features.py -q`

Expected: PASS。

Run: `python -m ruff check agent/src/stockpred/graph/schema.py agent/src/stockpred/graph/config.py agent/src/stockpred/graph/builder.py agent/src/stockpred/graph/features.py`

Expected: 无错误。

- [ ] **Step 5: 提交**

```bash
git add agent/src/stockpred/graph agent/tests/stockpred/graph/test_builder.py agent/tests/stockpred/graph/test_features.py
git commit -m "feat(stockpred): migrate graph construction and features"
```

### Task 3: 迁移 Predictor、Advisor 与风险解释

**Files:**
- Create: `agent/src/stockpred/graph/predictor.py`
- Create: `agent/src/stockpred/graph/advisor.py`
- Create: `agent/src/stockpred/graph/market_regime.py`
- Create: `agent/src/stockpred/graph/local_risk_features.py`
- Create: `agent/src/stockpred/graph/pattern_exposure.py`
- Create: `agent/tests/stockpred/graph/test_predictor.py`
- Create: `agent/tests/stockpred/graph/test_advisor.py`
- Create: `agent/tests/stockpred/graph/test_risk_features.py`

**Interfaces:**
- Produces: `predict_batch()`、`predict_batch_vectorized()`，返回含 `score/direction/stage` 的 DataFrame。
- Produces: `generate_advisory()`，返回 action、evidence、risks。
- Produces: `build_local_risk_features(eval_rows, frames)`，只接收评价行和 Gateway 已加载的 frames。

- [ ] **Step 1: 写标量/向量化等价和阶段测试**

```python
def test_vectorized_predictor_matches_scalar() -> None:
    scalar = predict_batch(FEATURES, cfg=PREDICTION_CONFIG).sort_values("ts_code").reset_index(drop=True)
    vectorized = predict_batch_vectorized(FEATURES, cfg=PREDICTION_CONFIG).sort_values("ts_code").reset_index(drop=True)
    np.testing.assert_allclose(scalar["score"], vectorized["score"], rtol=1e-8, atol=1e-10)
    assert scalar[["direction", "stage"]].equals(vectorized[["direction", "stage"]])


def test_risk_features_return_nan_when_coverage_is_not_proven() -> None:
    eval_rows = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20260105"]})
    result = build_local_risk_features(eval_rows, {"raw_pledge_detail": pd.DataFrame()})
    assert result["pledge_ratio"].isna().all()
```

- [ ] **Step 2: 运行并确认失败**

Run: `python -m pytest agent/tests/stockpred/graph/test_predictor.py agent/tests/stockpred/graph/test_advisor.py agent/tests/stockpred/graph/test_risk_features.py -q`

Expected: FAIL，模块不存在。

- [ ] **Step 3: 迁移同名生产模块**

按一对一文件映射迁移 StockPred 当前实现，统一导入前缀为 `src.stockpred.graph`。`local_risk_features.py` 删除 Lance 路径读取，只保留 `frames` 输入；覆盖信息放在 `frames["__coverage__"]`。不要迁移优化器、可视化、诊断迭代器和持久化 store，它们不是首期生产预测路径。

- [ ] **Step 4: 运行预测核心测试**

Run: `python -m pytest agent/tests/stockpred/graph/test_predictor.py agent/tests/stockpred/graph/test_advisor.py agent/tests/stockpred/graph/test_risk_features.py -q`

Expected: PASS，标量与向量化 score 在规定容差内一致。

- [ ] **Step 5: 提交**

```bash
git add agent/src/stockpred/graph agent/tests/stockpred/graph
git commit -m "feat(stockpred): migrate graph predictor and risk model"
```

### Task 4: 实现确定性 Top-N 组合

**Files:**
- Create: `agent/src/stockpred/graph/portfolio.py`
- Create: `agent/tests/stockpred/graph/test_portfolio.py`

**Interfaces:**
- Produces: `rank_signals(signals: pd.DataFrame) -> pd.DataFrame`。
- Produces: `select_buffered_portfolio(ranked_codes, previous_holdings, target_size, retain_rank) -> list[str]`。
- Produces: `build_equal_weight_targets(signals, *, top_n, previous_holdings, retain_rank) -> pd.DataFrame`。

- [ ] **Step 1: 写同分排序与默认 buffer 无效行为测试**

```python
def test_rank_signals_breaks_ties_by_ts_code() -> None:
    ranked = rank_signals(pd.DataFrame({"ts_code": ["B", "A"], "score": [1.0, 1.0]}))
    assert ranked["ts_code"].tolist() == ["A", "B"]


def test_default_buffer_still_selects_pure_top_50() -> None:
    codes = [f"S{i:03d}" for i in range(80)]
    selected = select_buffered_portfolio(
        codes,
        previous_holdings={"S060"},
        target_size=50,
        retain_rank=15,
    )
    assert selected == codes[:50]
```

- [ ] **Step 2: 运行并确认失败**

Run: `python -m pytest agent/tests/stockpred/graph/test_portfolio.py -q`

Expected: FAIL。

- [ ] **Step 3: 实现当前选择器和等权目标**

```python
def rank_signals(signals: pd.DataFrame) -> pd.DataFrame:
    ranked = signals.sort_values(["score", "ts_code"], ascending=[False, True]).reset_index(drop=True)
    ranked["rank"] = np.arange(1, len(ranked) + 1)
    return ranked


def build_equal_weight_targets(signals: pd.DataFrame, *, top_n: int, previous_holdings: set[str], retain_rank: int) -> pd.DataFrame:
    ranked = rank_signals(signals)
    codes = select_buffered_portfolio(
        ranked["ts_code"].tolist(), previous_holdings=previous_holdings,
        target_size=top_n, retain_rank=retain_rank,
    )
    selected = ranked[ranked["ts_code"].isin(codes)].copy()
    selected["target_weight"] = 1.0 / len(selected) if len(selected) else 0.0
    return selected.sort_values("rank").reset_index(drop=True)
```

- [ ] **Step 4: 运行测试**

Run: `python -m pytest agent/tests/stockpred/graph/test_portfolio.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add agent/src/stockpred/graph/portfolio.py agent/tests/stockpred/graph/test_portfolio.py
git commit -m "feat(stockpred): add deterministic graph portfolio"
```

### Task 5: 编排单评价日信号并输出差分报告

**Files:**
- Create: `agent/src/stockpred/graph/service.py`
- Create: `agent/src/stockpred/parity.py`
- Create: `agent/tests/stockpred/graph/test_signal_service.py`
- Create: `agent/tests/stockpred/test_parity.py`

**Interfaces:**
- Consumes: `StockPredDataGateway`、Graph Core、portfolio。
- Produces: `GraphSignalConfig` 与 `GraphSignalService.evaluate(eval_date, config) -> pd.DataFrame`。
- Produces: `compare_signal_frames(expected, actual, *, keys, numeric_columns) -> ParityReport`。
- Produces: `BacktestComparable` Protocol，包含 `signals/selected/trades/equity/metrics`。
- Produces: `compare_backtest_bundle(golden_dir: Path, result: BacktestComparable) -> ParityReport`。
- Produces: `ParityReport.to_json()`，包含逐列最大差异、集合差异和 `passed`。

```python
class BacktestComparable(Protocol):
    signals: pd.DataFrame
    selected: pd.DataFrame
    trades: pd.DataFrame
    equity: pd.DataFrame
    metrics: Mapping[str, float]
```

- [ ] **Step 1: 写未来数据隔离和差分失败测试**

```python
def test_signal_service_does_not_change_when_future_rows_are_appended(snapshot_gateway) -> None:
    before = GraphSignalService(snapshot_gateway).evaluate("20260105", CONFIG)
    snapshot_gateway.append_latest_version_only(future_date="20260106")
    after = GraphSignalService(snapshot_gateway).evaluate("20260105", CONFIG)
    pd.testing.assert_frame_equal(before, after)


def test_parity_fails_on_selected_symbol_difference() -> None:
    expected = pd.DataFrame({"trade_date": ["20260105"], "ts_code": ["A"], "score": [0.5]})
    actual = pd.DataFrame({"trade_date": ["20260105"], "ts_code": ["B"], "score": [0.5]})
    report = compare_signal_frames(expected, actual, keys=("trade_date", "ts_code"), numeric_columns=("score",))
    assert not report.passed
    assert report.missing_keys == [("20260105", "A")]


def test_backtest_bundle_compares_signals_selection_trades_and_nav(golden_dir) -> None:
    report = compare_backtest_bundle(golden_dir, matching_backtest_result())
    assert report.passed
    assert set(report.layers) == {"signals", "selected", "trades", "equity", "metrics"}
```

- [ ] **Step 2: 运行并确认失败**

Run: `python -m pytest agent/tests/stockpred/graph/test_signal_service.py agent/tests/stockpred/test_parity.py -q`

Expected: FAIL。

- [ ] **Step 3: 实现显式日级编排**

```python
class GraphSignalService:
    def __init__(self, gateway: StockPredDataGateway) -> None:
        self.gateway = gateway

    def evaluate(self, eval_date: str, config: GraphSignalConfig) -> pd.DataFrame:
        inputs = self._load_inputs(eval_date, config)
        universe, stats = build_pit_universe(**inputs.universe_kwargs())
        prices = apply_qfq(inputs.prices, inputs.adjustment_factors)
        require_adjustment_quality(prices, expected_stocks=len(universe), min_coverage=config.min_adj_coverage)
        graph, _, _ = build_daily_graph(universe, prices, eval_date=eval_date, config=config.graph)
        features = compute_all_graph_features(
            graph=graph, universe=universe, prices=prices,
            daily_basic=inputs.daily_basic, moneyflow=inputs.moneyflow,
            trade_date=eval_date, config=config.graph, fina_df=inputs.financials,
        )
        signals = predict_batch_vectorized(features, cfg=config.prediction)
        advised = generate_advisory(signals)
        return rank_signals(advised).assign(eval_date=eval_date, universe_size=len(universe))
```

`_load_inputs()` 的所有查询结束日均不得晚于 `eval_date`，并把 `UniverseStats` 写入结果元数据或单独 audit 结构。

`compare_backtest_bundle()` 依次读取 golden 的 `details.parquet`、`selected.csv`、`trades.csv`、`equity.csv` 和 `metrics.json`。信号数值使用规定容差；排序、selected keys、交易事件键与拒绝原因完全一致；金额先四舍五入到分再比较。缺少任一必需 golden 文件直接返回未通过报告。

- [ ] **Step 4: 与一个裁剪 golden 窗口对账**

Run: `python -m pytest agent/tests/stockpred/graph/test_signal_service.py agent/tests/stockpred/test_parity.py -q`

Expected: PASS，golden 的 keys、排序、Top-50 完全相同；数值满足 `rtol=1e-8`、`atol=1e-10`。

Run: `python -m ruff check agent/src/stockpred agent/tests/stockpred tools/migration/export_stockpred_graph_golden.py`

Expected: 无错误。

- [ ] **Step 5: 提交**

```bash
git add agent/src/stockpred/graph/service.py agent/src/stockpred/parity.py agent/tests/stockpred
git commit -m "feat(stockpred): add graph signal service and parity gate"
```
