"""Dual benchmark: target horizon and liquidation matched.

Implements design §13 and §27.11.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class BenchmarkResult:
    """Benchmark computation result."""

    benchmark_return: float | None
    entry_date: str = ""
    exit_date: str = ""


@dataclass(frozen=True)
class ExitEvent:
    """A proportional exit event for liquidation-matched benchmark."""

    date: str
    proportion: float  # fraction of total position exiting
    is_terminal: bool = False  # True if this is terminal valuation (not actual trade)


def _valid_adjusted_price(value: object) -> float | None:
    """Return a finite positive adjusted price, or None for invalid data."""
    price = pd.to_numeric(value, errors="coerce")
    if pd.isna(price) or not math.isfinite(float(price)) or float(price) <= 0:
        return None
    return float(price)


def compute_target_horizon_benchmark(
    *,
    index_market: pd.DataFrame,
    trade_dates: list[str],
    signal_date: str,
    holding_days: int,
    benchmark_code: str = "H00300.CSI",
) -> BenchmarkResult:
    """Fixed-horizon benchmark per §27.11.

    benchmark_return = adj_open(target_exit_date) / adj_open(T+1) - 1
    Does NOT follow strategy exit delays.
    """
    if index_market.empty:
        return BenchmarkResult(benchmark_return=None)

    # Find entry date (T+1)
    entry_idx = bisect.bisect_right(trade_dates, signal_date)
    if entry_idx >= len(trade_dates):
        return BenchmarkResult(benchmark_return=None)

    entry_date = trade_dates[entry_idx]

    # Target exit date
    exit_idx = entry_idx + holding_days
    if exit_idx >= len(trade_dates):
        return BenchmarkResult(benchmark_return=None)

    target_exit_date = trade_dates[exit_idx]

    # Get benchmark prices
    idx = index_market.copy()
    idx["trade_date"] = idx["trade_date"].astype(str)
    if "adj_open" not in idx.columns:
        return BenchmarkResult(benchmark_return=None, entry_date=entry_date, exit_date=target_exit_date)

    entry_row = idx[idx["trade_date"] == entry_date]
    exit_row = idx[idx["trade_date"] == target_exit_date]

    if entry_row.empty or exit_row.empty:
        return BenchmarkResult(benchmark_return=None, entry_date=entry_date, exit_date=target_exit_date)

    entry_price = _valid_adjusted_price(entry_row.iloc[0]["adj_open"])
    exit_price = _valid_adjusted_price(exit_row.iloc[0]["adj_open"])

    if entry_price is None or exit_price is None:
        return BenchmarkResult(benchmark_return=None, entry_date=entry_date, exit_date=target_exit_date)

    ret = exit_price / entry_price - 1.0
    return BenchmarkResult(
        benchmark_return=ret, entry_date=entry_date, exit_date=target_exit_date
    )


def compute_liquidation_matched_benchmark(
    *,
    index_market: pd.DataFrame,
    trade_dates: list[str],
    entry_date: str,
    exit_events: list[ExitEvent],
    benchmark_code: str = "H00300.CSI",
) -> BenchmarkResult:
    """Cash-flow matched benchmark per §27.11.

    Virtual benchmark position established at same time as strategy.
    Redeemed proportionally on each exit date.
    Residual valued at terminal date.
    """
    if index_market.empty:
        return BenchmarkResult(benchmark_return=None)

    idx = index_market.copy()
    idx["trade_date"] = idx["trade_date"].astype(str)
    if "adj_open" not in idx.columns:
        return BenchmarkResult(benchmark_return=None, entry_date=entry_date)

    # Get entry price
    entry_row = idx[idx["trade_date"] == entry_date]
    if entry_row.empty:
        return BenchmarkResult(benchmark_return=None, entry_date=entry_date)

    entry_price = _valid_adjusted_price(entry_row.iloc[0]["adj_open"])
    if entry_price is None:
        return BenchmarkResult(benchmark_return=None, entry_date=entry_date)

    # A cohort with no actual investment has a valid, all-cash matched
    # benchmark return of zero. This is distinct from unavailable index data.
    if not exit_events:
        return BenchmarkResult(benchmark_return=0.0, entry_date=entry_date)

    # Compute weighted return across exit events
    # Proportions represent exit_value / committed_capital, sum should be <= 1.0
    # (remainder is idle cash with zero return)
    weighted_return = 0.0
    total_proportion = 0.0

    for exit_event in exit_events:
        # Cap cumulative proportion at 1.0 to prevent amplification
        effective_proportion = min(exit_event.proportion, max(0.0, 1.0 - total_proportion))
        if effective_proportion <= 0:
            break

        exit_row = idx[idx["trade_date"] == exit_event.date]
        if exit_row.empty:
            return BenchmarkResult(benchmark_return=None, entry_date=entry_date)

        exit_price = _valid_adjusted_price(exit_row.iloc[0]["adj_open"])
        if exit_price is None:
            return BenchmarkResult(benchmark_return=None, entry_date=entry_date)
        ret = exit_price / entry_price - 1.0
        weighted_return += effective_proportion * ret
        total_proportion += effective_proportion

    if total_proportion <= 0:
        return BenchmarkResult(benchmark_return=None, entry_date=entry_date)

    return BenchmarkResult(
        benchmark_return=weighted_return,
        entry_date=entry_date,
        exit_date=exit_events[-1].date if exit_events else "",
    )
