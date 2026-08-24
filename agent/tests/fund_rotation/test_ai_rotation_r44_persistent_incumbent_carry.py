"""Focused tests for R44 persistent incumbent carry."""

import pytest

from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import apply_incumbent_carry
from backtest.fund_rotation.strategies.ai_rotation_r44_persistent_incumbent_carry.strategy import (
    DESCRIPTOR,
    AiRotationR44PersistentIncumbentCarryStrategy,
    apply_persistent_incumbent_carry,
)


def test_two_period_incumbent_receives_r39_carry():
    actual = apply_persistent_incumbent_carry(
        {"HELD": 1 / 3}, {"HELD": 1 / 3}, {"HELD": 1 / 3, "NEW": 1 / 6}
    )
    expected = apply_incumbent_carry(
        {"HELD": 1 / 3}, {"HELD": 1 / 3, "NEW": 1 / 6}
    )
    assert actual[:4] == expected
    assert actual[4] is True


def test_new_incumbent_keeps_released_weight_as_cash():
    actual = apply_persistent_incumbent_carry(
        {"HELD": 1 / 3}, {}, {"HELD": 1 / 3, "NEW": 1 / 6}
    )
    assert actual[:4] == (
        {"HELD": pytest.approx(1 / 3), "NEW": pytest.approx(1 / 6)},
        pytest.approx(1 / 2),
        {"NEW"},
        set(),
    )
    assert actual[4] is False


def test_extreme_integer_fails_closed():
    actual = apply_persistent_incumbent_carry(
        {"HELD": 10**10000}, {"HELD": 1 / 3}, {"HELD": 1 / 3, "NEW": 1 / 6}
    )
    assert actual[1] == pytest.approx(1 / 2)


def test_registered_identity_and_pipeline_are_r44_specific():
    strategy = AiRotationR44PersistentIncumbentCarryStrategy()
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())
    assert DESCRIPTOR.id == "ai_rotation_r44_persistent_incumbent_carry"
    assert "two prior" in str(pipeline).lower()
