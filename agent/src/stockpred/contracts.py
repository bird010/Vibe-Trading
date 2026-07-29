"""Stable data contracts for the StockPred Graph integration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class StockPredDataError(RuntimeError):
    """Fail-closed StockPred data error with a stable machine code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TableSpec(BaseModel):
    """Required physical schema and deterministic ordering for one table."""

    model_config = ConfigDict(frozen=True)

    layer: str = "market_core"
    required_columns: tuple[str, ...]
    watermark_column: str | None
    sort_columns: tuple[str, ...]


class TableSnapshot(BaseModel):
    """Pinned Lance version and visible watermark for one table."""

    model_config = ConfigDict(frozen=True)

    name: str
    version: int
    max_date: str | None
    schema_sha256: str


class ModelSnapshot(BaseModel):
    """Identity of the Graph model used by a run."""

    model_config = ConfigDict(frozen=True)

    id: str
    version: str
    config_sha256: str


class DataSnapshotManifest(BaseModel):
    """Complete pinned data and model identity for one run."""

    model_config = ConfigDict(frozen=True)

    contract: Literal["stockpred-data/v1"] = "stockpred-data/v1"
    as_of: str
    tables: dict[str, TableSnapshot]
    model: ModelSnapshot


REQUIRED_TABLES: dict[str, TableSpec] = {
    "dim_stock": TableSpec(
        required_columns=(
            "ts_code",
            "name",
            "industry",
            "list_date",
            "delist_date",
            "list_status",
            "exchange",
            "market",
        ),
        watermark_column=None,
        sort_columns=("ts_code",),
    ),
    "dim_stock_name_history": TableSpec(
        required_columns=(
            "ts_code",
            "security_name",
            "effective_from",
            "effective_to",
            "ann_date",
            "change_reason",
        ),
        watermark_column="effective_from",
        sort_columns=("ts_code", "effective_from", "ann_date"),
    ),
    "bridge_stock_industry": TableSpec(
        required_columns=(
            "ts_code",
            "industry_code",
            "industry_name",
            "level",
            "effective_from",
            "effective_to",
            "source",
        ),
        watermark_column="effective_from",
        sort_columns=("ts_code", "effective_from"),
    ),
    "dim_trade_cal": TableSpec(
        required_columns=("exchange", "cal_date", "is_open", "pretrade_date"),
        watermark_column="cal_date",
        sort_columns=("exchange", "cal_date"),
    ),
    "stock": TableSpec(
        required_columns=(
            "ts_code",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "pct_chg",
            "vol",
            "amount",
        ),
        watermark_column="trade_date",
        sort_columns=("ts_code", "trade_date"),
    ),
    "fact_adj_factor": TableSpec(
        required_columns=("ts_code", "trade_date", "adj_factor"),
        watermark_column="trade_date",
        sort_columns=("ts_code", "trade_date"),
    ),
    "fact_stock_limit": TableSpec(
        required_columns=("ts_code", "trade_date", "up_limit", "down_limit"),
        watermark_column="trade_date",
        sort_columns=("ts_code", "trade_date"),
    ),
    "fact_stock_daily_basic": TableSpec(
        required_columns=(
            "ts_code",
            "trade_date",
            "turnover_rate",
            "pe_ttm",
            "pb",
            "total_mv",
        ),
        watermark_column="trade_date",
        sort_columns=("ts_code", "trade_date"),
    ),
    "fact_moneyflow": TableSpec(
        required_columns=(
            "ts_code",
            "trade_date",
            "buy_elg_amount",
            "sell_elg_amount",
            "net_mf_amount",
        ),
        watermark_column="trade_date",
        sort_columns=("ts_code", "trade_date"),
    ),
    "fact_index_weight": TableSpec(
        required_columns=("index_code", "con_code", "trade_date", "weight"),
        watermark_column="trade_date",
        sort_columns=("index_code", "trade_date", "con_code"),
    ),
    "fact_index_daily": TableSpec(
        required_columns=(
            "ts_code",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "pct_chg",
        ),
        watermark_column="trade_date",
        sort_columns=("ts_code", "trade_date"),
    ),
    "fact_fina_indicator": TableSpec(
        required_columns=(
            "ts_code",
            "ann_date",
            "end_date",
            "eps",
            "dt_eps",
            "roe",
            "roe_dt",
            "roa",
            "grossprofit_margin",
            "netprofit_margin",
        ),
        watermark_column="ann_date",
        sort_columns=("ts_code", "ann_date", "end_date"),
    ),
}
