"""Benchmarks — §14.1. Three fixed benchmarks for every run."""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def compute_equal_weight_etf_index(
    weekly_returns: pd.DataFrame,
    eligible_per_week: dict[str, list[str]] | None = None,
) -> pd.Series:
    """§14.1.1 — Dynamic equal-weight ETF total return index (no costs).

    Args:
        weekly_returns: Wide DataFrame (rows=week_ending, cols=ts_codes).
        eligible_per_week: Optional week_ending -> eligible codes filter.
            If None, use all non-NaN codes each week.

    Returns:
        Series indexed by week_ending with cumulative index values (start=1.0).
    """
    if weekly_returns.empty:
        return pd.Series(dtype=float)

    if eligible_per_week:
        # Mask non-eligible to NaN
        masked = weekly_returns.copy()
        for week, codes in eligible_per_week.items():
            if week in masked.index:
                non_eligible = [c for c in masked.columns if c not in codes]
                masked.loc[week, non_eligible] = np.nan
        weekly_mean = masked.mean(axis=1, skipna=True)
    else:
        weekly_mean = weekly_returns.mean(axis=1, skipna=True)

    weekly_mean = weekly_mean.fillna(0.0)
    cumulative = (1.0 + weekly_mean).cumprod()
    cumulative.name = "equal_weight_etf"
    return cumulative


def compute_buy_and_hold(
    weekly_returns: pd.DataFrame,
    code: str = "510300.SH",
    commission_rate: float = 0.0,
) -> pd.Series:
    """§14.1.2 — Buy-and-hold benchmark with one-time entry commission.

    Args:
        weekly_returns: Wide DataFrame with the benchmark code as a column.
        code: Benchmark ETF code.
        commission_rate: One-time commission cost applied at entry (e.g. 0.00025).

    Returns:
        Cumulative return series (start=1.0 - commission). NaN weeks treated as 0.
    """
    if code not in weekly_returns.columns:
        return pd.Series(dtype=float)

    returns = weekly_returns[code].fillna(0.0)
    cumulative = (1.0 + returns).cumprod()
    # Apply one-time entry commission: start at (1 - commission) instead of 1.0
    if commission_rate > 0:
        cumulative = cumulative * (1.0 - commission_rate)
    cumulative.name = code
    return cumulative


def compute_cash_benchmark(weeks: list[str] | pd.Index) -> pd.Series:
    """§14.1.3 — Cash zero-return benchmark (constant 1.0)."""
    return pd.Series(1.0, index=weeks, name="cash")


def check_common_coverage(
    strategy_weeks: pd.Index,
    benchmark_returns: pd.Series,
) -> bool:
    """§14.1 — Verify at least some common coverage with 510300.SH."""
    if benchmark_returns.empty:
        return False
    common = strategy_weeks.intersection(benchmark_returns.index)
    return len(common) > 0


def _mean_with_missing_policy(member_returns: pd.Series) -> float:
    """Equal-weight mean with explicit NaN policy.

    NaN member returns are treated as 0 (hold unchanged), NOT implicitly
    redistributed to other members via skipna.

    Returns:
        Mean return across all members (NaN filled with 0).
        Returns 0.0 if all members are missing or series is empty.
    """
    if member_returns.empty:
        return 0.0
    filled = member_returns.fillna(0.0)
    n_total = len(member_returns)
    n_missing = int(member_returns.isna().sum())
    if n_missing == n_total:
        logger.warning(
            "All %d members missing returns at this week; index_return=0",
            n_total,
        )
        return 0.0
    return float(filled.mean())


def compute_equal_weight_theoretical_index(
    weekly_returns: pd.DataFrame,
    eligible_per_week: dict[str, list[str]],
    benchmark_weeks: list[str],
    common_start: str,
) -> pd.Series:
    """§14.1.1 — Dynamic equal-weight theoretical total return index (no costs).

    Correct timing: portfolio formed at signal week t earns returns at t+1.
    This avoids look-ahead bias where eligible list at t would be paired
    with already-realized returns at t.

    Args:
        weekly_returns: Wide DataFrame (index=week_ending, cols=ts_codes).
            weekly_returns[t] = close[t]/close[t-1] - 1 (historical).
        eligible_per_week: signal_week -> eligible ETF codes at that week's close.
        benchmark_weeks: Sorted signal weeks >= start_date (trimmed, no training).
        common_start: First date of the common executable interval (YYYYMMDD).
            NAV = 1.0 is anchored here.

    Returns:
        Series indexed by week_ending dates with cumulative NAV (start=1.0).
        Only contains values at week_ending dates where returns are realized.
    """
    if weekly_returns.empty or not benchmark_weeks:
        return pd.Series(dtype=float, name="equal_weight_etf")

    all_weeks_sorted = sorted(weekly_returns.index)
    # Map each week to the next week in the returns index
    week_to_next: dict[str, str] = {}
    for i, wk in enumerate(all_weeks_sorted):
        if i + 1 < len(all_weeks_sorted):
            week_to_next[wk] = all_weeks_sorted[i + 1]

    # Find the first week_ending >= common_start for gating
    common_start_week = next(
        (wk for wk in all_weeks_sorted if wk >= common_start), None
    )

    nav = 1.0
    index_values: dict[str, float] = {}
    # Anchor NAV = 1.0 at common_start for forward-fill alignment
    index_values[common_start] = 1.0

    for signal_week in sorted(benchmark_weeks):
        members = eligible_per_week.get(signal_week, [])
        if not members:
            continue
        next_week = week_to_next.get(signal_week)
        if next_week is None:
            continue
        # Only apply returns at or after common_start_week
        if common_start_week is not None and next_week < common_start_week:
            continue

        # Get member returns at next_week (the period t -> t+1)
        if next_week not in weekly_returns.index:
            continue
        member_returns = weekly_returns.loc[next_week, members]
        if isinstance(member_returns, pd.DataFrame):
            # Duplicate columns edge case: take first row
            member_returns = member_returns.iloc[0]
        index_return = _mean_with_missing_policy(member_returns)
        nav *= (1.0 + index_return)
        index_values[next_week] = nav

    if not index_values:
        return pd.Series(dtype=float, name="equal_weight_etf")

    result = pd.Series(index_values, name="equal_weight_etf", dtype=float)
    result = result.sort_index()
    return result
