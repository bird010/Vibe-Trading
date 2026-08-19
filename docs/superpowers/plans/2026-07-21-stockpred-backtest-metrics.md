# StockPred 回测报告指标实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**目标：** 为 StockPred Graph 回测详情增加组合总体绩效、按实际成交统计的可排序单标的绩效表，以及按需展开的既有图表。

**架构：** 后端从现有成交、OHLCV 和组合净值计算标准指标；新回测原子写入 \`symbol_metrics.csv\`，旧报告只读回退计算。前端在既有 \`RunDetail\` 的总体指标下挂载独立表格，展开行复用既有 \`chart_symbol\` API 与图表组件。

**技术栈：** Python 3.11、pandas、FastAPI/Pydantic、React 19、TypeScript、Vitest、pytest。

## 全局约束

- 仅改变 \`strategy_type == "stockpred_graph"\` 的详情表现；不改变信号、选股、执行或成本模型。
- 单标的指标来自实际成交与逐日盯市；不得改为买入并持有收益。
- 收益值均用小数比例；无有效样本的指标省略，前端显示 \`—\`。
- 图表只在用户展开一个 symbol 时通过 \`chart_symbol\` 加载；不得预取其他 symbol。
- 历史报告回退只读；保持当前 staging + 原子替换发布逻辑。

## 文件结构

- \`agent/backtest/stockpred_graph/performance.py\`：组合/单标的指标和单标的资金曲线。
- \`agent/backtest/stockpred_graph/runner.py\`：在 Graph 运行结果中加入单标的指标。
- \`agent/backtest/stockpred_graph/artifacts.py\`：原子写入 \`symbol_metrics.csv\`。
- \`agent/src/ui_services.py\`：读取新产物，并对旧 Graph 报告只读回退。
- \`agent/api_server.py\`：在 \`RunResponse\` 中返回可选 \`symbol_metrics\`。
- \`frontend/src/lib/api.ts\` 与 \`frontend/src/lib/formatters.ts\`：前端合约、标签与格式化。
- \`frontend/src/components/run/SymbolMetricsTable.tsx\`：排序、展开、延迟图表加载与缓存。
- \`frontend/src/pages/RunDetail.tsx\`：只为 Graph 详情挂载该表。

### Task 1：用 TDD 建立绩效计算模块

**Files:**

- Create: \`agent/backtest/stockpred_graph/performance.py\`
- Create: \`agent/tests/stockpred/test_performance.py\`

**Interfaces:**

- Consumes: 成交 DataFrame 的 \`timestamp, code, side, executed_value, qty, price, cost_bps, status, signal_date, exit_delay_days\`，以及每个 symbol 的 OHLCV DataFrame。
- Produces: \`calculate_performance_metrics(equity, trades) -> dict[str, float]\`；\`build_symbol_metrics(trades, ohlcv_by_symbol) -> list[dict[str, float | str]]\`。

- [ ] **Step 1: Write the failing portfolio-metrics test**

~~~python
def test_calculate_performance_metrics_uses_daily_nav_and_completed_trades() -> None:
    equity = pd.DataFrame({"time": ["2025-01-01", "2025-01-02", "2025-01-03"], "nav": [1.0, 1.1, 1.0]})
    trades = pd.DataFrame([
        {"timestamp": "2025-01-01", "code": "A", "side": "BUY", "executed_value": 100.0, "qty": 10.0, "price": 10.0, "cost_bps": 0.0, "status": "FILLED", "signal_date": "2025-01-01", "exit_delay_days": 0},
        {"timestamp": "2025-01-03", "code": "A", "side": "SELL", "executed_value": 110.0, "qty": 10.0, "price": 11.0, "cost_bps": 0.0, "status": "FILLED", "signal_date": "2025-01-01", "exit_delay_days": 0},
    ])

    metrics = calculate_performance_metrics(equity, trades)

    assert metrics["total_return"] == 0.0
    assert metrics["max_drawdown"] == pytest.approx(-1 / 11)
    assert metrics["trade_count"] == 1.0
    assert metrics["win_rate"] == 1.0
    assert metrics["profit_loss_ratio"] not in metrics
    assert metrics["avg_holding_days"] == 2.0
~~~

- [ ] **Step 2: Run the test to verify it fails**

Run: \`python -m pytest agent/tests/stockpred/test_performance.py::test_calculate_performance_metrics_uses_daily_nav_and_completed_trades -q\`

Expected: FAIL because \`backtest.stockpred_graph.performance\` does not exist.

- [ ] **Step 3: Write the minimal portfolio implementation**

~~~python
TRADING_DAYS = 252

def calculate_performance_metrics(equity: pd.DataFrame, trades: pd.DataFrame) -> dict[str, float]:
    nav = pd.to_numeric(equity.get("nav"), errors="coerce").dropna().reset_index(drop=True)
    metrics: dict[str, float] = {}
    if len(nav) >= 1 and nav.iloc[0] > 0:
        metrics["total_return"] = float(nav.iloc[-1] / nav.iloc[0] - 1.0)
    returns = nav.pct_change().dropna()
    if len(nav) >= 2 and nav.iloc[0] > 0:
        metrics["annual_return"] = float((nav.iloc[-1] / nav.iloc[0]) ** (TRADING_DAYS / (len(nav) - 1)) - 1.0)
        metrics["max_drawdown"] = float(nav.div(nav.cummax()).sub(1.0).min())
        if metrics["max_drawdown"] < 0:
            metrics["calmar"] = metrics["annual_return"] / abs(metrics["max_drawdown"])
    if len(returns) >= 2:
        std = returns.std(ddof=1)
        metrics["annual_volatility"] = float(std * np.sqrt(TRADING_DAYS))
        if std > 0:
            metrics["sharpe"] = float(returns.mean() / std * np.sqrt(TRADING_DAYS))
        downside = returns[returns < 0]
        if len(downside) >= 2 and downside.std(ddof=1) > 0:
            metrics["sortino"] = float(returns.mean() / downside.std(ddof=1) * np.sqrt(TRADING_DAYS))
    metrics.update(_completed_trade_metrics(trades))
    return {key: value for key, value in metrics.items() if np.isfinite(value)}
~~~

Implement \`_completed_trade_metrics\` with FIFO pairing for only \`FILLED\` and \`PARTIAL\` rows of the same \`code\`; subtract both buy/sell \`cost_bps\`; return \`trade_count\`, \`win_rate\`, \`profit_loss_ratio\` only when losses exist, and \`avg_holding_days\`.

- [ ] **Step 4: Run the test to verify it passes**

Run: \`python -m pytest agent/tests/stockpred/test_performance.py::test_calculate_performance_metrics_uses_daily_nav_and_completed_trades -q\`

Expected: PASS.

- [ ] **Step 5: Write the failing per-symbol test**

~~~python
def test_build_symbol_metrics_uses_filled_trades_and_ignores_rejected_orders() -> None:
    trades = pd.DataFrame([
        {"timestamp": "2025-01-01", "code": "A", "side": "BUY", "executed_value": 100.0, "qty": 10.0, "price": 10.0, "cost_bps": 0.0, "status": "FILLED", "signal_date": "2025-01-01", "exit_delay_days": 0},
        {"timestamp": "2025-01-02", "code": "A", "side": "SELL", "executed_value": 120.0, "qty": 10.0, "price": 12.0, "cost_bps": 0.0, "status": "FILLED", "signal_date": "2025-01-01", "exit_delay_days": 0},
        {"timestamp": "2025-01-02", "code": "B", "side": "BUY", "executed_value": 0.0, "qty": 0.0, "price": None, "cost_bps": 0.0, "status": "REJECTED", "signal_date": "2025-01-02", "exit_delay_days": 0},
    ])
    prices = {"A": pd.DataFrame({"ts_code": ["A", "A"], "trade_date": ["20250101", "20250102"], "adj_close": [10.0, 12.0]})}

    assert build_symbol_metrics(trades, prices)[0]["symbol"] == "A"
    assert build_symbol_metrics(trades, prices)[0]["total_return"] == pytest.approx(0.2)
    assert {row["symbol"] for row in build_symbol_metrics(trades, prices)} == {"A"}
~~~

- [ ] **Step 6: Run the test to verify it fails**

Run: \`python -m pytest agent/tests/stockpred/test_performance.py::test_build_symbol_metrics_uses_filled_trades_and_ignores_rejected_orders -q\`

Expected: FAIL because \`build_symbol_metrics\` does not exist.

- [ ] **Step 7: Write the minimal per-symbol implementation**

~~~python
def build_symbol_metrics(trades: pd.DataFrame, ohlcv_by_symbol: dict[str, pd.DataFrame]) -> list[dict[str, float | str]]:
    rows: list[dict[str, float | str]] = []
    for symbol, prices in sorted(ohlcv_by_symbol.items()):
        symbol_trades = trades.loc[trades["code"].astype(str) == symbol].copy()
        equity = _build_symbol_equity(symbol_trades, prices)
        metrics = calculate_performance_metrics(equity, symbol_trades)
        if metrics.get("trade_count", 0.0) > 0:
            rows.append({"symbol": symbol, **metrics})
    return rows
~~~

\`_build_symbol_equity\` must process valid events by \`timestamp\` with sells before buys, calculate the minimum opening cash that covers the maximum cumulative net cash outflow, and mark holdings using \`adj_close\` or \`close\`. It returns \`time\` and \`nav\` rows. Run: \`python -m pytest agent/tests/stockpred/test_performance.py -q\`. Expected: PASS.

- [ ] **Step 8: Commit**

~~~bash
git add agent/backtest/stockpred_graph/performance.py agent/tests/stockpred/test_performance.py
git commit -m "feat(stockpred): calculate backtest performance metrics"
~~~

### Task 2：持久化新回测的单标的指标

**Files:**

- Modify: \`agent/backtest/stockpred_graph/runner.py\`
- Modify: \`agent/backtest/stockpred_graph/artifacts.py\`
- Modify: \`agent/tests/stockpred/test_runner.py\`
- Modify: \`agent/tests/stockpred/test_artifacts.py\`

**Interfaces:**

- Consumes: Task 1 metric functions and the runner's existing \`trades\`、\`equity\`、\`ohlcv\`。
- Produces: \`GraphBacktestResult.symbol_metrics\` and \`artifacts/symbol_metrics.csv\`.

- [ ] **Step 1: Write the failing integration tests**

~~~python
def test_runner_collects_portfolio_and_symbol_performance_metrics() -> None:
    result = GraphBacktestRunner(_Gateway(), _SignalService()).run(GraphBacktestConfig(start="2025-01-01", end="2025-01-20"))

    assert {"annual_volatility", "max_drawdown", "trade_count"} <= set(result.metrics)
    assert {row["symbol"] for row in result.symbol_metrics} == {"A", "B", "C"}

def test_graph_artifacts_publish_symbol_metrics_csv(tmp_path: Path) -> None:
    write_graph_artifacts(tmp_path, _result(), _manifest(), GraphBacktestConfig(start="2025-01-01", end="2025-01-31"))

    assert (tmp_path / "artifacts" / "symbol_metrics.csv").is_file()
~~~

- [ ] **Step 2: Run tests to verify they fail**

Run: \`python -m pytest agent/tests/stockpred/test_runner.py::test_runner_collects_portfolio_and_symbol_performance_metrics agent/tests/stockpred/test_artifacts.py::test_graph_artifacts_publish_symbol_metrics_csv -q\`

Expected: FAIL because result and artifact lack \`symbol_metrics\`.

- [ ] **Step 3: Implement runner and artifact integration**

~~~python
# runner.py
metrics = {
    "scheduled_evaluations": float(total),
    "valid_evaluations": float(len(valid_dates)),
    "valid_eval_ratio": float(valid_ratio),
}
if not equity.empty:
    metrics.update(calculate_performance_metrics(equity, trades))
symbol_metrics = build_symbol_metrics(trades, ohlcv)

# GraphBacktestResult dataclass
symbol_metrics: list[dict[str, float | str]] = field(default_factory=list)

# artifacts.py, before staging.replace(artifacts)
_atomic_csv(staging / "symbol_metrics.csv", pd.DataFrame(result.symbol_metrics, columns=None))
~~~

If no rows exist, write an empty CSV with \`symbol\` as its only column. Add \`symbol_metrics.csv\` to the existing artifact set assertion.

- [ ] **Step 4: Run all performance, runner, and artifact tests**

Run: \`python -m pytest agent/tests/stockpred/test_performance.py agent/tests/stockpred/test_runner.py agent/tests/stockpred/test_artifacts.py -q\`

Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add agent/backtest/stockpred_graph/runner.py agent/backtest/stockpred_graph/artifacts.py agent/tests/stockpred/test_runner.py agent/tests/stockpred/test_artifacts.py
git commit -m "feat(stockpred): persist per-symbol backtest metrics"
~~~

### Task 3：在详情 API 读取新产物并回退旧报告

**Files:**

- Modify: \`agent/src/ui_services.py\`
- Modify: \`agent/api_server.py\`
- Modify: \`agent/tests/stockpred/test_run_analysis.py\`
- Create: \`agent/tests/stockpred/test_run_response.py\`

**Interfaces:**

- Consumes: \`symbol_metrics.csv\`; Task 1 metrics module; legacy \`trades.csv\` and \`ohlcv_*.csv\`.
- Produces: optional \`RunResponse.symbol_metrics: list[dict[str, Any]]\`.

- [ ] **Step 1: Write the failing persisted-and-fallback tests**

~~~python
def test_load_symbol_metrics_prefers_persisted_artifact(graph_run_dir: Path) -> None:
    (graph_run_dir / "artifacts" / "symbol_metrics.csv").write_text(
        "symbol,total_return,trade_count\n000001.SZ,0.12,3\n", encoding="utf-8"
    )

    assert load_symbol_metrics(graph_run_dir) == [
        {"symbol": "000001.SZ", "total_return": 0.12, "trade_count": 3.0}
    ]

def test_load_symbol_metrics_rebuilds_legacy_graph_artifacts(graph_run_dir: Path) -> None:
    _write_graph_trade_and_ohlcv_artifacts(graph_run_dir)

    assert load_symbol_metrics(graph_run_dir)[0]["symbol"] == "000001.SZ"
~~~

- [ ] **Step 2: Run tests to verify they fail**

Run: \`python -m pytest agent/tests/stockpred/test_run_analysis.py -q\`

Expected: FAIL because \`load_symbol_metrics\` does not exist.

- [ ] **Step 3: Implement read-only service loading**

~~~python
def load_symbol_metrics(run_dir: Path) -> list[dict[str, Any]]:
    if load_run_context(run_dir).get("strategy_type") != "stockpred_graph":
        return []
    persisted = load_csv_records(run_dir / "artifacts" / "symbol_metrics.csv")
    if persisted:
        return _numeric_metric_rows(persisted)
    trades = pd.DataFrame(load_csv_records(run_dir / "artifacts" / "trades.csv"))
    prices = _load_graph_ohlcv_frames(run_dir)
    return build_symbol_metrics(trades, prices) if not trades.empty and prices else []
~~~

\`_numeric_metric_rows\` retains only a non-empty \`symbol\` plus finite parsed floats. \`_load_graph_ohlcv_frames\` reads only \`artifacts/ohlcv_*.csv\`; it uses the safe filename suffix as the symbol and never writes to the run directory.

- [ ] **Step 4: Write the failing response test**

~~~python
def test_graph_run_detail_returns_symbol_metrics(client: TestClient, graph_run_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_server, "RUNS_DIR", graph_run_dir.parent)
    (graph_run_dir / "artifacts" / "symbol_metrics.csv").write_text(
        "symbol,total_return,trade_count\n000001.SZ,0.12,3\n", encoding="utf-8"
    )

    response = client.get("/runs/graph_123?chart_payload=summary")

    assert response.status_code == 200
    assert response.json()["symbol_metrics"] == [
        {"symbol": "000001.SZ", "total_return": 0.12, "trade_count": 3.0}
    ]
~~~

- [ ] **Step 5: Implement response model and population**

~~~python
class RunResponse(BaseModel):
    # existing fields
    symbol_metrics: Optional[List[Dict[str, Any]]] = Field(
        None, description="StockPred Graph metrics grouped by symbol"
    )

# _build_response_from_run_dir, after response.run_context is assigned
if response.run_context and response.run_context.get("strategy_type") == "stockpred_graph":
    symbol_metrics = load_symbol_metrics(run_dir)
    response.symbol_metrics = symbol_metrics or None
~~~

Do not add this field to \`GET /runs\`; do not change the \`chart_symbol\` query contract.

- [ ] **Step 6: Run API regression tests**

Run: \`python -m pytest agent/tests/stockpred/test_run_analysis.py agent/tests/stockpred/test_run_response.py -q\`

Expected: PASS, including current \`chart_symbol\` tests.

- [ ] **Step 7: Commit**

~~~bash
git add agent/src/ui_services.py agent/api_server.py agent/tests/stockpred/test_run_analysis.py agent/tests/stockpred/test_run_response.py
git commit -m "feat(stockpred): expose per-symbol report metrics"
~~~

### Task 4：扩展前端合约和指标格式化

**Files:**

- Modify: \`frontend/src/lib/api.ts\`
- Modify: \`frontend/src/lib/formatters.ts\`
- Modify: \`frontend/src/lib/__tests__/formatters.test.ts\`

**Interfaces:**

- Produces: \`SymbolPerformanceMetrics\` and \`formatOptionalMetric\`.

- [ ] **Step 1: Write the failing formatter test**

~~~typescript
it("formats volatility and missing optional metrics", () => {
  expect(formatMetricVal("annual_volatility", 0.142)).toBe("+14.20%");
  expect(formatOptionalMetric("sharpe", undefined)).toBe("—");
  expect(getMetricLabel("annual_volatility")).toBe("Annual Volatility");
});
~~~

- [ ] **Step 2: Run the test to verify it fails**

Run: \`npm run test:run -- src/lib/__tests__/formatters.test.ts\`  
Working directory: \`frontend\`  
Expected: FAIL because \`formatOptionalMetric\` and the volatility key are absent.

- [ ] **Step 3: Implement types and formatting**

~~~typescript
export interface SymbolPerformanceMetrics {
  symbol: string;
  total_return?: number; annual_return?: number; annual_volatility?: number;
  max_drawdown?: number; sharpe?: number; sortino?: number; calmar?: number;
  win_rate?: number; profit_loss_ratio?: number; trade_count?: number;
  avg_holding_days?: number;
}

// RunData
symbol_metrics?: SymbolPerformanceMetrics[];

export function formatOptionalMetric(key: string, value: number | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? formatMetricVal(key, value) : "—";
}
~~~

Add \`annual_volatility\` to the percentage keys, English/Chinese labels and \`DISPLAY_ORDER\` immediately after \`annual_return\`; do not turn missing values into zero.

- [ ] **Step 4: Run formatter tests**

Run: \`npm run test:run -- src/lib/__tests__/formatters.test.ts\`  
Working directory: \`frontend\`  
Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add frontend/src/lib/api.ts frontend/src/lib/formatters.ts frontend/src/lib/__tests__/formatters.test.ts
git commit -m "feat(stockpred): type per-symbol performance metrics"
~~~

### Task 5：实现可排序、可展开的指标表

**Files:**

- Create: \`frontend/src/components/run/SymbolMetricsTable.tsx\`
- Create: \`frontend/src/components/run/__tests__/SymbolMetricsTable.test.tsx\`

**Interfaces:**

- Consumes: \`SymbolPerformanceMetrics[]\` and \`onLoadSymbol(symbol): Promise<ChartPayload>\`.
- Produces: table with \`aria-sort\` headers; a row expansion that loads once and then uses local cache.

- [ ] **Step 1: Write the failing interaction test**

~~~tsx
it("sorts by total return and loads one chart only once", async () => {
  const user = userEvent.setup();
  const onLoadSymbol = vi.fn().mockResolvedValue({
    price_series: { "000001.SZ": [bar] }, indicator_series: {},
    trade_markers: [], graph_signal_series: {},
  });
  render(<SymbolMetricsTable metrics={[rowA, rowB]} onLoadSymbol={onLoadSymbol} />);

  await user.click(screen.getByRole("button", { name: /total return/i }));
  expect(screen.getAllByRole("row")[1]).toHaveTextContent("000002.SZ");
  await user.click(screen.getByRole("button", { name: /000001\.SZ/i }));
  await screen.findByTestId("symbol-chart-000001.SZ");
  await user.click(screen.getByRole("button", { name: /000001\.SZ/i }));
  await user.click(screen.getByRole("button", { name: /000001\.SZ/i }));
  expect(onLoadSymbol).toHaveBeenCalledTimes(1);
});
~~~

- [ ] **Step 2: Run the test to verify it fails**

Run: \`npm run test:run -- src/components/run/__tests__/SymbolMetricsTable.test.tsx\`  
Working directory: \`frontend\`  
Expected: FAIL because the component does not exist.

- [ ] **Step 3: Implement the table**

~~~tsx
export function SymbolMetricsTable({ metrics, onLoadSymbol }: Props) {
  const [sort, setSort] = useState<SortState>({ key: "total_return", direction: "desc" });
  const [expanded, setExpanded] = useState<string | null>(null);
  const [cache, setCache] = useState<Record<string, ChartPayload>>({});
  const [loading, setLoading] = useState<Record<string, boolean>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  // Render semantic table headers as buttons, then call toggleSymbol on a row button.
}
~~~

\`toggleSymbol\` closes without fetching when already open; otherwise fetches only absent cache entries, writes the resolved payload to \`cache\`, and writes a local error without clearing the table when it rejects. The expansion renders existing \`CandlestickChart\` with cached bars, markers and indicators; when points exist it renders existing \`GraphSignalPanel\`. The empty metric list returns \`null\`.

- [ ] **Step 4: Add failure/empty tests and run**

~~~tsx
it("keeps the table usable after one chart request fails", async () => {
  const user = userEvent.setup();
  render(<SymbolMetricsTable metrics={[rowA, rowB]} onLoadSymbol={vi.fn().mockRejectedValue(new Error("chart unavailable"))} />);
  await user.click(screen.getByRole("button", { name: /000001\.SZ/i }));
  expect(await screen.findByText("chart unavailable")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /000002\.SZ/i })).toBeEnabled();
});
~~~

Run: \`npm run test:run -- src/components/run/__tests__/SymbolMetricsTable.test.tsx\`  
Working directory: \`frontend\`  
Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add frontend/src/components/run/SymbolMetricsTable.tsx frontend/src/components/run/__tests__/SymbolMetricsTable.test.tsx
git commit -m "feat(stockpred): add sortable symbol metrics table"
~~~

### Task 6：在运行详情页接入并验证

**Files:**

- Modify: \`frontend/src/pages/RunDetail.tsx\`
- Create: \`frontend/src/pages/__tests__/RunDetail.test.tsx\`

**Interfaces:**

- Consumes: \`RunData.symbol_metrics\`, the existing \`loadChartSymbol\` and \`chartCacheRef\`.
- Produces: only Graph runs render \`SymbolMetricsTable\` between \`MetricsCard\` and tabs.

- [ ] **Step 1: Write failing Graph/non-Graph integration tests**

~~~tsx
it("shows symbol metrics for a graph run and requests only the expanded symbol", async () => {
  apiMock.getRun.mockResolvedValueOnce(graphRunWithSymbolMetrics).mockResolvedValueOnce(graphRunWithOneSymbolChart);
  render(<RunDetail />, { wrapper: routerFor("/runs/graph_123") });

  expect(await screen.findByText("Symbol performance")).toBeInTheDocument();
  await userEvent.setup().click(screen.getByRole("button", { name: /000001\.SZ/i }));
  await waitFor(() => expect(apiMock.getRun).toHaveBeenLastCalledWith("graph_123", { chart_symbol: "000001.SZ" }));
});

it("does not show symbol metrics for a non-graph run", async () => {
  apiMock.getRun.mockResolvedValue(plainRunWithMetrics);
  render(<RunDetail />, { wrapper: routerFor("/runs/plain_123") });

  await screen.findByText("plain_123");
  expect(screen.queryByText("Symbol performance")).not.toBeInTheDocument();
});
~~~

- [ ] **Step 2: Run tests to verify they fail**

Run: \`npm run test:run -- src/pages/__tests__/RunDetail.test.tsx\`  
Working directory: \`frontend\`  
Expected: FAIL because \`RunDetail\` does not mount the table.

- [ ] **Step 3: Integrate with the existing loader**

~~~tsx
{isGraphRun && run.symbol_metrics?.length ? (
  <SymbolMetricsTable
    metrics={run.symbol_metrics}
    onLoadSymbol={async (symbol) => {
      await loadChartSymbol(symbol);
      return chartCacheRef.current[symbol] || {
        price_series: {}, indicator_series: {}, trade_markers: [], graph_signal_series: {},
      };
    }}
  />
) : null}
~~~

Place this after \`MetricsCard\` and before the tab strip. Change \`loadChartSymbol\` to return the resolved \`ChartPayload\` while retaining its existing cache, chart-tab, selected-symbol and bulk-load behavior. Do not create a second \`api.getRun\` code path.

- [ ] **Step 4: Run focused frontend tests**

Run: \`npm run test:run -- src/pages/__tests__/RunDetail.test.tsx src/components/run/__tests__/SymbolMetricsTable.test.tsx src/lib/__tests__/formatters.test.ts\`  
Working directory: \`frontend\`  
Expected: PASS.

- [ ] **Step 5: Run full required verification**

Run: \`npm run build\`  
Working directory: \`frontend\`  
Expected: exit code 0.

Run: \`python -m pytest agent/tests/stockpred -q\`  
Working directory: repository root  
Expected: all StockPred tests PASS.

- [ ] **Step 6: Commit**

~~~bash
git add frontend/src/pages/RunDetail.tsx frontend/src/pages/__tests__/RunDetail.test.tsx
git commit -m "feat(stockpred): show report metrics by symbol"
~~~

## Final acceptance

- [ ] A new Graph report shows total metrics above a symbol table, with default total-return descending order and clickable ascending/descending headers.
- [ ] Expanding a symbol performs one \`chart_symbol\` request, then shows its current K line, execution markers and Graph diagnostics; closing/reopening uses cache.
- [ ] An old Graph report without \`symbol_metrics.csv\`, but with \`trades.csv\` and \`ohlcv_*.csv\`, shows read-only fallback metrics.
- [ ] A non-Graph report has no new block and retains existing charts, tabs and downloads.

