"""Focused tests for R47 breadth-conditional tight floor."""

import pytest

from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import apply_incumbent_carry
from backtest.fund_rotation.strategies.ai_rotation_r45_cash_floor_carry.strategy_r47_breadth_tight_floor import CASH_FLOOR, DESCRIPTOR, AiRotationR47BreadthTightFloorStrategy, apply_breadth_tight_floor


def test_two_incumbents_receive_tight_floor():
    actual = apply_breadth_tight_floor({"HELD1": 1 / 6, "HELD2": 1 / 6}, {"HELD1": 1 / 6, "HELD2": 1 / 6, "NEW1": 1 / 6, "NEW2": 1 / 6})
    assert actual[1] == pytest.approx(CASH_FLOOR)


def test_single_incumbent_preserves_r39():
    args = ({"HELD": 1 / 3}, {"HELD": 1 / 3, "NEW1": 1 / 6, "NEW2": 1 / 6})
    assert apply_breadth_tight_floor(*args)[:4] == apply_incumbent_carry(*args)


def test_extreme_integer_fails_closed():
    actual = apply_breadth_tight_floor({"HELD": 10**10000}, {"HELD": 1 / 3, "NEW": 1 / 6})
    assert actual[1] == pytest.approx(1 / 2)


def test_registered_identity_and_pipeline_are_r47_specific():
    strategy = AiRotationR47BreadthTightFloorStrategy()
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())
    assert DESCRIPTOR.id == "ai_rotation_r47_breadth_tight_floor"
    assert "at least two incumbents" in str(pipeline)
