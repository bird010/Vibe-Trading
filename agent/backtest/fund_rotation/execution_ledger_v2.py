"""Execution fact ledger and diagnostics contract v2.

The v2 contract keeps parent orders, execution attempts, executed trades, and
corporate actions as separate facts. Diagnostics are derived only from this
ledger, so fill rates, turnover, cash costs, and corporate-action audit counts
each have a single explicit meaning.
"""

from __future__ import annotations

import math
import json
from dataclasses import dataclass, field, replace
from enum import Enum
from statistics import median
from typing import Any


METRIC_CONTRACT_VERSION = "execution_diagnostics_v2"
TRADING_DAYS_PER_YEAR = 252


class OrderDirection(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class ParentOrderStatus(str, Enum):
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


class AttemptStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    BLOCKED = "BLOCKED"
    INVALID = "INVALID"


class CorporateActionType(str, Enum):
    SHARE_SPLIT = "SHARE_SPLIT"
    SHARE_MERGE = "SHARE_MERGE"
    SHARE_CONVERSION = "SHARE_CONVERSION"
    CASH_DIVIDEND = "CASH_DIVIDEND"
    CASH_IN_LIEU = "CASH_IN_LIEU"


_SHARE_ADJUSTMENT_TYPES = {
    CorporateActionType.SHARE_SPLIT,
    CorporateActionType.SHARE_MERGE,
    CorporateActionType.SHARE_CONVERSION,
}
_DEFAULT_BUY_LOT_SIZE = 100


def _as_enum(enum_type: type[Enum], value: Any, field_name: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except ValueError as exc:
        if enum_type is ParentOrderStatus and value == AttemptStatus.BLOCKED.value:
            raise ValueError("BLOCKED is not a parent order status") from exc
        raise ValueError(f"unknown {field_name}: {value}") from exc


def _require_finite(value: float | int, field_name: str) -> None:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{field_name} must be finite")


def _require_non_negative(value: float | int, field_name: str) -> None:
    _require_finite(value, field_name)
    if float(value) < 0:
        raise ValueError(f"{field_name} must be non-negative")


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator > 0 else None


def _sum(values: list[float]) -> float:
    return float(sum(values))


@dataclass(frozen=True)
class ParentOrderRecord:
    order_id: str
    decision_id: str
    ts_code: str
    direction: OrderDirection | str
    created_date: str
    original_requested_quantity: int
    cumulative_filled_quantity: int
    remaining_quantity: int
    quantity_basis_id: str
    replacement_of_order_id: str = ""
    replacement_chain_id: str = ""
    corporate_action_id: str = ""
    status: ParentOrderStatus | str = ParentOrderStatus.OPEN
    completed_date: str = ""
    cancel_reason: str = ""
    reject_reason: str = ""
    lot_size: int = _DEFAULT_BUY_LOT_SIZE

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "direction",
            _as_enum(OrderDirection, self.direction, "order direction"),
        )
        object.__setattr__(
            self,
            "status",
            _as_enum(ParentOrderStatus, self.status, "parent order status"),
        )
        if not self.order_id:
            raise ValueError("order_id is required")
        if not self.quantity_basis_id:
            raise ValueError("quantity_basis_id is required")
        if self.lot_size < 1:
            raise ValueError("lot_size must be positive")
        for field_name in (
            "original_requested_quantity",
            "cumulative_filled_quantity",
            "remaining_quantity",
        ):
            _require_non_negative(getattr(self, field_name), field_name)
        if self.cumulative_filled_quantity > self.original_requested_quantity:
            raise ValueError("parent cumulative filled exceeds original requested quantity")
        if (
            self.remaining_quantity
            != self.original_requested_quantity - self.cumulative_filled_quantity
        ):
            raise ValueError(
                "parent quantity conservation requires remaining_quantity = "
                "original_requested_quantity - cumulative_filled_quantity"
            )


@dataclass(frozen=True)
class ExecutionAttemptRecord:
    attempt_id: str
    order_id: str
    attempt_number: int
    trade_date: str
    requested_quantity: int
    filled_quantity: int
    unfilled_quantity: int
    quantity_basis_id: str
    raw_price: float
    executed_price: float
    commission: float
    explicit_fee: float
    slippage_cost: float
    participation_rate: float
    status: AttemptStatus | str
    reason_code: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "status",
            _as_enum(AttemptStatus, self.status, "attempt status"),
        )
        if not self.attempt_id:
            raise ValueError("attempt_id is required")
        if not self.order_id:
            raise ValueError("attempt order_id is required")
        if not self.quantity_basis_id:
            raise ValueError("attempt quantity_basis_id is required")
        _require_non_negative(self.attempt_number, "attempt_number")
        if self.attempt_number < 1:
            raise ValueError("attempt_number must be at least 1")
        for field_name in (
            "requested_quantity",
            "filled_quantity",
            "unfilled_quantity",
            "raw_price",
            "executed_price",
            "commission",
            "explicit_fee",
            "participation_rate",
        ):
            _require_non_negative(getattr(self, field_name), field_name)
        _require_finite(self.slippage_cost, "slippage_cost")
        if self.filled_quantity > self.requested_quantity:
            raise ValueError("attempt filled_quantity exceeds requested_quantity")
        if self.unfilled_quantity != self.requested_quantity - self.filled_quantity:
            raise ValueError(
                "attempt quantity conservation requires unfilled_quantity = "
                "requested_quantity - filled_quantity"
            )
        if self.status is AttemptStatus.BLOCKED and self.filled_quantity != 0:
            raise ValueError("BLOCKED attempts must have zero filled_quantity")


@dataclass(frozen=True)
class ExecutedTradeRecord:
    trade_id: str
    attempt_id: str
    order_id: str
    ts_code: str
    direction: OrderDirection | str
    quantity: int
    quantity_basis_id: str
    price: float
    notional: float
    commission: float
    explicit_fee: float
    slippage_cost: float
    trade_date: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "direction",
            _as_enum(OrderDirection, self.direction, "trade direction"),
        )
        if not self.trade_id:
            raise ValueError("trade_id is required")
        if not self.attempt_id:
            raise ValueError("trade attempt_id is required")
        if not self.order_id:
            raise ValueError("trade order_id is required")
        if not self.quantity_basis_id:
            raise ValueError("trade quantity_basis_id is required")
        _require_non_negative(self.quantity, "trade quantity")
        if self.quantity <= 0:
            raise ValueError("executed trade requires filled > 0")
        for field_name in ("price", "notional", "commission", "explicit_fee"):
            _require_non_negative(getattr(self, field_name), field_name)
        _require_finite(self.slippage_cost, "slippage_cost")
        expected_notional = self.quantity * self.price
        if not math.isclose(self.notional, expected_notional, rel_tol=1e-9, abs_tol=1e-7):
            raise ValueError("trade notional must equal quantity * price")


@dataclass(frozen=True)
class CorporateActionRecord:
    corporate_action_id: str
    ts_code: str
    action_type: CorporateActionType | str
    effective_date: str
    old_quantity: int
    new_quantity: int
    old_cost_basis: float
    new_cost_basis: float
    adjustment_factor: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "action_type",
            _as_enum(CorporateActionType, self.action_type, "corporate action type"),
        )
        if not self.corporate_action_id:
            raise ValueError("corporate_action_id is required")
        if not self.ts_code:
            raise ValueError("corporate_action ts_code is required")
        for field_name in (
            "old_quantity",
            "new_quantity",
            "old_cost_basis",
            "new_cost_basis",
            "adjustment_factor",
        ):
            _require_non_negative(getattr(self, field_name), field_name)
        if self.adjustment_factor <= 0:
            raise ValueError("adjustment_factor must be positive")


@dataclass(frozen=True)
class ExecutionLedger:
    parent_orders: tuple[ParentOrderRecord, ...] = field(default_factory=tuple)
    attempts: tuple[ExecutionAttemptRecord, ...] = field(default_factory=tuple)
    trades: tuple[ExecutedTradeRecord, ...] = field(default_factory=tuple)
    corporate_actions: tuple[CorporateActionRecord, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "parent_orders", tuple(self.parent_orders))
        object.__setattr__(self, "attempts", tuple(self.attempts))
        object.__setattr__(self, "trades", tuple(self.trades))
        object.__setattr__(self, "corporate_actions", tuple(self.corporate_actions))
        self._validate()

    def _validate(self) -> None:
        parents_by_id = {parent.order_id: parent for parent in self.parent_orders}
        if len(parents_by_id) != len(self.parent_orders):
            raise ValueError("duplicate parent order_id")

        corporate_actions_by_id = {
            action.corporate_action_id: action for action in self.corporate_actions
        }
        if len(corporate_actions_by_id) != len(self.corporate_actions):
            raise ValueError("duplicate corporate_action_id")

        for parent in self.parent_orders:
            lineage_fields = (
                parent.replacement_of_order_id,
                parent.replacement_chain_id,
                parent.corporate_action_id,
            )
            if any(lineage_fields):
                if not all(lineage_fields):
                    raise ValueError("replacement lineage requires old parent, chain, and corporate action")
                if parent.replacement_of_order_id not in parents_by_id:
                    raise ValueError("replacement lineage references unknown parent")
                if parent.corporate_action_id not in corporate_actions_by_id:
                    raise ValueError("replacement lineage references unknown corporate action")
                old_parent = parents_by_id[parent.replacement_of_order_id]
                action = corporate_actions_by_id[parent.corporate_action_id]
                if old_parent.status is not ParentOrderStatus.CANCELED:
                    raise ValueError("replacement old parent must be CANCELED")
                if old_parent.cancel_reason != "CORPORATE_ACTION_REPLACED":
                    raise ValueError(
                        "replacement old parent cancel_reason must be CORPORATE_ACTION_REPLACED"
                    )
                if action.action_type not in _SHARE_ADJUSTMENT_TYPES:
                    raise ValueError("replacement requires a share adjustment corporate action")
                if action.ts_code != parent.ts_code or action.ts_code != old_parent.ts_code:
                    raise ValueError("corporate action ts_code must match replacement lineage")
                if parent.quantity_basis_id == old_parent.quantity_basis_id:
                    raise ValueError(
                        "replacement quantity_basis_id must differ from old parent"
                    )
                adjusted_residual = _replacement_residual_quantity(
                    old_parent.direction,
                    old_parent.remaining_quantity,
                    action.adjustment_factor,
                    lot_size=parent.lot_size,
                )
                if parent.original_requested_quantity != adjusted_residual:
                    raise ValueError(
                        "replacement original_requested_quantity must equal adjusted residual"
                    )

        attempts_by_id = {attempt.attempt_id: attempt for attempt in self.attempts}
        if len(attempts_by_id) != len(self.attempts):
            raise ValueError("duplicate attempt_id")

        attempts_by_parent: dict[str, list[ExecutionAttemptRecord]] = {
            parent.order_id: [] for parent in self.parent_orders
        }
        active_attempt_dates: set[tuple[str, str]] = set()
        for attempt in self.attempts:
            parent = parents_by_id.get(attempt.order_id)
            if parent is None:
                raise ValueError(f"attempt {attempt.attempt_id} references unknown parent")
            if attempt.quantity_basis_id != parent.quantity_basis_id:
                raise ValueError("attempt quantity_basis_id must match parent quantity_basis_id")
            key = (attempt.order_id, attempt.trade_date)
            if key in active_attempt_dates:
                raise ValueError("duplicate active attempt for parent on trade_date")
            active_attempt_dates.add(key)
            attempts_by_parent[attempt.order_id].append(attempt)

        for parent in self.parent_orders:
            parent_attempts = attempts_by_parent[parent.order_id]
            total_filled = sum(attempt.filled_quantity for attempt in parent_attempts)
            if total_filled > parent.original_requested_quantity:
                raise ValueError("attempt cumulative filled exceeds parent original requested quantity")
            if total_filled != parent.cumulative_filled_quantity:
                raise ValueError(
                    "attempt cumulative filled must equal parent cumulative_filled_quantity"
                )
            if parent.status is ParentOrderStatus.FILLED and parent.remaining_quantity != 0:
                raise ValueError("FILLED parent must have zero remaining quantity")
            if parent.status is ParentOrderStatus.FILLED and parent.cumulative_filled_quantity != parent.original_requested_quantity:
                raise ValueError("FILLED parent must have cumulative filled equal to original requested quantity")
            if parent.status in {ParentOrderStatus.OPEN, ParentOrderStatus.PARTIALLY_FILLED} and parent.remaining_quantity <= 0:
                raise ValueError(f"{parent.status.value} parent must have positive remaining quantity")
            cumulative_filled = 0
            for attempt in sorted(parent_attempts, key=lambda item: item.attempt_number):
                if cumulative_filled >= parent.original_requested_quantity:
                    raise ValueError("FILLED parent cannot have later attempts")
                if attempt.status is AttemptStatus.FILLED and attempt.filled_quantity != attempt.requested_quantity:
                    raise ValueError("FILLED attempt must have filled equal to requested quantity")
                if attempt.status is AttemptStatus.PARTIALLY_FILLED and not 0 < attempt.filled_quantity < attempt.requested_quantity:
                    raise ValueError("PARTIALLY_FILLED attempt must have partial positive fill")
                cumulative_filled += attempt.filled_quantity

        trades_by_id = {trade.trade_id: trade for trade in self.trades}
        if len(trades_by_id) != len(self.trades):
            raise ValueError("duplicate trade_id")
        trade_quantities_by_attempt: dict[str, int] = {}
        for trade in self.trades:
            attempt = attempts_by_id.get(trade.attempt_id)
            if attempt is None:
                raise ValueError(f"trade {trade.trade_id} references unknown attempt")
            if trade.order_id != attempt.order_id:
                raise ValueError("trade order_id must match attempt order_id")
            if trade.trade_date != attempt.trade_date:
                raise ValueError("trade trade_date must match attempt trade_date")
            parent = parents_by_id.get(trade.order_id)
            if parent is None:
                raise ValueError("trade references unknown parent")
            if trade.ts_code != parent.ts_code:
                raise ValueError("trade ts_code must match parent ts_code")
            if trade.direction is not parent.direction:
                raise ValueError("trade direction must match parent direction")
            if trade.quantity_basis_id != parent.quantity_basis_id:
                raise ValueError("trade quantity_basis_id must match parent quantity_basis_id")
            trade_quantities_by_attempt[trade.attempt_id] = (
                trade_quantities_by_attempt.get(trade.attempt_id, 0) + trade.quantity
            )
        for attempt_id, quantity in trade_quantities_by_attempt.items():
            if quantity > attempts_by_id[attempt_id].filled_quantity:
                raise ValueError("trade quantity exceeds attempt filled_quantity")
        for attempt in self.attempts:
            trade_quantity = trade_quantities_by_attempt.get(attempt.attempt_id, 0)
            if trade_quantity != attempt.filled_quantity:
                raise ValueError("trade quantity must equal attempt filled_quantity")


def compute_order_diagnostics(ledger: ExecutionLedger) -> dict[str, Any]:
    rates = {
        parent.order_id: _ratio(
            float(parent.cumulative_filled_quantity),
            float(parent.original_requested_quantity),
        )
        for parent in ledger.parent_orders
    }
    non_null_rates = [rate for rate in rates.values() if rate is not None]
    ordered_rates = sorted(non_null_rates)
    count = len(ledger.parent_orders)
    return {
        "order_count": count,
        "replacement_order_count": sum(
            1 for parent in ledger.parent_orders if parent.replacement_of_order_id
        ),
        "fully_filled_order_count": sum(
            1 for parent in ledger.parent_orders if parent.status is ParentOrderStatus.FILLED
        ),
        "partially_filled_order_count": sum(
            1 for parent in ledger.parent_orders
            if parent.status is ParentOrderStatus.PARTIALLY_FILLED
        ),
        "open_order_count": sum(
            1 for parent in ledger.parent_orders if parent.status is ParentOrderStatus.OPEN
        ),
        "canceled_order_count": sum(
            1 for parent in ledger.parent_orders if parent.status is ParentOrderStatus.CANCELED
        ),
        "expired_order_count": sum(
            1 for parent in ledger.parent_orders if parent.status is ParentOrderStatus.EXPIRED
        ),
        "rejected_order_count": sum(
            1 for parent in ledger.parent_orders if parent.status is ParentOrderStatus.REJECTED
        ),
        "order_completion_rate": _ratio(
            float(sum(1 for parent in ledger.parent_orders if parent.status is ParentOrderStatus.FILLED)),
            float(count),
        ),
        "mean_parent_fill_rate": (
            _sum(non_null_rates) / len(non_null_rates) if non_null_rates else None
        ),
        "median_parent_fill_rate": median(non_null_rates) if non_null_rates else None,
        "quantile_parent_fill_rate": {
            "p25": _quantile(ordered_rates, 0.25),
            "p75": _quantile(ordered_rates, 0.75),
        },
        "parent_fill_rates_by_order_id": rates,
    }


def _quantile(sorted_values: list[float], q: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    index = (len(sorted_values) - 1) * q
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return sorted_values[lower]
    fraction = index - lower
    return sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction


def compute_attempt_diagnostics(ledger: ExecutionLedger) -> dict[str, Any]:
    attempt_count = len(ledger.attempts)
    total_requested = sum(attempt.requested_quantity for attempt in ledger.attempts)
    total_filled = sum(attempt.filled_quantity for attempt in ledger.attempts)
    order_ids = {attempt.order_id for attempt in ledger.attempts}
    per_attempt_rates = {
        attempt.attempt_id: _ratio(
            float(attempt.filled_quantity),
            float(attempt.requested_quantity),
        )
        for attempt in ledger.attempts
    }
    fill_rates_by_order_id = {}
    for order_id in sorted(order_ids):
        order_attempts = [
            attempt for attempt in ledger.attempts if attempt.order_id == order_id
        ]
        fill_rates_by_order_id[order_id] = _ratio(
            float(sum(attempt.filled_quantity for attempt in order_attempts)),
            float(sum(attempt.requested_quantity for attempt in order_attempts)),
        )
    quantity_basis_ids = {attempt.quantity_basis_id for attempt in ledger.attempts}
    return {
        "attempt_count": attempt_count,
        "filled_attempt_count": sum(
            1 for attempt in ledger.attempts if attempt.status is AttemptStatus.FILLED
        ),
        "partial_attempt_count": sum(
            1 for attempt in ledger.attempts
            if attempt.status is AttemptStatus.PARTIALLY_FILLED
        ),
        "blocked_attempt_count": sum(
            1 for attempt in ledger.attempts if attempt.status is AttemptStatus.BLOCKED
        ),
        "attempt_quantity_fill_rate": (
            _ratio(float(total_filled), float(total_requested))
            if len(order_ids) <= 1 and len(quantity_basis_ids) <= 1 else None
        ),
        "attempt_quantity_fill_rate_by_order_id": fill_rates_by_order_id,
        "blocked_attempt_rate": _ratio(
            float(sum(1 for attempt in ledger.attempts if attempt.status is AttemptStatus.BLOCKED)),
            float(attempt_count),
        ),
        "attempt_fill_rates_by_attempt_id": per_attempt_rates,
    }


def compute_trade_diagnostics(
    ledger: ExecutionLedger,
    average_portfolio_nav: float,
    evaluation_days: int | None = None,
    annualization_basis: int = TRADING_DAYS_PER_YEAR,
) -> dict[str, Any]:
    _require_non_negative(average_portfolio_nav, "average_portfolio_nav")
    if evaluation_days is not None:
        _require_non_negative(evaluation_days, "evaluation_days")
    buy_trades = [trade for trade in ledger.trades if trade.direction is OrderDirection.BUY]
    sell_trades = [trade for trade in ledger.trades if trade.direction is OrderDirection.SELL]
    buy_notional = sum(trade.notional for trade in buy_trades)
    sell_notional = sum(trade.notional for trade in sell_trades)
    total_notional = buy_notional + sell_notional
    commission = sum(trade.commission for trade in ledger.trades)
    explicit_fee = sum(trade.explicit_fee for trade in ledger.trades)
    explicit_cash_cost = commission + explicit_fee
    slippage_opportunity_cost = sum(trade.slippage_cost for trade in ledger.trades)
    one_way_turnover = (
        0.5 * total_notional / average_portfolio_nav
        if average_portfolio_nav > 0 else None
    )
    annualized_one_way_turnover = (
        one_way_turnover * annualization_basis / evaluation_days
        if one_way_turnover is not None and evaluation_days else None
    )
    return {
        "executed_trade_count": len(ledger.trades),
        "buy_trade_count": len(buy_trades),
        "sell_trade_count": len(sell_trades),
        "total_notional": float(total_notional),
        "buy_notional": float(buy_notional),
        "sell_notional": float(sell_notional),
        "commission": float(commission),
        "explicit_fee": float(explicit_fee),
        "explicit_cash_cost": float(explicit_cash_cost),
        "slippage_opportunity_cost": float(slippage_opportunity_cost),
        "commission_to_average_nav": _ratio(float(commission), average_portfolio_nav),
        "explicit_fee_to_average_nav": _ratio(float(explicit_fee), average_portfolio_nav),
        "explicit_cash_cost_to_average_nav": _ratio(float(explicit_cash_cost), average_portfolio_nav),
        "slippage_opportunity_cost_to_average_nav": _ratio(
            float(slippage_opportunity_cost),
            average_portfolio_nav,
        ),
        "buy_cash_out": float(
            sum(trade.notional + trade.commission + trade.explicit_fee for trade in buy_trades)
        ),
        "sell_cash_in": float(
            sum(trade.notional - trade.commission - trade.explicit_fee for trade in sell_trades)
        ),
        "gross_traded_notional_ratio": _ratio(float(total_notional), average_portfolio_nav),
        "one_way_turnover": one_way_turnover,
        "annualized_one_way_turnover": annualized_one_way_turnover,
    }


def compute_corporate_action_diagnostics(ledger: ExecutionLedger) -> dict[str, int]:
    share_actions = [
        action
        for action in ledger.corporate_actions
        if action.action_type in _SHARE_ADJUSTMENT_TYPES
    ]
    adjusted_positions = [
        action for action in share_actions
        if action.old_quantity != action.new_quantity
    ]
    return {
        "corporate_action_count": len(ledger.corporate_actions),
        "share_adjustment_count": len(share_actions),
        "adjusted_position_count": len(adjusted_positions),
    }


def compute_execution_diagnostics_v2(
    ledger: ExecutionLedger,
    average_portfolio_nav: float,
    evaluation_days: int | None = None,
) -> dict[str, Any]:
    return {
        "metric_contract_version": METRIC_CONTRACT_VERSION,
        "orders": compute_order_diagnostics(ledger),
        "attempts": compute_attempt_diagnostics(ledger),
        "trades": compute_trade_diagnostics(
            ledger,
            average_portfolio_nav=average_portfolio_nav,
            evaluation_days=evaluation_days,
        ),
        "corporate_actions": compute_corporate_action_diagnostics(ledger),
    }


def build_execution_ledger_from_pipeline_result(result: Any) -> ExecutionLedger:
    """Adapt legacy PipelineResult execution facts into the formal v2 ledger.

    The legacy execution loop remains the source of fills/orders for now.  This
    adapter is the narrow boundary that translates its serialized parent orders
    and trade events into explicit v2 parent/attempt/trade/corporate-action
    facts before diagnostics are computed.
    """

    trade_events = [
        event
        for event in getattr(result, "trade_events", [])
        if str(event.get("event_type", "")) != "CORPORATE_ACTION"
    ]
    events_by_attempt_id = {
        str(event.get("attempt_id", "")): event
        for event in trade_events
        if str(event.get("attempt_id", ""))
    }
    order_rows_by_id: dict[str, list[dict[str, Any]]] = {}
    for row in getattr(result, "orders", []):
        order_id = str(row.get("order_id", ""))
        if order_id:
            order_rows_by_id.setdefault(order_id, []).append(row)

    parent_orders: list[ParentOrderRecord] = []
    attempts: list[ExecutionAttemptRecord] = []
    quantity_basis_by_order: dict[str, str] = {}
    adapted_attempt_ids: dict[str, tuple[str, str]] = {}
    residual_adjustment_factor_by_ca: dict[str, float] = {}
    seen_parents: set[str] = set()
    for order_id, order_rows in order_rows_by_id.items():
        row = order_rows[0]
        ts_code = str(row.get("ts_code", ""))
        lot_size = _lot_size_from_row(row)
        adjustments = _corporate_action_adjustments(row)
        if adjustments:
            before = dict(adjustments[0].get("before", {}) or {})
            old_quantity_basis = before.get(
                "quantity_basis",
                row.get("attempt_quantity_basis", 1.0),
            )
            old_quantity_basis_id = _quantity_basis_id(ts_code, old_quantity_basis)
            old_attempt_rows = [
                attempt_row
                for attempt_row in order_rows
                if _quantity_basis_id(
                    ts_code,
                    attempt_row.get("attempt_quantity_basis", old_quantity_basis),
                )
                == old_quantity_basis_id
                and str(attempt_row.get("attempt_status", "")) != "NOT_ATTEMPTED"
            ]
            old_filled = sum(
                int(float(attempt_row.get("attempt_filled", 0) or 0))
                for attempt_row in old_attempt_rows
            )
            old_requested = int(float(before.get("requested", row.get("requested", 0)) or 0))
            old_remaining = int(float(before.get("remaining", max(old_requested - old_filled, 0)) or 0))
            parent_orders.append(
                ParentOrderRecord(
                    order_id=order_id,
                    decision_id=str(row.get("event_id", "")) or order_id,
                    ts_code=ts_code,
                    direction=str(row.get("direction", "")),
                    created_date=_created_date_from_event_id(str(row.get("event_id", ""))),
                    original_requested_quantity=old_requested,
                    cumulative_filled_quantity=old_filled,
                    remaining_quantity=old_remaining,
                    quantity_basis_id=old_quantity_basis_id,
                    status=ParentOrderStatus.CANCELED,
                    completed_date=str(adjustments[0].get("trade_date", "")),
                    cancel_reason="CORPORATE_ACTION_REPLACED",
                )
            )
            seen_parents.add(order_id)
            quantity_basis_by_order[order_id] = old_quantity_basis_id

            source_parent_id = order_id
            source_remaining = old_remaining
            source_quantity_basis = old_quantity_basis
            for index, adjustment in enumerate(adjustments, 1):
                after = dict(adjustment.get("after", {}) or {})
                replacement_order_id = f"{order_id}-R{index}"
                adjustment_before = dict(adjustment.get("before", {}) or {})
                source_remaining = int(
                    float(adjustment_before.get("remaining", source_remaining) or 0)
                )
                replacement_quantity_basis = after.get(
                    "quantity_basis",
                    row.get("current_quantity_basis", source_quantity_basis),
                )
                replacement_quantity_basis_id = _quantity_basis_id(
                    ts_code,
                    replacement_quantity_basis,
                )
                replacement_attempt_rows = [
                    attempt_row
                    for attempt_row in order_rows
                    if _quantity_basis_id(
                        ts_code,
                        attempt_row.get("attempt_quantity_basis", replacement_quantity_basis),
                    )
                    == replacement_quantity_basis_id
                    and str(attempt_row.get("attempt_status", "")) != "NOT_ATTEMPTED"
                ]
                replacement_filled = sum(
                    int(float(attempt_row.get("attempt_filled", 0) or 0))
                    for attempt_row in replacement_attempt_rows
                )
                scale = float(adjustment.get("scale", 1.0) or 1.0)
                replacement_requested = _replacement_residual_quantity(
                    str(row.get("direction", "")),
                    source_remaining,
                    scale,
                    lot_size=lot_size,
                )
                if replacement_filled > replacement_requested:
                    raise ValueError(
                        "replacement filled quantity exceeds corporate-action residual"
                    )
                residual_adjustment_factor_by_ca[str(adjustment.get("corporate_action_id", ""))] = (
                    replacement_requested / source_remaining if source_remaining > 0 else 1.0
                )
                parent_orders.append(
                    ParentOrderRecord(
                        order_id=replacement_order_id,
                        decision_id=str(row.get("event_id", "")) or replacement_order_id,
                        ts_code=ts_code,
                        direction=str(row.get("direction", "")),
                        created_date=str(adjustment.get("trade_date", "")),
                        original_requested_quantity=replacement_requested,
                        cumulative_filled_quantity=replacement_filled,
                        remaining_quantity=max(replacement_requested - replacement_filled, 0),
                        quantity_basis_id=replacement_quantity_basis_id,
                        replacement_of_order_id=source_parent_id,
                        replacement_chain_id=order_id,
                        corporate_action_id=str(adjustment.get("corporate_action_id", "")),
                        lot_size=lot_size,
                        status=(
                            ParentOrderStatus.CANCELED
                            if index < len(adjustments)
                            else _parent_status(row.get("final_status", ""))
                        ),
                        completed_date=(
                            str(adjustment.get("trade_date", ""))
                            if index < len(adjustments)
                            else ""
                        ),
                        cancel_reason=(
                            "CORPORATE_ACTION_REPLACED"
                            if index < len(adjustments)
                            else ""
                        ),
                    )
                )
                quantity_basis_by_order[replacement_order_id] = replacement_quantity_basis_id
                source_parent_id = replacement_order_id
                source_remaining = max(replacement_requested - replacement_filled, 0)
                source_quantity_basis = replacement_quantity_basis
        else:
            quantity_basis_id = _quantity_basis_id(
                ts_code,
                row.get("current_quantity_basis", row.get("attempt_quantity_basis", 1.0)),
            )
            quantity_basis_by_order[order_id] = quantity_basis_id
            attempt_fill_sum = sum(
                int(float(attempt_row.get("attempt_filled", 0) or 0))
                for attempt_row in order_rows
                if str(attempt_row.get("attempt_status", "")) != "NOT_ATTEMPTED"
            )
            remaining = int(float(row.get("remaining", 0) or 0))
            requested = max(
                int(float(row.get("requested", 0) or 0)),
                attempt_fill_sum + max(remaining, 0),
            )
            filled = attempt_fill_sum
            if order_id not in seen_parents:
                parent_orders.append(
                    ParentOrderRecord(
                        order_id=order_id,
                        decision_id=str(row.get("event_id", "")) or order_id,
                        ts_code=ts_code,
                        direction=str(row.get("direction", "")),
                        created_date=_created_date_from_event_id(str(row.get("event_id", ""))),
                        original_requested_quantity=requested,
                        cumulative_filled_quantity=filled,
                        remaining_quantity=max(requested - filled, 0),
                        quantity_basis_id=quantity_basis_id,
                        status=_parent_status(row.get("final_status", "")),
                    )
                )
                seen_parents.add(order_id)

        for row in order_rows:
            attempt_status_raw = str(row.get("attempt_status", ""))
            if attempt_status_raw == "NOT_ATTEMPTED":
                continue
            attempt_number = int(float(row.get("attempt_number", 0) or 0))
            if attempt_number < 1:
                continue
            original_attempt_id = f"{order_id}-A{attempt_number}"
            attempt_quantity_basis_id = _quantity_basis_id(
                ts_code,
                row.get("attempt_quantity_basis", row.get("current_quantity_basis", 1.0)),
            )
            adapted_order_id = _adapted_order_id_for_attempt(
                order_id,
                attempt_quantity_basis_id,
                quantity_basis_by_order,
            )
            attempt_id = (
                original_attempt_id
                if adapted_order_id == order_id
                else f"{adapted_order_id}-A{attempt_number}"
            )
            adapted_attempt_ids[original_attempt_id] = (adapted_order_id, attempt_id)
            event = events_by_attempt_id.get(original_attempt_id, {})
            attempt_requested = int(
                float(event.get("requested", row.get("requested", 0)) or 0)
            )
            attempt_filled = int(float(row.get("attempt_filled", 0) or 0))
            raw_price = float(event.get("raw_open", event.get("price", 0.0)) or 0.0)
            executed_price = float(event.get("price", 0.0) or 0.0)
            notional = abs(float(attempt_filled) * executed_price)
            slippage_cost = (
                notional * max(float(event.get("slippage_bps", 0.0) or 0.0), 0.0) / 10_000.0
            )
            attempts.append(
                ExecutionAttemptRecord(
                    attempt_id=attempt_id,
                    order_id=adapted_order_id,
                    attempt_number=attempt_number,
                    trade_date=str(row.get("trade_date", "") or event.get("trade_date", "")),
                    requested_quantity=attempt_requested,
                    filled_quantity=attempt_filled,
                    unfilled_quantity=max(attempt_requested - attempt_filled, 0),
                    quantity_basis_id=attempt_quantity_basis_id,
                    raw_price=raw_price,
                    executed_price=executed_price,
                    commission=float(event.get("commission", 0.0) or 0.0),
                    explicit_fee=float(event.get("explicit_fee", 0.0) or 0.0),
                    slippage_cost=slippage_cost,
                    participation_rate=float(event.get("participation_rate", 0.0) or 0.0),
                    status=_attempt_status(attempt_status_raw),
                    reason_code=str(event.get("reason", row.get("reason", "")) or ""),
                )
            )

    trades: list[ExecutedTradeRecord] = []
    for index, event in enumerate(trade_events, 1):
        filled = int(float(event.get("filled", 0) or 0))
        if filled <= 0:
            continue
        order_id = str(event.get("order_id", ""))
        attempt_id = str(event.get("attempt_id", ""))
        adapted_order_id, adapted_attempt_id = adapted_attempt_ids.get(
            attempt_id,
            (order_id, attempt_id),
        )
        ts_code = str(event.get("ts_code", ""))
        price = float(event.get("price", 0.0) or 0.0)
        notional = abs(float(filled) * price)
        trades.append(
            ExecutedTradeRecord(
                trade_id=f"{adapted_attempt_id or adapted_order_id}-T{index}",
                attempt_id=adapted_attempt_id,
                order_id=adapted_order_id,
                ts_code=ts_code,
                direction=str(event.get("action", "")),
                quantity=filled,
                quantity_basis_id=quantity_basis_by_order.get(
                    adapted_order_id,
                    _quantity_basis_id(ts_code, 1.0),
                ),
                price=price,
                notional=notional,
                commission=float(event.get("commission", 0.0) or 0.0),
                explicit_fee=float(event.get("explicit_fee", 0.0) or 0.0),
                slippage_cost=(
                    notional
                    * max(float(event.get("slippage_bps", 0.0) or 0.0), 0.0)
                    / 10_000.0
                ),
                trade_date=str(event.get("trade_date", "")),
            )
        )

    corporate_actions = [
        _corporate_action_from_event(
            event,
            adjustment_factor_override=residual_adjustment_factor_by_ca.get(
                str(event.get("corporate_action_id", ""))
            ),
        )
        for event in getattr(result, "trade_events", [])
        if str(event.get("event_type", "")) == "CORPORATE_ACTION"
    ]

    return ExecutionLedger(
        parent_orders=tuple(parent_orders),
        attempts=tuple(attempts),
        trades=tuple(trades),
        corporate_actions=tuple(corporate_actions),
    )


def compute_pipeline_execution_diagnostics_v2(
    result: Any,
    *,
    initial_capital: float,
) -> dict[str, Any]:
    ledger = build_execution_ledger_from_pipeline_result(result)
    equity = getattr(result, "executed_equity", None)
    if equity is None or getattr(equity, "empty", True):
        average_portfolio_nav = float(initial_capital)
        evaluation_days = None
    else:
        average_portfolio_nav = float(equity.mean()) * float(initial_capital)
        evaluation_days = int(len(equity))
    return compute_execution_diagnostics_v2(
        ledger,
        average_portfolio_nav=average_portfolio_nav,
        evaluation_days=evaluation_days,
    )


def _quantity_basis_id(ts_code: str, quantity_basis: object) -> str:
    return f"{ts_code}:shares:{float(quantity_basis or 1.0):.12g}"


def _replacement_residual_quantity(
    direction: OrderDirection | str,
    remaining_quantity: int,
    adjustment_factor: float,
    lot_size: int = _DEFAULT_BUY_LOT_SIZE,
) -> int:
    adjusted = int(float(remaining_quantity) * float(adjustment_factor))
    if _as_enum(OrderDirection, direction, "order direction") is OrderDirection.BUY:
        if lot_size < 1:
            raise ValueError("lot_size must be positive")
        return (adjusted // lot_size) * lot_size
    return adjusted


def _lot_size_from_row(row: dict[str, Any]) -> int:
    raw_lot_size = row.get("lot_size", _DEFAULT_BUY_LOT_SIZE)
    lot_size = int(raw_lot_size)
    if lot_size < 1:
        raise ValueError("order lot_size must be positive")
    return lot_size


def _created_date_from_event_id(event_id: str) -> str:
    parts = event_id.split("-")
    return parts[1] if len(parts) >= 3 and parts[0] == "SIG" else ""


def _corporate_action_adjustments(row: dict[str, Any]) -> list[dict[str, Any]]:
    raw = row.get("corporate_action_adjustments", "")
    if not raw:
        return []
    if isinstance(raw, list):
        return [dict(item) for item in raw if isinstance(item, dict)]
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [dict(item) for item in parsed if isinstance(item, dict)]


def _adapted_order_id_for_attempt(
    original_order_id: str,
    attempt_quantity_basis_id: str,
    quantity_basis_by_order: dict[str, str],
) -> str:
    if quantity_basis_by_order.get(original_order_id) == attempt_quantity_basis_id:
        return original_order_id
    for order_id, quantity_basis_id in quantity_basis_by_order.items():
        if (
            order_id.startswith(f"{original_order_id}-R")
            and quantity_basis_id == attempt_quantity_basis_id
        ):
            return order_id
    return original_order_id


def _parent_status(value: object) -> ParentOrderStatus:
    status = str(value)
    if status == "PENDING":
        return ParentOrderStatus.OPEN
    if status == "PARTIAL":
        return ParentOrderStatus.PARTIALLY_FILLED
    if status == "FILLED":
        return ParentOrderStatus.FILLED
    if status in {"CANCELLED", "CANCELED"}:
        return ParentOrderStatus.CANCELED
    if status == "REJECTED":
        return ParentOrderStatus.REJECTED
    if status == "EXPIRED":
        return ParentOrderStatus.EXPIRED
    return ParentOrderStatus.OPEN


def _attempt_status(value: object) -> AttemptStatus:
    status = str(value)
    if status == "PARTIAL":
        return AttemptStatus.PARTIALLY_FILLED
    if status == "FILLED":
        return AttemptStatus.FILLED
    if status == "BLOCKED":
        return AttemptStatus.BLOCKED
    if status == "INVALID":
        return AttemptStatus.INVALID
    return AttemptStatus.PENDING


def _corporate_action_from_event(
    event: dict[str, Any],
    *,
    adjustment_factor_override: float | None = None,
) -> CorporateActionRecord:
    old_quantity = int(float(event.get("requested", 0) or 0))
    new_quantity = int(float(event.get("filled", 0) or 0))
    old_factor = float(event.get("old_adj_factor", 0.0) or 0.0)
    new_factor = float(event.get("new_adj_factor", 0.0) or 0.0)
    if adjustment_factor_override is not None:
        adjustment_factor = adjustment_factor_override
    elif old_factor > 0 and new_factor > 0:
        adjustment_factor = new_factor / old_factor
    elif old_quantity > 0 and new_quantity > 0:
        adjustment_factor = new_quantity / old_quantity
    else:
        adjustment_factor = 1.0
    return CorporateActionRecord(
        corporate_action_id=str(event.get("corporate_action_id", "")),
        ts_code=str(event.get("ts_code", "")),
        action_type=CorporateActionType.SHARE_CONVERSION,
        effective_date=str(event.get("trade_date", "")),
        old_quantity=old_quantity,
        new_quantity=new_quantity,
        old_cost_basis=max(float(event.get("last_close_before", 0.0) or 0.0), 0.0),
        new_cost_basis=max(float(event.get("last_close_after", 0.0) or 0.0), 0.0),
        adjustment_factor=adjustment_factor,
    )


class UnknownExecutionRule(ValueError):
    """Raised when an instrument type has no PIT market-rule contract."""


@dataclass(frozen=True)
class FundInstrumentVersion:
    ts_code: str
    instrument_type: str
    version: str


@dataclass(frozen=True)
class MarketRules:
    instrument_type: str
    settlement: str
    lot_size: int
    tick_size: float
    price_limit_pct: float | None
    short_allowed: bool
    rule_version: str
    trade_date: str = ""
    knowledge_cutoff: str = ""


class MarketRuleResolver:
    """Minimal PIT market-rule resolver for the v2 contract.

    This resolver intentionally uses a fixed in-process table. It establishes
    the contract boundary and rejects unknown instrument types without
    defaulting to domestic equity ETF rules.
    """

    _RULES = {
        "domestic_equity_etf": MarketRules(
            instrument_type="domestic_equity_etf",
            settlement="T+1",
            lot_size=100,
            tick_size=0.001,
            price_limit_pct=0.10,
            short_allowed=False,
            rule_version="rules-v1",
        ),
        "bond_etf": MarketRules(
            instrument_type="bond_etf",
            settlement="T+0",
            lot_size=100,
            tick_size=0.001,
            price_limit_pct=0.10,
            short_allowed=False,
            rule_version="rules-v1",
        ),
        "commodity_etf": MarketRules(
            instrument_type="commodity_etf",
            settlement="T+0",
            lot_size=100,
            tick_size=0.001,
            price_limit_pct=0.10,
            short_allowed=False,
            rule_version="rules-v1",
        ),
        "cross_border_etf": MarketRules(
            instrument_type="cross_border_etf",
            settlement="T+0",
            lot_size=100,
            tick_size=0.001,
            price_limit_pct=0.10,
            short_allowed=False,
            rule_version="rules-v1",
        ),
        "money_market_etf": MarketRules(
            instrument_type="money_market_etf",
            settlement="T+0",
            lot_size=100,
            tick_size=0.001,
            price_limit_pct=None,
            short_allowed=False,
            rule_version="rules-v1",
        ),
        "other": MarketRules(
            instrument_type="other",
            settlement="T+1",
            lot_size=100,
            tick_size=0.001,
            price_limit_pct=0.10,
            short_allowed=False,
            rule_version="rules-v1",
        ),
    }

    def resolve(
        self,
        instrument: FundInstrumentVersion,
        trade_date: str,
        knowledge_cutoff: str,
    ) -> MarketRules:
        if not trade_date or not knowledge_cutoff:
            raise ValueError("trade_date and knowledge_cutoff are required for PIT rules")
        rules = self._RULES.get(instrument.instrument_type)
        if rules is None:
            raise UnknownExecutionRule(
                f"UNKNOWN_EXECUTION_RULE: {instrument.instrument_type}"
            )
        return replace(
            rules,
            trade_date=trade_date,
            knowledge_cutoff=knowledge_cutoff,
        )


@dataclass(frozen=True)
class MarketObservation:
    reference_price: float
    spread_bps: float = 0.0
    adv_notional: float = 0.0
    trade_date: str = ""
    knowledge_cutoff: str = ""
    rule_version: str = ""


@dataclass(frozen=True)
class CostScenario:
    scenario_id: str
    commission_rate: float = 0.0
    explicit_fee_rate: float = 0.0
    slippage_bps: float = 0.0
    market_impact_bps: float = 0.0


@dataclass(frozen=True)
class ExecutionCostEstimate:
    commission: float
    explicit_fee: float
    slippage_cost: float
    market_impact_cost: float
    model_version: str
    scenario_id: str
    trade_date: str
    knowledge_cutoff: str
    rule_version: str


class ExecutionCostModel:
    """Minimal cost-model contract, separate from hard market rules."""

    model_version = "execution_cost_model_v1"

    def estimate(
        self,
        order: ParentOrderRecord,
        market: MarketObservation,
        scenario: CostScenario,
    ) -> ExecutionCostEstimate:
        if (
            not market.trade_date
            or not market.knowledge_cutoff
            or not market.rule_version
            or not scenario.scenario_id
        ):
            raise ValueError(
                "trade_date, knowledge_cutoff, rule_version, and scenario_id are required"
            )
        _require_non_negative(market.reference_price, "reference_price")
        notional = order.remaining_quantity * market.reference_price
        commission = notional * scenario.commission_rate
        explicit_fee = notional * scenario.explicit_fee_rate
        slippage_cost = notional * scenario.slippage_bps / 10_000
        market_impact_cost = notional * scenario.market_impact_bps / 10_000
        return ExecutionCostEstimate(
            commission=commission,
            explicit_fee=explicit_fee,
            slippage_cost=slippage_cost,
            market_impact_cost=market_impact_cost,
            model_version=self.model_version,
            scenario_id=scenario.scenario_id,
            trade_date=market.trade_date,
            knowledge_cutoff=market.knowledge_cutoff,
            rule_version=market.rule_version,
        )
