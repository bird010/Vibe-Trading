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

    def test_53_weekend_prices_form_52_valid_returns(self):
        """§32.1 — 53 consecutive valid weekend prices form exactly 52 weekly returns."""
        start = pd.Timestamp("2022-01-07")  # a Friday
        rows, adj_rows = [], []
        price = 100.0
        for w in range(53):
            friday = start + pd.Timedelta(weeks=w)
            for offset in range(5):  # Mon-Fri
                d = (friday - pd.Timedelta(days=4) + pd.Timedelta(days=offset)).strftime("%Y%m%d")
                price *= 1.01
                rows.append({"ts_code": "A", "trade_date": d, "close": round(price, 3)})
                adj_rows.append({"ts_code": "A", "trade_date": d, "adj_factor": 1.0})
        daily = _fund_daily_df(rows)
        adj = _fund_adj_df(adj_rows)
        as_of = (start + pd.Timedelta(weeks=52)).strftime("%Y%m%d")
        result = compute_weekly_returns(daily, adj, as_of_date=as_of)
        # 53 week-endings -> 53 rows, the first is NaN -> exactly 52 valid returns.
        assert len(result) == 53
        assert len(result["A"].dropna()) == 52
        # The first (earliest) week-ending has no prior price -> NaN return.
        assert np.isnan(result["A"].iloc[0])


def _spy_pct_change(monkeypatch):
    """Record the fill_method kwarg of every DataFrame/Series pct_change call.

    A call that omits fill_method is recorded as the sentinel "ABSENT" so the
    contract test can distinguish "not passed" from "passed as None".
    """
    calls: list = []
    orig_df = pd.DataFrame.pct_change
    orig_series = pd.Series.pct_change

    def df_spy(self, *args, **kwargs):
        calls.append(kwargs.get("fill_method", "ABSENT"))
        return orig_df(self, *args, **kwargs)

    def series_spy(self, *args, **kwargs):
        calls.append(kwargs.get("fill_method", "ABSENT"))
        return orig_series(self, *args, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "pct_change", df_spy)
    monkeypatch.setattr(pd.Series, "pct_change", series_spy)
    return calls


class TestMissingReturnPolicy:
    """§6/§32.1 — missing prices must not be forward-filled before differencing."""

    def test_gap_yields_nan_not_cross_gap_return(self):
        """A missing week-ending close produces NaN returns, not a spurious jump.

        Two ETFs are used so the week with A's missing close is retained in the
        wide frame (B has a value there); A's weekly close is NaN that week, and
        the return after the gap must stay NaN rather than a forward-filled
        cross-gap value.
        """
        daily = _fund_daily_df([
            {"ts_code": "A", "trade_date": "20240105", "close": 100.0},
            {"ts_code": "A", "trade_date": "20240112", "close": float("nan")},  # missing
            {"ts_code": "A", "trade_date": "20240119", "close": 120.0},
            {"ts_code": "B", "trade_date": "20240105", "close": 50.0},
            {"ts_code": "B", "trade_date": "20240112", "close": 55.0},
            {"ts_code": "B", "trade_date": "20240119", "close": 60.0},
        ])
        adj = _fund_adj_df([
            {"ts_code": "A", "trade_date": "20240105", "adj_factor": 1.0},
            {"ts_code": "A", "trade_date": "20240112", "adj_factor": 1.0},
            {"ts_code": "A", "trade_date": "20240119", "adj_factor": 1.0},
            {"ts_code": "B", "trade_date": "20240105", "adj_factor": 1.0},
            {"ts_code": "B", "trade_date": "20240112", "adj_factor": 1.0},
            {"ts_code": "B", "trade_date": "20240119", "adj_factor": 1.0},
        ])
        result = compute_weekly_returns(daily, adj, as_of_date="20240119")
        a_by_week = result["A"].to_dict()
        # The week after A's gap (20240119) must be NaN, NOT 120/100-1 = 0.2
        # (which a forward-fill would fabricate across the missing week).
        assert np.isnan(a_by_week["20240119"]), (
            f"expected NaN return after gap, got {a_by_week['20240119']}"
        )
        assert not np.isclose(a_by_week["20240119"], 120.0 / 100.0 - 1.0)
        # B has no gap: its return is well-defined.
        assert np.isclose(result["B"].to_dict()["20240119"], 60.0 / 55.0 - 1.0)

    def test_compute_weekly_returns_passes_fill_method_none(self, monkeypatch):
        """Contract: the returns path must explicitly pass fill_method=None."""
        calls = _spy_pct_change(monkeypatch)
        daily = _fund_daily_df([
            {"ts_code": "A", "trade_date": "20240105", "close": 100.0},
            {"ts_code": "A", "trade_date": "20240112", "close": 110.0},
        ])
        adj = _fund_adj_df([
            {"ts_code": "A", "trade_date": "20240105", "adj_factor": 1.0},
            {"ts_code": "A", "trade_date": "20240112", "adj_factor": 1.0},
        ])
        compute_weekly_returns(daily, adj, as_of_date="20240112")
        assert calls, "pct_change was not called by compute_weekly_returns"
        assert all(fm is None for fm in calls), (
            f"every pct_change must pass fill_method=None, got {calls}"
        )
