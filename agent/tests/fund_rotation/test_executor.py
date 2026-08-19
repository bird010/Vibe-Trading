"""Tests for unified target weight executor — §12.1."""

import pytest

from backtest.fund_rotation.executor import PortfolioExecutor, RebalanceResult
from backtest.fund_rotation.etf_rules import ChinaETFExecutionRules


def _make_executor(cash: float = 1_000_000.0) -> PortfolioExecutor:
    return PortfolioExecutor(cash=cash, rules=ChinaETFExecutionRules())


class TestEquityAnchor:
    """§12.1 step 1 — equity anchor uses open prices."""

    def test_anchor_uses_open_price(self):
        ex = _make_executor(cash=500_000.0)
        # Hold 1000 shares of A bought earlier
        ex.set_positions({"A": {"size": 1000, "entry_date": "20240101"}})
        bars = {"A": {"open": 5.0, "close": 5.5, "vol": 100000, "high": 5.6, "low": 4.9}}
        result = ex.execute_rebalance(target_weights={}, bars=bars, trade_date="20240105")
        # Equity = cash + 1000 * 5.0 (open, not close)
        assert result.pre_equity == pytest.approx(500_000.0 + 1000 * 5.0)

    def test_stale_close_when_no_open(self):
        ex = _make_executor(cash=500_000.0)
        ex.set_positions({"A": {"size": 1000, "entry_date": "20240101"}})
        # No open price -> use last known close
        bars = {"A": {"open": None, "close": 4.8, "vol": 0, "high": 4.8, "low": 4.8}}
        ex.set_last_close({"A": 4.8})
        result = ex.execute_rebalance(target_weights={}, bars=bars, trade_date="20240105")
        assert result.pre_equity == pytest.approx(500_000.0 + 1000 * 4.8)

    def test_new_position_with_nan_close_replaces_pre_position_cache_with_fill(self):
        ex = _make_executor(cash=100_000.0)
        ex.set_last_close({"A": 100.0}, trade_date="20240101")
        bars = {
            "A": {
                "open": 120.0, "close": float("nan"), "vol": 1_000_000,
                "high": 121.0, "low": 119.0, "pre_close": 100.0,
            },
        }

        result = ex.execute_rebalance(
            target_weights={"A": 0.5}, bars=bars, trade_date="20240102",
        )

        fill = next(event for event in result.events if event.get("filled", 0) > 0)
        assert ex._last_close["A"] == pytest.approx(fill["price"])
        assert ex._last_close_date["A"] == "20240102"
        assert ex._last_close_source["A"] == "execution_price"


class TestSellFirst:
    """§12.1 steps 3-4 — all sells execute before any buys."""

    def test_sell_frees_cash_for_buy(self):
        ex = _make_executor(cash=100.0)  # Very little cash
        ex.set_positions({"A": {"size": 10000, "entry_date": "20240101"}})
        bars = {
            "A": {"open": 10.0, "close": 10.0, "vol": 1000000, "high": 10.1, "low": 9.9},
            "B": {"open": 5.0, "close": 5.0, "vol": 1000000, "high": 5.1, "low": 4.9},
        }
        # Target: sell all A, buy B with proceeds
        result = ex.execute_rebalance(
            target_weights={"B": 0.5},  # 50% in B, 0% in A (implicit sell)
            bars=bars,
            trade_date="20240105",
        )
        # A should be sold, B should be bought
        assert "A" not in result.final_positions or result.final_positions["A"]["size"] == 0
        assert result.cash >= 0

    def test_blocked_sell_does_not_free_cash(self):
        """If sell is blocked (limit-down), cash is not freed."""
        ex = _make_executor(cash=100.0)
        ex.set_positions({"A": {"size": 10000, "entry_date": "20240101"}})
        bars = {
            "A": {"open": 9.0, "close": 9.0, "vol": 100, "high": 9.0, "low": 9.0, "pre_close": 10.0},
            "B": {"open": 5.0, "close": 5.0, "vol": 1000000, "high": 5.1, "low": 4.9},
        }
        # A is limit-down (single price at -10%) -> sell blocked
        result = ex.execute_rebalance(
            target_weights={"B": 0.5},
            bars=bars,
            trade_date="20240105",
        )
        # A still held, B buy limited by available cash (only 100)
        assert result.final_positions.get("A", {}).get("size", 0) == 10000


class TestProportionalScaling:
    """§12.1 steps 5-7 — common scaling factor for buys."""

    def test_insufficient_cash_scales_all_buys(self):
        ex = _make_executor(cash=10_000.0)
        bars = {
            "A": {"open": 5.0, "close": 5.0, "vol": 1000000, "high": 5.1, "low": 4.9},
            "B": {"open": 5.0, "close": 5.0, "vol": 1000000, "high": 5.1, "low": 4.9},
        }
        # Target 50% each = 5000 each, but with commission need more
        result = ex.execute_rebalance(
            target_weights={"A": 0.5, "B": 0.5},
            bars=bars,
            trade_date="20240105",
        )
        # Both should get proportional allocation
        assert result.cash >= 0
        # Both A and B should have some position (or both zero if scaling = 0)
        size_a = result.final_positions.get("A", {}).get("size", 0)
        size_b = result.final_positions.get("B", {}).get("size", 0)
        # Symmetric targets -> symmetric fills
        assert size_a == size_b

    def test_cash_never_negative(self):
        ex = _make_executor(cash=1000.0)
        bars = {
            "A": {"open": 3.0, "close": 3.0, "vol": 1000000, "high": 3.1, "low": 2.9},
            "B": {"open": 4.0, "close": 4.0, "vol": 1000000, "high": 4.1, "low": 3.9},
            "C": {"open": 5.0, "close": 5.0, "vol": 1000000, "high": 5.1, "low": 4.9},
        }
        result = ex.execute_rebalance(
            target_weights={"A": 0.33, "B": 0.33, "C": 0.33},
            bars=bars,
            trade_date="20240105",
        )
        assert result.cash >= -1e-9  # Allow tiny float error


class TestOrderIndependence:
    """§12.1 — result must not depend on code traversal order."""

    def test_shuffled_symbols_same_result(self):
        bars = {
            "A": {"open": 4.0, "close": 4.0, "vol": 1000000, "high": 4.1, "low": 3.9},
            "B": {"open": 5.0, "close": 5.0, "vol": 1000000, "high": 5.1, "low": 4.9},
            "C": {"open": 6.0, "close": 6.0, "vol": 1000000, "high": 6.1, "low": 5.9},
        }
        targets = {"A": 0.3, "B": 0.3, "C": 0.3}

        ex1 = _make_executor(cash=100_000.0)
        r1 = ex1.execute_rebalance(target_weights=targets, bars=bars, trade_date="20240105")

        ex2 = _make_executor(cash=100_000.0)
        # Feed in different order
        bars_shuffled = {"C": bars["C"], "A": bars["A"], "B": bars["B"]}
        r2 = ex2.execute_rebalance(target_weights=targets, bars=bars_shuffled, trade_date="20240105")

        assert r1.cash == pytest.approx(r2.cash, abs=0.01)
        for code in ["A", "B", "C"]:
            s1 = r1.final_positions.get(code, {}).get("size", 0)
            s2 = r2.final_positions.get(code, {}).get("size", 0)
            assert s1 == s2


class TestAllBlocked:
    """§12.1 step 8 — if scaling yields zero for all, mark BLOCKED."""

    def test_all_buys_blocked_when_cash_too_low(self):
        # Cash so low that even 100 shares + min commission is unaffordable
        ex = _make_executor(cash=3.0)  # Less than min commission of 5
        bars = {
            "A": {"open": 4.0, "close": 4.0, "vol": 1000000, "high": 4.1, "low": 3.9},
        }
        result = ex.execute_rebalance(
            target_weights={"A": 1.0},
            bars=bars,
            trade_date="20240105",
        )
        # Cannot buy anything
        assert result.final_positions.get("A", {}).get("size", 0) == 0
        assert result.cash >= 0
        # Should have a blocked record
        assert any(e.get("status") == "BLOCKED" for e in result.events)


class TestLotRounding:
    """§12.1 step 6 — round down to lot size."""

    def test_buy_rounds_to_100(self):
        ex = _make_executor(cash=1_000_000.0)
        bars = {
            "A": {"open": 3.33, "close": 3.33, "vol": 1000000, "high": 3.4, "low": 3.2},
        }
        result = ex.execute_rebalance(
            target_weights={"A": 0.01},  # 1% of 1M = 10000 -> ~3003 shares -> 3000
            bars=bars,
            trade_date="20240105",
        )
        size = result.final_positions.get("A", {}).get("size", 0)
        assert size % 100 == 0
        assert size > 0
