from __future__ import annotations

from src.stockpred.contracts import (
    DataSnapshotManifest,
    ModelSnapshot,
    REQUIRED_TABLES,
    StockPredDataError,
    TableSnapshot,
)


def test_required_contract_contains_graph_inputs() -> None:
    assert set(REQUIRED_TABLES) == {
        "dim_stock",
        "dim_stock_name_history",
        "bridge_stock_industry",
        "dim_trade_cal",
        "stock",
        "fact_adj_factor",
        "fact_stock_limit",
        "fact_stock_daily_basic",
        "fact_moneyflow",
        "fact_index_weight",
        "fact_index_daily",
        "fact_fina_indicator",
    }
    assert REQUIRED_TABLES["fact_fina_indicator"].watermark_column == "ann_date"
    assert REQUIRED_TABLES["stock"].sort_columns == ("ts_code", "trade_date")


def test_manifest_round_trip_is_stable() -> None:
    manifest = DataSnapshotManifest(
        as_of="2026-06-30T15:00:00+08:00",
        tables={
            "stock": TableSnapshot(
                name="stock",
                version=53,
                max_date="20260630",
                schema_sha256="abc",
            )
        },
        model=ModelSnapshot(
            id="stockpred-graph",
            version="graph-v1",
            config_sha256="cfg",
        ),
    )

    restored = DataSnapshotManifest.model_validate_json(manifest.model_dump_json())

    assert restored == manifest
    assert restored.contract == "stockpred-data/v1"


def test_data_error_exposes_stable_code() -> None:
    error = StockPredDataError("STOCKPRED_TABLE_MISSING", "missing: stock")

    assert error.code == "STOCKPRED_TABLE_MISSING"
    assert str(error) == "missing: stock"
