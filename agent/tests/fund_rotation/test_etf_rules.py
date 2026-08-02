"""Tests for ETF execution rules — §13.2."""

import pytest

from backtest.fund_rotation.etf_rules import ChinaETFExecutionRules


@pytest.fixture
def rules():
    return ChinaETFExecutionRules()


class TestLotSize:
    """§13.2 — buy in 100-share lots; odd lots can be sold."""

    def test_buy_rounds_down_to_100(self, rules):
        assert rules.round_buy_size(1250) == 1200
        assert rules.round_buy_size(100) == 100
        assert rules.round_buy_size(99) == 0

    def test_sell_allows_odd_lots(self, rules):
        """Odd lots from adj factor changes can be sold."""
        assert rules.round_sell_size(1237) == 1237

    def test_sell_zero(self, rules):
        assert rules.round_sell_size(0) == 0


class TestTickSize:
    """§13.2 — min tick 0.001, round against trader."""

    def test_buy_rounds_up(self, rules):
        """Buy price rounds up (unfavorable to buyer)."""
        assert rules.apply_tick(4.1234, direction=1) == pytest.approx(4.124)

    def test_sell_rounds_down(self, rules):
        """Sell price rounds down (unfavorable to seller)."""
        assert rules.apply_tick(4.1236, direction=-1) == pytest.approx(4.123)

    def test_exact_tick_unchanged(self, rules):
        assert rules.apply_tick(4.123, direction=1) == pytest.approx(4.123)
        assert rules.apply_tick(4.123, direction=-1) == pytest.approx(4.123)


class TestCommission:
    """§13.2 — 2.5 bps, min 5 CNY, no stamp tax, no transfer fee."""

    def test_normal_commission(self, rules):
        # 10000 shares * 4.0 = 40000 notional; 40000 * 0.00025 = 10
        assert rules.calc_commission(10000, 4.0) == pytest.approx(10.0)

    def test_minimum_commission(self, rules):
        # 100 shares * 1.0 = 100 notional; 100 * 0.00025 = 0.025 < 5
        assert rules.calc_commission(100, 1.0) == pytest.approx(5.0)

    def test_same_for_buy_and_sell(self, rules):
        """No asymmetric stamp tax for ETFs."""
        assert rules.calc_commission(1000, 10.0) == rules.calc_commission(1000, 10.0)

    def test_zero_size_zero_commission(self, rules):
        assert rules.calc_commission(0, 4.0) == 0.0


class TestExecutionBlocking:
    """§13.2 — blocked when no open, zero volume, or adverse limit."""

    def test_no_open_price_blocked(self, rules):
        bar = {"open": None, "close": 4.0, "vol": 1000000, "high": 4.1, "low": 3.9}
        assert rules.can_buy(bar) is False
        assert rules.can_sell(bar) is False

    def test_zero_volume_blocked(self, rules):
        bar = {"open": 4.0, "close": 4.0, "vol": 0, "high": 4.0, "low": 4.0}
        assert rules.can_buy(bar) is False
        assert rules.can_sell(bar) is False

    def test_normal_bar_allowed(self, rules):
        bar = {"open": 4.0, "close": 4.05, "vol": 1000000, "high": 4.1, "low": 3.9}
        assert rules.can_buy(bar) is True
        assert rules.can_sell(bar) is True

    def test_limit_up_blocks_buy(self, rules):
        """Single-price limit-up (high==low==close, all at limit) blocks buy."""
        bar = {"open": 4.4, "close": 4.4, "vol": 100, "high": 4.4, "low": 4.4, "pre_close": 4.0}
        # 10% limit up for ETF
        assert rules.can_buy(bar) is False
        assert rules.can_sell(bar) is True

    def test_limit_down_blocks_sell(self, rules):
        """Single-price limit-down blocks sell."""
        bar = {"open": 3.6, "close": 3.6, "vol": 100, "high": 3.6, "low": 3.6, "pre_close": 4.0}
        assert rules.can_sell(bar) is False
        assert rules.can_buy(bar) is True


class TestTPlus1:
    """§13.2 — T+1: cannot sell shares bought today."""

    def test_same_day_sell_blocked(self, rules):
        assert rules.can_sell_today(entry_date="20240105", current_date="20240105") is False

    def test_next_day_sell_allowed(self, rules):
        assert rules.can_sell_today(entry_date="20240105", current_date="20240108") is True


class TestLeverage:
    """§13.2 — long only, leverage 1."""

    def test_leverage_is_one(self, rules):
        assert rules.leverage == 1.0

    def test_long_only(self, rules):
        assert rules.allow_short is False
