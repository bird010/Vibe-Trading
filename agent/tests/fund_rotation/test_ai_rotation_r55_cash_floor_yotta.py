"""Focused tests for R55 yotta cash-floor carry."""
import pytest
from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import apply_incumbent_carry
from backtest.fund_rotation.strategies.ai_rotation_r45_cash_floor_carry.strategy_r55_cash_floor_yotta import CASH_FLOOR, DESCRIPTOR, AiRotationR55CashFloorYottaStrategy, apply_cash_floor_carry
def test_cash_floor_reduces_extreme_carry(): assert apply_cash_floor_carry({"HELD":1/3},{"HELD":1/3,"NEW1":1/6,"NEW2":1/6})[1] == pytest.approx(CASH_FLOOR)
def test_normal_r39_state_is_unchanged():
    args=({"HELD":1/3},{"HELD":1/3,"NEW":1/6}); assert apply_cash_floor_carry(*args)[:4] == apply_incumbent_carry(*args)
def test_extreme_integer_fails_closed(): assert apply_cash_floor_carry({"HELD":10**10000},{"HELD":1/3,"NEW":1/6})[1] == pytest.approx(1/2)
def test_registered_identity_and_pipeline_are_r55_specific():
    strategy=AiRotationR55CashFloorYottaStrategy(); assert DESCRIPTOR.id == "ai_rotation_r55_cash_floor_yotta"; assert "1/3072" in str(strategy.describe_decision_pipeline(strategy.config_model()))

