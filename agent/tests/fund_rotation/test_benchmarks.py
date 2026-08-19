"""Tests for benchmarks and metrics — §14."""

import numpy as np
import pandas as pd
import pytest

from backtest.fund_rotation.benchmarks import (
    compute_equal_weight_etf_index,
    compute_equal_weight_theoretical_index,
    compute_buy_and_hold,
    compute_cash_benchmark,
    check_common_coverage,
    _mean_with_missing_policy,
)
from backtest.fund_rotation.metrics import (
    compute_performance_metrics,
    compute_execution_diagnostics,
)


class TestEqualWeightIndex:
    """§14.1.1 — dynamic equal-weight ETF index."""

    def test_basic_cumulative(self):
        returns = pd.DataFrame({
            "A": [0.01, 0.02],
            "B": [0.03, 0.01],
        }, index=["20240105", "20240112"])
        idx = compute_equal_weight_etf_index(returns)
        # Week 1: mean(0.01, 0.03) = 0.02 -> 1.02
        # Week 2: mean(0.02, 0.01) = 0.015 -> 1.02 * 1.015
        assert idx.iloc[0] == pytest.approx(1.02)
        assert idx.iloc[1] == pytest.approx(1.02 * 1.015)

    def test_nan_excluded_from_mean(self):
        returns = pd.DataFrame({
            "A": [0.02, 0.01],
            "B": [np.nan, 0.03],
        }, index=["20240105", "20240112"])
        idx = compute_equal_weight_etf_index(returns)
        # Week 1: only A -> 0.02
        assert idx.iloc[0] == pytest.approx(1.02)


class TestBuyAndHold:
    """§14.1.2 — 510300.SH buy and hold."""

    def test_basic(self):
        returns = pd.DataFrame({
            "510300.SH": [0.01, 0.02, -0.01],
        }, index=["20240105", "20240112", "20240119"])
        idx = compute_buy_and_hold(returns, "510300.SH")
        expected = 1.01 * 1.02 * 0.99
        assert idx.iloc[-1] == pytest.approx(expected)

    def test_missing_code_empty(self):
        returns = pd.DataFrame({"A": [0.01]}, index=["20240105"])
        idx = compute_buy_and_hold(returns, "510300.SH")
        assert idx.empty


class TestCashBenchmark:
    """§14.1.3 — constant 1.0."""

    def test_all_ones(self):
        weeks = ["20240105", "20240112", "20240119"]
        idx = compute_cash_benchmark(weeks)
        assert all(v == 1.0 for v in idx.values)
        assert len(idx) == 3


class TestCommonCoverage:
    """§14.1 — must have common coverage."""

    def test_has_coverage(self):
        weeks = pd.Index(["20240105", "20240112"])
        bench = pd.Series([1.0, 1.01], index=["20240105", "20240112"])
        assert check_common_coverage(weeks, bench) is True

    def test_no_coverage(self):
        weeks = pd.Index(["20240105"])
        bench = pd.Series([1.0], index=["20250101"])
        assert check_common_coverage(weeks, bench) is False

    def test_empty_benchmark(self):
        weeks = pd.Index(["20240105"])
        bench = pd.Series(dtype=float)
        assert check_common_coverage(weeks, bench) is False


class TestPerformanceMetrics:
    """§14.2 — return and risk metrics."""

    def test_flat_series_zero_metrics(self):
        cumulative = pd.Series([1.0, 1.0, 1.0])
        m = compute_performance_metrics(cumulative)
        assert m["annual_return"] == pytest.approx(0.0, abs=1e-9)
        assert m["max_drawdown"] == pytest.approx(0.0, abs=1e-9)

    def test_positive_return(self):
        # Varying positive returns -> positive Sharpe
        np.random.seed(42)
        weekly_rets = np.random.normal(0.005, 0.01, 52)  # mean 0.5%, std 1%
        cumulative = pd.Series(np.cumprod(1.0 + weekly_rets))
        cumulative = pd.concat([pd.Series([1.0]), cumulative]).reset_index(drop=True)
        m = compute_performance_metrics(cumulative, periods_per_year=52)
        assert m["annual_return"] > 0
        assert m["sharpe"] > 0

    def test_max_drawdown(self):
        cumulative = pd.Series([1.0, 1.2, 0.9, 1.1])
        m = compute_performance_metrics(cumulative)
        # DD from 1.2 to 0.9 = -25%
        assert m["max_drawdown"] == pytest.approx(-0.25, rel=1e-6)

    def test_empty_series(self):
        m = compute_performance_metrics(pd.Series(dtype=float))
        assert m["sharpe"] == 0.0


class TestExecutionDiagnostics:
    """§14.2 — execution diagnostics."""

    def test_basic_diagnostics(self):
        events = [
            {"action": "BUY", "filled": 1000, "price": 5.0, "commission": 5.0, "status": "FILLED", "requested": 1000},
            {"action": "SELL", "filled": 500, "price": 6.0, "commission": 5.0, "status": "FILLED", "requested": 500},
            {"action": "BUY", "filled": 0, "price": 0, "commission": 0, "status": "BLOCKED", "requested": 200},
        ]
        d = compute_execution_diagnostics(events, initial_capital=100_000.0)
        assert d["total_buy_notional"] == pytest.approx(5000.0)
        assert d["total_sell_notional"] == pytest.approx(3000.0)
        assert d["total_commission"] == pytest.approx(10.0)
        assert d["blocked_count"] == 1
        assert d["fill_rate"] == pytest.approx(1500 / 1700)

    def test_empty_events(self):
        d = compute_execution_diagnostics([], initial_capital=100_000.0)
        assert d["num_trades"] == 0
        assert d["turnover"] == 0.0


class TestMeanWithMissingPolicy:
    """NaN policy: fill with 0, not skipna redistribution."""

    def test_no_missing(self):
        s = pd.Series([0.02, 0.04])
        assert _mean_with_missing_policy(s) == pytest.approx(0.03)

    def test_partial_missing_fills_zero(self):
        # A=0.02, B=NaN -> filled=[0.02, 0.0] -> mean=0.01
        s = pd.Series([0.02, np.nan])
        assert _mean_with_missing_policy(s) == pytest.approx(0.01)

    def test_all_missing_returns_zero(self):
        s = pd.Series([np.nan, np.nan])
        assert _mean_with_missing_policy(s) == 0.0

    def test_empty_returns_zero(self):
        s = pd.Series(dtype=float)
        assert _mean_with_missing_policy(s) == 0.0


class TestEqualWeightTheoreticalIndex:
    """§14.1.1 — theoretical index with correct t/t+1 timing."""

    def test_one_period_shift(self):
        """Portfolio formed at week t earns returns at week t+1."""
        # 5 weeks of returns for 3 ETFs
        weekly_returns = pd.DataFrame({
            "A": [0.01, 0.02, 0.03, 0.04, 0.05],
            "B": [0.02, 0.01, 0.02, 0.03, 0.01],
            "C": [0.03, 0.03, 0.01, 0.02, 0.02],
        }, index=["20240105", "20240112", "20240119", "20240126", "20240202"])
        # Signal at week 1 -> earns week 2 returns
        eligible = {"20240105": ["A", "B", "C"]}
        result = compute_equal_weight_theoretical_index(
            weekly_returns, eligible,
            benchmark_weeks=["20240105"],
            common_start="20240105",
        )
        # NAV at common_start = 1.0
        # At week 20240112: mean(0.02, 0.01, 0.03) = 0.02 -> NAV = 1.02
        assert result.loc["20240105"] == pytest.approx(1.0)
        assert result.loc["20240112"] == pytest.approx(1.02)
        assert len(result) == 2  # common_start + one return period

    def test_multiple_periods_cumulative(self):
        weekly_returns = pd.DataFrame({
            "A": [0.01, 0.02, 0.03, 0.04],
            "B": [0.03, 0.01, 0.02, 0.01],
        }, index=["20240105", "20240112", "20240119", "20240126"])
        eligible = {
            "20240105": ["A", "B"],
            "20240112": ["A", "B"],
        }
        result = compute_equal_weight_theoretical_index(
            weekly_returns, eligible,
            benchmark_weeks=["20240105", "20240112"],
            common_start="20240105",
        )
        # Signal 20240105 -> earns 20240112: mean(0.02, 0.01) = 0.015 -> 1.015
        # Signal 20240112 -> earns 20240119: mean(0.03, 0.02) = 0.025 -> 1.015 * 1.025
        assert result.loc["20240112"] == pytest.approx(1.015)
        assert result.loc["20240119"] == pytest.approx(1.015 * 1.025)

    def test_missing_member_treated_as_zero(self):
        weekly_returns = pd.DataFrame({
            "A": [0.01, 0.04],
            "B": [0.03, np.nan],  # B missing at week 2
        }, index=["20240105", "20240112"])
        eligible = {"20240105": ["A", "B"]}
        result = compute_equal_weight_theoretical_index(
            weekly_returns, eligible,
            benchmark_weeks=["20240105"],
            common_start="20240105",
        )
        # mean(0.04, 0.0) = 0.02 (NaN filled with 0, not skipna)
        assert result.loc["20240112"] == pytest.approx(1.02)

    def test_all_members_missing_nav_unchanged(self):
        weekly_returns = pd.DataFrame({
            "A": [0.01, np.nan],
            "B": [0.03, np.nan],
        }, index=["20240105", "20240112"])
        eligible = {"20240105": ["A", "B"]}
        result = compute_equal_weight_theoretical_index(
            weekly_returns, eligible,
            benchmark_weeks=["20240105"],
            common_start="20240105",
        )
        # All missing -> return = 0 -> NAV unchanged
        assert result.loc["20240112"] == pytest.approx(1.0)

    def test_empty_benchmark_weeks(self):
        weekly_returns = pd.DataFrame({"A": [0.01]}, index=["20240105"])
        result = compute_equal_weight_theoretical_index(
            weekly_returns, {}, benchmark_weeks=[], common_start="20240105",
        )
        assert result.empty

    def test_initial_nav_is_one(self):
        weekly_returns = pd.DataFrame({
            "A": [0.01, 0.02, 0.03],
        }, index=["20240105", "20240112", "20240119"])
        eligible = {"20240105": ["A"]}
        result = compute_equal_weight_theoretical_index(
            weekly_returns, eligible,
            benchmark_weeks=["20240105"],
            common_start="20240105",
        )
        assert result.iloc[0] == pytest.approx(1.0)
