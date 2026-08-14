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
    CorporateAction,
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
from backtest.fund_rotation.native_execution import NativeExecutionState
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
    ShadowExecutionFacts,
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
    def execute_formal(self, *, decision: ShadowDecision, orders: tuple, previous_state: ShadowAccountState, market_data: MarketDataForExecution, execution_as_of_time: datetime) -> ShadowExecutionFacts: ...


@runtime_checkable
class _AccountingAdapterContract(Protocol):
    def apply_formal(self, *, decision: ShadowDecision, previous_state: ShadowAccountState, fills: tuple[ShadowFill, ...], execution_state: object | None, market_data: MarketDataForExecution, execution_as_of_time: datetime) -> ShadowAccountState: ...


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
        _ExecutionAdapterContract: {"decision", "orders", "previous_state", "market_data", "execution_as_of_time"},
        _AccountingAdapterContract: {"decision", "previous_state", "fills", "execution_state", "market_data", "execution_as_of_time"},
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


def _state_value(state: object | None, name: str, default: object = None) -> object:
    if state is None:
        return default
    if isinstance(state, Mapping):
        return state.get(name, default)
    return getattr(state, name, default)


def _state_snapshot(state: object | None) -> Mapping[str, object] | None:
    if state is None:
        return None
    serializer = getattr(state, "to_snapshot", None)
    if callable(serializer):
        value = serializer()
        if isinstance(value, Mapping):
            return dict(value)
    if isinstance(state, Mapping):
        return dict(state)
    return None


def _restore_native_state(state: object | None) -> object | None:
    if isinstance(state, Mapping) and "cash" in state and "positions" in state:
        return NativeExecutionState.from_snapshot(dict(state))
    return state


def _native_record_ids(state: object | None, field_name: str, id_name: str) -> set[str]:
    return {
        str(_state_value(record, id_name, ""))
        for record in (_state_value(state, field_name, ()) or ())
        if str(_state_value(record, id_name, ""))
    }


def _native_parent_target_weights(state: object | None) -> dict[str, float]:
    return {
        str(_state_value(parent, "ts_code", "")): float(
            _state_value(parent, "target_weight", 0.0)
        )
        for parent in (_state_value(state, "parent_orders", ()) or ())
        if str(_state_value(parent, "ts_code", ""))
        and _state_value(parent, "target_weight", None) is not None
    }


def _corporate_actions_for_accounting(
    *,
    previous_state: ShadowAccountState,
    execution_state: object | None,
    market_data: MarketDataForExecution,
) -> tuple[CorporateAction, ...]:
    """Return only today's native CA facts in the shared accounting shape."""
    previous_execution_state = (
        previous_state.execution_state
        if previous_state.execution_state is not None
        else previous_state.execution_state_snapshot
    )
    previous_ids = {
        str(row.get("corporate_action_id", ""))
        for row in (_state_value(previous_execution_state, "corporate_actions", ()) or ())
        if isinstance(row, Mapping)
    }
    rows = _state_value(execution_state, "corporate_actions", ()) or market_data.corporate_actions
    actions: list[CorporateAction] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        action_id = str(row.get("corporate_action_id", ""))
        if action_id and action_id in previous_ids:
            continue
        old_quantity = float(row.get("old_quantity", 0.0))
        new_quantity = float(row.get("new_quantity", 0.0))
        old_price = float(row.get("old_cost_basis", 0.0))
        new_price = float(row.get("new_cost_basis", 0.0))
        cash_in_lieu = float(row.get("cash_in_lieu", 0.0))
        if old_quantity == 0.0 and new_quantity == 0.0 and cash_in_lieu == 0.0:
            continue
        actions.append(
            CorporateAction(
                symbol=str(row.get("ts_code", "")),
                pre_quantity=old_quantity,
                pre_price=old_price,
                post_quantity=new_quantity,
                post_price=new_price,
                cash_in_lieu=cash_in_lieu,
            )
        )
    return tuple(actions)


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
        self._sessions: dict[str, object] = {}
        self._evaluated_signal_dates: dict[str, set[str]] = {}
        self._signals: dict[tuple[str, str], ScheduledSignal] = {}

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
        for persisted_signal in store.scheduled_signals:
            if persisted_signal.strategy_version_id == strategy_version_id:
                self._signals[(strategy_version_id, persisted_signal.signal_date)] = persisted_signal
        cutoff_date = as_of_time.date().strftime("%Y%m%d")
        eligible_dates = tuple(date for date in calendar if str(date) <= cutoff_date)
        if not eligible_dates:
            return None
        session = self._sessions.get(strategy_version_id)
        if session is None:
            session = self.strategy_binding.strategy.create_session(
                StrategyInitializationContext(
                    run_id=self.run_id,
                    evaluation_calendar=calendar,
                ),
                _config_for_binding(self.strategy_binding),
            )
            persisted_snapshot = store.strategy_session_snapshots.get(strategy_version_id)
            snapshot = persisted_snapshot
            if isinstance(persisted_snapshot, Mapping):
                snapshot = persisted_snapshot.get(
                    "__session_snapshot__", persisted_snapshot
                )
            restore = getattr(session, "restore_snapshot", None)
            if snapshot is not None and not callable(restore):
                raise ProductionAdapterError(
                    "formal strategy session cannot restore its persisted snapshot"
                )
            if snapshot is not None:
                restore(snapshot)
            self._sessions[strategy_version_id] = session
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
        evaluated = self._evaluated_signal_dates.setdefault(strategy_version_id, set())
        persisted_snapshot = store.strategy_session_snapshots.get(strategy_version_id)
        if isinstance(persisted_snapshot, Mapping):
            evaluated.update(
                str(date)
                for date in persisted_snapshot.get("__evaluated_signal_dates__", ())
            )
        pending_signal_dates = [date for date in signal_dates if date not in evaluated]
        if pending_signal_dates:
            previous_targets = dict(previous_state.target_weights)
            previous_cash_weight = previous_state.cash_weight
            for signal_date in pending_signal_dates:
                data_view = self.data_view_factory(signal_date, as_of_time)
                decision = session.evaluate(
                    StrategyDecisionContext(
                        signal_date=signal_date,
                        data_view=data_view,
                        previous_target_weights=previous_targets,
                    )
                )
                if decision.action is DecisionKind.INVALID:
                    raise ProductionAdapterError(
                        f"formal strategy decision invalid: {decision.reason_code}"
                    )
                if decision.action is DecisionKind.HOLD_TARGETS:
                    targets = tuple(sorted(previous_targets.items()))
                    cash_weight = previous_cash_weight
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
                self._signals[(strategy_version_id, signal_date)] = ScheduledSignal(
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
                store.save_scheduled_signal(
                    self._signals[(strategy_version_id, signal_date)]
                )
                evaluated.add(signal_date)
                previous_targets = dict(targets)
                previous_cash_weight = cash_weight
            snapshotter = getattr(session, "to_snapshot", None)
            if callable(snapshotter):
                snapshot = snapshotter()
                if not isinstance(snapshot, Mapping):
                    raise ProductionAdapterError(
                        "formal strategy session snapshot must be a mapping"
                    )
                store.save_strategy_session_snapshot(
                    strategy_version_id,
                    {
                        "__session_snapshot__": dict(snapshot),
                        "__evaluated_signal_dates__": tuple(sorted(evaluated)),
                    },
                )
        return self._signals.get((strategy_version_id, signal_dates[-1]))


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
        facts = self.execute_formal(
            decision=decision,
            orders=orders,
            previous_state=ShadowAccountState(
                strategy_version_id=decision.strategy_version_id,
                as_of_time=decision.as_of_time,
                cash=decision.previous_cash,
                positions=(),
                target_weights=decision.previous_targets,
                residual_orders=(),
                shadow_ideal_nav=decision.previous_nav,
                shadow_executable_nav=decision.previous_nav,
                accounting_contract_version=decision.accounting_contract_version,
                completed_rebalance_cycles=0,
            ),
            market_data=market_data,
            execution_as_of_time=execution_as_of_time,
        )
        return facts.attempts, facts.fills

    def execute_formal(
        self,
        *,
        decision: ShadowDecision,
        orders: tuple[object, ...],
        previous_state: ShadowAccountState,
        market_data: MarketDataForExecution,
        execution_as_of_time: datetime,
    ) -> ShadowExecutionFacts:
        request = self.request_factory(
            decision=decision,
            orders=orders,
            previous_state=previous_state,
            initial_state=(
                _restore_native_state(
                    previous_state.execution_state
                    if previous_state.execution_state is not None
                    else previous_state.execution_state_snapshot
                )
            ),
            market_data=market_data,
            execution_as_of_time=execution_as_of_time,
            strategy_identity=self.strategy_identity,
            rule_identity=self.rule_identity,
        )
        native_result = self.engine.execute(request)
        ledger = getattr(native_result, "ledger", None)
        if not isinstance(ledger, ExecutionLedger):
            raise ProductionAdapterError("native execution must return an ExecutionLedger")

        parent_by_id = {parent.order_id: parent for parent in ledger.parent_orders}
        previous_execution_state = (
            previous_state.execution_state
            if previous_state.execution_state is not None
            else previous_state.execution_state_snapshot
        )
        target_by_symbol = dict(previous_state.target_weights)
        target_by_symbol.update(
            {order.symbol: order.target_weight for order in orders}
        )
        historical_targets = _native_parent_target_weights(previous_execution_state)
        for symbol, _quantity in previous_state.residual_orders:
            if symbol not in target_by_symbol and symbol in historical_targets:
                target_by_symbol[symbol] = historical_targets[symbol]
        previous_attempt_ids = _native_record_ids(
            previous_execution_state, "attempts", "attempt_id"
        )
        previous_trade_ids = _native_record_ids(
            previous_execution_state, "trades", "trade_id"
        )
        delta_attempt_records = tuple(
            attempt for attempt in ledger.attempts
            if attempt.attempt_id not in previous_attempt_ids
        )
        delta_trade_records = tuple(
            trade for trade in ledger.trades
            if trade.trade_id not in previous_trade_ids
        )
        attempts: list[ShadowExecutionAttempt] = []
        for attempt in delta_attempt_records:
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
        for trade in delta_trade_records:
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
        return ShadowExecutionFacts(
            attempts=tuple(attempts),
            fills=tuple(fills),
            execution_state=getattr(native_result, "state", None),
        )


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
        return self._apply_with_native_state(
            decision=decision,
            previous_state=previous_state,
            fills=fills,
            execution_state=None,
            market_data=market_data,
            execution_as_of_time=execution_as_of_time,
            require_open_prices=False,
        )

    def apply_formal(
        self,
        *,
        decision: ShadowDecision,
        previous_state: ShadowAccountState,
        fills: tuple[ShadowFill, ...],
        execution_state: object | None,
        market_data: MarketDataForExecution,
        execution_as_of_time: datetime,
    ) -> ShadowAccountState:
        return self._apply_with_native_state(
            decision=decision,
            previous_state=previous_state,
            fills=fills,
            execution_state=execution_state,
            market_data=market_data,
            execution_as_of_time=execution_as_of_time,
            require_open_prices=True,
        )

    def _apply_with_native_state(
        self,
        *,
        decision: ShadowDecision,
        previous_state: ShadowAccountState,
        fills: tuple[ShadowFill, ...],
        execution_state: object | None,
        market_data: MarketDataForExecution,
        execution_as_of_time: datetime,
        require_open_prices: bool,
    ) -> ShadowAccountState:
        prices = dict(market_data.prices)
        accounting_symbols = {
            symbol for symbol, _quantity in previous_state.positions
        }
        accounting_symbols.update(fill.symbol for fill in fills)
        corporate_actions = _corporate_actions_for_accounting(
            previous_state=previous_state,
            execution_state=execution_state,
            market_data=market_data,
        )
        accounting_symbols.update(action.symbol for action in corporate_actions)
        missing_symbols = sorted(
            symbol for symbol in accounting_symbols if symbol not in prices
        )
        if missing_symbols:
            raise ProductionAdapterError(
                "market data price is required for accounting symbols: "
                + ", ".join(missing_symbols)
            )
        prior_prices = dict(previous_state.valuation_prices)
        prior_prices.update(dict(market_data.prior_close_prices))
        missing_begin_prices = sorted(
            symbol for symbol, _quantity in previous_state.positions
            if symbol not in prior_prices
        )
        if missing_begin_prices:
            raise ProductionAdapterError(
                "previous valuation price is required for accounting symbols: "
                + ", ".join(missing_begin_prices)
            )
        open_prices = dict(market_data.open_prices)
        missing_open_prices = sorted(
            symbol for symbol in accounting_symbols if symbol not in open_prices
        )
        if require_open_prices and missing_open_prices:
            raise ProductionAdapterError(
                "open price is required for accounting symbols: "
                + ", ".join(missing_open_prices)
            )
        native_positions = _state_value(execution_state, "positions")
        if native_positions is not None:
            quantities = {
                str(symbol): float(position.get("size", 0.0))
                for symbol, position in native_positions.items()
            }
        else:
            quantities = {symbol: float(quantity) for symbol, quantity in previous_state.positions}
            for fill in fills:
                quantities[fill.symbol] = quantities.get(fill.symbol, 0.0) + fill.quantity
        end_positions = tuple(
            Position(symbol, quantity, prices[symbol])
            for symbol, quantity in sorted(quantities.items())
            if abs(quantity) > 1e-12
        )
        begin_positions = tuple(
            Position(symbol, quantity, prior_prices[symbol])
            for symbol, quantity in sorted(previous_state.positions)
        )
        accounting = compute_accounting_day(
            AccountDayInput(
                begin_cash=previous_state.cash,
                begin_positions=begin_positions,
                end_positions=end_positions,
                prices={
                    symbol: PricePoint(
                        prior_close=prior_prices.get(symbol),
                        open_price=open_prices.get(symbol),
                        close_price=price,
                    )
                    for symbol, price in prices.items()
                    if symbol in accounting_symbols
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
                corporate_actions=corporate_actions,
            )
        )
        if (
            accounting.quality_status == "INVALID"
            or not accounting.reconciliation.publishable
            or accounting.ending_nav <= 0
        ):
            raise ProductionAdapterError("shared accounting result is not publishable")
        native_cash = _state_value(execution_state, "cash")
        if native_cash is not None and not math.isclose(
            accounting.ending_cash,
            float(native_cash),
            rel_tol=1e-9,
            abs_tol=1e-6,
        ):
            raise ProductionAdapterError("shared accounting cash does not match native execution state")
        cash_weight = accounting.ending_cash / accounting.ending_nav
        residual_orders = previous_state.residual_orders
        if execution_state is not None:
            residual_orders = tuple(
                (str(order["ts_code"]), float(order.get("remaining", 0.0)))
                for order in (_state_value(execution_state, "active_orders", ()) or ())
                if float(order.get("remaining", 0.0)) > 0
            )
        return ShadowAccountState(
            strategy_version_id=decision.strategy_version_id,
            as_of_time=execution_as_of_time,
            cash=accounting.ending_cash,
            positions=tuple((position.symbol, position.quantity) for position in end_positions),
            target_weights=decision.new_targets,
            residual_orders=residual_orders,
            shadow_ideal_nav=market_data.ideal_nav,
            shadow_executable_nav=accounting.ending_nav,
            accounting_contract_version=accounting.accounting_contract_version,
            completed_rebalance_cycles=previous_state.completed_rebalance_cycles + 1,
            cash_weight=cash_weight,
            daily_accounting_event_order=accounting.daily_accounting_event_order,
            valuation_prices=tuple(
                (position.symbol, prices[position.symbol])
                for position in end_positions
                if position.symbol in prices and abs(position.quantity) > 1e-12
            ),
            execution_state=execution_state,
            execution_state_snapshot=_state_snapshot(execution_state),
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
