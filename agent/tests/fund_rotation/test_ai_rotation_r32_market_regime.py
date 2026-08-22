"""Focused behavior tests for the R32 benchmark regime filter."""

from __future__ import annotations

import pytest

from backtest.fund_rotation.strategies.ai_rotation_r32_market_regime.strategy import (
    apply_market_regime,
)


def test_non_positive_benchmark_moves_all_r11_targets_to_cash():
    targets, cash, risk_off = apply_market_regime(
        {"A": 1 / 3, "B": 1 / 3},
        0.0,
    )

    assert targets == {}
    assert cash == pytest.approx(1.0)
    assert risk_off is True


def test_positive_benchmark_preserves_r11_targets():
    targets, cash, risk_off = apply_market_regime({"A": 1 / 3}, 0.01)

    assert targets == {"A": pytest.approx(1 / 3)}
    assert cash == pytest.approx(2 / 3)
    assert risk_off is False


def test_missing_benchmark_preserves_r11_targets_without_risk_off():
    targets, cash, risk_off = apply_market_regime({"A": 1 / 3}, None)

    assert targets == {"A": pytest.approx(1 / 3)}
    assert cash == pytest.approx(2 / 3)
    assert risk_off is False
