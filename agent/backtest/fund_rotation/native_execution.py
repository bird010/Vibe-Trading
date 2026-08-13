"""Native v2 execution engine for fund-rotation targets.

The engine shares the low-level execution primitives with the legacy loop, but
owns the formal v2 execution facts directly.  It does not adapt a
``PipelineResult`` and does not call ``run_execution_loop``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

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
from backtest.fund_rotation.executor import PortfolioExecutor
from backtest.fund_rotation.orders import OrderManager
from backtest.fund_rotation.share_adjustment import adjust_shares_for_factor_change


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


@dataclass(frozen=True)
class NativeExecutionResult:
    ledger: ExecutionLedger
    executed_equity: pd.Series
    trade_events: list[dict]
    orders: list[dict]
    positions_history: list[dict]
    ending_cash: float
    ending_positions: dict[str, dict]


@dataclass
class _ParentState:
    order_id: str
    decision_id: str
    ts_code: str
    direction: str
    created_date: str
    original_requested_quantity: int
    quantity_basis: float
    lot_size: int
    cumulative_filled_quantity: int = 0
    remaining_quantity: int = 0
    replacement_of_order_id: str = ""
    replacement_chain_id: str = ""
    corporate_action_id: str = ""
    status: ParentOrderStatus = ParentOrderStatus.OPEN
    completed_date: str = ""
    cancel_reason: str = ""

    def __post_init__(self) -> None:
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
        )


class FundRotationExecutionEngine:
    def execute(
        self,
        request: NativeExecutionRequest,
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> NativeExecutionResult:
        config = request.execution
        evaluation_dates = list(request.evaluation_dates)
        ctx = build_execution_context(request.fund_daily, request.fund_adj, config)

        if not request.targets:
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
        if not exec_date_map:
            return _cash_hold_result(evaluation_dates, request.initial_capital)

        rules = ChinaETFExecutionRules(
            commission_rate=config.commission_rate,
            commission_min=config.commission_min,
            other_fee_rate=config.other_fee_rate,
        )
        executor = PortfolioExecutor(cash=request.initial_capital, rules=rules)
        order_mgr = OrderManager()
        profiler = ExecutionProfiler()

        parent_states: dict[str, _ParentState] = {}
        active_parent_by_code: dict[str, str] = {}
        attempt_counts: dict[str, int] = {}
        replacement_counts: dict[str, int] = {}
        attempts: list[ExecutionAttemptRecord] = []
        trades: list[ExecutedTradeRecord] = []
        corporate_actions: list[CorporateActionRecord] = []
        trade_events: list[dict] = []
        equity_records: list[dict] = []
        positions_history: list[dict] = []

        active_targets: dict[str, float] = {}
        position_adj_factor: dict[str, float] = {}
        date_ordinal = {date: index for index, date in enumerate(evaluation_dates)}
        event_counter = 0

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
                lot_size=rules.lot_size,
            )

            if trade_date in exec_date_map:
                signal_week, targets = exec_date_map[trade_date]
                active_targets = targets
                event_counter += 1
                signal_event_id = f"SIG-{signal_week}-{event_counter:04d}"

                all_codes = set(targets) | set(executor._positions)
                bars = {
                    code: ctx.bar_lookup[(trade_date, code)]
                    for code in all_codes
                    if (trade_date, code) in ctx.bar_lookup
                }
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
                    target_size = int(target_weight * pre_equity / price)
                    delta = target_size - current_size
                    if delta:
                        deltas[code] = delta

                _cancel_active_parents(
                    trade_date,
                    parent_states,
                    active_parent_by_code,
                    reason="SUPERSEDED_BY_NEW_TARGET",
                )
                order_mgr.create_orders(deltas, event_id=signal_event_id)
                for code, delta in sorted(deltas.items()):
                    order_id = f"{signal_event_id}-{code}"
                    parent_states[order_id] = _ParentState(
                        order_id=order_id,
                        decision_id=signal_event_id,
                        ts_code=code,
                        direction="BUY" if delta > 0 else "SELL",
                        created_date=trade_date,
                        original_requested_quantity=abs(int(delta)),
                        quantity_basis=1.0,
                        lot_size=rules.lot_size,
                    )
                    active_parent_by_code[code] = order_id

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
                rebalance_result = execute_with_capacity(
                    executor=executor,
                    order_mgr=order_mgr,
                    targets=active_targets,
                    bars=bars,
                    trade_date=trade_date,
                    config=config,
                    adv_index=ctx.adv_index,
                    rules=rules,
                    profiler=profiler,
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
                    )

            for code in executor._positions:
                factor = ctx.adj_lookup.get((trade_date, code))
                if factor is not None:
                    position_adj_factor.setdefault(code, factor)

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
                    "execution_failure_cash": 0.0,
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
            orders=_orders_from_ledger(ledger),
            positions_history=positions_history,
            ending_cash=float(executor.cash),
            ending_positions={code: dict(pos) for code, pos in executor._positions.items()},
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
    )


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
    lot_size: int,
) -> None:
    for code, pos in list(executor._positions.items()):
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
            executor._positions[code] = {**pos, "size": new_size}
            scale = new_factor / old_factor
            corporate_action_id = f"CA-{trade_date}-{code}"

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
                )
            )

            old_parent_id = active_parent_by_code.get(code)
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
                        lot_size=lot_size,
                    )
                    parent_states[replacement_id] = _ParentState(
                        order_id=replacement_id,
                        decision_id=old_parent.decision_id,
                        ts_code=code,
                        direction=old_parent.direction,
                        created_date=trade_date,
                        original_requested_quantity=replacement_requested,
                        quantity_basis=old_parent.quantity_basis * scale,
                        lot_size=lot_size,
                        replacement_of_order_id=old_parent_id,
                        replacement_chain_id=(
                            old_parent.replacement_chain_id or old_parent_id
                        ),
                        corporate_action_id=corporate_action_id,
                    )
                    active_parent_by_code[code] = replacement_id

            order_mgr.adjust_for_factor(
                code,
                scale,
                trade_date=trade_date,
                corporate_action_id=corporate_action_id,
            )

            adjustment_price = float(
                bar_lookup.get((trade_date, code), {}).get("open", 0.0)
                or bar_lookup.get((trade_date, code), {}).get("close", 0.0)
                or last_close_after
            )
            cash_in_lieu = fractional * adjustment_price
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
                    "cash_in_lieu": cash_in_lieu,
                    "old_adj_factor": old_factor,
                    "new_adj_factor": new_factor,
                    "fractional_remainder": fractional,
                    "last_close_before": last_close_before,
                    "last_close_after": last_close_after,
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
) -> None:
    code = str(event.get("code", ""))
    parent_id = active_parent_by_code.get(code)
    if not parent_id:
        return
    parent = parent_states[parent_id]
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
        "target_weight": float(active_targets.get(code, 0.0)),
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


def _orders_from_ledger(ledger: ExecutionLedger) -> list[dict]:
    attempts_by_order: dict[str, list[ExecutionAttemptRecord]] = {}
    for attempt in ledger.attempts:
        attempts_by_order.setdefault(attempt.order_id, []).append(attempt)

    rows: list[dict] = []
    for parent in ledger.parent_orders:
        parent_attempts = attempts_by_order.get(parent.order_id) or [None]
        for attempt in parent_attempts:
            rows.append(
                {
                    "order_id": parent.order_id,
                    "event_id": parent.decision_id,
                    "ts_code": parent.ts_code,
                    "direction": parent.direction.value,
                    "requested": parent.original_requested_quantity,
                    "filled": parent.cumulative_filled_quantity,
                    "attempt_number": attempt.attempt_number if attempt else 0,
                    "trade_date": attempt.trade_date if attempt else "",
                    "attempt_filled": attempt.filled_quantity if attempt else 0,
                    "attempt_status": attempt.status.value if attempt else "NOT_ATTEMPTED",
                    "reason": attempt.reason_code if attempt else "",
                    "remaining": parent.remaining_quantity,
                    "final_status": parent.status.value,
                    "current_quantity_basis": _quantity_basis_from_id(
                        parent.quantity_basis_id
                    ),
                }
            )
    return rows


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
