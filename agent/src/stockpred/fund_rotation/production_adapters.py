"""Production Shadow adapters for the formal fund-rotation contracts.

The deterministic providers in :mod:`forward_validation` remain useful for
fixtures.  This module is the explicit production boundary: decisions come
from a catalog-bound strategy session, execution comes from the native v2
engine, and accounting delegates to the shared attribution primitive.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping
from datetime import datetime
from inspect import Parameter, signature
from typing import Any, Protocol, runtime_checkable

from backtest.fund_rotation.attribution import (
    AccountDayInput,
    Fill,
    Position,
    PricePoint,
    compute_accounting_day,
)
from backtest.fund_rotation.contracts import (
    DecisionKind,
    StrategyDecisionContext,
    StrategyInitializationContext,
)
from backtest.fund_rotation.execution_ledger_v2 import ExecutionLedger, OrderDirection
from src.stockpred.fund_rotation.forward_validation import (
    ACCOUNTING_CONTRACT_VERSION,
    DAILY_ACCOUNTING_EVENT_ORDER,
    FrozenStrategyDecisionProvider,
    InMemoryForwardValidationStore,
    MarketDataForExecution,
    ScheduledSignal,
    ShadowAccountState,
    ShadowDecision,
    ShadowExecutionAdapter,
    ShadowExecutionAttempt,
    ShadowExecutionService,
    ShadowFill,
)


class ProductionAdapterError(ValueError):
    """Raised when formal production facts cannot be constructed safely."""


@runtime_checkable
class _StrategyProviderContract(Protocol):
    def next_signal(self, *, store: InMemoryForwardValidationStore, strategy_version_id: str, as_of_time: datetime) -> ScheduledSignal | None: ...


@runtime_checkable
class _ExecutionAdapterContract(Protocol):
    def execute(self, *, decision: ShadowDecision, orders: tuple, market_data: MarketDataForExecution, execution_as_of_time: datetime) -> tuple[tuple[ShadowExecutionAttempt, ...], tuple[ShadowFill, ...]]: ...


@runtime_checkable
class _AccountingAdapterContract(Protocol):
    def apply(self, *, decision: ShadowDecision, previous_state: ShadowAccountState, fills: tuple[ShadowFill, ...], market_data: MarketDataForExecution, execution_as_of_time: datetime) -> ShadowAccountState: ...


def _implements_contract(value: object, contract: type[Protocol]) -> bool:
    if value is None or isinstance(value, type) or not isinstance(value, contract):
        return False
    method_name = next(name for name in contract.__dict__ if not name.startswith("_"))
    method = getattr(value, method_name, None)
    if method is None:
        return False
    try:
        params = signature(method).parameters.values()
    except (TypeError, ValueError):
        return False
    expected_names = {
        _StrategyProviderContract: {"store", "strategy_version_id", "as_of_time"},
        _ExecutionAdapterContract: {"decision", "orders", "market_data", "execution_as_of_time"},
        _AccountingAdapterContract: {"decision", "previous_state", "fills", "market_data", "execution_as_of_time"},
    }[contract]
    return {
        p.name for p in params if p.kind is Parameter.KEYWORD_ONLY
    } == expected_names and all(
        p.kind is Parameter.KEYWORD_ONLY and p.default is Parameter.empty
        for p in params
    )


def _require_identity(value: object, name: str) -> str:
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, Mapping) and value:
        canonical = json.dumps(dict(value), sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    raise ProductionAdapterError(f"{name} identity is required")


def _config_for_binding(binding: object) -> object:
    spec = getattr(binding, "spec", None)
    resolved = dict(getattr(spec, "resolved_config", {}) or {})
    registered = getattr(binding, "registered", None)
    config_model = getattr(registered, "config_model", None)
    if config_model is None:
        return resolved
    return config_model.model_validate(resolved)


def _snapshot_fingerprint(binding: object, view: object, signal_date: str) -> str:
    explicit = getattr(view, "snapshot_fingerprint", None)
    if explicit:
        return str(explicit)
    spec = getattr(binding, "spec", None)
    payload = {
        "strategy_id": getattr(spec, "strategy_id", ""),
        "implementation_hash": getattr(spec, "implementation_hash", ""),
        "resolved_config_hash": getattr(spec, "resolved_config_hash", ""),
        "signal_date": signal_date,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


class ProductionFrozenStrategyDecisionProvider(FrozenStrategyDecisionProvider):
    """Evaluate a catalog-bound strategy session at the scheduler cutoff."""

    def __init__(
        self,
        *,
        strategy_binding: object,
        data_view_factory: Callable[[str, datetime], object],
        calendar_factory: Callable[[datetime], tuple[str, ...]],
        run_id: str,
        strategy_identity: object | None = None,
        rule_identity: object | None = None,
    ) -> None:
        self.strategy_binding = strategy_binding
        self.data_view_factory = data_view_factory
        self.calendar_factory = calendar_factory
        self.run_id = _require_identity(run_id, "run")
        self.strategy_identity = _require_identity(
            strategy_identity
            if strategy_identity is not None
            else getattr(getattr(strategy_binding, "spec", None), "strategy_id", None),
            "strategy",
        )
        self.rule_identity = _require_identity(rule_identity, "rule") if rule_identity is not None else None

    def next_signal(
        self,
        *,
        store: InMemoryForwardValidationStore,
        strategy_version_id: str,
        as_of_time: datetime,
    ) -> ScheduledSignal | None:
        previous_state = store.account_states.get(strategy_version_id)
        if previous_state is None:
            raise ProductionAdapterError("formal account state is required")

        calendar = tuple(self.calendar_factory(as_of_time))
        if not calendar:
            return None
        cutoff_date = as_of_time.date().strftime("%Y%m%d")
        eligible_dates = tuple(date for date in calendar if str(date) <= cutoff_date)
        if not eligible_dates:
            return None
        session = self.strategy_binding.strategy.create_session(
            StrategyInitializationContext(
                run_id=self.run_id,
                evaluation_calendar=calendar,
            ),
            _config_for_binding(self.strategy_binding),
        )
        signal_dates = tuple(
            str(date)
            for date in session.scheduled_dates(
                calendar,
                eligible_dates[0],
                eligible_dates[-1],
            )
            if str(date) in eligible_dates
        )
        if not signal_dates:
            return None
        signal_date = signal_dates[-1]
        data_view = self.data_view_factory(signal_date, as_of_time)
        decision = session.evaluate(
            StrategyDecisionContext(
                signal_date=signal_date,
                data_view=data_view,
                previous_target_weights=dict(previous_state.target_weights),
            )
        )
        if decision.action is DecisionKind.INVALID:
            raise ProductionAdapterError(
                f"formal strategy decision invalid: {decision.reason_code}"
            )
        if decision.action is DecisionKind.HOLD_TARGETS:
            targets = tuple(previous_state.target_weights)
            cash_weight = previous_state.cash_weight
        else:
            targets = tuple(sorted((str(code), float(weight)) for code, weight in decision.target_weights.items()))
            cash_weight = float(decision.cash_weight)
        if not math.isfinite(cash_weight) or cash_weight < 0:
            raise ProductionAdapterError("formal strategy returned invalid cash weight")
        expected_execution_date = next(
            (str(date) for date in calendar if str(date) > signal_date),
            signal_date,
        )
        diagnostics = dict(decision.diagnostics)
        raw_signal = {
            "decision_id": decision.decision_id,
            "action": decision.action.value,
            "reason_code": decision.reason_code,
            "diagnostics": diagnostics,
            "strategy_identity": self.strategy_identity,
            "rule_identity": self.rule_identity,
        }
        return ScheduledSignal(
            strategy_version_id=strategy_version_id,
            signal_date=signal_date,
            data_available_at=getattr(data_view, "available_at", as_of_time),
            snapshot_fingerprint=_snapshot_fingerprint(
                self.strategy_binding, data_view, signal_date
            ),
            raw_signal=raw_signal,
            selected_clusters=tuple(diagnostics.get("selected_clusters", ())),
            target_weights=targets,
            target_change_reasons=(decision.reason_code,) if decision.reason_code else (),
            expected_execution_date=expected_execution_date,
            cash_weight=cash_weight,
        )


class ProductionShadowExecutionAdapter(ShadowExecutionAdapter):
    """Map native v2 ledger facts into the Shadow execution contract."""

    def __init__(
        self,
        *,
        engine: object,
        request_factory: Callable[..., object],
        strategy_identity: object,
        rule_identity: object,
    ) -> None:
        self.engine = engine
        self.request_factory = request_factory
        self.strategy_identity = _require_identity(strategy_identity, "strategy")
        self.rule_identity = _require_identity(rule_identity, "rule")

    def execute(
        self,
        *,
        decision: ShadowDecision,
        orders: tuple[object, ...],
        market_data: MarketDataForExecution,
        execution_as_of_time: datetime,
    ) -> tuple[tuple[ShadowExecutionAttempt, ...], tuple[ShadowFill, ...]]:
        request = self.request_factory(
            decision=decision,
            orders=orders,
            market_data=market_data,
            execution_as_of_time=execution_as_of_time,
            strategy_identity=self.strategy_identity,
            rule_identity=self.rule_identity,
        )
        native_result = self.engine.execute(request)
        ledger = getattr(native_result, "ledger", None)
        if not isinstance(ledger, ExecutionLedger):
            raise ProductionAdapterError("native execution must return an ExecutionLedger")

        target_by_symbol = {order.symbol: order.target_weight for order in orders}
        parent_by_id = {parent.order_id: parent for parent in ledger.parent_orders}
        attempts: list[ShadowExecutionAttempt] = []
        for attempt in ledger.attempts:
            parent = parent_by_id.get(attempt.order_id)
            if parent is None or parent.ts_code not in target_by_symbol:
                raise ProductionAdapterError("native attempt is not tied to a Shadow order")
            attempts.append(
                ShadowExecutionAttempt(
                    attempt_id=attempt.attempt_id,
                    shadow_decision_id=decision.shadow_decision_id,
                    symbol=parent.ts_code,
                    target_weight=target_by_symbol[parent.ts_code],
                    execution_as_of_time=execution_as_of_time,
                )
            )

        attempt_ids = {attempt.attempt_id for attempt in attempts}
        fills: list[ShadowFill] = []
        for trade in ledger.trades:
            if trade.attempt_id not in attempt_ids:
                raise ProductionAdapterError("native trade references an unmapped attempt")
            signed_quantity = (
                float(trade.quantity)
                if trade.direction is OrderDirection.BUY
                else -float(trade.quantity)
            )
            fills.append(
                ShadowFill(
                    fill_id=trade.trade_id,
                    attempt_id=trade.attempt_id,
                    symbol=trade.ts_code,
                    quantity=signed_quantity,
                    price=float(trade.price),
                    explicit_cost=float(trade.commission + trade.explicit_fee),
                )
            )
        return tuple(attempts), tuple(fills)


class ProductionShadowAccountingAdapter:
    """Apply formal fills through the shared daily accounting primitive."""

    def apply(
        self,
        *,
        decision: ShadowDecision,
        previous_state: ShadowAccountState,
        fills: tuple[ShadowFill, ...],
        market_data: MarketDataForExecution,
        execution_as_of_time: datetime,
    ) -> ShadowAccountState:
        prices = dict(market_data.prices)
        accounting_symbols = {
            symbol for symbol, _quantity in previous_state.positions
        }
        accounting_symbols.update(fill.symbol for fill in fills)
        missing_symbols = sorted(
            symbol for symbol in accounting_symbols if symbol not in prices
        )
        if missing_symbols:
            raise ProductionAdapterError(
                "market data price is required for accounting symbols: "
                + ", ".join(missing_symbols)
            )
        quantities = {symbol: float(quantity) for symbol, quantity in previous_state.positions}
        for fill in fills:
            quantities[fill.symbol] = quantities.get(fill.symbol, 0.0) + fill.quantity
        end_positions = tuple(
            Position(symbol, quantity, prices[symbol])
            for symbol, quantity in sorted(quantities.items())
            if abs(quantity) > 1e-12
        )
        begin_positions = tuple(
            Position(symbol, quantity, prices[symbol])
            for symbol, quantity in sorted(previous_state.positions)
        )
        accounting = compute_accounting_day(
            AccountDayInput(
                begin_cash=previous_state.cash,
                begin_positions=begin_positions,
                end_positions=end_positions,
                prices={
                    symbol: PricePoint(prior_close=None, open_price=None, close_price=price)
                    for symbol, price in prices.items()
                },
                fills=tuple(
                    Fill(
                        symbol=fill.symbol,
                        quantity=fill.quantity,
                        executed_price=fill.price,
                        other_fee=fill.explicit_cost,
                    )
                    for fill in fills
                ),
            )
        )
        if (
            accounting.quality_status == "INVALID"
            or not accounting.reconciliation.publishable
            or accounting.ending_nav <= 0
        ):
            raise ProductionAdapterError("shared accounting result is not publishable")
        cash_weight = accounting.ending_cash / accounting.ending_nav
        return ShadowAccountState(
            strategy_version_id=decision.strategy_version_id,
            as_of_time=execution_as_of_time,
            cash=accounting.ending_cash,
            positions=tuple((position.symbol, position.quantity) for position in end_positions),
            target_weights=decision.new_targets,
            residual_orders=previous_state.residual_orders,
            shadow_ideal_nav=market_data.ideal_nav,
            shadow_executable_nav=accounting.ending_nav,
            accounting_contract_version=accounting.accounting_contract_version,
            completed_rebalance_cycles=previous_state.completed_rebalance_cycles + 1,
            cash_weight=cash_weight,
            daily_accounting_event_order=accounting.daily_accounting_event_order,
        )


def build_production_shadow_execution_service(
    store: InMemoryForwardValidationStore,
    *,
    strategy_provider: FrozenStrategyDecisionProvider | None,
    execution_adapter: ShadowExecutionAdapter | None,
    accounting_adapter: object | None,
    strategy_identity: object | None,
    rule_identity: object | None,
) -> ShadowExecutionService:
    """Build the production service, disabling it when formal wiring is incomplete."""
    try:
        formal_ready = all(
            (
                _implements_contract(strategy_provider, _StrategyProviderContract),
                _implements_contract(execution_adapter, _ExecutionAdapterContract),
                _implements_contract(accounting_adapter, _AccountingAdapterContract),
                strategy_identity is not None,
                rule_identity is not None,
            )
        )
        if formal_ready:
            _require_identity(strategy_identity, "strategy")
            _require_identity(rule_identity, "rule")
    except ProductionAdapterError:
        formal_ready = False
    if formal_ready:
        service = ShadowExecutionService(
            store,
            decision_provider=strategy_provider,
            execution_adapter=execution_adapter,
            accounting_adapter=accounting_adapter,
        )
        service.strategy_identity = _require_identity(strategy_identity, "strategy")
        service.rule_identity = _require_identity(rule_identity, "rule")
        return service
    return ShadowExecutionService(store, execution_adapter=None, accounting_adapter=None)


__all__ = [
    "ProductionAdapterError",
    "ProductionFrozenStrategyDecisionProvider",
    "ProductionShadowExecutionAdapter",
    "ProductionShadowAccountingAdapter",
    "build_production_shadow_execution_service",
]
