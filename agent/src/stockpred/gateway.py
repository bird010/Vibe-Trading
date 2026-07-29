"""Version-pinned, read-only gateway for StockPred Lance data."""

from __future__ import annotations

import re
from collections.abc import Sequence
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pandas as pd

from src.stockpred.contracts import (
    DataSnapshotManifest,
    REQUIRED_TABLES,
    StockPredDataError,
)
from src.stockpred.snapshot import open_snapshot_dataset, resolve_stockpred_root


_SAFE_FILTER_VALUE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _safe_filter_value(value: str) -> str:
    normalized = str(value).strip()
    if not normalized or not _SAFE_FILTER_VALUE.fullmatch(normalized):
        raise StockPredDataError(
            "STOCKPRED_FILTER_INVALID",
            f"unsafe StockPred filter value: {value!r}",
        )
    return normalized


def _date(value: str) -> str:
    normalized = _safe_filter_value(value).replace("-", "")
    if len(normalized) != 8 or not normalized.isdigit():
        raise StockPredDataError(
            "STOCKPRED_FILTER_INVALID",
            f"invalid StockPred date: {value!r}",
        )
    return normalized


def _date_range(column: str, start: str, end: str) -> str:
    start_date = _date(start)
    end_date = _date(end)
    if start_date > end_date:
        raise StockPredDataError(
            "STOCKPRED_FILTER_INVALID",
            f"start date {start_date} is after end date {end_date}",
        )
    return f"{column} >= '{start_date}' AND {column} <= '{end_date}'"


def _values_filter(column: str, values: Sequence[str]) -> str | None:
    safe_values = [_safe_filter_value(value) for value in values]
    if not safe_values:
        return None
    literals = ", ".join(f"'{value}'" for value in safe_values)
    return f"{column} IN ({literals})"


class StockPredDataGateway:
    """Read domain frames from exactly the versions in a snapshot manifest."""

    def __init__(self, root: Path, manifest: DataSnapshotManifest) -> None:
        self.root = resolve_stockpred_root(root)
        self.manifest = manifest
        self.rows_read = 0
        self.cache_hits = 0
        self.cache_misses = 0
        self._phase_timer: Any | None = None

    def set_phase_timer(self, timer: Any | None) -> None:
        self._phase_timer = timer

    def _read(
        self,
        table: str,
        *,
        columns: Sequence[str],
        filter_expr: str | None = None,
    ) -> pd.DataFrame:
        spec = REQUIRED_TABLES.get(table)
        snapshot = self.manifest.tables.get(table)
        if spec is None or snapshot is None:
            raise StockPredDataError(
                "STOCKPRED_SNAPSHOT_INCOMPLETE",
                f"table is not present in snapshot: {table}",
            )
        try:
            dataset = open_snapshot_dataset(self.root, snapshot)
            phase = self._phase_timer.phase("data_load") if self._phase_timer is not None else nullcontext()
            with phase:
                frame = dataset.to_table(
                    columns=list(columns),
                    filter=filter_expr,
                ).to_pandas()
        except StockPredDataError:
            raise
        except Exception as exc:
            raise StockPredDataError(
                "STOCKPRED_READ_FAILED",
                f"failed to read {table}: {exc}",
            ) from exc
        sort_columns = [column for column in spec.sort_columns if column in frame.columns]
        if sort_columns and not frame.empty:
            frame = frame.sort_values(sort_columns, kind="stable")
        self.rows_read += len(frame)
        self.cache_misses += 1
        return frame.reset_index(drop=True)

    def read_metrics(self) -> dict[str, int]:
        return {"rows_read": self.rows_read, "cache_hits": self.cache_hits, "cache_misses": self.cache_misses}

    def trade_dates(self, start: str, end: str) -> list[str]:
        frame = self._read(
            "dim_trade_cal",
            columns=("exchange", "cal_date", "is_open", "pretrade_date"),
            filter_expr=(
                "exchange = 'SSE' AND is_open = 1 AND "
                + _date_range("cal_date", start, end)
            ),
        )
        return frame["cal_date"].astype(str).tolist()

    def stock_dimension(self) -> pd.DataFrame:
        return self._read(
            "dim_stock",
            columns=REQUIRED_TABLES["dim_stock"].required_columns,
        )

    def name_history(self) -> pd.DataFrame:
        return self._read(
            "dim_stock_name_history",
            columns=REQUIRED_TABLES["dim_stock_name_history"].required_columns,
        )

    def industry_history(self) -> pd.DataFrame:
        return self._read(
            "bridge_stock_industry",
            columns=REQUIRED_TABLES["bridge_stock_industry"].required_columns,
            filter_expr="level = 'L1'",
        )

    def prices(
        self,
        start: str,
        end: str,
        codes: Sequence[str] | None = None,
    ) -> pd.DataFrame:
        return self._dated_codes(
            "stock",
            "trade_date",
            start,
            end,
            "ts_code",
            codes,
        )

    def adjustment_factors(
        self,
        start: str,
        end: str,
        codes: Sequence[str] | None = None,
    ) -> pd.DataFrame:
        return self._dated_codes(
            "fact_adj_factor",
            "trade_date",
            start,
            end,
            "ts_code",
            codes,
        )

    def stock_limits(
        self,
        start: str,
        end: str,
        codes: Sequence[str] | None = None,
    ) -> pd.DataFrame:
        return self._dated_codes(
            "fact_stock_limit",
            "trade_date",
            start,
            end,
            "ts_code",
            codes,
        )

    def daily_basic(self, start: str, end: str) -> pd.DataFrame:
        return self._dated_codes(
            "fact_stock_daily_basic",
            "trade_date",
            start,
            end,
            "ts_code",
            None,
        )

    def moneyflow(self, start: str, end: str) -> pd.DataFrame:
        return self._dated_codes(
            "fact_moneyflow",
            "trade_date",
            start,
            end,
            "ts_code",
            None,
        )

    def index_weights(self, index_code: str, start: str, end: str) -> pd.DataFrame:
        code = _safe_filter_value(index_code)
        return self._read(
            "fact_index_weight",
            columns=REQUIRED_TABLES["fact_index_weight"].required_columns,
            filter_expr=(
                f"index_code = '{code}' AND "
                + _date_range("trade_date", start, end)
            ),
        )

    def index_daily(self, index_code: str, start: str, end: str) -> pd.DataFrame:
        code = _safe_filter_value(index_code)
        frame = self._read(
            "fact_index_daily",
            columns=REQUIRED_TABLES["fact_index_daily"].required_columns,
            filter_expr=(
                f"ts_code = '{code}' AND "
                + _date_range("trade_date", start, end)
            ),
        )
        # H00300.CSI is the declared total-return benchmark. Its published
        # open is already the comparable total-return level; expose that fact
        # at the gateway boundary without altering the raw fact schema.
        if code == "H00300.CSI" and "open" in frame.columns:
            frame = frame.copy()
            frame["adj_open"] = frame["open"]
        return frame

    def financials_pit(
        self,
        start: str,
        end: str,
        *,
        eval_date: str,
    ) -> pd.DataFrame:
        visible_end = min(_date(end), _date(eval_date))
        frame = self._read(
            "fact_fina_indicator",
            columns=REQUIRED_TABLES["fact_fina_indicator"].required_columns,
            filter_expr=_date_range("ann_date", start, visible_end),
        )
        if frame.empty:
            return frame
        return (
            frame.sort_values(
                ["ts_code", "end_date", "ann_date"],
                kind="stable",
            )
            .drop_duplicates("ts_code", keep="last")
            .reset_index(drop=True)
        )

    def _dated_codes(
        self,
        table: str,
        date_column: str,
        start: str,
        end: str,
        code_column: str,
        codes: Sequence[str] | None,
    ) -> pd.DataFrame:
        filter_expr = _date_range(date_column, start, end)
        if codes is not None:
            code_filter = _values_filter(code_column, codes)
            if code_filter is None:
                return pd.DataFrame(columns=REQUIRED_TABLES[table].required_columns)
            filter_expr = f"{filter_expr} AND {code_filter}"
        return self._read(
            table,
            columns=REQUIRED_TABLES[table].required_columns,
            filter_expr=filter_expr,
        )
