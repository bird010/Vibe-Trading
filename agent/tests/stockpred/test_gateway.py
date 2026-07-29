from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import lance
import pyarrow as pa
import pytest

from src.stockpred.contracts import ModelSnapshot, StockPredDataError
from src.stockpred.gateway import StockPredDataGateway, _values_filter
from src.stockpred.snapshot import build_snapshot


AS_OF = datetime(2026, 6, 30, 15, tzinfo=ZoneInfo("Asia/Taipei"))
MODEL = ModelSnapshot(
    id="stockpred-graph",
    version="graph-v1",
    config_sha256="cfg",
)


def _gateway(root: Path) -> StockPredDataGateway:
    return StockPredDataGateway(root, build_snapshot(root, as_of=AS_OF, model=MODEL))


class _RecordingTimer:
    def __init__(self) -> None:
        self.phases: list[str] = []

    @contextmanager
    def phase(self, name: str):
        self.phases.append(name)
        yield


def _append_stock(root: Path, *, code: str, trade_date: str) -> None:
    lance.write_dataset(
        pa.table(
            {
                "ts_code": [code],
                "trade_date": [trade_date],
                "open": [20.0],
                "high": [20.5],
                "low": [19.8],
                "close": [20.2],
                "pct_chg": [1.0],
                "vol": [500.0],
                "amount": [6000.0],
            }
        ),
        root / "data" / "lance" / "market_core" / "stock.lance",
        mode="append",
    )


def test_large_code_filter_uses_non_recursive_in_expression() -> None:
    codes = [f"{index:06d}.SZ" for index in range(5_000)]

    expression = _values_filter("ts_code", codes)

    assert expression is not None
    assert expression.startswith("ts_code IN (")
    assert " OR " not in expression


def test_gateway_reads_manifest_version_after_new_commit(stockpred_root: Path) -> None:
    gateway = _gateway(stockpred_root)
    _append_stock(stockpred_root, code="000001.SZ", trade_date="20260701")

    rows = gateway.prices("20260630", "20260701", ["000001.SZ"])

    assert rows["trade_date"].tolist() == ["20260630"]


def test_gateway_outputs_deterministic_price_order(stockpred_root: Path) -> None:
    _append_stock(stockpred_root, code="600000.SH", trade_date="20260630")
    gateway = _gateway(stockpred_root)

    rows = gateway.prices(
        "2026-06-30",
        "2026-06-30",
        ["600000.SH", "000001.SZ"],
    )

    assert rows[["ts_code", "trade_date"]].values.tolist() == [
        ["000001.SZ", "20260630"],
        ["600000.SH", "20260630"],
    ]


def test_gateway_times_the_actual_snapshot_read_as_shared_data_load(stockpred_root: Path) -> None:
    gateway = _gateway(stockpred_root)
    timer = _RecordingTimer()

    gateway.set_phase_timer(timer)
    gateway.trade_dates("20260601", "20260630")

    assert timer.phases == ["data_load"]


def test_financials_pit_never_returns_future_announcement(stockpred_root: Path) -> None:
    lance.write_dataset(
        pa.table(
            {
                "ts_code": ["000001.SZ"],
                "ann_date": ["20260701"],
                "end_date": ["20260630"],
                "eps": [0.7],
                "dt_eps": [0.68],
                "roe": [4.0],
                "roe_dt": [3.9],
                "roa": [0.4],
                "grossprofit_margin": [41.0],
                "netprofit_margin": [21.0],
            }
        ),
        stockpred_root
        / "data"
        / "lance"
        / "market_core"
        / "fact_fina_indicator.lance",
        mode="append",
    )
    gateway = _gateway(stockpred_root)

    rows = gateway.financials_pit(
        "20260101",
        "20261231",
        eval_date="20260501",
    )

    assert rows["ann_date"].tolist() == ["20260430"]
    assert rows["end_date"].tolist() == ["20260331"]


def test_gateway_domain_queries_use_pinned_contract(stockpred_root: Path) -> None:
    gateway = _gateway(stockpred_root)

    assert gateway.trade_dates("20260601", "20260630") == ["20260630"]
    assert gateway.stock_dimension()["ts_code"].tolist() == ["000001.SZ"]
    assert gateway.name_history()["security_name"].tolist() == ["平安银行"]
    assert gateway.industry_history()["industry_code"].tolist() == ["801780"]
    assert gateway.adjustment_factors("20260630", "20260630")["adj_factor"].tolist() == [1.2]
    assert gateway.stock_limits("20260630", "20260630")["up_limit"].tolist() == [11.0]
    assert gateway.daily_basic("20260630", "20260630")["pe_ttm"].tolist() == [8.0]
    assert gateway.moneyflow("20260630", "20260630")["net_mf_amount"].tolist() == [20.0]
    assert gateway.index_weights("000300.SH", "20260630", "20260630")["con_code"].tolist() == [
        "000001.SZ"
    ]
    assert gateway.index_daily("000300.SH", "20260630", "20260630")["close"].tolist() == [
        3920.0
    ]


@pytest.fixture
def index_gateway_root(stockpred_root_factory) -> Path:
    return stockpred_root_factory(
        index_daily_rows={
            "ts_code": ["H00300.CSI", "000300.SH"],
            "trade_date": ["20260630", "20260630"],
            "open": [3920.0, 3900.0],
            "high": [3930.0, 3950.0],
            "low": [3910.0, 3880.0],
            "close": [3921.0, 3920.0],
            "pct_chg": [0.6, 0.5],
        }
    )


def test_total_return_index_exposes_adjusted_open_without_changing_fact_schema(index_gateway_root: Path) -> None:
    gateway = _gateway(index_gateway_root)

    result = gateway.index_daily("H00300.CSI", "20260630", "20260630")

    assert set(result["ts_code"]) == {"H00300.CSI"}
    assert result["open"].tolist() == [3920.0]
    assert result["adj_open"].tolist() == [3920.0]


def test_price_index_does_not_claim_adjusted_open(index_gateway_root: Path) -> None:
    gateway = _gateway(index_gateway_root)

    result = gateway.index_daily("000300.SH", "20260630", "20260630")

    assert set(result["ts_code"]) == {"000300.SH"}
    assert result["open"].tolist() == [3900.0]
    assert "adj_open" not in result.columns


def test_gateway_rejects_unsafe_filter_value(stockpred_root: Path) -> None:
    gateway = _gateway(stockpred_root)

    with pytest.raises(StockPredDataError) as exc_info:
        gateway.prices("20260630", "20260630", ["000001.SZ' OR 1=1"])

    assert exc_info.value.code == "STOCKPRED_FILTER_INVALID"
