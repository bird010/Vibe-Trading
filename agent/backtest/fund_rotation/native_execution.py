"""Native v2 execution engine for fund-rotation targets.

The engine shares the low-level execution primitives with the legacy loop, but
owns the formal v2 execution facts directly.  It does not adapt a
``PipelineResult`` and does not call ``run_execution_loop``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace

import pandas as pd

from backtest.fund_rotation.config import FundRotationConfig
from backtest.fund_rotation.etf_rules import ChinaETFExecutionRules
from backtest.fund_rotation.evaluation import TargetSnapshot, schedule_targets
from backtest.fund_rotation.execution import (
    ExecutionProfiler,
    build_execution_context,
    execute_with_capacity,
    mark_to_market,
)
from backtest.fund_rotation.execution_ledger_v2 import (
    AttemptStatus as LedgerAttemptStatus,
    CorporateActionRecord,
    CorporateActionType,
    ExecutedTradeRecord,
    ExecutionAttemptRecord,
    ExecutionLedger,
    OrderDirection,
    ParentOrderRecord,
    ParentOrderStatus,
)
from backtest.fund_rotation.market_rules import (
    FundInstrumentVersion,
    MarketRuleResolver,
    MarketRules,
    PITInvalidMarketRule,
    UnknownExecutionRule,
)
from backtest.fund_rotation.executor import PortfolioExecutor
from backtest.fund_rotation.factor_basis import (
    FactorBasisOwnershipError,
    cleanup_native_factor_basis,
    migrate_legacy_native_factor_basis,
    validate_factor_basis_ownership,
)
from backtest.fund_rotation.orders import Order, OrderManager, OrderStatus
from backtest.fund_rotation.pit_universe import PITQueryMode
from backtest.fund_rotation.share_adjustment import adjust_shares_for_factor_change


@dataclass(frozen=True)
class NativeExecutionState:
    CURRENT_SCHEMA_VERSION = 2

    cash: float
    positions: dict[str, dict] = field(default_factory=dict)
    last_close: dict[str, float] = field(default_factory=dict)
    last_close_date: dict[str, str] = field(default_factory=dict)
    last_close_source: dict[str, str] = field(default_factory=dict)
    position_adj_factor: dict[str, float] = field(default_factory=dict)
    active_targets: dict[str, float] = field(default_factory=dict)
    active_orders: tuple[dict, ...] = field(default_factory=tuple)
    parent_orders: tuple[dict, ...] = field(default_factory=tuple)
    attempts: tuple[dict, ...] = field(default_factory=tuple)
    trades: tuple[dict, ...] = field(default_factory=tuple)
    corporate_actions: tuple[dict, ...] = field(default_factory=tuple)
    event_counter: int = 0
    state_schema_version: int = CURRENT_SCHEMA_VERSION
    migration_diagnostics: tuple[str, ...] = field(default_factory=tuple)

    def to_snapshot(self) -> dict:
        """Return a JSON-safe checkpoint for cross-process Shadow recovery."""
        return json.loads(json.dumps(asdict(self), sort_keys=True, default=str))

    @classmethod
    def from_snapshot(cls, snapshot: dict) -> "NativeExecutionState":
        if not isinstance(snapshot, dict):
            raise TypeError("native execution snapshot must be a mapping")
        values = dict(snapshot)
        for name in (
            "active_orders", "parent_orders", "attempts", "trades", "corporate_actions",
            "migration_diagnostics",
        ):
            values[name] = tuple(values.get(name) or ())
        schema_version = int(values.get("state_schema_version", 1))
        if schema_version < 1 or schema_version > cls.CURRENT_SCHEMA_VERSION:
            raise ValueError(f"unsupported native execution state schema: {schema_version}")
        return cls(
            cash=float(values.get("cash", 0.0)),
            positions=dict(values.get("positions") or {}),
            last_close=dict(values.get("last_close") or {}),
            last_close_date=dict(values.get("last_close_date") or {}),
            last_close_source=dict(values.get("last_close_source") or {}),
            position_adj_factor=dict(values.get("position_adj_factor") or {}),
            active_targets=dict(values.get("active_targets") or {}),
            active_orders=values["active_orders"],
            parent_orders=values["parent_orders"],
            attempts=values["attempts"],
            trades=values["trades"],
            corporate_actions=values["corporate_actions"],
            event_counter=int(values.get("event_counter", 0)),
            state_schema_version=schema_version,
            migration_diagnostics=values["migration_diagnostics"],
        )


@dataclass(frozen=True)
class NativeExecutionRequest:
    targets: dict[str, dict[str, float]]
    evaluation_dates: tuple[str, ...]
    fund_daily: pd.DataFrame
    fund_adj: pd.DataFrame
    execution: FundRotationConfig
    initial_capital: float
    knowledge_cutoff: str
    snapshot_version: int
    run_id: str
    rule_resolver: MarketRuleResolver
    instrument_versions: dict[str, FundInstrumentVersion]
    rule_mode: PITQueryMode
    knowledge_cutoffs: dict[str, str] = field(default_factory=dict)
    initial_state: NativeExecutionState | None = None
    decision_ids: dict[str, str] = field(default_factory=dict)
    order_ids: dict[str, dict[str, str]] = field(default_factory=dict)


def _state_live_order_codes(state: NativeExecutionState) -> set[str]:
    return {
        str(order["ts_code"])
        for order in state.active_orders
        if order.get("ts_code")
    }


def _prepare_resume_state(
    initial_state: NativeExecutionState | None,
) -> NativeExecutionState | None:
    if initial_state is None:
        return None
    live_order_codes = _state_live_order_codes(initial_state)
    if initial_state.state_schema_version < NativeExecutionState.CURRENT_SCHEMA_VERSION:
        migrated_basis, diagnostics = migrate_legacy_native_factor_basis(
            initial_state.position_adj_factor,
            positions=initial_state.positions,
            live_order_codes=live_order_codes,
        )
        return replace(
            initial_state,
            position_adj_factor=migrated_basis,
            state_schema_version=NativeExecutionState.CURRENT_SCHEMA_VERSION,
            migration_diagnostics=(
                *initial_state.migration_diagnostics,
                *diagnostics,
            ),
        )
    validate_factor_basis_ownership(
        initial_state.position_adj_factor,
        positions=initial_state.positions,
        live_order_codes=live_order_codes,
        native=True,
    )
    return initial_state


@dataclass(frozen=True)
class NativeExecutionResult:
    ledger: ExecutionLedger
    executed_equity: pd.Series
    trade_events: list[dict]
    orders: list[dict]
    positions_history: list[dict]
    ending_cash: float
    ending_positions: dict[str, dict]
    state: NativeExecutionState


@dataclass
class _ParentState:
    order_id: str
    decision_id: str
    signal_week: str
    ts_code: str
    direction: str
    created_date: str
    original_requested_quantity: int
    quantity_basis: float
    lot_size: int
    target_weight: float = 0.0
    cumulative_filled_quantity: int = 0
    remaining_quantity: int = 0
    replacement_of_order_id: str = ""
    replacement_chain_id: str = ""
    corporate_action_id: str = ""
    status: ParentOrderStatus = ParentOrderStatus.OPEN
    completed_date: str = ""
    cancel_reason: str = ""
    rule_version: str = ""
    source_record_id: str = ""
    rule_knowledge_cutoff: str = ""
    corporate_action_adjustments: tuple[dict, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.original_requested_quantity <= 0:
            raise ValueError("parent original_requested_quantity must be positive")
        self.remaining_quantity = self.original_requested_quantity

    @property
    def quantity_basis_id(self) -> str:
        return _quantity_basis_id(self.ts_code, self.quantity_basis)

    def record_fill(self, filled: int) -> None:
        self.cumulative_filled_quantity += int(filled)
        self.remaining_quantity = max(
            self.original_requested_quantity - self.cumulative_filled_quantity,
            0,
        )
        if self.remaining_quantity == 0:
            self.status = ParentOrderStatus.FILLED
        elif self.cumulative_filled_quantity > 0:
            self.status = ParentOrderStatus.PARTIALLY_FILLED
        elif self.status is not ParentOrderStatus.CANCELED:
            self.status = ParentOrderStatus.OPEN

    def cancel(self, trade_date: str, reason: str) -> None:
        self.status = ParentOrderStatus.CANCELED
        self.completed_date = trade_date
        self.cancel_reason = reason

    def to_record(self) -> ParentOrderRecord:
        return ParentOrderRecord(
            order_id=self.order_id,
            decision_id=self.decision_id,
            ts_code=self.ts_code,
            direction=self.direction,
            created_date=self.created_date,
            original_requested_quantity=self.original_requested_quantity,
            cumulative_filled_quantity=self.cumulative_filled_quantity,
            remaining_quantity=self.remaining_quantity,
            quantity_basis_id=self.quantity_basis_id,
            replacement_of_order_id=self.replacement_of_order_id,
            replacement_chain_id=self.replacement_chain_id,
            corporate_action_id=self.corporate_action_id,
            status=self.status,
            completed_date=self.completed_date,
            cancel_reason=self.cancel_reason,
            lot_size=self.lot_size,
            knowledge_cutoff=self.rule_knowledge_cutoff,
        )

    def to_state_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "decision_id": self.decision_id,
            "signal_week": self.signal_week,
            "ts_code": self.ts_code,
            "direction": self.direction,
            "created_date": self.created_date,
            "original_requested_quantity": self.original_requested_quantity,
            "cumulative_filled_quantity": self.cumulative_filled_quantity,
            "remaining_quantity": self.remaining_quantity,
            "quantity_basis": self.quantity_basis,
            "target_weight": self.target_weight,
            "lot_size": self.lot_size,
            "replacement_of_order_id": self.replacement_of_order_id,
            "replacement_chain_id": self.replacement_chain_id,
            "corporate_action_id": self.corporate_action_id,
            "status": self.status.value,
            "completed_date": self.completed_date,
            "cancel_reason": self.cancel_reason,
            "rule_version": self.rule_version,
            "source_record_id": self.source_record_id,
            "rule_knowledge_cutoff": self.rule_knowledge_cutoff,
            "corporate_action_adjustments": list(self.corporate_action_adjustments),
        }

    @classmethod
    def from_state_dict(cls, data: dict) -> "_ParentState":
        parent = cls(
            order_id=str(data["order_id"]),
            decision_id=str(data["decision_id"]),
            signal_week=str(data.get("signal_week", "")),
            ts_code=str(data["ts_code"]),
            direction=str(data["direction"]),
            created_date=str(data["created_date"]),
            original_requested_quantity=int(data["original_requested_quantity"]),
            quantity_basis=float(data.get("quantity_basis", 1.0) or 1.0),
            lot_size=int(data.get("lot_size", 100) or 100),
            target_weight=float(data.get("target_weight", 0.0) or 0.0),
            replacement_of_order_id=str(data.get("replacement_of_order_id", "")),
            replacement_chain_id=str(data.get("replacement_chain_id", "")),
            corporate_action_id=str(data.get("corporate_action_id", "")),
            status=ParentOrderStatus(data.get("status", ParentOrderStatus.OPEN.value)),
            completed_date=str(data.get("completed_date", "")),
            cancel_reason=str(data.get("cancel_reason", "")),
            rule_version=str(data.get("rule_version", "")),
            source_record_id=str(data.get("source_record_id", "")),
            rule_knowledge_cutoff=str(data.get("rule_knowledge_cutoff", "")),
            corporate_action_adjustments=tuple(
                dict(item) for item in data.get("corporate_action_adjustments", ())
            ),
        )
        parent.cumulative_filled_quantity = int(
            data.get("cumulative_filled_quantity", 0) or 0
        )
        parent.remaining_quantity = int(
            data.get(
                "remaining_quantity",
                parent.original_requested_quantity - parent.cumulative_filled_quantity,
            )
            or 0
        )
        return parent


class FundRotationExecutionEngine:
    def execute(
        self,
        request: NativeExecutionRequest,
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> NativeExecutionResult:
        config = request.execution
        if abs(float(request.initial_capital) - float(config.initial_capital)) > 1e-9:
            raise ValueError("request.initial_capital must equal execution.initial_capital")
        evaluation_dates = list(request.evaluation_dates)
        ctx = build_execution_context(request.fund_daily, request.fund_adj, config)

        initial_state = _prepare_resume_state(request.initial_state)
        has_initial_state = initial_state is not None
        if not request.targets and not has_initial_state:
            return _cash_hold_result(evaluation_dates, request.initial_capital)

        snapshots = [
            TargetSnapshot(pd.Timestamp(signal_date), targets)
            for signal_date, targets in request.targets.items()
        ]
        schedule = schedule_targets(
            snapshots,
            [pd.Timestamp(trade_date) for trade_date in evaluation_dates],
        )
        exec_date_map: dict[str, tuple[str, dict[str, float]]] = {
            exec_date.strftime("%Y%m%d"): (
                snap.signal_date.strftime("%Y%m%d"),
                dict(snap.weights),
            )
            for exec_date, snap in sorted(schedule.items())
        }
        if not exec_date_map and not has_initial_state:
            return _cash_hold_result(evaluation_dates, request.initial_capital)

        initial_rule_code = _first_execution_code(request, initial_state)
        if not initial_rule_code:
            return _state_hold_result(evaluation_dates, request, initial_state)
        initial_rules, _ = _resolve_execution_rules(
            request,
            initial_rule_code,
            evaluation_dates[0],
            config,
        )
        executor = PortfolioExecutor(
            cash=float(initial_state.cash) if initial_state else request.initial_capital,
            rules=initial_rules,
        )
        if initial_state:
            executor.set_positions(initial_state.positions)
            executor._last_close = dict(initial_state.last_close)
            executor._last_close_date = dict(initial_state.last_close_date)
            executor._last_close_source = dict(initial_state.last_close_source)

        order_mgr = _restore_order_manager(initial_state)
        profiler = ExecutionProfiler()

        parent_states: dict[str, _ParentState] = _restore_parent_states(initial_state)
        active_parent_by_code: dict[str, str] = {
            str(order["ts_code"]): str(order["parent_order_id"])
            for order in (initial_state.active_orders if initial_state else ())
        }
        attempts: list[ExecutionAttemptRecord] = _restore_attempts(initial_state)
        trades: list[ExecutedTradeRecord] = _restore_trades(initial_state)
        corporate_actions: list[CorporateActionRecord] = _restore_corporate_actions(
            initial_state
        )
        attempt_counts: dict[str, int] = _attempt_counts(attempts)
        replacement_counts: dict[str, int] = {}
        trade_events: list[dict] = []
        equity_records: list[dict] = []
        positions_history: list[dict] = []

        active_targets: dict[str, float] = (
            dict(initial_state.active_targets) if initial_state else {}
        )
        position_adj_factor: dict[str, float] = (
            dict(initial_state.position_adj_factor) if initial_state else {}
        )
        date_ordinal = {date: index for index, date in enumerate(evaluation_dates)}
        event_counter = int(initial_state.event_counter) if initial_state else 0

        for trade_date in evaluation_dates:
            if should_cancel is not None and should_cancel():
                break

            _apply_corporate_actions(
                trade_date=trade_date,
                executor=executor,
                order_mgr=order_mgr,
                bar_lookup=ctx.bar_lookup,
                adj_lookup=ctx.adj_lookup,
                position_adj_factor=position_adj_factor,
                parent_states=parent_states,
                active_parent_by_code=active_parent_by_code,
                replacement_counts=replacement_counts,
                corporate_actions=corporate_actions,
                trade_events=trade_events,
            )

            if trade_date in exec_date_map:
                signal_week, targets = exec_date_map[trade_date]
                active_targets = targets
                event_counter += 1
                signal_event_id = _caller_decision_id(
                    request,
                    signal_week,
                    event_counter,
                )

                all_codes = set(targets) | set(executor._positions)
                bars = {
                    code: ctx.bar_lookup[(trade_date, code)]
                    for code in all_codes
                    if (trade_date, code) in ctx.bar_lookup
                }
                creation_rules: dict[str, ChinaETFExecutionRules] = {}
                creation_provenance: dict[str, MarketRules] = {}
                for code in all_codes:
                    rule, provenance = _resolve_execution_rules(
                        request,
                        code,
                        trade_date,
                        config,
                    )
                    creation_rules[code] = rule
                    creation_provenance[code] = provenance
                pre_equity = executor._compute_equity_anchor(sorted(all_codes), bars)
                deltas: dict[str, int] = {}
                for code in all_codes:
                    current_size = executor._positions.get(code, {}).get("size", 0)
                    target_weight = targets.get(code, 0.0)
                    price = bars.get(code, {}).get("open", 0.0)
                    if price <= 0:
                        price = executor._last_close.get(code, 0.0)
                    if price <= 0:
                        continue
                    raw_target_size = int(target_weight * pre_equity / price)
                    _validate_short_gate(
                        code=code,
                        target_weight=target_weight,
                        target_size=raw_target_size,
                        rules=creation_provenance[code],
                    )
                    delta = raw_target_size - current_size
                    if delta > 0:
                        delta = creation_rules[code].round_buy_size(delta)
                    if delta:
                        deltas[code] = delta

                basis_factors: dict[str, float] = {}
                for code in deltas:
                    current_size = int(executor._positions.get(code, {}).get("size", 0))
                    if current_size > 0 and code in position_adj_factor:
                        continue
                    current_factor = ctx.adj_lookup.get((trade_date, code))
                    if current_factor is None or current_factor <= 0:
                        raise ValueError(
                            f"adj_factor is required to initialize basis: {code} {trade_date}"
                        )
                    basis_factors[code] = float(current_factor)

                _cancel_active_parents(
                    trade_date,
                    parent_states,
                    active_parent_by_code,
                    reason="SUPERSEDED_BY_NEW_TARGET",
                )
                order_mgr.create_orders(deltas, event_id=signal_event_id)
                for code, delta in sorted(deltas.items()):
                    order_id = _caller_order_id(
                        request,
                        signal_week,
                        signal_event_id,
                        code,
                    )
                    provenance = creation_provenance[code]
                    parent_states[order_id] = _ParentState(
                        order_id=order_id,
                        decision_id=signal_event_id,
                        signal_week=signal_week,
                        ts_code=code,
                        direction="BUY" if delta > 0 else "SELL",
                        created_date=trade_date,
                        original_requested_quantity=abs(int(delta)),
                        quantity_basis=1.0,
                        lot_size=creation_rules[code].lot_size,
                        target_weight=float(targets.get(code, 0.0)),
                        rule_version=provenance.rule_version,
                        source_record_id=provenance.source_record_id,
                        rule_knowledge_cutoff=provenance.knowledge_cutoff,
                    )
                    active_parent_by_code[code] = order_id
                    if code in basis_factors:
                        position_adj_factor[code] = basis_factors[code]

            pending = order_mgr.get_pending_orders()
            all_codes = (
                {order.ts_code for order in pending}
                | set(executor._positions)
                | set(active_targets)
            )
            bars = {
                code: ctx.bar_lookup[(trade_date, code)]
                for code in all_codes
                if (trade_date, code) in ctx.bar_lookup
            }
            for code, bar in bars.items():
                if bar.get("close", 0.0) > 0:
                    executor._last_close[code] = bar["close"]
                    executor._last_close_date[code] = trade_date
                    executor._last_close_source[code] = "close"

            if pending:
                rules_by_code: dict[str, ChinaETFExecutionRules] = {}
                provenance_by_code: dict[str, MarketRules] = {}
                for order in pending:
                    rule, provenance = _resolve_execution_rules(
                        request,
                        order.ts_code,
                        trade_date,
                        config,
                    )
                    rules_by_code[order.ts_code] = rule
                    provenance_by_code[order.ts_code] = provenance
                rebalance_result = execute_with_capacity(
                    executor=executor,
                    order_mgr=order_mgr,
                    targets=active_targets,
                    bars=bars,
                    trade_date=trade_date,
                    config=config,
                    adv_index=ctx.adv_index,
                    rules=initial_rules,
                    profiler=profiler,
                    rules_by_code=rules_by_code,
                )
                for event in rebalance_result.events:
                    _record_execution_event(
                        event=event,
                        trade_date=trade_date,
                        active_targets=active_targets,
                        parent_states=parent_states,
                        active_parent_by_code=active_parent_by_code,
                        attempt_counts=attempt_counts,
                        attempts=attempts,
                        trades=trades,
                        trade_events=trade_events,
                        provenance_by_code=provenance_by_code,
                    )

            live_order_codes = {
                order.ts_code for order in order_mgr.get_pending_orders()
            } | set(active_parent_by_code)
            cleanup_native_factor_basis(
                position_adj_factor,
                positions=executor._positions,
                live_order_codes=live_order_codes,
            )

            daily_equity = mark_to_market(executor, trade_date, ctx.close_lookup)
            holdings = _holdings_snapshot(
                executor=executor,
                trade_date=trade_date,
                daily_equity=daily_equity,
                active_targets=active_targets,
                close_lookup=ctx.close_lookup,
                adj_lookup=ctx.adj_lookup,
                position_adj_factor=position_adj_factor,
                date_ordinal=date_ordinal,
            )
            positions_history.append(
                {
                    "trade_date": trade_date,
                    "positions": {
                        code: pos["size"] for code, pos in executor._positions.items()
                    },
                    "holdings": holdings,
                    "cash": executor.cash,
                    "signal_cash": daily_equity
                    * max(
                        0.0,
                        1.0
                        - sum(max(weight, 0.0) for weight in active_targets.values()),
                    ),
                    "execution_failure_cash": max(
                        executor.cash
                        - daily_equity
                        * max(
                            0.0,
                            1.0
                            - sum(max(weight, 0.0) for weight in active_targets.values()),
                        ),
                        0.0,
                    ),
                    "equity": daily_equity,
                }
            )
            equity_records.append({"trade_date": trade_date, "equity": daily_equity})

        executed_equity = _equity_series(equity_records, request.initial_capital)
        ledger = ExecutionLedger(
            parent_orders=tuple(parent.to_record() for parent in parent_states.values()),
            attempts=tuple(attempts),
            trades=tuple(trades),
            corporate_actions=tuple(corporate_actions),
        )
        return NativeExecutionResult(
            ledger=ledger,
            executed_equity=executed_equity,
            trade_events=trade_events,
            orders=_orders_from_ledger(ledger, parent_states),
            positions_history=positions_history,
            ending_cash=float(executor.cash),
            ending_positions={code: dict(pos) for code, pos in executor._positions.items()},
            state=_state_from_execution(
                cash=float(executor.cash),
                executor=executor,
                order_mgr=order_mgr,
                parent_states=parent_states,
                active_parent_by_code=active_parent_by_code,
                ledger=ledger,
                position_adj_factor=position_adj_factor,
                active_targets=active_targets,
                event_counter=event_counter,
                migration_diagnostics=initial_state.migration_diagnostics if initial_state else (),
            ),
        )


def _cash_hold_result(
    evaluation_dates: list[str],
    initial_capital: float,
) -> NativeExecutionResult:
    equity = pd.Series(1.0, index=list(evaluation_dates), name="executed_strategy")
    return NativeExecutionResult(
        ledger=ExecutionLedger(),
        executed_equity=equity,
        trade_events=[],
        orders=[],
        positions_history=[
            {
                "trade_date": trade_date,
                "positions": {},
                "holdings": [],
                "cash": initial_capital,
                "signal_cash": initial_capital,
                "execution_failure_cash": 0.0,
                "equity": initial_capital,
            }
            for trade_date in evaluation_dates
        ],
        ending_cash=float(initial_capital),
        ending_positions={},
        state=NativeExecutionState(cash=float(initial_capital)),
    )


def _state_hold_result(
    evaluation_dates: list[str],
    request: NativeExecutionRequest,
    initial_state: NativeExecutionState | None,
) -> NativeExecutionResult:
    if initial_state is None:
        return _cash_hold_result(evaluation_dates, request.initial_capital)
    parent_states = _restore_parent_states(initial_state)
    ledger = ExecutionLedger(
        parent_orders=tuple(parent.to_record() for parent in parent_states.values()),
        attempts=tuple(_restore_attempts(initial_state)),
        trades=tuple(_restore_trades(initial_state)),
        corporate_actions=tuple(_restore_corporate_actions(initial_state)),
    )
    equity_records = [
        {"trade_date": trade_date, "equity": float(initial_state.cash)}
        for trade_date in evaluation_dates
    ]
    positions_history = [
        {
            "trade_date": trade_date,
            "positions": dict(initial_state.positions),
            "holdings": [],
            "cash": float(initial_state.cash),
            "signal_cash": float(initial_state.cash),
            "execution_failure_cash": 0.0,
            "equity": float(initial_state.cash),
        }
        for trade_date in evaluation_dates
    ]
    return NativeExecutionResult(
        ledger=ledger,
        executed_equity=_equity_series(equity_records, request.initial_capital),
        trade_events=[],
        orders=_orders_from_ledger(ledger, parent_states),
        positions_history=positions_history,
        ending_cash=float(initial_state.cash),
        ending_positions={code: dict(pos) for code, pos in initial_state.positions.items()},
        state=initial_state,
    )


def _first_execution_code(
    request: NativeExecutionRequest,
    initial_state: NativeExecutionState | None,
) -> str:
    for targets in request.targets.values():
        for code in targets:
            return str(code)
    if initial_state:
        for order in initial_state.active_orders:
            return str(order["ts_code"])
        for code in initial_state.positions:
            return str(code)
    return ""


def _resolve_execution_rules(
    request: NativeExecutionRequest,
    code: str,
    trade_date: str,
    config: FundRotationConfig,
) -> tuple[ChinaETFExecutionRules, MarketRules]:
    instrument = request.instrument_versions.get(code)
    if instrument is None:
        raise UnknownExecutionRule(f"UNKNOWN_EXECUTION_RULE: missing instrument {code}")
    if request.rule_mode is PITQueryMode.AS_WAS_KNOWN and trade_date not in request.knowledge_cutoffs:
        raise PITInvalidMarketRule(
            "PIT_INVALID_EXECUTION_RULE: missing knowledge cutoff for trade date "
            f"{trade_date}"
        )
    provenance = request.rule_resolver.resolve(
        instrument=instrument,
        trade_date=trade_date,
        knowledge_cutoff=request.knowledge_cutoffs.get(
            trade_date,
            request.knowledge_cutoff,
        ),
        snapshot_version=request.snapshot_version,
        mode=request.rule_mode,
    )
    return _rules_from_market_rules(provenance, config), provenance


def _validate_short_gate(
    *,
    code: str,
    target_weight: float,
    target_size: int,
    rules: MarketRules,
) -> None:
    if target_weight >= 0 and target_size >= 0:
        return
    if rules.short_allowed:
        raise ValueError(
            f"short execution unsupported for {code}: native engine is long-only"
        )
    raise ValueError(
        f"short execution disallowed by PIT rule for {code}: target would be negative"
    )


def _rules_from_market_rules(
    market_rules: MarketRules,
    config: FundRotationConfig,
) -> ChinaETFExecutionRules:
    price_limit_rule = str(market_rules.price_limit_rule or "").strip().upper()
    if market_rules.price_limit_pct is None and price_limit_rule not in {
        "NONE",
        "NO_LIMIT",
        "UNLIMITED",
    }:
        raise UnknownExecutionRule(
            f"UNKNOWN_EXECUTION_RULE: missing explicit price-limit rule for {market_rules.rule_version}"
        )
    return ChinaETFExecutionRules(
        lot_size=market_rules.lot_size,
        tick_size=market_rules.tick_size,
        commission_rate=config.commission_rate,
        commission_min=config.commission_min,
        other_fee_rate=config.other_fee_rate,
        allow_short=market_rules.short_allowed,
        price_limit_pct=market_rules.price_limit_pct,
        settlement=market_rules.settlement,
    )


def _caller_decision_id(
    request: NativeExecutionRequest,
    signal_week: str,
    event_counter: int,
) -> str:
    return request.decision_ids.get(signal_week, f"SIG-{signal_week}-{event_counter:04d}")


def _caller_order_id(
    request: NativeExecutionRequest,
    signal_week: str,
    decision_id: str,
    code: str,
) -> str:
    if decision_id in request.order_ids and code in request.order_ids[decision_id]:
        return request.order_ids[decision_id][code]
    if signal_week in request.order_ids and code in request.order_ids[signal_week]:
        return request.order_ids[signal_week][code]
    return f"{decision_id}-{code}"


def _restore_order_manager(initial_state: NativeExecutionState | None) -> OrderManager:
    order_mgr = OrderManager()
    if initial_state is None:
        return order_mgr
    for row in initial_state.active_orders:
        order = Order(
            ts_code=str(row["ts_code"]),
            requested=int(row["requested"]),
            event_id=str(row["event_id"]),
            status=OrderStatus(row.get("status", OrderStatus.PENDING.value)),
            filled=int(row.get("filled", 0) or 0),
            attempts=[dict(item) for item in row.get("attempts", [])],
            quantity_basis=float(row.get("quantity_basis", 1.0) or 1.0),
            corporate_action_adjustments=[
                dict(item) for item in row.get("corporate_action_adjustments", [])
            ],
        )
        order_mgr._active[order.ts_code] = order
    return order_mgr


def _restore_parent_states(
    initial_state: NativeExecutionState | None,
) -> dict[str, _ParentState]:
    if initial_state is None:
        return {}
    return {
        str(row["order_id"]): _ParentState.from_state_dict(dict(row))
        for row in initial_state.parent_orders
    }


def _restore_attempts(
    initial_state: NativeExecutionState | None,
) -> list[ExecutionAttemptRecord]:
    if initial_state is None:
        return []
    return [ExecutionAttemptRecord(**dict(row)) for row in initial_state.attempts]


def _restore_trades(
    initial_state: NativeExecutionState | None,
) -> list[ExecutedTradeRecord]:
    if initial_state is None:
        return []
    return [ExecutedTradeRecord(**dict(row)) for row in initial_state.trades]


def _restore_corporate_actions(
    initial_state: NativeExecutionState | None,
) -> list[CorporateActionRecord]:
    if initial_state is None:
        return []
    return [
        CorporateActionRecord(**dict(row))
        for row in initial_state.corporate_actions
    ]


def _attempt_counts(attempts: list[ExecutionAttemptRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for attempt in attempts:
        counts[attempt.order_id] = max(
            counts.get(attempt.order_id, 0),
            int(attempt.attempt_number),
        )
    return counts


def _state_from_execution(
    *,
    cash: float,
    executor: PortfolioExecutor,
    order_mgr: OrderManager,
    parent_states: dict[str, _ParentState],
    active_parent_by_code: dict[str, str],
    ledger: ExecutionLedger,
    position_adj_factor: dict[str, float],
    active_targets: dict[str, float],
    event_counter: int,
    migration_diagnostics: tuple[str, ...] = (),
) -> NativeExecutionState:
    active_orders: list[dict] = []
    for code, order in sorted(order_mgr._active.items()):
        parent_order_id = active_parent_by_code.get(code)
        if not parent_order_id:
            continue
        active_orders.append(
            {
                "parent_order_id": parent_order_id,
                "ts_code": code,
                "requested": order.requested,
                "event_id": order.event_id,
                "status": order.status.value,
                "filled": order.filled,
                "remaining": order.remaining,
                "attempts": [dict(item) for item in order.attempts],
                "quantity_basis": order.quantity_basis,
                "corporate_action_adjustments": [
                    dict(item) for item in order.corporate_action_adjustments
                ],
            }
        )
    return NativeExecutionState(
        cash=cash,
        positions={code: dict(pos) for code, pos in executor._positions.items()},
        last_close=dict(executor._last_close),
        last_close_date=dict(executor._last_close_date),
        last_close_source=dict(executor._last_close_source),
        position_adj_factor=dict(position_adj_factor),
        active_targets=dict(active_targets),
        active_orders=tuple(active_orders),
        parent_orders=tuple(parent.to_state_dict() for parent in parent_states.values()),
        attempts=tuple(_attempt_to_state(attempt) for attempt in ledger.attempts),
        trades=tuple(_trade_to_state(trade) for trade in ledger.trades),
        corporate_actions=tuple(
            _corporate_action_to_state(action)
            for action in ledger.corporate_actions
        ),
        event_counter=event_counter,
        state_schema_version=NativeExecutionState.CURRENT_SCHEMA_VERSION,
        migration_diagnostics=tuple(migration_diagnostics),
    )


def _attempt_to_state(attempt: ExecutionAttemptRecord) -> dict:
    return {
        "attempt_id": attempt.attempt_id,
        "order_id": attempt.order_id,
        "attempt_number": attempt.attempt_number,
        "trade_date": attempt.trade_date,
        "requested_quantity": attempt.requested_quantity,
        "filled_quantity": attempt.filled_quantity,
        "unfilled_quantity": attempt.unfilled_quantity,
        "quantity_basis_id": attempt.quantity_basis_id,
        "raw_price": attempt.raw_price,
        "executed_price": attempt.executed_price,
        "commission": attempt.commission,
        "explicit_fee": attempt.explicit_fee,
        "slippage_cost": attempt.slippage_cost,
        "participation_rate": attempt.participation_rate,
        "status": attempt.status.value,
        "reason_code": attempt.reason_code,
        "knowledge_cutoff": attempt.knowledge_cutoff,
    }


def _trade_to_state(trade: ExecutedTradeRecord) -> dict:
    return {
        "trade_id": trade.trade_id,
        "attempt_id": trade.attempt_id,
        "order_id": trade.order_id,
        "ts_code": trade.ts_code,
        "direction": trade.direction.value,
        "quantity": trade.quantity,
        "quantity_basis_id": trade.quantity_basis_id,
        "price": trade.price,
        "notional": trade.notional,
        "commission": trade.commission,
        "explicit_fee": trade.explicit_fee,
        "slippage_cost": trade.slippage_cost,
        "trade_date": trade.trade_date,
        "knowledge_cutoff": trade.knowledge_cutoff,
    }


def _corporate_action_to_state(action: CorporateActionRecord) -> dict:
    return {
        "corporate_action_id": action.corporate_action_id,
        "ts_code": action.ts_code,
        "action_type": action.action_type.value,
        "effective_date": action.effective_date,
        "old_quantity": action.old_quantity,
        "new_quantity": action.new_quantity,
        "old_cost_basis": action.old_cost_basis,
        "new_cost_basis": action.new_cost_basis,
        "adjustment_factor": action.adjustment_factor,
        "economic_new_quantity": action.economic_new_quantity,
        "fractional_quantity": action.fractional_quantity,
        "cash_in_lieu": action.cash_in_lieu,
    }


def _cancel_active_parents(
    trade_date: str,
    parent_states: dict[str, _ParentState],
    active_parent_by_code: dict[str, str],
    *,
    reason: str,
) -> None:
    for code, parent_id in list(active_parent_by_code.items()):
        parent = parent_states[parent_id]
        if parent.status in {
            ParentOrderStatus.OPEN,
            ParentOrderStatus.PARTIALLY_FILLED,
        }:
            parent.cancel(trade_date, reason)
        del active_parent_by_code[code]


def _apply_corporate_actions(
    *,
    trade_date: str,
    executor: PortfolioExecutor,
    order_mgr: OrderManager,
    bar_lookup: dict[tuple[str, str], dict],
    adj_lookup: dict[tuple[str, str], float],
    position_adj_factor: dict[str, float],
    parent_states: dict[str, _ParentState],
    active_parent_by_code: dict[str, str],
    replacement_counts: dict[str, int],
    corporate_actions: list[CorporateActionRecord],
    trade_events: list[dict],
) -> None:
    pending_codes = {order.ts_code for order in order_mgr.get_pending_orders()}
    codes = set(executor._positions) | set(active_parent_by_code) | pending_codes
    for code in sorted(codes):
        pos = executor._positions.get(code, {})
        new_factor = adj_lookup.get((trade_date, code))
        old_factor = position_adj_factor.get(code)
        if new_factor is None:
            continue
        if old_factor is not None and new_factor != old_factor:
            last_close_before = executor._last_close.get(code, 0.0)
            last_close_after = (
                last_close_before * old_factor / new_factor
                if last_close_before > 0
                else 0.0
            )
            if last_close_after > 0:
                executor._last_close[code] = last_close_after

            old_size = int(pos.get("size", 0))
            new_size, fractional = adjust_shares_for_factor_change(
                old_size,
                old_factor,
                new_factor,
            )
            if old_size > 0:
                executor._positions[code] = {**pos, "size": new_size}
            scale = new_factor / old_factor
            corporate_action_id = f"CA-{trade_date}-{code}"
            adjustment_price = float(
                bar_lookup.get((trade_date, code), {}).get("open", 0.0)
                or bar_lookup.get((trade_date, code), {}).get("close", 0.0)
                or last_close_after
            )
            cash_in_lieu = fractional * adjustment_price

            corporate_actions.append(
                CorporateActionRecord(
                    corporate_action_id=corporate_action_id,
                    ts_code=code,
                    action_type=CorporateActionType.SHARE_CONVERSION,
                    effective_date=trade_date,
                    old_quantity=old_size,
                    new_quantity=new_size,
                    old_cost_basis=max(last_close_before, 0.0),
                    new_cost_basis=max(last_close_after, 0.0),
                    adjustment_factor=scale,
                    economic_new_quantity=old_size * scale,
                    fractional_quantity=fractional,
                    cash_in_lieu=cash_in_lieu,
                )
            )

            old_parent_id = active_parent_by_code.get(code)
            if old_parent_id is None:
                prior_parents = [
                    parent
                    for parent in parent_states.values()
                    if parent.ts_code == code and parent.created_date < trade_date
                ]
                if prior_parents:
                    old_parent_id = max(
                        prior_parents,
                        key=lambda parent: (parent.created_date, parent.order_id),
                    ).order_id
            if old_parent_id:
                old_parent = parent_states[old_parent_id]
                if old_parent.remaining_quantity > 0:
                    old_parent.cancel(trade_date, "CORPORATE_ACTION_REPLACED")
                    replacement_counts[old_parent_id] = (
                        replacement_counts.get(old_parent_id, 0) + 1
                    )
                    replacement_id = f"{old_parent_id}-R{replacement_counts[old_parent_id]}"
                    replacement_requested = _replacement_residual_quantity(
                        old_parent.direction,
                        old_parent.remaining_quantity,
                        scale,
                        lot_size=old_parent.lot_size,
                    )
                    order_mgr.replace_for_corporate_action(
                        code,
                        replacement_requested,
                        scale=scale,
                        trade_date=trade_date,
                        corporate_action_id=corporate_action_id,
                        expected_remaining=old_parent.remaining_quantity,
                    )
                    adjustment = {
                        "corporate_action_id": corporate_action_id,
                        "trade_date": trade_date,
                        "scale": scale,
                        "before": {
                            "requested": old_parent.original_requested_quantity,
                            "filled": old_parent.cumulative_filled_quantity,
                            "remaining": old_parent.remaining_quantity,
                            "quantity_basis": old_parent.quantity_basis,
                        },
                        "after": {
                            "requested": replacement_requested,
                            "filled": 0,
                            "remaining": replacement_requested,
                            "quantity_basis": old_parent.quantity_basis * scale,
                        },
                    }
                    old_parent.corporate_action_adjustments = (
                        *old_parent.corporate_action_adjustments,
                        adjustment,
                    )
                    if replacement_requested > 0:
                        parent_states[replacement_id] = _ParentState(
                            order_id=replacement_id,
                            decision_id=old_parent.decision_id,
                            signal_week=old_parent.signal_week,
                            ts_code=code,
                            direction=old_parent.direction,
                            created_date=trade_date,
                            original_requested_quantity=replacement_requested,
                            quantity_basis=old_parent.quantity_basis * scale,
                            lot_size=old_parent.lot_size,
                            target_weight=old_parent.target_weight,
                            replacement_of_order_id=old_parent_id,
                            replacement_chain_id=(
                                old_parent.replacement_chain_id or old_parent_id
                            ),
                            corporate_action_id=corporate_action_id,
                            corporate_action_adjustments=(adjustment,),
                        )
                        active_parent_by_code[code] = replacement_id
                    else:
                        active_parent_by_code.pop(code, None)

            executor.cash += cash_in_lieu
            trade_events.append(
                {
                    "trade_date": trade_date,
                    "event_type": "CORPORATE_ACTION",
                    "corporate_action_id": corporate_action_id,
                    "ts_code": code,
                    "action": "SHARE_ADJUSTMENT",
                    "status": "APPLIED",
                    "requested": old_size,
                    "filled": new_size,
                    "unfilled": 0,
                    "reason": "fund_adj_factor_change",
                    "price": 0.0,
                    "commission": 0.0,
                    "slippage_bps": 0.0,
                    "adv20": 0.0,
                    "adv_observations": 0,
                    "participation_rate": 0.0,
                    "post_holding": new_size,
                    "remaining": 0,
                    "cash_in_lieu": cash_in_lieu,
                    "old_adj_factor": old_factor,
                    "new_adj_factor": new_factor,
                    "fractional_remainder": fractional,
                    "last_close_before": last_close_before,
                    "last_close_after": last_close_after,
                    "last_valid_close_date": (
                        executor._last_close_date.get(code, "")
                        if executor._last_close_source.get(code) == "close" else ""
                    ),
                    "valuation_anchor_date": executor._last_close_date.get(code, ""),
                    "valuation_anchor_source": executor._last_close_source.get(code, ""),
                    "signal_event_id": "",
                    "signal_week": "",
                    "order_id": "",
                    "attempt_id": "",
                }
            )
        position_adj_factor[code] = new_factor


def _record_execution_event(
    *,
    event: dict,
    trade_date: str,
    active_targets: dict[str, float],
    parent_states: dict[str, _ParentState],
    active_parent_by_code: dict[str, str],
    attempt_counts: dict[str, int],
    attempts: list[ExecutionAttemptRecord],
    trades: list[ExecutedTradeRecord],
    trade_events: list[dict],
    provenance_by_code: dict[str, MarketRules],
) -> None:
    code = str(event.get("code", ""))
    parent_id = active_parent_by_code.get(code)
    if not parent_id:
        return
    parent = parent_states[parent_id]
    provenance = provenance_by_code.get(code)
    if provenance is not None:
        parent.rule_version = provenance.rule_version
        parent.source_record_id = provenance.source_record_id
    requested = int(event.get("requested", 0) or 0)
    filled = int(event.get("filled", 0) or 0)
    attempt_counts[parent_id] = attempt_counts.get(parent_id, 0) + 1
    attempt_number = attempt_counts[parent_id]
    attempt_id = f"{parent_id}-A{attempt_number}"
    raw_price = float(event.get("raw_open", event.get("price", 0.0)) or 0.0)
    executed_price = float(event.get("price", 0.0) or 0.0)
    commission = float(event.get("commission", 0.0) or 0.0)
    explicit_fee = float(event.get("explicit_fee", 0.0) or 0.0)
    slippage_bps = float(event.get("slippage_bps", 0.0) or 0.0)
    notional = abs(filled * executed_price)
    status = _attempt_status(str(event.get("status", "")))

    attempts.append(
        ExecutionAttemptRecord(
            attempt_id=attempt_id,
            order_id=parent_id,
            attempt_number=attempt_number,
            trade_date=trade_date,
            requested_quantity=requested,
            filled_quantity=filled,
            unfilled_quantity=max(requested - filled, 0),
            quantity_basis_id=parent.quantity_basis_id,
            raw_price=raw_price,
            executed_price=executed_price,
            commission=commission,
            explicit_fee=explicit_fee,
            slippage_cost=notional * max(slippage_bps, 0.0) / 10_000.0,
            participation_rate=float(event.get("participation_rate", 0.0) or 0.0),
            status=status,
            reason_code=str(event.get("reason", "") or ""),
            knowledge_cutoff=(
                provenance.knowledge_cutoff
                if provenance is not None
                else parent.rule_knowledge_cutoff
            ),
        )
    )

    if filled > 0:
        trades.append(
            ExecutedTradeRecord(
                trade_id=f"{attempt_id}-T1",
                attempt_id=attempt_id,
                order_id=parent_id,
                ts_code=code,
                direction=parent.direction,
                quantity=filled,
                quantity_basis_id=parent.quantity_basis_id,
                price=executed_price,
                notional=notional,
                commission=commission,
                explicit_fee=explicit_fee,
                slippage_cost=notional * max(slippage_bps, 0.0) / 10_000.0,
                trade_date=trade_date,
                knowledge_cutoff=(
                    provenance.knowledge_cutoff
                    if provenance is not None
                    else parent.rule_knowledge_cutoff
                ),
            )
        )

    parent.record_fill(filled)
    if parent.status is ParentOrderStatus.FILLED:
        active_parent_by_code.pop(code, None)

    enriched = {
        **event,
        "trade_date": trade_date,
        "ts_code": code,
        "code": code,
        "order_id": parent_id,
        "attempt_id": attempt_id,
        "signal_event_id": parent.decision_id,
        "signal_week": parent.signal_week,
        "target_weight": float(active_targets.get(code, 0.0)),
        "rule_version": provenance.rule_version if provenance else parent.rule_version,
        "source_record_id": (
            provenance.source_record_id if provenance else parent.source_record_id
        ),
    }
    trade_events.append(enriched)


def _attempt_status(value: str) -> LedgerAttemptStatus:
    if value == "FILLED":
        return LedgerAttemptStatus.FILLED
    if value == "PARTIAL":
        return LedgerAttemptStatus.PARTIALLY_FILLED
    if value == "BLOCKED":
        return LedgerAttemptStatus.BLOCKED
    if value == "INVALID":
        return LedgerAttemptStatus.INVALID
    return LedgerAttemptStatus.PENDING


def _equity_series(equity_records: list[dict], initial_capital: float) -> pd.Series:
    if not equity_records:
        return pd.Series(dtype=float, name="executed_strategy")
    frame = pd.DataFrame(equity_records)
    return pd.Series(
        frame["equity"].values / initial_capital,
        index=frame["trade_date"].values,
        name="executed_strategy",
    )


def _holdings_snapshot(
    *,
    executor: PortfolioExecutor,
    trade_date: str,
    daily_equity: float,
    active_targets: dict[str, float],
    close_lookup: dict[tuple[str, str], float],
    adj_lookup: dict[tuple[str, str], float],
    position_adj_factor: dict[str, float],
    date_ordinal: dict[str, int],
) -> list[dict]:
    holdings: list[dict] = []
    for code, pos in sorted(executor._positions.items()):
        mark_price = close_lookup.get((trade_date, code), executor._last_close.get(code, 0.0))
        market_value = float(pos.get("size", 0)) * mark_price
        anchor_date = executor._last_close_date.get(code, "")
        anchor_source = executor._last_close_source.get(code, "")
        holdings.append(
            {
                "ts_code": code,
                "quantity": int(pos.get("size", 0)),
                "mark_price": mark_price,
                "market_value": market_value,
                "target_weight": float(active_targets.get(code, 0.0)),
                "actual_weight": market_value / daily_equity if daily_equity > 0 else 0.0,
                "adj_factor": adj_lookup.get(
                    (trade_date, code),
                    position_adj_factor.get(code, 0.0),
                ),
                "stale_days": (
                    date_ordinal.get(trade_date, 0) - date_ordinal.get(anchor_date, 0)
                    if anchor_date
                    else 0
                ),
                "last_valid_close_date": anchor_date if anchor_source == "close" else "",
                "valuation_anchor_date": anchor_date,
                "valuation_anchor_source": anchor_source or "unavailable",
            }
        )
    return holdings


def _orders_from_ledger(
    ledger: ExecutionLedger,
    parent_states: dict[str, _ParentState],
) -> list[dict]:
    attempts_by_order: dict[str, list[ExecutionAttemptRecord]] = {}
    for attempt in ledger.attempts:
        attempts_by_order.setdefault(attempt.order_id, []).append(attempt)

    rows: list[dict] = []
    for parent in ledger.parent_orders:
        parent_state = parent_states.get(parent.order_id)
        parent_attempts = attempts_by_order.get(parent.order_id) or [None]
        for attempt in parent_attempts:
            rows.append(
                {
                    "order_id": parent.order_id,
                    "parent_order_id": parent.order_id,
                    "replacement_of_order_id": parent.replacement_of_order_id,
                    "replacement_chain_id": parent.replacement_chain_id,
                    "corporate_action_id": parent.corporate_action_id,
                    "event_id": parent.decision_id,
                    "decision_id": parent.decision_id,
                    "ts_code": parent.ts_code,
                    "direction": parent.direction.value,
                    "requested": parent.original_requested_quantity,
                    "filled": parent.cumulative_filled_quantity,
                    "attempt_id": attempt.attempt_id if attempt else "",
                    "attempt_number": attempt.attempt_number if attempt else 0,
                    "trade_date": attempt.trade_date if attempt else "",
                    "attempt_filled": attempt.filled_quantity if attempt else 0,
                    "attempt_status": attempt.status.value if attempt else "NOT_ATTEMPTED",
                    "reason": attempt.reason_code if attempt else "",
                    "cumulative_filled_at_attempt": (
                        _cumulative_filled_at_attempt(ledger.attempts, attempt)
                        if attempt
                        else 0
                    ),
                    "attempt_quantity_basis": (
                        _quantity_basis_from_id(attempt.quantity_basis_id)
                        if attempt
                        else _quantity_basis_from_id(parent.quantity_basis_id)
                    ),
                    "remaining": parent.remaining_quantity,
                    "final_status": parent.status.value,
                    "current_quantity_basis": _quantity_basis_from_id(
                        parent.quantity_basis_id
                    ),
                    "corporate_action_adjustments": json.dumps(
                        list(
                            parent_state.corporate_action_adjustments
                            if parent_state
                            else ()
                        ),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    "rule_version": parent_state.rule_version if parent_state else "",
                    "source_record_id": (
                        parent_state.source_record_id if parent_state else ""
                    ),
                }
            )
    return rows


def _cumulative_filled_at_attempt(
    attempts: tuple[ExecutionAttemptRecord, ...],
    attempt: ExecutionAttemptRecord,
) -> int:
    return sum(
        item.filled_quantity
        for item in attempts
        if item.order_id == attempt.order_id
        and item.attempt_number <= attempt.attempt_number
    )


def _quantity_basis_id(ts_code: str, quantity_basis: object) -> str:
    return f"{ts_code}:shares:{float(quantity_basis or 1.0):.12g}"


def _quantity_basis_from_id(quantity_basis_id: str) -> float:
    try:
        return float(quantity_basis_id.rsplit(":", 1)[-1])
    except ValueError:
        return 1.0


def _replacement_residual_quantity(
    direction: OrderDirection | str,
    remaining_quantity: int,
    adjustment_factor: float,
    *,
    lot_size: int,
) -> int:
    adjusted = int(float(remaining_quantity) * float(adjustment_factor))
    if OrderDirection(direction) is OrderDirection.BUY:
        return (adjusted // lot_size) * lot_size
    return adjusted
