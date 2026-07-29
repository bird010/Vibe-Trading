"""StockPred local Lance-backed data loader.

Reads A-share OHLCV from StockPred's Lance ``stock`` dataset.
When pylance is unavailable or the dataset is missing, ``is_available()``
returns ``False`` and the registry fallback chain moves to the next loader.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from backtest.loaders.base import validate_date_range, validate_ohlc
from backtest.loaders.registry import register
from src.tools.stockpred_base import (
    _get_lance_dataset,
    _resolve_stockpred_root,
    _sanitize_filter_value,
)

logger = logging.getLogger(__name__)


@register
class DataLoader:
    """StockPred Lance-backed OHLCV loader for A-share."""

    name = "stockpred"
    markets = {"a_share"}
    requires_auth = False

    def is_available(self) -> bool:
        """True when STOCKPRED_DATA_ROOT has a stock.lance dataset."""
        root = _resolve_stockpred_root()
        if root is None:
            return False
        lance_path = root / "data" / "lance" / "market_core" / "stock.lance"
        return lance_path.exists()

    def fetch(
        self,
        codes: List[str],
        start_date: str,
        end_date: str,
        *,
        interval: str = "1D",
        fields: Optional[List[str]] = None,
    ) -> Dict[str, pd.DataFrame]:
        """Fetch OHLCV from StockPred Lance stock dataset.

        Args:
            codes: Stock codes (e.g. ``["000001.SZ", "600519.SH"]``).
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).
            interval: Only ``"1D"`` is supported.
            fields: Ignored (StockPred Lance returns fixed OHLCV schema).

        Returns:
            Mapping ``{symbol: DataFrame}`` with DatetimeIndex ``trade_date``
            and columns ``[open, high, low, close, volume]``.
        """
        validate_date_range(start_date, end_date)
        # Interval contract: only daily data is supported
        if interval.upper() != "1D":
            raise ValueError("stockpred loader supports only interval='1D'")
        if not codes:
            return {}

        root = _resolve_stockpred_root()
        if root is None:
            return {}

        lance_path = root / "data" / "lance" / "market_core" / "stock.lance"
        if not lance_path.exists():
            return {}

        try:
            return self._fetch_lance(lance_path, codes, start_date, end_date)
        except Exception as exc:
            logger.warning("stockpred Lance fetch failed: %s", exc)
            return {}

    def _fetch_lance(
        self,
        lance_path: Path,
        codes: List[str],
        start_date: str,
        end_date: str,
    ) -> Dict[str, pd.DataFrame]:
        """Read from Lance dataset with filter pushdown."""
        ds = _get_lance_dataset(lance_path)

        sd = start_date.replace("-", "")
        ed = end_date.replace("-", "")
        safe_codes = [_sanitize_filter_value(c) for c in codes]
        code_filter = " OR ".join(f"ts_code = '{c}'" for c in safe_codes)
        filt = f"({code_filter}) AND trade_date >= '{sd}' AND trade_date <= '{ed}'"

        table = ds.to_table(filter=filt)
        df = table.to_pandas()

        if df.empty:
            return {}

        result: Dict[str, pd.DataFrame] = {}
        for code, group in df.groupby("ts_code"):
            frame = group.copy()
            frame["trade_date"] = pd.to_datetime(frame["trade_date"])
            frame = frame.set_index("trade_date").sort_index()
            frame.index.name = "trade_date"

            # Rename vol → volume, select only OHLCV
            frame = frame.rename(columns={"vol": "volume"})
            ohlcv_cols = ["open", "high", "low", "close", "volume"]
            available = [c for c in ohlcv_cols if c in frame.columns]
            frame = frame[available]

            for col in available:
                frame[col] = pd.to_numeric(frame[col], errors="coerce")
            ohlc_present = [c for c in ("open", "high", "low", "close") if c in frame.columns]
            frame = frame.dropna(subset=ohlc_present)
            frame = validate_ohlc(frame)

            if not frame.empty:
                result[code] = frame

        return result
