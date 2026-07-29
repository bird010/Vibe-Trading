"""Tests for cost policy and max-affordable quantity solver."""

from __future__ import annotations

import pytest

from backtest.stockpred.execution.costs import (
    DEFAULT_COST_POLICY,
    CostPolicy,
    FeeBreakdown,
    max_affordable_quantity,
)


def test_buy_fees_include_commission_and_slippage():
    fees = DEFAULT_COST_POLICY.estimate_buy_fees(quantity=1000, price=10.0, adv=5_000_000.0)
    assert fees.commission > 0
    assert fees.transfer_fee > 0
    assert fees.slippage >= 0
    assert fees.market_impact >= 0
    assert fees.stamp_duty == 0  # no stamp duty on buy
    assert fees.total > 0


def test_sell_fees_include_stamp_duty():
    fees = DEFAULT_COST_POLICY.estimate_sell_fees(quantity=1000, price=10.0, adv=5_000_000.0)
    assert fees.stamp_duty > 0
    assert fees.commission > 0
    assert fees.total > 0


def test_fee_breakdown_total_is_sum():
    fees = DEFAULT_COST_POLICY.estimate_buy_fees(quantity=500, price=20.0, adv=10_000_000.0)
    expected = fees.commission + fees.stamp_duty + fees.transfer_fee + fees.slippage + fees.market_impact
    assert fees.total == pytest.approx(expected)


def test_max_affordable_respects_lot_size():
    qty = max_affordable_quantity(
        cash_budget=100_000.0,
        reference_price=10.0,
        adv=5_000_000.0,
        max_participation=0.05,
        fee_policy=DEFAULT_COST_POLICY,
        lot_size=100,
    )
    assert qty % 100 == 0
    assert qty > 0
    # Total cost including fees must not exceed budget
    total = qty * 10.0 + DEFAULT_COST_POLICY.estimate_buy_fees(qty, 10.0, 5_000_000.0).total
    assert total <= 100_000.0


def test_max_affordable_one_more_lot_exceeds_budget():
    qty = max_affordable_quantity(
        cash_budget=100_000.0,
        reference_price=10.0,
        adv=5_000_000.0,
        max_participation=0.05,
        fee_policy=DEFAULT_COST_POLICY,
        lot_size=100,
    )
    # One more lot should exceed budget
    next_qty = qty + 100
    next_total = next_qty * 10.0 + DEFAULT_COST_POLICY.estimate_buy_fees(next_qty, 10.0, 5_000_000.0).total
    assert next_total > 100_000.0


def test_max_affordable_capacity_limited():
    # ADV=1M, participation=5% -> capacity=50K -> at price=10, max ~5000 shares before fees
    qty = max_affordable_quantity(
        cash_budget=1_000_000.0,
        reference_price=10.0,
        adv=1_000_000.0,
        max_participation=0.05,
        fee_policy=DEFAULT_COST_POLICY,
        lot_size=100,
    )
    # Capacity limit: 50_000 / 10 = 5000 shares max (before fee adjustment)
    assert qty <= 5000
    assert qty * 10.0 <= 50_000.0


def test_max_affordable_zero_when_budget_too_small():
    qty = max_affordable_quantity(
        cash_budget=500.0,
        reference_price=10.0,
        adv=5_000_000.0,
        max_participation=0.05,
        fee_policy=DEFAULT_COST_POLICY,
        lot_size=100,
    )
    assert qty == 0


def test_max_affordable_zero_when_adv_too_small():
    qty = max_affordable_quantity(
        cash_budget=1_000_000.0,
        reference_price=10.0,
        adv=100.0,  # extremely low liquidity
        max_participation=0.05,
        fee_policy=DEFAULT_COST_POLICY,
        lot_size=100,
    )
    # capacity = 100 * 0.05 = 5 CNY -> can't buy even 1 share
    assert qty == 0


def test_fees_monotonic_in_quantity():
    f1 = DEFAULT_COST_POLICY.estimate_buy_fees(100, 10.0, 5_000_000.0).total
    f2 = DEFAULT_COST_POLICY.estimate_buy_fees(200, 10.0, 5_000_000.0).total
    f3 = DEFAULT_COST_POLICY.estimate_buy_fees(1000, 10.0, 5_000_000.0).total
    assert f2 >= f1
    assert f3 >= f2


def test_higher_participation_means_higher_slippage():
    # Low participation
    fees_low = DEFAULT_COST_POLICY.estimate_buy_fees(100, 10.0, 10_000_000.0)
    # High participation (same trade value, much lower ADV)
    fees_high = DEFAULT_COST_POLICY.estimate_buy_fees(100, 10.0, 100_000.0)
    assert fees_high.slippage + fees_high.market_impact >= fees_low.slippage + fees_low.market_impact


def test_custom_cost_policy():
    policy = CostPolicy(
        commission_rate_bps=10.0,
        stamp_duty_rate_bps=5.0,
        transfer_fee_rate_bps=0.1,
        base_slippage_bps=3.0,
        impact_coefficient=100.0,
        max_slippage_bps=20.0,
        min_commission=1.0,
    )
    fees = policy.estimate_buy_fees(1000, 10.0, 5_000_000.0)
    assert fees.commission >= 1.0
    assert fees.stamp_duty == 0  # buy side
