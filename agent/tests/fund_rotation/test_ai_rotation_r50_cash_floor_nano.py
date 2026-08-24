"""Focused tests for R50 nano cash-floor carry."""

import pytest

from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import apply_incumbent_carry
from backtest.fund_rotation.strategies.ai_rotation_r45_cash_floor_carry.strategy_r50_cash_floor_nano import CASH_FLOOR, DESCRIPTOR, AiRotationR50CashFloorNanoStrategy, apply_cash_floor_carry


def test_cash_floor_reduces_extreme_carry_to_one_ninety_sixth():
    actual = apply_cash_floor_carry({"HELD": 1 / 3}, {"HELD": 1 / 3, "NEW1": 1 / 6, "NEW2": 1 / 6})
    assert actual[1] == pytest.approx(CASH_FLOOR)


def test_normal_r39_state_is_unchanged():
    args = ({"HELD": 1 / 3}, {"HELD": 1 / 3, "NEW": 1 / 6})
    assert apply_cash_floor_carry(*args)[:4] == apply_incumbent_carry(*args)


def test_extreme_integer_fails_closed():
    actual = apply_cash_floor_carry({"HELD": 10**10000}, {"HELD": 1 / 3, "NEW": 1 / 6})
    assert actual[1] == pytest.approx(1 / 2)


def test_registered_identity_and_pipeline_are_r50_specific():
    strategy = AiRotationR50CashFloorNanoStrategy()
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())
    assert DESCRIPTOR.id == "ai_rotation_r50_cash_floor_nano"
    assert "1/96" in str(pipeline)
