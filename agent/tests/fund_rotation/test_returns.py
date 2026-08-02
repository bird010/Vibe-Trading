"""Tests for weekly returns computation — §9.1."""

import numpy as np
import pandas as pd
import pytest

from backtest.fund_rotation.returns import (
    compute_adjusted_close,
    compute_weekly_returns,
)


def _fund_daily_df(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal fund daily DataFrame."""
    df = pd.DataFrame(rows)
    return df


def _fund_adj_df(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal fund_adj DataFrame."""
    return pd.DataFrame(rows)


class TestAdjustedClose:
    """§9.1 — adjusted price = close * adj_factor / latest_adj_factor."""

    def test_basic_adjustment(self):
        daily = _fund_daily_df([
            {"ts_code": "510300.SH", "trade_date": "20240101", "close": 4.0},
            {"ts_code": "510300.SH", "trade_date": "20240102", "close": 4.1},
            {"ts_code": "510300.SH", "trade_date": "20240103", "close": 4.2},
        ])
        adj = _fund_adj_df([
            {"ts_code": "510300.SH", "trade_date": "20240101", "adj_factor": 1.0},
            {"ts_code": "510300.SH", "trade_date": "20240102", "adj_factor": 1.0},
            {"ts_code": "510300.SH", "trade_date": "20240103", "adj_factor": 1.0},
        ])
        result = compute_adjusted_close(daily, adj, as_of_date="20240103")
        # With constant adj_factor, adjusted == raw close
        np.testing.assert_allclose(result["510300.SH"].values, [4.0, 4.1, 4.2])

    def test_factor_change_adjusts_history(self):
        """When adj_factor changes, earlier prices are scaled."""
        daily = _fund_daily_df([
            {"ts_code": "510300.SH", "trade_date": "20240101", "close": 4.0},
            {"ts_code": "510300.SH", "trade_date": "20240102", "close": 4.0},
        ])
        adj = _fund_adj_df([
            {"ts_code": "510300.SH", "trade_date": "20240101", "adj_factor": 1.0},
            {"ts_code": "510300.SH", "trade_date": "20240102", "adj_factor": 2.0},
        ])
        result = compute_adjusted_close(daily, adj, as_of_date="20240102")
        # latest factor = 2.0; day1: 4.0 * 1.0/2.0 = 2.0; day2: 4.0 * 2.0/2.0 = 4.0
        np.testing.assert_allclose(result["510300.SH"].values, [2.0, 4.0])

    def test_pit_truncation(self):
        """Only data up to as_of_date is visible."""
        daily = _fund_daily_df([
            {"ts_code": "510300.SH", "trade_date": "20240101", "close": 4.0},
            {"ts_code": "510300.SH", "trade_date": "20240102", "close": 4.1},
            {"ts_code": "510300.SH", "trade_date": "20240103", "close": 99.0},
        ])
        adj = _fund_adj_df([
            {"ts_code": "510300.SH", "trade_date": "20240101", "adj_factor": 1.0},
            {"ts_code": "510300.SH", "trade_date": "20240102", "adj_factor": 1.0},
            {"ts_code": "510300.SH", "trade_date": "20240103", "adj_factor": 1.0},
        ])
        result = compute_adjusted_close(daily, adj, as_of_date="20240102")
        assert len(result) == 2
        assert "20240103" not in result.index.get_level_values("trade_date").astype(str).values

    def test_missing_adj_excludes_etf(self):
        """ETF with no adj_factor records is excluded entirely."""
        daily = _fund_daily_df([
            {"ts_code": "510300.SH", "trade_date": "20240101", "close": 4.0},
            {"ts_code": "159915.SZ", "trade_date": "20240101", "close": 3.0},
        ])
        adj = _fund_adj_df([
            {"ts_code": "510300.SH", "trade_date": "20240101", "adj_factor": 1.0},
            # 159915.SZ has no adj records
        ])
        result = compute_adjusted_close(daily, adj, as_of_date="20240101")
        assert "510300.SH" in result.columns
        assert "159915.SZ" not in result.columns

    def test_multiple_etfs(self):
        daily = _fund_daily_df([
            {"ts_code": "510300.SH", "trade_date": "20240101", "close": 4.0},
            {"ts_code": "159915.SZ", "trade_date": "20240101", "close": 3.0},
        ])
        adj = _fund_adj_df([
            {"ts_code": "510300.SH", "trade_date": "20240101", "adj_factor": 1.0},
            {"ts_code": "159915.SZ", "trade_date": "20240101", "adj_factor": 1.5},
        ])
        result = compute_adjusted_close(daily, adj, as_of_date="20240101")
        assert set(result.columns) == {"510300.SH", "159915.SZ"}


class TestWeeklyReturns:
    """§9.1 — weekly frequency, last trading day of each week."""

    def test_basic_weekly_returns(self):
        """Two weeks of data -> one weekly return."""
        # Week 1: Mon-Fri = 2024-01-01 to 2024-01-05
        # Week 2: Mon-Fri = 2024-01-08 to 2024-01-12
        daily = _fund_daily_df([
            {"ts_code": "A", "trade_date": "20240101", "close": 100.0},
            {"ts_code": "A", "trade_date": "20240105", "close": 110.0},
            {"ts_code": "A", "trade_date": "20240108", "close": 115.0},
            {"ts_code": "A", "trade_date": "20240112", "close": 120.0},
        ])
        adj = _fund_adj_df([
            {"ts_code": "A", "trade_date": "20240101", "adj_factor": 1.0},
            {"ts_code": "A", "trade_date": "20240105", "adj_factor": 1.0},
            {"ts_code": "A", "trade_date": "20240108", "adj_factor": 1.0},
            {"ts_code": "A", "trade_date": "20240112", "adj_factor": 1.0},
        ])
        result = compute_weekly_returns(daily, adj, as_of_date="20240112")
        # Week ending 20240105: close=110, Week ending 20240112: close=120
        # Return = 120/110 - 1 = 0.0909...
        assert result.shape[0] >= 1
        last_return = result["A"].dropna().iloc[-1]
        np.testing.assert_allclose(last_return, 120.0 / 110.0 - 1.0, rtol=1e-10)

    def test_pit_cutoff_respected(self):
        """Data after as_of_date must not appear."""
        daily = _fund_daily_df([
            {"ts_code": "A", "trade_date": "20240105", "close": 100.0},
            {"ts_code": "A", "trade_date": "20240112", "close": 110.0},
            {"ts_code": "A", "trade_date": "20240119", "close": 999.0},
        ])
        adj = _fund_adj_df([
            {"ts_code": "A", "trade_date": "20240105", "adj_factor": 1.0},
            {"ts_code": "A", "trade_date": "20240112", "adj_factor": 1.0},
            {"ts_code": "A", "trade_date": "20240119", "adj_factor": 1.0},
        ])
        result = compute_weekly_returns(daily, adj, as_of_date="20240112")
        # Should not include week of 20240119
        dates = result.index.get_level_values("week_ending").astype(str)
        assert not any("20240119" in d for d in dates)

    def test_wide_format_multiple_etfs(self):
        """Returns are a wide DataFrame: rows=weeks, cols=ts_codes."""
        daily = _fund_daily_df([
            {"ts_code": "A", "trade_date": "20240105", "close": 100.0},
            {"ts_code": "A", "trade_date": "20240112", "close": 110.0},
            {"ts_code": "B", "trade_date": "20240105", "close": 50.0},
            {"ts_code": "B", "trade_date": "20240112", "close": 55.0},
        ])
        adj = _fund_adj_df([
            {"ts_code": "A", "trade_date": "20240105", "adj_factor": 1.0},
            {"ts_code": "A", "trade_date": "20240112", "adj_factor": 1.0},
            {"ts_code": "B", "trade_date": "20240105", "adj_factor": 1.0},
            {"ts_code": "B", "trade_date": "20240112", "adj_factor": 1.0},
        ])
        result = compute_weekly_returns(daily, adj, as_of_date="20240112")
        assert set(result.columns) == {"A", "B"}

    def test_no_adj_factor_returns_empty(self):
        """If no ETF has adj data, result is empty."""
        daily = _fund_daily_df([
            {"ts_code": "A", "trade_date": "20240105", "close": 100.0},
        ])
        adj = _fund_adj_df([])
        result = compute_weekly_returns(daily, adj, as_of_date="20240105")
        assert result.empty or len(result.columns) == 0

    def test_week_ending_is_last_trading_day(self):
        """Week observation point is the last actual trading day, not Friday."""
        # 20240105 is Friday, 20240104 is Thursday (both trading days)
        # If Friday is missing, Thursday becomes week-ending
        daily = _fund_daily_df([
            {"ts_code": "A", "trade_date": "20240101", "close": 100.0},
            {"ts_code": "A", "trade_date": "20240104", "close": 108.0},
            # No 20240105 (Friday holiday)
            {"ts_code": "A", "trade_date": "20240108", "close": 110.0},
            {"ts_code": "A", "trade_date": "20240112", "close": 120.0},
        ])
        adj = _fund_adj_df([
            {"ts_code": "A", "trade_date": "20240101", "adj_factor": 1.0},
            {"ts_code": "A", "trade_date": "20240104", "adj_factor": 1.0},
            {"ts_code": "A", "trade_date": "20240108", "adj_factor": 1.0},
            {"ts_code": "A", "trade_date": "20240112", "adj_factor": 1.0},
        ])
        result = compute_weekly_returns(daily, adj, as_of_date="20240112")
        # First week ending should be 20240104 (Thursday, last available)
        week_endings = result.index.get_level_values("week_ending").astype(str).tolist()
        assert "20240104" in week_endings
