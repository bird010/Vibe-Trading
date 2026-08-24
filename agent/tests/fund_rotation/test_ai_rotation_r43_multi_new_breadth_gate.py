"""Focused tests for R43 narrow multi-new breadth gate."""

from __future__ import annotations

import pytest

from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import apply_incumbent_carry
from backtest.fund_rotation.strategies.ai_rotation_r43_multi_new_breadth_gate.strategy import (
    DESCRIPTOR,
    AiRotationR43MultiNewBreadthGateStrategy,
    apply_multi_new_breadth_gate,
)


def test_one_incumbent_two_new_targets_cancel_carry():
    actual = apply_multi_new_breadth_gate(
        {"HELD": 1 / 3}, {"HELD": 1 / 3, "NEW1": 1 / 6, "NEW2": 1 / 6}
    )
    assert actual[:4] == (
        {"HELD": pytest.approx(1 / 3), "NEW1": pytest.approx(1 / 6), "NEW2": pytest.approx(1 / 6)},
        pytest.approx(1 / 3),
        {"NEW1", "NEW2"},
        set(),
    )
    assert actual[4] is False


def test_one_incumbent_one_new_target_is_exactly_r39():
    args = ({"HELD": 1 / 3}, {"HELD": 1 / 3, "NEW": 1 / 6})
    actual = apply_multi_new_breadth_gate(*args)
    expected = apply_incumbent_carry(*args)
    assert actual[:4] == expected
    assert actual[4] is True


def test_multiple_incumbents_are_exactly_r39():
    args = ({"A": 1 / 6, "B": 1 / 6}, {"A": 1 / 6, "B": 1 / 3, "NEW": 1 / 6})
    actual = apply_multi_new_breadth_gate(*args)
    expected = apply_incumbent_carry(*args)
    assert actual[:4] == expected


def test_extreme_integer_fails_closed_without_overflow():
    actual = apply_multi_new_breadth_gate(
        {"HELD": 10**10000}, {"HELD": 1 / 3, "NEW1": 1 / 12, "NEW2": 1 / 12}
    )
    assert actual[0] == {"HELD": pytest.approx(1 / 3), "NEW1": pytest.approx(1 / 12), "NEW2": pytest.approx(1 / 12)}


def test_registered_identity_and_pipeline_are_r43_specific():
    strategy = AiRotationR43MultiNewBreadthGateStrategy()
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())
    assert DESCRIPTOR.id == "ai_rotation_r43_multi_new_breadth_gate"
    assert "at least two" in str(pipeline).lower()
