"""Residual order management — §12.2.

Order lifecycle: PENDING -> PARTIAL -> FILLED or CANCELLED.
Attempt status: FILLED, PARTIAL, BLOCKED (not terminal for parent order).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"


class AttemptStatus(str, Enum):
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"


@dataclass
class Order:
    """One parent order."""

    ts_code: str
    requested: int  # positive = buy, negative = sell
    event_id: str
    status: OrderStatus = OrderStatus.PENDING
    filled: int = 0
    attempts: list[dict] = field(default_factory=list)
    quantity_basis: float = 1.0
    corporate_action_adjustments: list[dict] = field(default_factory=list)

    @property
    def remaining(self) -> int:
        return abs(self.requested) - self.filled

    @property
    def direction(self) -> str:
        return "BUY" if self.requested > 0 else "SELL"


class OrderManager:
    """§12.2 — Manages order lifecycle across rebalance events."""

    def __init__(self) -> None:
        self._active: dict[str, Order] = {}
        self._history: list[Order] = []

    def create_orders(self, deltas: dict[str, int], event_id: str) -> None:
        """Create new orders, cancelling old pending/partial ones.

        Args:
            deltas: ts_code -> signed share delta (positive=buy, negative=sell).
            event_id: Target event identifier.
        """
        # Cancel/archive all active orders before creating new ones
        for code, order in list(self._active.items()):
            if order.status in (OrderStatus.PENDING, OrderStatus.PARTIAL):
                order.status = OrderStatus.CANCELLED
            # Move to history (filled orders preserved as-is)
            self._history.append(order)
            del self._active[code]

        # Create new orders
        for code, delta in sorted(deltas.items()):
            if delta == 0:
                continue
            order = Order(ts_code=code, requested=delta, event_id=event_id)
            self._active[code] = order

    def record_attempt(
        self,
        ts_code: str,
        filled: int,
        attempt_status: AttemptStatus,
        details: dict | None = None,
    ) -> None:
        """Record an execution attempt against an active order.

        Args:
            ts_code: ETF code.
            filled: Shares filled in this attempt.
            attempt_status: FILLED, PARTIAL, or BLOCKED.
        """
        order = self._active.get(ts_code)
        if order is None:
            return

        order.attempts.append({
            "filled": filled,
            "status": attempt_status.value,
            "quantity_basis": order.quantity_basis,
            "cumulative_filled_after_attempt": order.filled + filled,
            **(details or {}),
        })
        order.filled += filled

        # Update parent order status
        if order.filled >= abs(order.requested):
            order.status = OrderStatus.FILLED
        elif order.filled > 0:
            order.status = OrderStatus.PARTIAL
        # BLOCKED with 0 filled: stays PENDING (retryable)

    def get_order(self, ts_code: str) -> Order | None:
        """Get active order for a code, or most recent from history."""
        if ts_code in self._active:
            return self._active[ts_code]
        # Check history (most recent first)
        for order in reversed(self._history):
            if order.ts_code == ts_code:
                return order
        return None

    def get_history(self, ts_code: str) -> list[Order]:
        """Get all orders (active + history) for a code."""
        result = [o for o in self._history if o.ts_code == ts_code]
        if ts_code in self._active:
            result.append(self._active[ts_code])
        return result

    def get_pending_orders(self) -> list[Order]:
        """Get all active orders, sells first then buys."""
        active = list(self._active.values())
        sells = [o for o in active if o.requested < 0]
        buys = [o for o in active if o.requested > 0]
        return sorted(sells, key=lambda o: o.ts_code) + sorted(buys, key=lambda o: o.ts_code)

    def adjust_for_factor(
        self, ts_code: str, scale: float, *, trade_date: str = "", corporate_action_id: str = "",
    ) -> None:
        """Restate an active residual order in post-corporate-action shares."""
        order = self._active.get(ts_code)
        if order is None or scale <= 0:
            return
        before = {
            "requested": abs(order.requested), "filled": order.filled,
            "remaining": order.remaining, "quantity_basis": order.quantity_basis,
        }
        sign = 1 if order.requested > 0 else -1
        order.requested = sign * int(abs(order.requested) * scale)
        order.filled = int(order.filled * scale)
        order.quantity_basis *= scale
        order.corporate_action_adjustments.append({
            "corporate_action_id": corporate_action_id,
            "trade_date": trade_date,
            "scale": scale,
            "before": before,
            "after": {
                "requested": abs(order.requested), "filled": order.filled,
                "remaining": order.remaining, "quantity_basis": order.quantity_basis,
            },
        })
        if order.remaining <= 0:
            order.status = OrderStatus.FILLED
