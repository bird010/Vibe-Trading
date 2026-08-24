"""Focused tests for R45 cash-floor carry."""

import pytest

from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import apply_incumbent_carry
from backtest.fund_rotation.strategies.ai_rotation_r45_cash_floor_carry.strategy import DESCRIPTOR, AiRotationR45CashFloorCarryStrategy, apply_cash_floor_carry


def test_cash_floor_reduces_extreme_single_incumbent_carry():
    actual = apply_cash_floor_carry(
        {"HELD": 1 / 3}, {"HELD": 1 / 3, "NEW1": 1 / 6, "NEW2": 1 / 6}
    )
    assert actual[1] == pytest.approx(1 / 6)
    assert sum(actual[0].values()) == pytest.approx(5 / 6)


def test_normal_r39_state_is_unchanged():
    args = ({"HELD": 1 / 3}, {"HELD": 1 / 3, "NEW": 1 / 6})
    actual = apply_cash_floor_carry(*args)
    expected = apply_incumbent_carry(*args)
    assert actual[:4] == expected


def test_extreme_integer_fails_closed():
    actual = apply_cash_floor_carry(
        {"HELD": 10**10000}, {"HELD": 1 / 3, "NEW": 1 / 6}
    )
    assert actual[1] == pytest.approx(1 / 2)


def test_registered_identity_and_pipeline_are_r45_specific():
    strategy = AiRotationR45CashFloorCarryStrategy()
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())
    assert DESCRIPTOR.id == "ai_rotation_r45_cash_floor_carry"
    assert "1/6" in str(pipeline)
