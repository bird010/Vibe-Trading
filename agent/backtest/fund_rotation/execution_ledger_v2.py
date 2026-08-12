"""Execution fact ledger and diagnostics contract v2.

The v2 contract keeps parent orders, execution attempts, executed trades, and
corporate actions as separate facts. Diagnostics are derived only from this
ledger, so fill rates, turnover, cash costs, and corporate-action audit counts
each have a single explicit meaning.
"""

from __future__ import annotations

import math
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
                if parent.quantity_basis_id == old_parent.quantity_basis_id:
                    raise ValueError(
                        "replacement quantity_basis_id must differ from old parent"
                    )
                adjusted_residual = int(
                    old_parent.remaining_quantity * action.adjustment_factor
                )
                if parent.original_requested_quantity != adjusted_residual:
                    raise ValueError(
                        "replacement original_requested_quantity must equal adjusted residual"
                    )
                if parent.cumulative_filled_quantity != 0:
                    raise ValueError("replacement parent must start with zero cumulative filled")
                if parent.remaining_quantity != parent.original_requested_quantity:
                    raise ValueError("replacement parent must start with full remaining quantity")

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
            cumulative_filled = 0
            for attempt in sorted(parent_attempts, key=lambda item: item.attempt_number):
                if cumulative_filled >= parent.original_requested_quantity:
                    raise ValueError("FILLED parent cannot have later attempts")
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
            parent = parents_by_id.get(trade.order_id)
            if parent is None:
                raise ValueError("trade references unknown parent")
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
