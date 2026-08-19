"""StockPred data and Graph integration."""

from src.stockpred.contracts import (
    DataSnapshotManifest,
    ModelSnapshot,
    REQUIRED_TABLES,
    StockPredDataError,
    TableSnapshot,
    TableSpec,
)
from src.stockpred.snapshot import build_snapshot, resolve_stockpred_root
from src.stockpred.gateway import StockPredDataGateway

__all__ = [
    "DataSnapshotManifest",
    "ModelSnapshot",
    "REQUIRED_TABLES",
    "StockPredDataError",
    "StockPredDataGateway",
    "TableSnapshot",
    "TableSpec",
    "build_snapshot",
    "resolve_stockpred_root",
]
