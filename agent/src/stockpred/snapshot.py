"""Build and open immutable StockPred Lance data snapshots."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow.compute as pc

from src.stockpred.contracts import (
    DataSnapshotManifest,
    ModelSnapshot,
    REQUIRED_TABLES,
    StockPredDataError,
    TableSnapshot,
)


def resolve_stockpred_root(explicit: Path | None = None) -> Path:
    """Resolve and validate the StockPred project root."""
    raw = explicit if explicit is not None else os.getenv("STOCKPRED_DATA_ROOT", "")
    if not raw:
        raise StockPredDataError(
            "STOCKPRED_ROOT_MISSING",
            "STOCKPRED_DATA_ROOT is not configured",
        )
    root = Path(raw).expanduser().resolve()
    market_core = root / "data" / "lance" / "market_core"
    if not market_core.is_dir():
        raise StockPredDataError(
            "STOCKPRED_ROOT_MISSING",
            "StockPred market_core Lance directory is missing",
        )
    return root


def _load_lance() -> Any:
    try:
        import lance
    except ImportError as exc:
        raise StockPredDataError(
            "STOCKPRED_LANCE_UNAVAILABLE",
            "pylance is required for StockPred data access",
        ) from exc
    return lance


def _schema_sha256(schema: Any) -> str:
    return hashlib.sha256(str(schema).encode("utf-8")).hexdigest()


def _max_visible_date(dataset: Any, column: str | None, as_of: datetime) -> str | None:
    if column is None:
        return None
    cutoff = as_of.strftime("%Y%m%d")
    table = dataset.to_table(
        columns=[column],
        filter=f"{column} <= '{cutoff}'",
    )
    maximum = pc.max(table.column(column)).as_py()
    return str(maximum) if maximum is not None else None


def build_snapshot(
    root: Path,
    *,
    as_of: datetime,
    model: ModelSnapshot,
) -> DataSnapshotManifest:
    """Validate all required datasets and pin their current versions."""
    resolved_root = resolve_stockpred_root(root)
    lance = _load_lance()
    tables: dict[str, TableSnapshot] = {}
    for name, spec in REQUIRED_TABLES.items():
        path = resolved_root / "data" / "lance" / spec.layer / f"{name}.lance"
        if not path.is_dir():
            raise StockPredDataError(
                "STOCKPRED_TABLE_MISSING",
                f"required table missing: {name}",
            )
        try:
            dataset = lance.dataset(path)
            missing = sorted(set(spec.required_columns) - set(dataset.schema.names))
            if missing:
                raise StockPredDataError(
                    "STOCKPRED_SCHEMA_MISMATCH",
                    f"{name} missing columns: {missing}",
                )
            tables[name] = TableSnapshot(
                name=name,
                version=int(dataset.version),
                max_date=_max_visible_date(dataset, spec.watermark_column, as_of),
                schema_sha256=_schema_sha256(dataset.schema),
            )
        except StockPredDataError:
            raise
        except Exception as exc:
            raise StockPredDataError(
                "STOCKPRED_READ_FAILED",
                f"failed to inspect {name}: {exc}",
            ) from exc
    return DataSnapshotManifest(
        as_of=as_of.isoformat(),
        tables=tables,
        model=model,
    )


def open_snapshot_dataset(root: Path, snapshot: TableSnapshot) -> Any:
    """Open exactly the Lance version recorded by a table snapshot."""
    resolved_root = resolve_stockpred_root(root)
    spec = REQUIRED_TABLES.get(snapshot.name)
    if spec is None:
        raise StockPredDataError(
            "STOCKPRED_TABLE_UNKNOWN",
            f"unknown table in snapshot: {snapshot.name}",
        )
    path = resolved_root / "data" / "lance" / spec.layer / f"{snapshot.name}.lance"
    try:
        return _load_lance().dataset(path, version=snapshot.version)
    except Exception as exc:
        raise StockPredDataError(
            "STOCKPRED_READ_FAILED",
            f"failed to open {snapshot.name} version {snapshot.version}: {exc}",
        ) from exc
