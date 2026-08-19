# StockPred Graph 数据契约与 Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Vibe 中建立只读、可固定版本、fail-closed 的 StockPred Lance 数据边界，并复现 PIT 股票池与前复权语义。

**Architecture:** `StockPredDataGateway` 是唯一接触 Lance 的模块；`snapshot.py` 在运行开始前固定全部表版本，Graph 层只接收规范化 DataFrame。现有通用 `backtest/loaders/stockpred_loader.py` 保持不变，Graph 不通过它读取数据。

**Tech Stack:** Python 3.11、pydantic 2、pandas 2、numpy、pylance/lance 7、pytest

## Global Constraints

- StockPred 是唯一写入方；Vibe 对 `STOCKPRED_DATA_ROOT/data/lance/market_core` 只读。
- 数据契约固定为 `stockpred-data/v1`。
- 必需表或字段缺失、PIT 时间字段不可验证、复权覆盖率低于 `0.98` 时失败，不回退网络或原始价格。
- 财务记录只允许 `ann_date <= eval_date`；名称、ST、行业按 `[effective_from, effective_to)` 生效。
- 所有输出按明确键稳定排序；相同输入必须产生相同结果。
- 不导入 `stockpred_ai`，不使用 StockPred 的全局 `PROJECT_ROOT`。
- 设计依据：`docs/superpowers/specs/2026-07-02-stockpred-graph-vibe-integration-design.md`。

---

## File Structure

- Create `agent/src/stockpred/__init__.py`：公开数据契约入口。
- Create `agent/src/stockpred/contracts.py`：表规格、manifest、错误类型。
- Create `agent/src/stockpred/snapshot.py`：校验表并固定 Lance 版本。
- Create `agent/src/stockpred/gateway.py`：版本固定的领域读取 API。
- Create `agent/src/stockpred/graph/__init__.py`：Graph 包边界。
- Create `agent/src/stockpred/graph/adjustment.py`：前复权和覆盖率质量门。
- Create `agent/src/stockpred/graph/universe.py`：PIT 股票池。
- Create `agent/tests/stockpred/conftest.py`：临时 Lance 数据集夹具。
- Create `agent/tests/stockpred/test_contracts.py`。
- Create `agent/tests/stockpred/test_snapshot.py`。
- Create `agent/tests/stockpred/test_gateway.py`。
- Create `agent/tests/stockpred/test_adjustment.py`。
- Create `agent/tests/stockpred/test_universe.py`。

### Task 1: 定义稳定数据契约

**Files:**
- Create: `agent/src/stockpred/__init__.py`
- Create: `agent/src/stockpred/contracts.py`
- Test: `agent/tests/stockpred/test_contracts.py`

**Interfaces:**
- Produces: `StockPredDataError(code: str, message: str)`。
- Produces: `TableSpec(layer, required_columns, watermark_column, sort_columns)`。
- Produces: `TableSnapshot(name, version, max_date, schema_sha256)`。
- Produces: `DataSnapshotManifest(contract, as_of, tables, model)`。
- Produces: `REQUIRED_TABLES: dict[str, TableSpec]`。

- [ ] **Step 1: 写失败测试，固定表清单和序列化契约**

```python
from src.stockpred.contracts import DataSnapshotManifest, ModelSnapshot, REQUIRED_TABLES, TableSnapshot


def test_required_contract_contains_graph_inputs() -> None:
    assert set(REQUIRED_TABLES) == {
        "dim_stock", "dim_stock_name_history", "bridge_stock_industry",
        "dim_trade_cal", "stock", "fact_adj_factor", "fact_stock_limit",
        "fact_stock_daily_basic", "fact_moneyflow", "fact_index_weight",
        "fact_index_daily", "fact_fina_indicator",
    }
    assert REQUIRED_TABLES["fact_fina_indicator"].watermark_column == "ann_date"


def test_manifest_round_trip_is_stable() -> None:
    manifest = DataSnapshotManifest(
        as_of="2026-06-30T15:00:00+08:00",
        tables={"stock": TableSnapshot(name="stock", version=53, max_date="20260630", schema_sha256="abc")},
        model=ModelSnapshot(id="stockpred-graph", version="graph-v1", config_sha256="cfg"),
    )
    assert DataSnapshotManifest.model_validate_json(manifest.model_dump_json()).contract == "stockpred-data/v1"
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest agent/tests/stockpred/test_contracts.py -q`

Expected: FAIL，提示 `src.stockpred.contracts` 不存在。

- [ ] **Step 3: 实现最小契约**

```python
class StockPredDataError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TableSpec(BaseModel):
    layer: str = "market_core"
    required_columns: tuple[str, ...]
    watermark_column: str | None
    sort_columns: tuple[str, ...]


class TableSnapshot(BaseModel):
    name: str
    version: int
    max_date: str | None
    schema_sha256: str


class ModelSnapshot(BaseModel):
    id: str
    version: str
    config_sha256: str


class DataSnapshotManifest(BaseModel):
    contract: Literal["stockpred-data/v1"] = "stockpred-data/v1"
    as_of: str
    tables: dict[str, TableSnapshot]
    model: ModelSnapshot
```

`REQUIRED_TABLES` 使用以下精确规格：

```python
REQUIRED_TABLES = {
    "dim_stock": TableSpec(required_columns=("ts_code", "name", "industry", "list_date", "delist_date", "list_status", "exchange", "market"), watermark_column=None, sort_columns=("ts_code",)),
    "dim_stock_name_history": TableSpec(required_columns=("ts_code", "security_name", "effective_from", "effective_to", "ann_date", "change_reason"), watermark_column="effective_from", sort_columns=("ts_code", "effective_from", "ann_date")),
    "bridge_stock_industry": TableSpec(required_columns=("ts_code", "industry_code", "industry_name", "level", "effective_from", "effective_to", "source"), watermark_column="effective_from", sort_columns=("ts_code", "effective_from")),
    "dim_trade_cal": TableSpec(required_columns=("exchange", "cal_date", "is_open", "pretrade_date"), watermark_column="cal_date", sort_columns=("exchange", "cal_date")),
    "stock": TableSpec(required_columns=("ts_code", "trade_date", "open", "high", "low", "close", "pct_chg", "vol", "amount"), watermark_column="trade_date", sort_columns=("ts_code", "trade_date")),
    "fact_adj_factor": TableSpec(required_columns=("ts_code", "trade_date", "adj_factor"), watermark_column="trade_date", sort_columns=("ts_code", "trade_date")),
    "fact_stock_limit": TableSpec(required_columns=("ts_code", "trade_date", "up_limit", "down_limit"), watermark_column="trade_date", sort_columns=("ts_code", "trade_date")),
    "fact_stock_daily_basic": TableSpec(required_columns=("ts_code", "trade_date", "turnover_rate", "pe_ttm", "pb", "total_mv"), watermark_column="trade_date", sort_columns=("ts_code", "trade_date")),
    "fact_moneyflow": TableSpec(required_columns=("ts_code", "trade_date", "buy_elg_amount", "sell_elg_amount", "net_mf_amount"), watermark_column="trade_date", sort_columns=("ts_code", "trade_date")),
    "fact_index_weight": TableSpec(required_columns=("index_code", "con_code", "trade_date", "weight"), watermark_column="trade_date", sort_columns=("index_code", "trade_date", "con_code")),
    "fact_index_daily": TableSpec(required_columns=("ts_code", "trade_date", "open", "high", "low", "close", "pct_chg"), watermark_column="trade_date", sort_columns=("ts_code", "trade_date")),
    "fact_fina_indicator": TableSpec(required_columns=("ts_code", "ann_date", "end_date", "eps", "dt_eps", "roe", "roe_dt", "roa", "grossprofit_margin", "netprofit_margin"), watermark_column="ann_date", sort_columns=("ts_code", "ann_date", "end_date")),
}
```

- [ ] **Step 4: 运行测试和静态检查**

Run: `python -m pytest agent/tests/stockpred/test_contracts.py -q`

Expected: PASS。

Run: `python -m ruff check agent/src/stockpred/contracts.py agent/tests/stockpred/test_contracts.py`

Expected: 无错误。

- [ ] **Step 5: 提交**

```bash
git add agent/src/stockpred/__init__.py agent/src/stockpred/contracts.py agent/tests/stockpred/test_contracts.py
git commit -m "feat(stockpred): define graph data contract"
```

### Task 2: 固定 Lance 数据快照

**Files:**
- Create: `agent/src/stockpred/snapshot.py`
- Create: `agent/tests/stockpred/conftest.py`
- Create: `agent/tests/stockpred/test_snapshot.py`

**Interfaces:**
- Consumes: `REQUIRED_TABLES`、`DataSnapshotManifest`、`ModelSnapshot`。
- Produces: `resolve_stockpred_root(explicit: Path | None = None) -> Path`。
- Produces: `build_snapshot(root: Path, *, as_of: datetime, model: ModelSnapshot) -> DataSnapshotManifest`。
- Produces: `open_snapshot_dataset(root: Path, snapshot: TableSnapshot)`，按指定 `version` 打开。

- [ ] **Step 1: 用临时 Lance 表写失败测试**

```python
def test_build_snapshot_pins_version(stockpred_lance_root: Path) -> None:
    manifest = build_snapshot(
        stockpred_lance_root,
        as_of=datetime(2026, 6, 30, 15, tzinfo=ZoneInfo("Asia/Taipei")),
        model=ModelSnapshot(id="stockpred-graph", version="graph-v1", config_sha256="cfg"),
    )
    assert manifest.tables["stock"].version == 1
    assert manifest.tables["stock"].max_date == "20260630"


def test_build_snapshot_fails_on_missing_required_column(stockpred_lance_root: Path) -> None:
    rewrite_without_column(stockpred_lance_root, "fact_adj_factor", "adj_factor")
    with pytest.raises(StockPredDataError) as exc:
        build_snapshot(stockpred_lance_root, as_of=AS_OF, model=MODEL)
    assert exc.value.code == "STOCKPRED_SCHEMA_MISMATCH"
```

`stockpred_lance_root` 在 `conftest.py` 使用 `lance.write_dataset()` 写出 12 张最小表，字段名与当前 StockPred schema 一致。

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest agent/tests/stockpred/test_snapshot.py -q`

Expected: FAIL，提示 `build_snapshot` 不存在。

- [ ] **Step 3: 实现快照构建**

```python
def build_snapshot(root: Path, *, as_of: datetime, model: ModelSnapshot) -> DataSnapshotManifest:
    tables: dict[str, TableSnapshot] = {}
    for name, spec in REQUIRED_TABLES.items():
        path = root / "data" / "lance" / spec.layer / f"{name}.lance"
        if not path.is_dir():
            raise StockPredDataError("STOCKPRED_TABLE_MISSING", f"required table missing: {name}")
        dataset = lance.dataset(path)
        missing = set(spec.required_columns) - set(dataset.schema.names)
        if missing:
            raise StockPredDataError("STOCKPRED_SCHEMA_MISMATCH", f"{name} missing columns: {sorted(missing)}")
        max_date = _max_visible_date(dataset, spec.watermark_column, as_of)
        tables[name] = TableSnapshot(
            name=name,
            version=int(dataset.version),
            max_date=max_date,
            schema_sha256=_schema_sha256(dataset.schema),
        )
    return DataSnapshotManifest(as_of=as_of.isoformat(), tables=tables, model=model)
```

`_max_visible_date` 对日期水位列只读取单列并限制到 `as_of` 日期；维度表没有水位列时返回 `None`。`_schema_sha256` 对 `str(schema)` 的 UTF-8 字节计算 SHA-256。

- [ ] **Step 4: 运行快照测试**

Run: `python -m pytest agent/tests/stockpred/test_snapshot.py -q`

Expected: PASS，包括缺表、缺列、环境变量缺失和固定旧版本读取。

- [ ] **Step 5: 提交**

```bash
git add agent/src/stockpred/snapshot.py agent/tests/stockpred/conftest.py agent/tests/stockpred/test_snapshot.py
git commit -m "feat(stockpred): pin Lance data snapshots"
```

### Task 3: 实现只读领域 Gateway

**Files:**
- Create: `agent/src/stockpred/gateway.py`
- Create: `agent/tests/stockpred/test_gateway.py`

**Interfaces:**
- Consumes: `DataSnapshotManifest`、`open_snapshot_dataset()`。
- Produces: `StockPredDataGateway(root: Path, manifest: DataSnapshotManifest)`。
- Produces: `trade_dates(start, end)`, `stock_dimension()`, `name_history()`, `industry_history()`。
- Produces: `prices(start: str, end: str, codes: Sequence[str] | None = None) -> pd.DataFrame`。
- Produces: `adjustment_factors(start: str, end: str, codes: Sequence[str] | None = None) -> pd.DataFrame`。
- Produces: `stock_limits(start: str, end: str, codes: Sequence[str] | None = None) -> pd.DataFrame`。
- Produces: `daily_basic(start: str, end: str) -> pd.DataFrame`、`moneyflow(start: str, end: str) -> pd.DataFrame`。
- Produces: `index_weights(index_code: str, start: str, end: str) -> pd.DataFrame`、`index_daily(index_code: str, start: str, end: str) -> pd.DataFrame`。
- Produces: `financials_pit(start: str, end: str, *, eval_date: str) -> pd.DataFrame`。

- [ ] **Step 1: 写版本固定、PIT 和排序失败测试**

```python
def test_gateway_reads_manifest_version_after_new_commit(gateway, append_new_stock_version) -> None:
    append_new_stock_version(trade_date="20260701")
    rows = gateway.prices("20260630", "20260701", ["000001.SZ"])
    assert rows["trade_date"].tolist() == ["20260630"]


def test_financials_pit_never_returns_future_announcement(gateway) -> None:
    rows = gateway.financials_pit("20260101", "20260630", eval_date="20260331")
    assert (rows["ann_date"] <= "20260331").all()


def test_gateway_outputs_deterministic_order(gateway) -> None:
    rows = gateway.prices("20260101", "20260630", ["600000.SH", "000001.SZ"])
    assert rows[["ts_code", "trade_date"]].values.tolist() == sorted(
        rows[["ts_code", "trade_date"]].values.tolist()
    )
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest agent/tests/stockpred/test_gateway.py -q`

Expected: FAIL，提示 `StockPredDataGateway` 不存在。

- [ ] **Step 3: 实现统一读取函数和领域方法**

```python
class StockPredDataGateway:
    def __init__(self, root: Path, manifest: DataSnapshotManifest) -> None:
        self.root = root
        self.manifest = manifest

    def _read(self, table: str, *, columns: Sequence[str], filter_expr: str | None = None) -> pd.DataFrame:
        spec = REQUIRED_TABLES[table]
        snap = self.manifest.tables[table]
        dataset = lance.dataset(
            self.root / "data" / "lance" / spec.layer / f"{table}.lance",
            version=snap.version,
        )
        frame = dataset.to_table(columns=list(columns), filter=filter_expr).to_pandas()
        return _normalize_frame(frame, spec.sort_columns)

    def financials_pit(self, start: str, end: str, *, eval_date: str) -> pd.DataFrame:
        visible_end = min(end, eval_date)
        frame = self._read(
            "fact_fina_indicator",
            columns=REQUIRED_TABLES["fact_fina_indicator"].required_columns,
            filter_expr=f"ann_date >= '{_date(start)}' AND ann_date <= '{_date(visible_end)}'",
        )
        return frame.sort_values(["ts_code", "end_date", "ann_date"]).drop_duplicates("ts_code", keep="last")
```

所有字符串过滤值先经过仅允许 `[A-Za-z0-9_.-]` 的校验函数。任何 Lance 读取异常转换为 `StockPredDataError("STOCKPRED_READ_FAILED", f"failed to read {table}: {exc}")`，不得返回空表伪装成功。

- [ ] **Step 4: 运行 Gateway 契约测试**

Run: `python -m pytest agent/tests/stockpred/test_gateway.py agent/tests/test_stockpred_loader.py -q`

Expected: PASS；现有通用 loader 无回归。

- [ ] **Step 5: 提交**

```bash
git add agent/src/stockpred/gateway.py agent/tests/stockpred/test_gateway.py
git commit -m "feat(stockpred): add versioned read-only gateway"
```

### Task 4: 复现前复权与质量门

**Files:**
- Create: `agent/src/stockpred/graph/__init__.py`
- Create: `agent/src/stockpred/graph/adjustment.py`
- Create: `agent/tests/stockpred/test_adjustment.py`

**Interfaces:**
- Produces: `AdjustmentQuality(coverage, missing_rows, missing_stocks, passed)`。
- Produces: `apply_qfq(prices, factors) -> pd.DataFrame`，增加 `adj_open/adj_close/adj_factor_missing`。
- Produces: `require_adjustment_quality(prices, expected_stocks, min_coverage=0.98)`。

- [ ] **Step 1: 写缺因子不得回退原价的失败测试**

```python
def test_qfq_keeps_missing_factor_as_nan() -> None:
    prices = pd.DataFrame({"ts_code": ["A"], "trade_date": ["20260102"], "open": [10.0], "close": [11.0]})
    result = apply_qfq(prices, pd.DataFrame(columns=["ts_code", "trade_date", "adj_factor"]))
    assert pd.isna(result.loc[0, "adj_open"])
    assert pd.isna(result.loc[0, "adj_close"])
    assert bool(result.loc[0, "adj_factor_missing"])


def test_quality_gate_rejects_below_98_percent() -> None:
    with pytest.raises(StockPredDataError) as exc:
        require_adjustment_quality(frame_with_97_of_100_complete_stocks(), expected_stocks=100)
    assert exc.value.code == "STOCKPRED_ADJUSTMENT_COVERAGE"
```

- [ ] **Step 2: 运行并确认失败**

Run: `python -m pytest agent/tests/stockpred/test_adjustment.py -q`

Expected: FAIL，模块不存在。

- [ ] **Step 3: 按 StockPred 公式实现**

```python
latest = merged.sort_values("trade_date").groupby("ts_code")["adj_factor"].transform("last")
missing = merged["adj_factor"].isna() | latest.isna() | (merged["adj_factor"] <= 0) | (latest <= 0)
for raw, adjusted in (("open", "adj_open"), ("close", "adj_close")):
    merged[adjusted] = merged[raw] * merged["adj_factor"] / latest
    merged.loc[missing, adjusted] = np.nan
merged["adj_factor_missing"] = missing
```

质量覆盖率按“至少有一行缺失复权因子的证券数”计算，与 StockPred `summarize_adjustment_quality()` 保持一致。

- [ ] **Step 4: 运行测试**

Run: `python -m pytest agent/tests/stockpred/test_adjustment.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add agent/src/stockpred/graph/__init__.py agent/src/stockpred/graph/adjustment.py agent/tests/stockpred/test_adjustment.py
git commit -m "feat(stockpred): reproduce qfq adjustment rules"
```

### Task 5: 复现 PIT 股票池

**Files:**
- Create: `agent/src/stockpred/graph/universe.py`
- Create: `agent/tests/stockpred/test_universe.py`

**Interfaces:**
- Produces: `UniverseStats`，字段与 StockPred 当前实现一致。
- Produces: `build_pit_universe(stocks, *, eval_date, trade_dates, min_listed_trade_days, name_history, industry_history, exclude_st=True)`。

- [ ] **Step 1: 写上市、退市、ST 和行业时点测试**

```python
def test_universe_uses_half_open_history_intervals() -> None:
    selected, stats = build_pit_universe(
        STOCKS,
        eval_date="20260331",
        trade_dates=TRADE_DATES,
        min_listed_trade_days=60,
        name_history=NAME_HISTORY,
        industry_history=INDUSTRY_HISTORY,
        exclude_st=True,
    )
    assert selected["ts_code"].tolist() == ["000001.SZ"]
    assert stats.st_excluded == 1
    assert selected.loc[0, "industry_code"] == "801780"
```

- [ ] **Step 2: 运行并确认失败**

Run: `python -m pytest agent/tests/stockpred/test_universe.py -q`

Expected: FAIL，模块不存在。

- [ ] **Step 3: 实现当前 StockPred 语义**

```python
active = history.loc[
    (history["effective_from"].fillna("").astype(str) <= eval_date)
    & ((history["effective_to"].fillna("").astype(str) == "")
       | (eval_date < history["effective_to"].fillna("").astype(str)))
]
```

上市天数使用 SSE 开市日序列和 `bisect_left/bisect_right`；退市日满足 `delist_date <= eval_date` 时排除；ST 正则直接从 StockPred 当前 `universe.py` 迁移并补充中文退市名称夹具。

- [ ] **Step 4: 运行数据层完整测试**

Run: `python -m pytest agent/tests/stockpred/test_contracts.py agent/tests/stockpred/test_snapshot.py agent/tests/stockpred/test_gateway.py agent/tests/stockpred/test_adjustment.py agent/tests/stockpred/test_universe.py -q`

Expected: PASS。

Run: `python -m ruff check agent/src/stockpred agent/tests/stockpred`

Expected: 无错误。

- [ ] **Step 5: 提交**

```bash
git add agent/src/stockpred/graph/universe.py agent/tests/stockpred/test_universe.py
git commit -m "feat(stockpred): reproduce PIT universe selection"
```
