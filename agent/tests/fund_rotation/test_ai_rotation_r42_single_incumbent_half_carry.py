"""Focused behavior tests for R42 single-incumbent half carry."""

from __future__ import annotations

import math

import pytest

from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import (
    apply_incumbent_carry,
)
from backtest.fund_rotation.strategies.ai_rotation_r42_single_incumbent_half_carry.strategy import (
    DESCRIPTOR,
    AiRotationR42SingleIncumbentHalfCarryStrategy,
    apply_single_incumbent_half_carry,
)


def test_single_incumbent_carries_half_of_released_weight():
    targets, cash, staged, incumbents, applied = apply_single_incumbent_half_carry(
        {"HELD": 1 / 3}, {"HELD": 1 / 3, "NEW": 1 / 6}
    )
    assert targets == {"HELD": pytest.approx(5 / 12), "NEW": pytest.approx(1 / 6)}
    assert cash == pytest.approx(5 / 12)
    assert staged == {"NEW"}
    assert incumbents == {"HELD"}
    assert applied is True


def test_multiple_incumbents_are_exactly_r39():
    args = ({"A": 1 / 6, "B": 1 / 6}, {"A": 1 / 6, "B": 1 / 3, "NEW": 1 / 6})
    actual = apply_single_incumbent_half_carry(*args)
    expected = apply_incumbent_carry(*args)
    assert actual[:4] == expected
    assert actual[4] is True


def test_no_staged_target_is_exactly_r39():
    args = ({"HELD": 1 / 3}, {"HELD": 1 / 3})
    actual = apply_single_incumbent_half_carry(*args)
    expected = apply_incumbent_carry(*args)
    assert actual[:4] == expected
    assert actual[4] is False


def test_extreme_integer_fails_closed_without_overflow():
    result = apply_single_incumbent_half_carry(
        {"HELD": 10**10000}, {"HELD": 1 / 3, "NEW": 1 / 6}
    )
    assert result[:2] == ({"HELD": pytest.approx(1 / 3), "NEW": pytest.approx(1 / 6)}, 1 / 2)
    assert result[2:] == ({"NEW"}, set(), False)


def test_registered_identity_and_pipeline_are_r42_specific():
    strategy = AiRotationR42SingleIncumbentHalfCarryStrategy()
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())
    assert DESCRIPTOR.id == "ai_rotation_r42_single_incumbent_half_carry"
    assert "half" in str(pipeline).lower()
    assert math.isfinite(0.5)
