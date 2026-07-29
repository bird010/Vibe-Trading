from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import lance
import pyarrow as pa
import pytest
import src.stockpred.snapshot as snapshot_module

from src.stockpred.contracts import ModelSnapshot, StockPredDataError
from src.stockpred.snapshot import (
    _max_visible_date,
    build_snapshot,
    open_snapshot_dataset,
    resolve_stockpred_root,
)


AS_OF = datetime(2026, 6, 30, 15, tzinfo=ZoneInfo("Asia/Taipei"))
MODEL = ModelSnapshot(
    id="stockpred-graph",
    version="graph-v1",
    config_sha256="cfg",
)


def test_visible_watermark_uses_columnar_max_without_python_row_expansion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Column:
        def to_pylist(self):
            raise AssertionError("watermark must not expand the full column into Python objects")

    class Table:
        def column(self, _name: str) -> Column:
            return Column()

    class Dataset:
        def to_table(self, **_kwargs) -> Table:
            return Table()

    monkeypatch.setattr(
        snapshot_module,
        "pc",
        SimpleNamespace(max=lambda _column: pa.scalar("20260630")),
        raising=False,
    )

    assert _max_visible_date(Dataset(), "trade_date", AS_OF) == "20260630"


def test_build_snapshot_pins_version_and_visible_watermark(stockpred_root: Path) -> None:
    manifest = build_snapshot(stockpred_root, as_of=AS_OF, model=MODEL)

    assert manifest.tables["stock"].version == 1
    assert manifest.tables["stock"].max_date == "20260630"
    assert len(manifest.tables["stock"].schema_sha256) == 64


def test_open_snapshot_dataset_keeps_old_version_after_append(stockpred_root: Path) -> None:
    manifest = build_snapshot(stockpred_root, as_of=AS_OF, model=MODEL)
    stock_path = stockpred_root / "data" / "lance" / "market_core" / "stock.lance"
    lance.write_dataset(
        pa.table(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": ["20260701"],
                "open": [10.2],
                "high": [10.6],
                "low": [10.0],
                "close": [10.4],
                "pct_chg": [1.96],
                "vol": [900.0],
                "amount": [9500.0],
            }
        ),
        stock_path,
        mode="append",
    )

    pinned = open_snapshot_dataset(stockpred_root, manifest.tables["stock"])

    assert pinned.version == 1
    assert pinned.count_rows() == 1


def test_build_snapshot_fails_on_missing_required_column(
    stockpred_root_factory: Callable[..., Path],
) -> None:
    root = stockpred_root_factory(drop_column=("fact_adj_factor", "adj_factor"))

    with pytest.raises(StockPredDataError) as exc_info:
        build_snapshot(root, as_of=AS_OF, model=MODEL)

    assert exc_info.value.code == "STOCKPRED_SCHEMA_MISMATCH"
    assert "adj_factor" in str(exc_info.value)


def test_build_snapshot_fails_on_missing_table(
    stockpred_root_factory: Callable[..., Path],
) -> None:
    root = stockpred_root_factory(omit_table="fact_stock_limit")

    with pytest.raises(StockPredDataError) as exc_info:
        build_snapshot(root, as_of=AS_OF, model=MODEL)

    assert exc_info.value.code == "STOCKPRED_TABLE_MISSING"


def test_resolve_stockpred_root_from_environment(
    monkeypatch: pytest.MonkeyPatch,
    stockpred_root: Path,
) -> None:
    monkeypatch.setenv("STOCKPRED_DATA_ROOT", str(stockpred_root))

    assert resolve_stockpred_root() == stockpred_root.resolve()
