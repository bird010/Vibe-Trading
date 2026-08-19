"""Cost policy and max-affordable quantity solver.

Implements deterministic fee estimation and lot-aligned binary search
per design §27.2.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class FeeBreakdown:
    """Absolute fee amounts for a trade."""

    commission: float
    stamp_duty: float
    transfer_fee: float
    slippage: float
    market_impact: float

    @property
    def total(self) -> float:
        return (
            self.commission
            + self.stamp_duty
            + self.transfer_fee
            + self.slippage
            + self.market_impact
        )


@dataclass(frozen=True)
class CostPolicy:
    """Transaction cost model parameters.

    All rates are in basis points (bps) of trade value unless noted.
    Slippage and impact are participation-rate dependent.
    """

    commission_rate_bps: float = 15.0
    stamp_duty_rate_bps: float = 10.0  # sell only
    transfer_fee_rate_bps: float = 0.1
    base_slippage_bps: float = 5.0
    impact_coefficient: float = 200.0  # multiplied by participation rate
    max_slippage_bps: float = 30.0
    min_commission: float = 5.0

    def _participation_rate(self, trade_value: float, adv: float) -> float:
        if adv <= 0:
            return 1.0
        return min(abs(trade_value) / adv, 1.0)

    def _slippage_bps(self, participation: float) -> float:
        raw = self.base_slippage_bps + self.impact_coefficient * participation
        return float(np.clip(raw, self.base_slippage_bps, self.max_slippage_bps))

    def estimate_buy_fees(
        self, quantity: int, price: float, adv: float
    ) -> FeeBreakdown:
        """Estimate fees for a buy order."""
        trade_value = abs(quantity * price)
        participation = self._participation_rate(trade_value, adv)

        commission = max(trade_value * self.commission_rate_bps / 10_000.0, self.min_commission)
        transfer_fee = trade_value * self.transfer_fee_rate_bps / 10_000.0
        slip_bps = self._slippage_bps(participation)
        slippage = trade_value * slip_bps / 10_000.0
        # Market impact: additional cost beyond slippage for large orders
        market_impact = trade_value * (self.impact_coefficient * participation * 0.1) / 10_000.0

        return FeeBreakdown(
            commission=commission,
            stamp_duty=0.0,  # no stamp duty on buy
            transfer_fee=transfer_fee,
            slippage=slippage,
            market_impact=market_impact,
        )

    def estimate_sell_fees(
        self, quantity: int, price: float, adv: float
    ) -> FeeBreakdown:
        """Estimate fees for a sell order."""
        trade_value = abs(quantity * price)
        participation = self._participation_rate(trade_value, adv)

        commission = max(trade_value * self.commission_rate_bps / 10_000.0, self.min_commission)
        stamp_duty = trade_value * self.stamp_duty_rate_bps / 10_000.0
        transfer_fee = trade_value * self.transfer_fee_rate_bps / 10_000.0
        slip_bps = self._slippage_bps(participation)
        slippage = trade_value * slip_bps / 10_000.0
        market_impact = trade_value * (self.impact_coefficient * participation * 0.1) / 10_000.0

        return FeeBreakdown(
            commission=commission,
            stamp_duty=stamp_duty,
            transfer_fee=transfer_fee,
            slippage=slippage,
            market_impact=market_impact,
        )


# Default policy matching current A-share market parameters
DEFAULT_COST_POLICY = CostPolicy()


def max_affordable_quantity(
    *,
    cash_budget: float,
    reference_price: float,
    adv: float,
    max_participation: float,
    fee_policy: CostPolicy,
    lot_size: int = 100,
) -> int:
    """Find maximum lot-aligned quantity affordable within budget and capacity.

    Uses bounded integer binary search per §27.2.
    Fees must be monotonic in quantity for correctness.

    Returns:
        Maximum quantity (multiple of lot_size) such that
        quantity * price + buy_fees(quantity) <= cash_budget
        and quantity * price <= adv * max_participation.
    """
    if reference_price <= 0 or cash_budget <= 0 or adv <= 0:
        return 0

    # Capacity constraint: trade value <= adv * participation
    capacity_value = adv * max_participation
    max_qty_by_capacity = int(capacity_value / reference_price)

    # Budget constraint (rough upper bound without fees)
    max_qty_by_budget = int(cash_budget / reference_price)

    # Combined upper bound, lot-aligned
    upper = min(max_qty_by_capacity, max_qty_by_budget)
    upper = (upper // lot_size) * lot_size

    if upper <= 0:
        return 0

    # Binary search on lot-aligned quantities
    lo = 0
    hi = upper // lot_size  # search in units of lots

    while lo < hi:
        mid = (lo + hi + 1) // 2
        qty = mid * lot_size
        fees = fee_policy.estimate_buy_fees(qty, reference_price, adv)
        total_cost = qty * reference_price + fees.total
        if total_cost <= cash_budget:
            lo = mid
        else:
            hi = mid - 1

    return lo * lot_size
