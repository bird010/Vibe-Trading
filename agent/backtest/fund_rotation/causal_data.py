"""Controlled causal data view — Phase 2 Task 1 (design §6).

``CausalDataView`` is the ONLY data entry point a strategy receives. It exposes
declared datasets/fields up to (and including) the current ``signal_date``;
historical returns may use the pinned candidate source set, while current
selection queries remain limited to the signal-date PIT universe. It returns
read-only copies and audits access.
Undeclared datasets/fields, lookback overruns, post-signal dates and
non-snapshot ETFs are rejected with ``UNDECLARED_STRATEGY_DATA_ACCESS``.

This is a contract/enforcement boundary for internally-registered strategies —
it is NOT a malicious-code sandbox.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal, Sequence

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
    pit_universe_lookup: Callable[[str], frozenset[str]] | None = None
    historical_candidate_codes: frozenset[str] = frozenset()

    # Method -> datasets that method reads (for whitelist enforcement, §6).
    _METHOD_DATASETS = {
        "daily_bars": ("fund",),
        "adjusted_closes": ("fund", "fact_fund_adj"),
        "returns": ("fund", "fact_fund_adj"),
        "causal_adv": ("fund",),
        "fund_adjustments": ("fact_fund_adj",),
        "eligible_universe": ("dim_fund",),
        "trading_calendar": ("fund",),
    }

    # Fields read implicitly by each controlled query.  These are just as
    # strategy-visible as explicit ``daily_bars(fields=...)`` requests.
    _METHOD_FIELDS = {
        "adjusted_closes": ("ts_code", "trade_date", "close", "adj_factor"),
        "returns": ("ts_code", "trade_date", "close", "adj_factor"),
        "causal_adv": ("ts_code", "trade_date", "amount"),
        "fund_adjustments": ("ts_code", "trade_date", "adj_factor"),
        "eligible_universe": ("ts_code", "name", "list_date"),
        "trading_calendar": ("trade_date",),
    }

    @property
    def signal_date(self) -> pd.Timestamp:
        return self._signal_date

    @property
    def access_log(self) -> tuple[AccessRecord, ...]:
        return tuple(self._access_log)

    def pit_universe_codes(self, signal_date: str) -> frozenset[str]:
        """Return the resolver-backed universe for a historical signal date."""
        if self.pit_universe_lookup is not None:
            return frozenset(self.pit_universe_lookup(str(signal_date)))
        return self._universe_codes

    def historical_signal_date_eligible(
        self,
        codes: Sequence[str],
        signal_date: str,
    ) -> tuple[str, ...]:
        """Check historical PIT candidates without applying today's universe."""
        code_set = {str(code) for code in codes}
        daily = self._fund_daily[
            self._fund_daily["trade_date"].astype(str).eq(str(signal_date))
            & self._fund_daily["ts_code"].astype(str).isin(code_set)
        ]
        adj = self._fund_adj[
            self._fund_adj["trade_date"].astype(str).eq(str(signal_date))
            & self._fund_adj["ts_code"].astype(str).isin(code_set)
        ]
        close_by_code = {
            str(row.ts_code): float(row.close)
            for row in daily.itertuples()
            if pd.notna(row.close) and float(row.close) > 0
        }
        adj_by_code = {
            str(row.ts_code): float(row.adj_factor)
            for row in adj.itertuples()
            if pd.notna(row.adj_factor) and float(row.adj_factor) > 0
        }
        return tuple(
            code for code in sorted(code_set)
            if code in close_by_code and code in adj_by_code
        )

    # ── enforcement helpers ──

    def _check_datasets(self, method: str) -> None:
        declared = set(self._requirements.required_datasets)
        needed = self._METHOD_DATASETS[method]
        missing = {d for d in needed if d not in declared}
        if missing:
            raise UndeclaredStrategyDataAccess(
                f"{method} requires undeclared datasets {sorted(missing)} "
                f"(declared: {sorted(declared)})"
            )

    def _check_fields(self, fields: Sequence[str]) -> None:
        declared = set(self._requirements.required_fields)
        for f in fields:
            if f not in declared:
                raise UndeclaredStrategyDataAccess(
                    f"field {f!r} is not declared in strategy requirements "
                    f"(declared: {sorted(declared)})"
                )

    def _check_lookback(self, method: str, lookback: int | None, unit_days: int) -> int:
        """Reject lookbacks exceeding the declared warmup (in the method's unit).

        ``unit_days`` converts the method's lookback unit to trading days
        (1 for daily bars/calendar/adv, 5 for weekly, 21 for monthly).
        """
        max_lookback = self._requirements.warmup_trade_days // unit_days
        if lookback is None:
            return max_lookback
        if lookback < 0:
            raise ValueError(f"{method} lookback must be non-negative, got {lookback}")
        if lookback > max_lookback:
            raise UndeclaredStrategyDataAccess(
                f"{method} lookback {lookback} exceeds declared warmup "
                f"({max_lookback} in this unit; warmup_trade_days="
                f"{self._requirements.warmup_trade_days})"
            )
        return lookback

    def _causal_filter(
        self,
        df: pd.DataFrame,
        *,
        universe_codes: frozenset[str] | None = None,
    ) -> pd.DataFrame:
        """Restrict to snapshot universe and dates <= signal_date."""
        out = df
        signal_str = self._signal_date.strftime("%Y%m%d")
        if "trade_date" in out.columns:
            out = out[out["trade_date"].astype(str) <= signal_str]
        if "ts_code" in out.columns:
            codes = self._universe_codes if universe_codes is None else universe_codes
            out = out[out["ts_code"].astype(str).isin(codes)]
        return out

    def _tail_dates(self, df: pd.DataFrame, lookback: int | None) -> pd.DataFrame:
        if lookback is None or df.empty or "trade_date" not in df.columns:
            return df
        if lookback == 0:
            return df.iloc[0:0]
        dates = sorted(df["trade_date"].astype(str).unique())
        keep = set(dates[-lookback:])
        return df[df["trade_date"].astype(str).isin(keep)]

    def _audit(self, method: str, fields: Sequence[str], df: pd.DataFrame) -> None:
        date_min = date_max = ""
        if not df.empty:
            date_col = next(
                (c for c in ("trade_date", "week_ending") if c in df.columns), None,
            )
            if date_col is not None:
                ds = df[date_col].astype(str)
                date_min, date_max = ds.min(), ds.max()
            elif isinstance(df.index, pd.DatetimeIndex) and len(df.index) > 0:
                date_min = df.index.min().strftime("%Y%m%d")
                date_max = df.index.max().strftime("%Y%m%d")
        self._access_log.append(AccessRecord(
            method=method, fields=tuple(fields), rows=len(df),
            date_min=date_min, date_max=date_max,
        ))

    # ── query surface ──

    def daily_bars(self, fields: Sequence[str], lookback: int | None = None) -> pd.DataFrame:
        self._check_datasets("daily_bars")
        self._check_fields(("ts_code", "trade_date", *fields))
        lookback = self._check_lookback("daily_bars", lookback, unit_days=1)
        cols = ["ts_code", "trade_date", *[f for f in fields if f not in ("ts_code", "trade_date")]]
        df = self._causal_filter(self._fund_daily)
        df = df[[c for c in cols if c in df.columns]]
        df = self._tail_dates(df, lookback)
        self._audit("daily_bars", cols, df)
        return df.copy()

    def adjusted_closes(self, lookback: int | None = None) -> pd.DataFrame:
        self._check_datasets("adjusted_closes")
        self._check_fields(self._METHOD_FIELDS["adjusted_closes"])
        lookback = self._check_lookback("adjusted_closes", lookback, unit_days=1)
        signal_str = self._signal_date.strftime("%Y%m%d")
        adj_close = compute_adjusted_close(self._fund_daily, self._fund_adj, signal_str)
        if adj_close.empty:
            self._audit("adjusted_closes", self._METHOD_FIELDS["adjusted_closes"], adj_close)
            return adj_close.copy()
        # Keep the default strategy surface bounded to the signal-date universe.
        cols = [c for c in adj_close.columns if str(c) in self._universe_codes]
        adj_close = adj_close[cols]
        if lookback == 0:
            adj_close = adj_close.iloc[0:0]
        elif lookback is not None:
            adj_close = adj_close.iloc[-lookback:]
        self._audit(
            "adjusted_closes",
            self._METHOD_FIELDS["adjusted_closes"],
            adj_close.reset_index(),
        )
        return adj_close.copy()

    def returns(
        self,
        frequency: Literal["daily", "weekly", "monthly"],
        lookback: int,
    ) -> pd.DataFrame:
        self._check_datasets("returns")
        self._check_fields(self._METHOD_FIELDS["returns"])
        unit_days = {"daily": 1, "weekly": 5, "monthly": 21}[frequency]
        lookback = self._check_lookback("returns", lookback, unit_days=unit_days)
        signal_str = self._signal_date.strftime("%Y%m%d")
        # Keep the default strategy surface bounded to the signal-date universe.
        current_codes = self._universe_codes
        if frequency == "weekly":
            rets = compute_weekly_returns(self._fund_daily, self._fund_adj, signal_str)
            rets = rets[[c for c in rets.columns if str(c) in current_codes]]
        else:
            adj_close = compute_adjusted_close(self._fund_daily, self._fund_adj, signal_str)
            if adj_close.empty:
                self._audit("returns", self._METHOD_FIELDS["returns"], adj_close)
                return adj_close.copy()
            cols = [c for c in adj_close.columns if str(c) in current_codes]
            adj_close = adj_close[cols]
        if frequency == "daily":
            rets = adj_close.pct_change(fill_method=None)
        elif frequency == "weekly":
            pass
        elif frequency == "monthly":
            monthly = adj_close.copy()
            monthly.index = pd.to_datetime(monthly.index, format="%Y%m%d")
            try:
                monthly_close = monthly.resample("ME").last()  # pandas >= 2.2
            except ValueError:  # pragma: no cover - pandas 2.0/2.1 alias
                monthly_close = monthly.resample("M").last()
            rets = monthly_close.pct_change(fill_method=None)
        else:  # pragma: no cover - guarded by Literal
            raise UndeclaredStrategyDataAccess(f"unknown frequency {frequency!r}")
        if lookback == 0:
            rets = rets.iloc[0:0]
        else:
            rets = rets.iloc[-lookback:]
        self._audit("returns", self._METHOD_FIELDS["returns"], rets.reset_index())
        return rets.copy()

    def causal_adv(self, lookback_days: int = 20) -> pd.Series:
        """Causal average daily turnover (amount) per ETF, using only completed
        trading days strictly before the signal date."""
        self._check_datasets("causal_adv")
        self._check_fields(self._METHOD_FIELDS["causal_adv"])
        lookback_days = self._check_lookback("causal_adv", lookback_days, unit_days=1)
        df = self._causal_filter(self._fund_daily)
        if "amount" not in df.columns or df.empty:
            self._audit("causal_adv", self._METHOD_FIELDS["causal_adv"], df)
            return pd.Series(dtype=float, name="adv")
        signal_str = self._signal_date.strftime("%Y%m%d")
        # Strictly before signal date (completed days only).
        df = df[df["trade_date"].astype(str) < signal_str]
        df = self._tail_dates(df, lookback_days)
        adv = df.groupby("ts_code")["amount"].mean()
        adv.name = "adv"
        self._audit("causal_adv", self._METHOD_FIELDS["causal_adv"], df)
        return adv.copy()

    def fund_adjustments(self, lookback: int | None = None) -> pd.DataFrame:
        self._check_datasets("fund_adjustments")
        self._check_fields(self._METHOD_FIELDS["fund_adjustments"])
        lookback = self._check_lookback("fund_adjustments", lookback, unit_days=1)
        df = self._causal_filter(self._fund_adj)
        df = self._tail_dates(df, lookback)
        self._audit(
            "fund_adjustments", self._METHOD_FIELDS["fund_adjustments"], df,
        )
        return df.copy()

    def eligible_universe(self) -> tuple[FundInstrument, ...]:
        self._check_datasets("eligible_universe")
        self._check_fields(self._METHOD_FIELDS["eligible_universe"])
        dim = self._dim_fund
        dim = dim[dim["ts_code"].astype(str).isin(self._universe_codes)]
        list_dates = pd.to_datetime(dim["list_date"], errors="coerce")
        dim = dim[list_dates.notna() & (list_dates <= self._signal_date)]
        instruments = tuple(
            FundInstrument(
                ts_code=str(row["ts_code"]),
                name=str(row.get("name", "")),
                list_date=str(row.get("list_date", "")),
            )
            for _, row in dim.iterrows()
        )
        self._audit(
            "eligible_universe", self._METHOD_FIELDS["eligible_universe"], dim,
        )
        return instruments

    def trading_calendar(self, lookback: int | None = None) -> tuple[pd.Timestamp, ...]:
        self._check_datasets("trading_calendar")
        self._check_fields(self._METHOD_FIELDS["trading_calendar"])
        lookback = self._check_lookback("trading_calendar", lookback, unit_days=1)
        df = self._causal_filter(self._fund_daily)
        dates = sorted({str(d) for d in df["trade_date"]}) if not df.empty else []
        if lookback == 0:
            dates = []
        elif lookback is not None:
            dates = dates[-lookback:]
        self._audit("trading_calendar", self._METHOD_FIELDS["trading_calendar"], df)
        return tuple(pd.Timestamp(d) for d in dates)
