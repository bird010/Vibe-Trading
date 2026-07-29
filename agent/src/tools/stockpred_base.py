"""Shared base for StockPred local-data tools.

All StockPred tools read from Lance datasets under
``$STOCKPRED_DATA_ROOT/data/lance/{layer}/{table}.lance``.
The base class provides:
- ``_resolve_root()`` — locate the StockPred data root
- ``_query_lance()`` — read a Lance dataset with filter pushdown
- ``check_available()`` — True when root exists and pylance is importable
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from pathlib import Path
from typing import Any

import pandas as pd

from src.agent.tools import BaseTool

logger = logging.getLogger(__name__)

# Lance search order: market_core first (most tables), then source_raw, then graph.
_LANCE_LAYERS = ("market_core", "source_raw", "graph")

# Module-level cache: path → lance.LanceDataset.
_lance_cache: dict[str, Any] = {}


def _resolve_stockpred_root() -> Path | None:
    """Return the StockPred project root if the Lance market_core dir exists."""
    root = os.getenv("STOCKPRED_DATA_ROOT", "").strip()
    if not root:
        return None
    p = Path(root)
    if (p / "data" / "lance" / "market_core").is_dir():
        return p
    return None


def _resolve_lance_path(root: Path, table: str) -> Path | None:
    """Find ``{table}.lance`` across layers (market_core → source_raw → graph)."""
    for layer in _LANCE_LAYERS:
        candidate = root / "data" / "lance" / layer / f"{table}.lance"
        if candidate.exists():
            return candidate
    return None


def _get_lance_dataset(lance_path: Path) -> Any:
    """Return a cached Lance dataset."""
    key = str(lance_path)
    if key not in _lance_cache:
        import lance

        _lance_cache[key] = lance.dataset(key)
    return _lance_cache[key]


_SAFE_FILTER_RE = re.compile(r"^[A-Za-z0-9_.]+$")


def _sanitize_filter_value(value: str) -> str:
    """Validate a filter value contains only safe characters.

    Raises ``ValueError`` if the value contains characters that could
    alter Lance filter expression semantics (e.g. quotes, spaces,
    operators).  Allowed pattern: ``[A-Za-z0-9_.]+`` — covers stock
    codes (``000001.SZ``), dates (``20260102``), series IDs
    (``rate.shibor_on``), and curve types (``treasury``).
    """
    stripped = value.strip("'\"")
    if not _SAFE_FILTER_RE.match(stripped):
        raise ValueError(f"Unsafe Lance filter value: {value!r}")
    return stripped


def _json_safe(value: Any) -> Any:
    """Convert non-serializable values for JSON output."""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _rows_to_safe_dicts(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Convert a DataFrame to a list of JSON-safe dicts."""
    records = df.to_dict(orient="records")
    for row in records:
        for key, value in row.items():
            row[key] = _json_safe(value)
    return records


class StockPredBaseTool(BaseTool):
    """Base class for tools reading StockPred local Lance data."""

    is_readonly = True

    @classmethod
    def check_available(cls) -> bool:
        """True when STOCKPRED_DATA_ROOT has a market_core Lance dir."""
        return _resolve_stockpred_root() is not None

    def _get_root(self) -> Path | None:
        return _resolve_stockpred_root()

    def _query_lance(
        self,
        table: str,
        filt: str | None = None,
        columns: list[str] | None = None,
    ) -> pd.DataFrame:
        """Read from a Lance dataset. Returns empty DataFrame on any failure."""
        root = self._get_root()
        if root is None:
            return pd.DataFrame()

        lance_path = _resolve_lance_path(root, table)
        if lance_path is None:
            return pd.DataFrame()

        try:
            ds = _get_lance_dataset(lance_path)
            kwargs: dict[str, Any] = {}
            if filt:
                kwargs["filter"] = filt
            if columns:
                kwargs["columns"] = columns
            table_obj = ds.to_table(**kwargs)
            return table_obj.to_pandas()
        except Exception as exc:
            logger.warning("stockpred Lance query failed for %s: %s", table, exc)
            return pd.DataFrame()

    def _ok(self, data: list[dict[str, Any]], **extra: Any) -> str:
        """Build a success JSON envelope."""
        envelope: dict[str, Any] = {"ok": True, "data": data, "rows": len(data)}
        envelope.update(extra)
        return json.dumps(envelope, ensure_ascii=False)

    def _error(self, message: str) -> str:
        """Build a failure JSON envelope."""
        return json.dumps({"ok": False, "error": message, "data": []}, ensure_ascii=False)
