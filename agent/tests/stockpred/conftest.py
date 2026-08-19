from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import lance
import pyarrow as pa
import pytest


def _table_rows() -> dict[str, dict[str, list[object]]]:
    return {
        "dim_stock": {
            "ts_code": ["000001.SZ"],
            "name": ["平安银行"],
            "industry": ["银行"],
            "list_date": ["19910403"],
            "delist_date": [""],
            "list_status": ["L"],
            "exchange": ["SZSE"],
            "market": ["主板"],
        },
        "dim_stock_name_history": {
            "ts_code": ["000001.SZ"],
            "security_name": ["平安银行"],
            "effective_from": ["19910403"],
            "effective_to": [""],
            "ann_date": ["19910403"],
            "change_reason": ["上市"],
        },
        "bridge_stock_industry": {
            "ts_code": ["000001.SZ"],
            "industry_code": ["801780"],
            "industry_name": ["银行"],
            "level": ["L1"],
            "effective_from": ["20200101"],
            "effective_to": [""],
            "source": ["sw"],
        },
        "dim_trade_cal": {
            "exchange": ["SSE"],
            "cal_date": ["20260630"],
            "is_open": [1],
            "pretrade_date": ["20260629"],
        },
        "stock": {
            "ts_code": ["000001.SZ"],
            "trade_date": ["20260630"],
            "open": [10.0],
            "high": [10.5],
            "low": [9.8],
            "close": [10.2],
            "pct_chg": [2.0],
            "vol": [1000.0],
            "amount": [10000.0],
        },
        "fact_adj_factor": {
            "ts_code": ["000001.SZ"],
            "trade_date": ["20260630"],
            "adj_factor": [1.2],
        },
        "fact_stock_limit": {
            "ts_code": ["000001.SZ"],
            "trade_date": ["20260630"],
            "up_limit": [11.0],
            "down_limit": [9.0],
        },
        "fact_stock_daily_basic": {
            "ts_code": ["000001.SZ"],
            "trade_date": ["20260630"],
            "turnover_rate": [1.0],
            "pe_ttm": [8.0],
            "pb": [1.0],
            "total_mv": [1000000.0],
        },
        "fact_moneyflow": {
            "ts_code": ["000001.SZ"],
            "trade_date": ["20260630"],
            "buy_elg_amount": [100.0],
            "sell_elg_amount": [80.0],
            "net_mf_amount": [20.0],
        },
        "fact_index_weight": {
            "index_code": ["000300.SH"],
            "con_code": ["000001.SZ"],
            "trade_date": ["20260630"],
            "weight": [0.5],
        },
        "fact_index_daily": {
            "ts_code": ["000300.SH"],
            "trade_date": ["20260630"],
            "open": [3900.0],
            "high": [3950.0],
            "low": [3880.0],
            "close": [3920.0],
            "pct_chg": [0.5],
        },
        "fact_fina_indicator": {
            "ts_code": ["000001.SZ"],
            "ann_date": ["20260430"],
            "end_date": ["20260331"],
            "eps": [0.5],
            "dt_eps": [0.48],
            "roe": [3.0],
            "roe_dt": [2.9],
            "roa": [0.3],
            "grossprofit_margin": [40.0],
            "netprofit_margin": [20.0],
        },
    }


@pytest.fixture
def stockpred_root_factory(tmp_path: Path) -> Callable[..., Path]:
    def _build(
        *,
        omit_table: str | None = None,
        drop_column: tuple[str, str] | None = None,
        index_daily_rows: dict[str, list[object]] | None = None,
    ) -> Path:
        root = tmp_path / "StockPred"
        market_core = root / "data" / "lance" / "market_core"
        market_core.mkdir(parents=True, exist_ok=True)
        for name, rows in _table_rows().items():
            if name == omit_table:
                continue
            current = dict(index_daily_rows) if name == "fact_index_daily" and index_daily_rows else dict(rows)
            if drop_column and drop_column[0] == name:
                current.pop(drop_column[1])
            lance.write_dataset(
                pa.table(current),
                market_core / f"{name}.lance",
            )
        return root

    return _build


@pytest.fixture
def stockpred_root(stockpred_root_factory: Callable[..., Path]) -> Path:
    return stockpred_root_factory()
