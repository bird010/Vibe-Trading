"""Per-cohort ledger maintaining cash, positions, and fund identity.

Implements design §8.6. Cash never negative. No cross-cohort sharing.
"""

from __future__ import annotations

import logging
import math

from backtest.stockpred.cohort.contracts import CohortStatus, ExecutionEvent

logger = logging.getLogger(__name__)


class CohortLedger:
    """Mutable ledger for a single cohort.

    Invariant (§3.3): committed_capital =
        available_cash + position_cost_basis + total_fees_paid - total_exit_proceeds + total_exit_proceeds
    Simplified: committed = cash + residual_at_cost + fees - exit_net + exit_net
    Actual: committed = cash + sum(pos_qty * entry_price) + fees_paid - exit_proceeds + exit_proceeds

    The true fund identity:
        committed = available_cash + position_cost_basis + total_fees_paid + total_exit_proceeds - total_exit_proceeds
    Which simplifies to:
        committed = available_cash + position_cost_basis + total_fees_paid
    Wait - exit proceeds ADD to cash. So:
        committed = available_cash + position_cost_basis + total_fees_paid
    is only true if no exits happened. After exits:
        cash went up by exit_proceeds - exit_fees
        position went down
        fees went up by exit_fees

    Correct invariant:
        committed = available_cash + position_cost_basis + total_fees_paid - realized_pnl
    But simpler: track all outflows and inflows.
        committed = available_cash + position_cost_basis + total_fees_paid - net_exit_gains
    Actually the simplest correct form:
        committed = available_cash + sum(pos * entry_price) + total_fees
    This holds because:
        - Entry: cash -= (value + fees), positions += qty, fees += fees
          => cash + pos_cost + fees = (C - value - fees) + value + fees = C ✓
        - Exit: cash += (proceeds - fees), positions -= qty, fees += fees
          => cash + pos_cost + fees = (prev_cash + proceeds - fees) + (prev_pos - qty*entry) + (prev_fees + fees)
          = prev_cash + proceeds + prev_pos - qty*entry + prev_fees
          But proceeds = qty * exit_price, and we need pos at entry cost...

    Let's use the design's formulation (§3.3):
        C = idle_cash + position_value + paid_fees + exited_cash + residual
    Where position_value is at cost, exited_cash is net proceeds already in cash.
    Simplified: C = available_cash + position_cost_basis + total_fees_paid
    This works because available_cash already includes net exit proceeds.
    """

    def __init__(self, *, cohort_id: str, committed_capital: float, evaluation_date: str) -> None:
        self.cohort_id = cohort_id
        self.committed_capital = committed_capital
        self.available_cash = committed_capital
        self.evaluation_date = evaluation_date
        self.positions: dict[str, int] = {}
        self.status = CohortStatus.PLANNED
        self.total_fees_paid: float = 0.0
        self.total_exit_proceeds: float = 0.0  # net of exit fees
        self.realized_pnl: float = 0.0
        self._entry_prices: dict[str, float] = {}
        self._initial_entry_values: dict[str, float] = {}
        self._initial_entry_quantities: dict[str, int] = {}
        self._initial_entry_dates: dict[str, str] = {}
        self._entry_requested_value: float = 0.0
        self._entry_executed_value: float = 0.0
        self._total_requested_value: float = 0.0  # includes rejected orders
        self._peak_deployed_value: float = 0.0  # max capital deployed during holding
        self._processed_event_ids: set[str] = set()

    def _fail_execution(self) -> None:
        self.status = CohortStatus.FAILED_EXECUTION

    def _accept_event(self, event: ExecutionEvent, side: str) -> bool:
        if event.order_id in self._processed_event_ids or event.cohort_id != self.cohort_id or event.side != side:
            self._fail_execution()
            return False
        self._processed_event_ids.add(event.order_id)
        return True

    @staticmethod
    def _is_finite_nonnegative(value: object) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0

    def _validate_event(self, event: ExecutionEvent) -> bool:
        if not isinstance(event.requested_quantity_known, bool):
            return False
        quantities = (event.requested_quantity, event.executed_quantity, event.remaining_quantity)
        if not all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in quantities):
            return False
        values = (event.requested_value, event.executed_value, event.price)
        if not all(self._is_finite_nonnegative(value) for value in values):
            return False
        if not isinstance(event.fee_components, dict) or not all(
            self._is_finite_nonnegative(value) for value in event.fee_components.values()
        ):
            return False
        total_fees = event.total_fees
        if not self._is_finite_nonnegative(total_fees) or total_fees > event.executed_value:
            return False
        if event.requested_quantity != event.executed_quantity + event.remaining_quantity:
            return False
        if not event.requested_quantity_known:
            return (
                event.status == "REJECTED"
                and event.requested_quantity == 0
                and event.executed_quantity == 0
                and event.remaining_quantity == 0
                and event.price == 0
                and event.executed_value == 0
                and total_fees == 0
            )
        if event.executed_quantity > 0 and (
            event.price <= 0
            or not math.isclose(
                event.executed_value,
                event.executed_quantity * event.price,
                rel_tol=1e-9,
                abs_tol=1e-6,
            )
        ):
            return False
        if event.status == "REJECTED":
            return event.executed_quantity == 0 and event.executed_value == 0 and total_fees == 0
        if event.status == "FILLED":
            return event.requested_quantity > 0 and event.executed_quantity == event.requested_quantity
        if event.status == "PARTIAL":
            return 0 < event.executed_quantity < event.requested_quantity and event.remaining_quantity > 0
        return False

    def apply_entry(self, event: ExecutionEvent) -> None:
        """Apply a buy execution event to the ledger."""
        if self.status == CohortStatus.FAILED_EXECUTION:
            return
        if not self._validate_event(event):
            self._fail_execution()
            return
        if not self._accept_event(event, "BUY"):
            return

        # Track ALL requested value (including rejected) for accurate fill_rate
        requested_value = event.requested_value
        self._total_requested_value += requested_value

        if event.status == "REJECTED" or event.executed_quantity <= 0:
            return

        code = event.code
        total_cost = event.executed_value + event.total_fees

        # Enforce non-negative cash
        if total_cost > self.available_cash:
            logger.warning(
                "Entry %s rejected by ledger: cost %.2f > cash %.2f",
                event.order_id, total_cost, self.available_cash,
            )
            self._fail_execution()
            return

        self.available_cash -= total_cost
        self.positions[code] = self.positions.get(code, 0) + event.executed_quantity
        self._entry_prices[code] = event.price
        self._initial_entry_values[code] = self._initial_entry_values.get(code, 0.0) + event.executed_value
        self._initial_entry_quantities[code] = self._initial_entry_quantities.get(code, 0) + event.executed_quantity
        self._initial_entry_dates.setdefault(code, event.trade_date)
        self.total_fees_paid += event.total_fees
        self._entry_requested_value += requested_value
        self._entry_executed_value += event.executed_value

        # Track peak deployed value for idle_cash calculation
        deployed = self.committed_capital - self.available_cash
        if deployed > self._peak_deployed_value:
            self._peak_deployed_value = deployed

        if self.status == CohortStatus.PLANNED:
            self.status = CohortStatus.HOLDING

    def apply_exit(self, event: ExecutionEvent) -> bool:
        """Apply a sell execution event to the ledger."""
        if self.status == CohortStatus.FAILED_EXECUTION:
            return False
        if not self._validate_event(event):
            self._fail_execution()
            return False
        if not self._accept_event(event, "SELL"):
            self._fail_execution()
            return False

        if event.executed_quantity <= 0:
            return True

        code = event.code
        # Direction validation: exit quantity must not exceed position
        current = self.positions.get(code, 0)
        if event.executed_quantity > current:
            self._fail_execution()
            return False

        net_proceeds = event.executed_value - event.total_fees

        # Compute realized P&L: gross profit (exit fees tracked in total_fees_paid)
        entry_price = self._entry_prices.get(code, 0.0)
        cost_basis = event.executed_quantity * entry_price
        self.realized_pnl += event.executed_value - cost_basis

        self.available_cash += net_proceeds
        self.total_exit_proceeds += net_proceeds
        self.total_fees_paid += event.total_fees

        remaining = current - event.executed_quantity
        if remaining <= 0:
            self.positions.pop(code, None)
            self._entry_prices.pop(code, None)
        else:
            self.positions[code] = remaining
        return True

    def begin_exit(self) -> None:
        """Transition to EXITING status."""
        if self.status in (CohortStatus.HOLDING, CohortStatus.ENTERING):
            self.status = CohortStatus.EXITING

    def finalize_exit(self) -> None:
        """Determine final status after exit attempts."""
        if self.status == CohortStatus.FAILED_EXECUTION:
            return
        has_positions = any(qty > 0 for qty in self.positions.values())
        if has_positions:
            self.status = CohortStatus.UNLIQUIDATED
        else:
            self.status = CohortStatus.LIQUIDATED

    def fund_identity_holds(self, entry_prices: dict[str, float] | None = None, tolerance: float = 0.01) -> bool:
        """Verify: committed + realized_pnl = available_cash + position_cost_basis + total_fees_paid."""
        prices = entry_prices or self._entry_prices
        position_cost = sum(
            qty * prices.get(code, 0.0) for code, qty in self.positions.items()
        )
        reconstructed = self.available_cash + position_cost + self.total_fees_paid - self.realized_pnl
        return abs(reconstructed - self.committed_capital) <= tolerance

    @property
    def position_cost_basis(self) -> float:
        return sum(qty * self._entry_prices.get(code, 0.0) for code, qty in self.positions.items())

    def initial_entry_cost(self, code: str, quantity: int | None = None) -> float:
        """Return immutable original notional for all or part of an entry."""
        total_quantity = self._initial_entry_quantities.get(code, 0)
        total_value = self._initial_entry_values.get(code, 0.0)
        if quantity is None:
            return total_value
        if quantity < 0 or quantity > total_quantity:
            raise ValueError(f"quantity {quantity} is outside initial position for {code}")
        return total_value * quantity / total_quantity if total_quantity else 0.0

    def initial_entry_date(self, code: str) -> str:
        """Return the immutable first execution date for a position."""
        return self._initial_entry_dates.get(code, "")

    @property
    def fill_rate(self) -> float:
        """Executed value / total requested value (includes rejected orders)."""
        if self._total_requested_value <= 0:
            return 0.0
        return self._entry_executed_value / self._total_requested_value

    @property
    def idle_cash_ratio(self) -> float:
        """Undeployed capital ratio: capital never deployed during holding.

        Uses peak deployed value to avoid counting exit proceeds as idle cash.
        """
        if self.committed_capital <= 0:
            return 0.0
        undeployed = self.committed_capital - self._peak_deployed_value
        return max(0.0, undeployed / self.committed_capital)

    @property
    def cost_ratio(self) -> float:
        if self.committed_capital <= 0:
            return 0.0
        return self.total_fees_paid / self.committed_capital
