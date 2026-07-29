"""Execution policy: T+1 entry, limit/suspension checks, causal ADV capacity, multi-day exit.

Implements design §8.4 and §27.1. Stateless policy object.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field

import pandas as pd

from backtest.stockpred.cohort.contracts import ExecutionEvent
from backtest.stockpred.execution.adv import compute_causal_adv
from backtest.stockpred.execution.costs import CostPolicy, DEFAULT_COST_POLICY, max_affordable_quantity

_LIMIT_EPSILON = 1e-6


@dataclass(frozen=True)
class MarketView:
    """Market data view for execution decisions."""

    market: pd.DataFrame
    trade_dates: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.trade_dates and not self.market.empty:
            dates = sorted(self.market["trade_date"].astype(str).unique())
            object.__setattr__(self, "trade_dates", dates)

    def next_trade_date(self, date: str) -> str | None:
        """First trade date strictly after the given date."""
        idx = bisect.bisect_right(self.trade_dates, date)
        return self.trade_dates[idx] if idx < len(self.trade_dates) else None

    def stock_row(self, code: str, date: str) -> pd.Series | None:
        """Get market row for a stock on a specific date."""
        mkt = self.market
        mask = (mkt["ts_code"].astype(str) == code) & (mkt["trade_date"].astype(str) == date)
        rows = mkt[mask]
        if rows.empty:
            return None
        return rows.iloc[0]


@dataclass(frozen=True)
class PositionInfo:
    """Information about a position to exit."""

    code: str
    quantity: int
    entry_date: str
    target_exit_date: str
    cohort_id: str = ""


@dataclass(frozen=True)
class ExecutionPolicy:
    """Stateless execution policy per §8.4.

    Covers: T+1 entry, suspension, limit-up/down, causal ADV capacity,
    lot alignment, multi-day exit continuation.
    """

    cost_policy: CostPolicy = DEFAULT_COST_POLICY
    max_participation: float = 0.05
    adv_lookback_days: int = 20
    min_adv_observations: int = 10
    max_exit_extension_days: int = 20
    lot_size: int = 100

    def execute_entry(
        self,
        *,
        code: str,
        signal_date: str,
        cash_budget: float,
        target_value: float,
        market_view: MarketView,
        cohort_id: str = "",
    ) -> ExecutionEvent:
        """Execute a buy order at T+1 open with causal ADV capacity."""
        entry_date = market_view.next_trade_date(signal_date)
        if entry_date is None:
            return self._rejected_event(code, signal_date, "no_trade_date", cohort_id=cohort_id, requested_value=target_value)

        row = market_view.stock_row(code, entry_date)
        if row is None:
            return self._rejected_event(code, signal_date, "no_market_data", trade_date=entry_date, cohort_id=cohort_id, requested_value=target_value)

        # Check suspension
        vol = pd.to_numeric(row.get("vol"), errors="coerce")
        open_price = pd.to_numeric(row.get("open"), errors="coerce")
        has_known_price = pd.notna(open_price) and math.isfinite(float(open_price)) and open_price > 0
        if not has_known_price or pd.isna(vol) or vol <= 0:
            return self._rejected_event(
                code, signal_date, "suspended", trade_date=entry_date, cohort_id=cohort_id,
                requested_value=target_value, price=float(open_price) if has_known_price else 0.0,
            )

        # Check limit-up (cannot buy at limit-up)
        up_limit = pd.to_numeric(row.get("up_limit"), errors="coerce")
        if pd.notna(up_limit) and open_price >= up_limit - _LIMIT_EPSILON:
            return self._rejected_event(
                code, signal_date, "limit_up", trade_date=entry_date, cohort_id=cohort_id,
                requested_value=target_value, price=float(open_price),
            )

        # Compute causal ADV as of signal date
        adv_result = compute_causal_adv(
            market_view.market,
            code,
            as_of_date=signal_date,
            lookback=self.adv_lookback_days,
            min_observations=self.min_adv_observations,
            trade_dates=market_view.trade_dates,
        )
        if not adv_result.is_valid:
            return self._rejected_event(
                code, signal_date, "adv_insufficient", trade_date=entry_date, cohort_id=cohort_id,
                requested_value=target_value, price=float(open_price),
            )

        # Solve max affordable quantity
        price = float(open_price)
        requested_quantity = int(target_value / price) if price > 0 else 0
        quantity = max_affordable_quantity(
            cash_budget=cash_budget,
            reference_price=price,
            adv=adv_result.adv_value,
            max_participation=self.max_participation,
            fee_policy=self.cost_policy,
            lot_size=self.lot_size,
        )

        # Cap by target value (don't buy more than requested)
        max_qty_by_target = int(target_value / price) if price > 0 else 0
        max_qty_by_target = (max_qty_by_target // self.lot_size) * self.lot_size
        quantity = min(quantity, max_qty_by_target)

        if quantity <= 0:
            return self._rejected_event(
                code, signal_date, "capacity", trade_date=entry_date, cohort_id=cohort_id,
                requested_value=target_value, price=price,
            )

        executed_value = quantity * price
        fees = self.cost_policy.estimate_buy_fees(quantity, price, adv_result.adv_value)

        # Determine status
        remaining_quantity = max(0, requested_quantity - quantity)
        if remaining_quantity == 0:
            status = "FILLED"
            reason = None
        else:
            status = "PARTIAL"
            reason = "capacity"

        return ExecutionEvent(
            order_id=f"entry_{code}_{signal_date}",
            cohort_id=cohort_id,
            trade_date=entry_date,
            code=code,
            side="BUY",
            requested_quantity=requested_quantity,
            executed_quantity=quantity,
            executed_value=executed_value,
            price=price,
            requested_value=target_value,
            fee_components={
                "commission": fees.commission,
                "stamp_duty": fees.stamp_duty,
                "transfer_fee": fees.transfer_fee,
                "slippage": fees.slippage,
                "market_impact": fees.market_impact,
            },
            status=status,
            reason_code=reason,
            remaining_quantity=remaining_quantity,
            market_data_as_of=signal_date,
        )

    def execute_exit(
        self,
        position: PositionInfo,
        *,
        market_view: MarketView,
    ) -> list[ExecutionEvent]:
        """Execute multi-day exit starting from target_exit_date.

        Uses causal ADV as of D-1 for each exit day D.
        Continues until fully liquidated or max_exit_extension_days reached.
        """
        events: list[ExecutionEvent] = []
        remaining = position.quantity
        code = position.code

        # Find starting position in trade calendar
        start_idx = bisect.bisect_left(market_view.trade_dates, position.target_exit_date)
        max_idx = start_idx + self.max_exit_extension_days

        day_idx = start_idx
        while remaining > 0 and day_idx < len(market_view.trade_dates) and day_idx <= max_idx:
            trade_date = market_view.trade_dates[day_idx]
            row = market_view.stock_row(code, trade_date)

            if row is None:
                events.append(self._rejected_exit_event(position, trade_date, remaining, "no_market_data"))
                day_idx += 1
                continue

            # Check if sellable
            open_price = pd.to_numeric(row.get("open"), errors="coerce")
            vol = pd.to_numeric(row.get("vol"), errors="coerce")
            down_limit = pd.to_numeric(row.get("down_limit"), errors="coerce")

            # Not sellable: no data, suspended, or limit-down
            if pd.isna(open_price) or pd.isna(vol) or vol <= 0:
                price = float(open_price) if pd.notna(open_price) and open_price > 0 else 0.0
                events.append(self._rejected_exit_event(position, trade_date, remaining, "suspended", price))
                day_idx += 1
                continue
            if pd.notna(down_limit) and open_price <= down_limit + _LIMIT_EPSILON:
                events.append(self._rejected_exit_event(position, trade_date, remaining, "limit_down", float(open_price)))
                day_idx += 1
                continue

            # Compute ADV as of previous trade date (D-1)
            prev_date = market_view.trade_dates[day_idx - 1] if day_idx > 0 else trade_date
            adv_result = compute_causal_adv(
                market_view.market,
                code,
                as_of_date=prev_date,
                lookback=self.adv_lookback_days,
                min_observations=self.min_adv_observations,
                trade_dates=market_view.trade_dates,
            )

            price = float(open_price)
            if not adv_result.is_valid or adv_result.adv_value <= 0:
                events.append(self._rejected_exit_event(position, trade_date, remaining, "adv_insufficient", price))
                day_idx += 1
                continue

            # Capacity for this day
            capacity_value = adv_result.adv_value * self.max_participation
            max_sell_by_capacity = int(capacity_value / price) if price > 0 else 0

            # Sell what we can (odd lots allowed on sell)
            sell_qty = min(remaining, max_sell_by_capacity)
            if sell_qty <= 0:
                events.append(self._rejected_exit_event(position, trade_date, remaining, "capacity", price))
                day_idx += 1
                continue

            requested_quantity = remaining
            executed_value = sell_qty * price
            fees = self.cost_policy.estimate_sell_fees(sell_qty, price, adv_result.adv_value)
            remaining -= sell_qty

            status = "FILLED" if remaining == 0 else "PARTIAL"
            events.append(
                ExecutionEvent(
                    order_id=f"exit_{code}_{position.target_exit_date}_{trade_date}",
                    cohort_id=position.cohort_id,
                    trade_date=trade_date,
                    code=code,
                    side="SELL",
                    requested_quantity=requested_quantity,
                    executed_quantity=sell_qty,
                    executed_value=executed_value,
                    price=price,
                    requested_value=requested_quantity * price,
                    fee_components={
                        "commission": fees.commission,
                        "stamp_duty": fees.stamp_duty,
                        "transfer_fee": fees.transfer_fee,
                        "slippage": fees.slippage,
                        "market_impact": fees.market_impact,
                    },
                    status=status,
                    reason_code="capacity" if status == "PARTIAL" else None,
                    remaining_quantity=remaining,
                    market_data_as_of=prev_date,
                )
            )
            day_idx += 1

        return events

    @staticmethod
    def _rejected_exit_event(
        position: PositionInfo,
        trade_date: str,
        requested_quantity: int,
        reason: str,
        price: float = 0.0,
    ) -> ExecutionEvent:
        return ExecutionEvent(
            order_id=f"exit_{position.code}_{position.target_exit_date}_{trade_date}",
            cohort_id=position.cohort_id,
            trade_date=trade_date,
            code=position.code,
            side="SELL",
            requested_quantity=requested_quantity,
            requested_value=requested_quantity * price,
            executed_quantity=0,
            executed_value=0.0,
            price=price,
            fee_components={},
            status="REJECTED",
            reason_code=reason,
            remaining_quantity=requested_quantity,
        )

    def _rejected_event(
        self, code: str, signal_date: str, reason: str, *, trade_date: str = "", cohort_id: str = "", requested_value: float = 0.0,
        price: float = 0.0,
    ) -> ExecutionEvent:
        requested_quantity_known = price > 0 and math.isfinite(price)
        requested_quantity = int(requested_value / price) if requested_quantity_known else 0
        return ExecutionEvent(
            order_id=f"entry_{code}_{signal_date}",
            cohort_id=cohort_id,
            trade_date=trade_date or signal_date,
            code=code,
            side="BUY",
            requested_quantity=requested_quantity,
            executed_quantity=0,
            executed_value=0.0,
            price=price if requested_quantity_known else 0.0,
            requested_value=requested_value,
            requested_quantity_known=requested_quantity_known,
            fee_components={},
            status="REJECTED",
            reason_code=reason,
            remaining_quantity=requested_quantity,
        )
