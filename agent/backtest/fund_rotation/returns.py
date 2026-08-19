"""Weekly returns computation — §9.1.

Adjusted prices use fund_adj factors; weekly observation point is the
last actual trading day of each ISO week. PIT: only data <= as_of_date.
"""

from __future__ import annotations

import pandas as pd


def compute_adjusted_close(
    fund_daily: pd.DataFrame,
    fund_adj: pd.DataFrame,
    as_of_date: str,
) -> pd.DataFrame:
    """Compute backward-adjusted close prices for all ETFs with adj data.

    adjusted_close = close * adj_factor / latest_adj_factor_per_etf

    Args:
        fund_daily: Columns [ts_code, trade_date, close].
        fund_adj: Columns [ts_code, trade_date, adj_factor].
        as_of_date: PIT cutoff (YYYYMMDD). Only rows <= this date are used.

    Returns:
        DataFrame with MultiIndex (trade_date) and columns = ts_codes,
        values = adjusted close. ETFs without adj data are excluded.
    """
    if fund_adj.empty:
        return pd.DataFrame()

    # PIT filter
    daily = fund_daily[fund_daily["trade_date"].astype(str) <= as_of_date].copy()
    adj = fund_adj[fund_adj["trade_date"].astype(str) <= as_of_date].copy()

    if daily.empty or adj.empty:
        return pd.DataFrame()

    # Only keep ETFs that have adj records
    adj_codes = set(adj["ts_code"].astype(str).unique())
    daily = daily[daily["ts_code"].astype(str).isin(adj_codes)]
    if daily.empty:
        return pd.DataFrame()

    # Merge close with adj_factor
    daily["ts_code"] = daily["ts_code"].astype(str)
    daily["trade_date"] = daily["trade_date"].astype(str)
    adj["ts_code"] = adj["ts_code"].astype(str)
    adj["trade_date"] = adj["trade_date"].astype(str)

    merged = daily.merge(adj, on=["ts_code", "trade_date"], how="inner")
    if merged.empty:
        return pd.DataFrame()

    # Latest adj_factor per ETF (as of as_of_date)
    latest_idx = merged.groupby("ts_code")["trade_date"].transform("max")
    latest_factors = merged.loc[merged["trade_date"] == latest_idx].groupby("ts_code")["adj_factor"].last()

    # Compute adjusted close
    merged["latest_factor"] = merged["ts_code"].map(latest_factors)
    merged["adj_close"] = merged["close"] * merged["adj_factor"] / merged["latest_factor"]

    # Pivot to wide format: index=trade_date, columns=ts_code
    result = merged.pivot_table(index="trade_date", columns="ts_code", values="adj_close", aggfunc="last")
    result = result.sort_index()
    return result


def compute_weekly_returns(
    fund_daily: pd.DataFrame,
    fund_adj: pd.DataFrame,
    as_of_date: str,
) -> pd.DataFrame:
    """Compute weekly returns from adjusted close prices.

    Weekly observation point = last actual trading day in each ISO week.
    Return = adj_close[week_end] / adj_close[prev_week_end] - 1.

    Args:
        fund_daily: Columns [ts_code, trade_date, close].
        fund_adj: Columns [ts_code, trade_date, adj_factor].
        as_of_date: PIT cutoff (YYYYMMDD).

    Returns:
        DataFrame with index named 'week_ending' (YYYYMMDD strings),
        columns = ts_codes, values = weekly returns.
        First row is NaN (no prior week to compare).
    """
    adj_close = compute_adjusted_close(fund_daily, fund_adj, as_of_date)
    if adj_close.empty:
        return pd.DataFrame()

    # Convert index to datetime for week grouping
    dates = pd.to_datetime(adj_close.index, format="%Y%m%d")
    adj_close = adj_close.copy()
    adj_close.index = dates

    # Group by ISO year-week, take last trading day of each week
    iso = dates.isocalendar()
    week_key = list(zip(iso["year"].values, iso["week"].values))
    adj_close["_week_key"] = week_key

    # For each week, find the last trading day
    week_endings: dict[tuple[int, int], str] = {}
    for dt, wk in zip(dates, week_key):
        existing = week_endings.get(wk)
        dt_str = dt.strftime("%Y%m%d")
        if existing is None or dt_str > existing:
            week_endings[wk] = dt_str

    # Keep only week-ending rows
    ending_dates_set = set(week_endings.values())
    weekly_close = adj_close[adj_close.index.strftime("%Y%m%d").isin(ending_dates_set)].copy()
    weekly_close = weekly_close.drop(columns=["_week_key"], errors="ignore")
    weekly_close = weekly_close.sort_index()

    # Rename index to week_ending strings
    weekly_close.index = weekly_close.index.strftime("%Y%m%d")
    weekly_close.index.name = "week_ending"

    # Compute returns (fill_method=None: never forward-fill missing prices
    # before differencing, so a price gap yields NaN rather than a spurious
    # cross-gap return — design §6/§32.1, robust across pandas versions).
    weekly_returns = weekly_close.pct_change(fill_method=None)
    return weekly_returns
