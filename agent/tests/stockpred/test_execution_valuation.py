"""Tests for valuation policy: horizon mark, stale price, terminal haircut."""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.stockpred.execution.valuation import (
    TerminalValuation,
    ValuationPolicy,
    ValuationResult,
    compute_liquidity_haircut_rate,
)


def _market(days: int = 30, close: float = 10.0) -> pd.DataFrame:
    dates = [f"202501{d:02d}" for d in range(1, days + 1)]
    n = len(dates)
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ"] * n,
            "trade_date": dates,
            "open": [close] * n,
            "close": [close] * n,
            "adj_open": [close] * n,
            "adj_close": [close] * n,
            "vol": [10000.0] * n,
        }
    )


class TestHorizonMark:
    @pytest.mark.parametrize("invalid_price", [float("nan"), float("inf"), 0.0, -1.0])
    def test_invalid_adjusted_target_open_is_quality_failure(self, invalid_price: float):
        mkt = _market(30, close=10.0)
        mkt.loc[mkt["trade_date"] == "20250116", "adj_open"] = invalid_price

        result = ValuationPolicy(stale_price_limit_days=5).horizon_mark(
            "000001.SZ", "20250116", mkt
        )

        assert result.quality_failure

    def test_uses_target_exit_date_open(self):
        mkt = _market(30, close=10.0)
        # Make day 16 have different price
        mkt.loc[mkt["trade_date"] == "20250116", "adj_open"] = 12.0
        policy = ValuationPolicy(stale_price_limit_days=5)

        result = policy.horizon_mark("000001.SZ", "20250116", mkt)

        assert result.price == pytest.approx(12.0)
        assert result.stale_days == 0
        assert not result.quality_failure

    def test_stale_price_when_no_data_on_target(self):
        mkt = _market(30, close=10.0)
        # Remove day 16 data
        mkt = mkt[mkt["trade_date"] != "20250116"]
        policy = ValuationPolicy(stale_price_limit_days=5)

        result = policy.horizon_mark("000001.SZ", "20250116", mkt)

        # Should use last valid (day 15)
        assert result.price == pytest.approx(10.0)
        assert result.stale_days == 1
        assert not result.quality_failure

    def test_stale_beyond_limit_flags_quality_failure(self):
        mkt = _market(10, close=10.0)  # only 10 days
        policy = ValuationPolicy(stale_price_limit_days=3)

        # Target date far beyond available data
        result = policy.horizon_mark("000001.SZ", "20250120", mkt)

        assert result.stale_days > 3
        assert result.quality_failure

    def test_no_data_at_all(self):
        mkt = _market(30)
        policy = ValuationPolicy(stale_price_limit_days=5)

        result = policy.horizon_mark("UNKNOWN.SZ", "20250116", mkt)

        assert result.price == 0.0
        assert result.quality_failure


class TestTerminalHaircut:
    def test_haircut_rate_base(self):
        # No stale days, 10% limit band
        rate = compute_liquidity_haircut_rate(stale_days=0, limit_band_rate=0.10)
        assert rate == pytest.approx(0.10)

    def test_haircut_rate_with_stale_penalty(self):
        # 10 stale days -> 10 * 0.5% = 5% penalty
        rate = compute_liquidity_haircut_rate(stale_days=10, limit_band_rate=0.10)
        # max(10%, 10%) + 5% = 15%
        assert rate == pytest.approx(0.15)

    def test_haircut_rate_capped_at_30_percent(self):
        rate = compute_liquidity_haircut_rate(stale_days=100, limit_band_rate=0.20)
        assert rate == pytest.approx(0.30)

    def test_haircut_rate_uses_higher_of_base_and_limit_band(self):
        # limit_band = 20% > base 10%
        rate = compute_liquidity_haircut_rate(stale_days=0, limit_band_rate=0.20)
        assert rate == pytest.approx(0.20)

    def test_terminal_valuation(self):
        policy = ValuationPolicy(stale_price_limit_days=5)
        result = policy.terminal_value(
            quantity=1000,
            last_valid_price=10.0,
            stale_days=5,
            limit_band_rate=0.10,
            adv=5_000_000.0,
        )

        assert isinstance(result, TerminalValuation)
        # haircut_rate = max(10%, 10%) + min(5*0.5%, 10%) = 10% + 2.5% = 12.5%
        assert result.haircut_rate == pytest.approx(0.125)
        assert result.liquidity_haircut == pytest.approx(10000.0 * 0.125)
        assert result.terminal_value < 10000.0
        assert result.terminal_value > 0

    def test_stress_scenarios(self):
        policy = ValuationPolicy(stale_price_limit_days=5)
        result = policy.terminal_value(
            quantity=1000,
            last_valid_price=10.0,
            stale_days=5,
            limit_band_rate=0.10,
            adv=5_000_000.0,
        )

        assert len(result.stress_scenarios) == 4
        rates = [s["rate"] for s in result.stress_scenarios]
        assert rates == [0.05, 0.10, 0.20, 0.30]
