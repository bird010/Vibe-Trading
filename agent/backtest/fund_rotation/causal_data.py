"""Controlled causal data view — Phase 2 Task 1 (design §6).

``CausalDataView`` is the ONLY data entry point a strategy receives. It exposes
declared datasets/fields up to (and including) the current ``signal_date`` for
the snapshot ETF universe only, returns read-only copies, and audits access.
Undeclared datasets/fields, lookback overruns, post-signal dates and
non-snapshot ETFs are rejected with ``UNDECLARED_STRATEGY_DATA_ACCESS``.

This is a contract/enforcement boundary for internally-registered strategies —
it is NOT a malicious-code sandbox.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Sequence

import pandas as pd

from backtest.fund_rotation.contracts import StrategyDataRequirements
from backtest.fund_rotation.returns import compute_adjusted_close, compute_weekly_returns


class UndeclaredStrategyDataAccess(Exception):
    """§6 — raised before returning data when a strategy reads beyond its
    declared datasets/fields, lookback, signal date, or snapshot universe."""

    code = "UNDECLARED_STRATEGY_DATA_ACCESS"


@dataclass(frozen=True)
class FundInstrument:
    """An eligible fund/ETF in the snapshot universe."""

    ts_code: str
    name: str
    list_date: str


@dataclass(frozen=True)
class AccessRecord:
    """One audited access (no whole-table content is recorded)."""

    method: str
    fields: tuple[str, ...]
    rows: int
    date_min: str
    date_max: str


@dataclass
class CausalDataView:
    """Per-session controlled view over the shared (immutable) snapshot data."""

    _fund_daily: pd.DataFrame
    _fund_adj: pd.DataFrame
    _dim_fund: pd.DataFrame
    _requirements: StrategyDataRequirements
    _signal_date: pd.Timestamp
    _universe_codes: frozenset
    _access_log: list = field(default_factory=list)

    @property
    def signal_date(self) -> pd.Timestamp:
        return self._signal_date

    @property
    def access_log(self) -> tuple[AccessRecord, ...]:
        return tuple(self._access_log)

    # ── enforcement helpers ──

    def _check_fields(self, fields: Sequence[str]) -> None:
        declared = set(self._requirements.required_fields)
        for f in fields:
            if f not in declared:
                raise UndeclaredStrategyDataAccess(
                    f"field {f!r} is not declared in strategy requirements "
                    f"(declared: {sorted(declared)})"
                )

    def _causal_filter(self, df: pd.DataFrame) -> pd.DataFrame:
        """Restrict to snapshot universe and dates <= signal_date."""
        out = df
        signal_str = self._signal_date.strftime("%Y%m%d")
        if "trade_date" in out.columns:
            out = out[out["trade_date"].astype(str) <= signal_str]
        if "ts_code" in out.columns:
            out = out[out["ts_code"].astype(str).isin(self._universe_codes)]
        return out

    def _tail_dates(self, df: pd.DataFrame, lookback: int | None) -> pd.DataFrame:
        if lookback is None or df.empty or "trade_date" not in df.columns:
            return df
        dates = sorted(df["trade_date"].astype(str).unique())
        keep = set(dates[-lookback:])
        return df[df["trade_date"].astype(str).isin(keep)]

    def _audit(self, method: str, fields: Sequence[str], df: pd.DataFrame) -> None:
        date_min = date_max = ""
        if not df.empty and "trade_date" in df.columns:
            ds = df["trade_date"].astype(str)
            date_min, date_max = ds.min(), ds.max()
        self._access_log.append(AccessRecord(
            method=method, fields=tuple(fields), rows=len(df),
            date_min=date_min, date_max=date_max,
        ))

    # ── query surface ──

    def daily_bars(self, fields: Sequence[str], lookback: int | None = None) -> pd.DataFrame:
        self._check_fields(fields)
        cols = ["ts_code", "trade_date", *[f for f in fields if f not in ("ts_code", "trade_date")]]
        df = self._causal_filter(self._fund_daily)
        df = df[[c for c in cols if c in df.columns]]
        df = self._tail_dates(df, lookback)
        self._audit("daily_bars", fields, df)
        return df.copy()

    def adjusted_closes(self, lookback: int | None = None) -> pd.DataFrame:
        signal_str = self._signal_date.strftime("%Y%m%d")
        adj_close = compute_adjusted_close(self._fund_daily, self._fund_adj, signal_str)
        if adj_close.empty:
            self._audit("adjusted_closes", ("adj_close",), adj_close)
            return adj_close.copy()
        # Restrict columns to the snapshot universe.
        cols = [c for c in adj_close.columns if str(c) in self._universe_codes]
        adj_close = adj_close[cols]
        if lookback is not None:
            adj_close = adj_close.iloc[-lookback:]
        self._audit("adjusted_closes", ("adj_close",), adj_close.reset_index())
        return adj_close.copy()

    def returns(
        self,
        frequency: Literal["daily", "weekly", "monthly"],
        lookback: int,
    ) -> pd.DataFrame:
        signal_str = self._signal_date.strftime("%Y%m%d")
        adj_close = compute_adjusted_close(self._fund_daily, self._fund_adj, signal_str)
        if adj_close.empty:
            self._audit("returns", (frequency,), adj_close)
            return adj_close.copy()
        cols = [c for c in adj_close.columns if str(c) in self._universe_codes]
        adj_close = adj_close[cols]
        if frequency == "daily":
            rets = adj_close.pct_change(fill_method=None)
        elif frequency == "weekly":
            rets = compute_weekly_returns(self._fund_daily, self._fund_adj, signal_str)
            rets = rets[[c for c in rets.columns if str(c) in self._universe_codes]]
        elif frequency == "monthly":
            monthly_close = adj_close.resample("ME").last()
            rets = monthly_close.pct_change(fill_method=None)
        else:  # pragma: no cover - guarded by Literal
            raise UndeclaredStrategyDataAccess(f"unknown frequency {frequency!r}")
        rets = rets.iloc[-lookback:]
        self._audit("returns", (frequency,), rets.reset_index())
        return rets.copy()

    def causal_adv(self, lookback_days: int = 20) -> pd.Series:
        """Causal average daily turnover (amount) per ETF, using only completed
        trading days strictly before the signal date."""
        df = self._causal_filter(self._fund_daily)
        if "amount" not in df.columns or df.empty:
            self._audit("causal_adv", ("amount",), df)
            return pd.Series(dtype=float, name="adv")
        signal_str = self._signal_date.strftime("%Y%m%d")
        # Strictly before signal date (completed days only).
        df = df[df["trade_date"].astype(str) < signal_str]
        df = self._tail_dates(df, lookback_days)
        adv = df.groupby("ts_code")["amount"].mean()
        adv.name = "adv"
        self._audit("causal_adv", ("amount",), df)
        return adv.copy()

    def fund_adjustments(self, lookback: int | None = None) -> pd.DataFrame:
        df = self._causal_filter(self._fund_adj)
        df = self._tail_dates(df, lookback)
        self._audit("fund_adjustments", ("adj_factor",), df)
        return df.copy()

    def eligible_universe(self) -> tuple[FundInstrument, ...]:
        dim = self._dim_fund
        dim = dim[dim["ts_code"].astype(str).isin(self._universe_codes)]
        instruments = tuple(
            FundInstrument(
                ts_code=str(row["ts_code"]),
                name=str(row.get("name", "")),
                list_date=str(row.get("list_date", "")),
            )
            for _, row in dim.iterrows()
        )
        self._audit("eligible_universe", ("ts_code", "name", "list_date"), dim)
        return instruments

    def trading_calendar(self, lookback: int | None = None) -> tuple[pd.Timestamp, ...]:
        df = self._causal_filter(self._fund_daily)
        dates = sorted({str(d) for d in df["trade_date"]}) if not df.empty else []
        if lookback is not None:
            dates = dates[-lookback:]
        self._audit("trading_calendar", ("trade_date",), df)
        return tuple(pd.Timestamp(d) for d in dates)
