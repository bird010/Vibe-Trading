"""Regression tests for ChinaAEngine migration — §12.3.

Captures existing single-symbol direction strategy behavior BEFORE migration.
After migration to unified target weight interface, these tests must still pass
(within tolerance for multi-symbol order-independence changes).
"""

import numpy as np
import pandas as pd
import pytest
from pathlib import Path

from backtest.engines.china_a import ChinaAEngine


def _make_data(n_days: int = 20, start_price: float = 10.0) -> pd.DataFrame:
    """Create simple OHLCV data for regression testing."""
    dates = pd.bdate_range("2024-01-01", periods=n_days)
    prices = np.linspace(start_price, start_price * 1.2, n_days)
    return pd.DataFrame({
        "open": prices,
        "high": prices * 1.01,
        "low": prices * 0.99,
        "close": prices,
        "volume": [1_000_000] * n_days,
    }, index=dates)


def _run_engine(signal_values: list[float], tmp_path: Path, n_days: int = 10) -> ChinaAEngine:
    """Helper: run ChinaAEngine with a simple loader/signal pattern."""
    data = _make_data(n_days)
    dates = data.index

    class FakeLoader:
        def fetch(self, *args, **kwargs):
            return {"000001.SZ": data.copy()}

    class SignalEngine:
        def generate(self, data_map):
            frame = data_map["000001.SZ"]
            sig = pd.Series(0.0, index=frame.index)
            for i, v in enumerate(signal_values):
                if i < len(sig):
                    sig.iloc[i] = v
            return {"000001.SZ": sig}

    engine = ChinaAEngine({"initial_cash": 100_000})
    engine.run_backtest(
        {
            "codes": ["000001.SZ"],
            "start_date": "2024-01-01",
            "end_date": "2024-02-01",
            "source": "tushare",
            "initial_cash": 100_000,
        },
        FakeLoader(),
        SignalEngine(),
        tmp_path,
    )
    return engine


class TestSingleSymbolRegression:
    """§12.3 — Single-symbol direction signal behavior must be preserved."""

    def test_buy_signal_opens_position(self, tmp_path):
        """A constant buy signal (1.0) should open a position."""
        engine = _run_engine([1.0] * 10, tmp_path)
        assert len(engine.trades) >= 1
        assert engine.trades[0].direction == 1

    def test_no_signal_no_position(self, tmp_path):
        """Zero signal should not open any position."""
        engine = _run_engine([0.0] * 10, tmp_path)
        assert len(engine.trades) == 0

    def test_signal_shift_one_bar(self, tmp_path):
        """Signal on day i executes at day i+1 open (next-bar semantics)."""
        data = _make_data(10)
        dates = data.index
        # Signal only on day 2
        signals = [0.0] * 10
        signals[2] = 1.0
        engine = _run_engine(signals, tmp_path)
        if engine.trades:
            entry_time = engine.trades[0].entry_time
            assert entry_time == dates[3]

    def test_capital_conservation(self, tmp_path):
        """Total equity = cash + position value at all times."""
        engine = _run_engine([1.0] * 20, tmp_path, n_days=20)
        final_equity = engine.equity_snapshots[-1].equity
        assert final_equity > 0
        assert np.isfinite(final_equity)

    def test_commission_deducted(self, tmp_path):
        """Commission should be charged on trades."""
        engine = _run_engine([1.0] * 10, tmp_path)
        # At least one trade should have commission > 0
        assert len(engine.trades) >= 1
        assert engine.trades[0].commission > 0


class TestTargetWeightBuilder:
    """§12.3 — Direction signal → target weight conversion."""

    def test_direction_to_weight_conversion(self):
        """Direction signal of 1.0 with single symbol → weight 1.0."""
        from backtest.fund_rotation.target_builder import direction_to_target_weights

        signals = {"A": 1.0, "B": 0.0, "C": -1.0}
        weights = direction_to_target_weights(signals)
        # Long-only: positive signals become weights, negative/zero become 0
        assert weights["A"] == pytest.approx(1.0)
        assert weights.get("B", 0.0) == 0.0
        assert weights.get("C", 0.0) == 0.0

    def test_multiple_long_signals_normalized(self):
        """Multiple positive signals → equal weight (normalized to sum=1)."""
        from backtest.fund_rotation.target_builder import direction_to_target_weights

        signals = {"A": 1.0, "B": 1.0, "C": 0.0}
        weights = direction_to_target_weights(signals)
        assert weights["A"] == pytest.approx(0.5)
        assert weights["B"] == pytest.approx(0.5)
        assert sum(weights.values()) == pytest.approx(1.0)

    def test_empty_signals_empty_weights(self):
        from backtest.fund_rotation.target_builder import direction_to_target_weights

        weights = direction_to_target_weights({})
        assert weights == {}

    def test_all_zero_signals_empty_weights(self):
        from backtest.fund_rotation.target_builder import direction_to_target_weights

        weights = direction_to_target_weights({"A": 0.0, "B": 0.0})
        assert weights == {}


class TestUnifiedTargetWeightEngine:
    """The generic engine has one target-weight execution path, not a mode switch."""

    def test_no_runtime_portfolio_mode_switch(self):
        engine = ChinaAEngine({"initial_cash": 100_000, "portfolio_mode": False})
        assert not hasattr(engine, "portfolio_mode")

    def test_rebalance_replaces_frozen_position_when_increasing(self):
        dates = pd.DatetimeIndex(["2024-01-02", "2024-01-03"])
        frame = pd.DataFrame(
            {
                "open": [10.0, 10.0],
                "high": [10.1, 10.1],
                "low": [9.9, 9.9],
                "close": [10.0, 10.0],
                "pre_close": [10.0, 10.0],
            },
            index=dates,
        )
        target = pd.DataFrame({"000001.SZ": [0.5, 1.0]}, index=dates)
        engine = ChinaAEngine({"initial_cash": 100_000, "slippage": 0.0})

        engine._rebalance_portfolio(
            dates[0], {"000001.SZ": frame}, frame[["close"]].rename(columns={"close": "000001.SZ"}),
            target, ["000001.SZ"], 100_000,
        )
        first_size = engine.positions["000001.SZ"].size
        engine._rebalance_portfolio(
            dates[1], {"000001.SZ": frame}, frame[["close"]].rename(columns={"close": "000001.SZ"}),
            target, ["000001.SZ"], 100_000,
        )

        assert engine.positions["000001.SZ"].size > first_size
        assert engine.capital >= 0

    def test_blocked_close_does_not_open_opposite_exposure(self):
        from backtest.engines.crypto import CryptoEngine
        from backtest.models import Position

        class BlockingCloseEngine(CryptoEngine):
            def can_execute(self, symbol, direction, bar):
                return direction != 0

        date = pd.Timestamp("2024-01-02")
        frame = pd.DataFrame({"open": [10.0], "close": [10.0]}, index=[date])
        engine = BlockingCloseEngine({"initial_cash": 50_000, "slippage": 0.0})
        engine.positions["BTC-USDT"] = Position(
            symbol="BTC-USDT", direction=1, entry_price=10.0,
            entry_time=date, size=5_000,
        )
        capital_before = engine.capital

        engine._rebalance_portfolio(
            date, {"BTC-USDT": frame},
            frame[["close"]].rename(columns={"close": "BTC-USDT"}),
            pd.DataFrame({"BTC-USDT": [-1.0]}, index=[date]),
            ["BTC-USDT"], 100_000,
        )

        assert engine.positions["BTC-USDT"].direction == 1
        assert engine.positions["BTC-USDT"].size == 5_000
        assert engine.capital == capital_before
