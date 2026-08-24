"""Focused tests for R56 ronna cash-floor carry."""
import pytest
from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import apply_incumbent_carry
from backtest.fund_rotation.strategies.ai_rotation_r45_cash_floor_carry.strategy_r56_cash_floor_ronna import CASH_FLOOR,DESCRIPTOR,AiRotationR56CashFloorRonnaStrategy,apply_cash_floor_carry
def test_floor():assert apply_cash_floor_carry({"HELD":1/3},{"HELD":1/3,"NEW1":1/6,"NEW2":1/6})[1]==pytest.approx(CASH_FLOOR)
def test_normal():
 a=({"HELD":1/3},{"HELD":1/3,"NEW":1/6});assert apply_cash_floor_carry(*a)[:4]==apply_incumbent_carry(*a)
def test_fail_closed():assert apply_cash_floor_carry({"HELD":10**10000},{"HELD":1/3,"NEW":1/6})[1]==pytest.approx(1/2)
def test_identity():s=AiRotationR56CashFloorRonnaStrategy();assert DESCRIPTOR.id=="ai_rotation_r56_cash_floor_ronna" and "1/6144" in str(s.describe_decision_pipeline(s.config_model()))

