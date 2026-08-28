"""Common execution & valuation module — Phase 2 Task 2 (design §12/§32.3).

Extracted verbatim from the legacy fixed pipeline so the strategy-neutral Runner
(Phase 2 Task 3) can drive execution directly. Contains the read-only execution
context, the continuous-account execution loop (sell-before-buy, common cash
scaling, ADV20 capacity, ETF 100-share lots), mark-to-market valuation, and
full-interval equity generation.

Strategy plug-ins never import this module's internals; only the common Runner
and the (legacy) pipeline orchestration do.
"""

from __future__ import annotations

import json
import logging
import time as _time
from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

from backtest.fund_rotation.capacity import ADVIndex, apply_capacity_and_slippage
from backtest.fund_rotation.config import FundRotationConfig
from backtest.fund_rotation.etf_rules import ChinaETFExecutionRules
from backtest.fund_rotation.evaluation import TargetSnapshot, schedule_targets
from backtest.fund_rotation.executor import PortfolioExecutor, RebalanceResult
from backtest.fund_rotation.factor_basis import sync_position_factor_basis
from backtest.fund_rotation.orders import AttemptStatus, OrderManager
from backtest.fund_rotation.share_adjustment import adjust_shares_for_factor_change
from backtest.fund_rotation.universe import ExclusionRecord

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Complete backtest result."""

    # Strategy outputs
    weekly_targets: dict[str, dict[str, float]] = field(default_factory=dict)
    cluster_history: list[dict] = field(default_factory=list)
    exclusions: list[ExclusionRecord] = field(default_factory=list)

    # Execution outputs (continuous account)
    executed_equity: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    trade_events: list[dict] = field(default_factory=list)
    orders: list[dict] = field(default_factory=list)
    positions_history: list[dict] = field(default_factory=list)

    # Benchmark series
    strategy_cumulative: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    equal_weight_benchmark: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    buy_hold_benchmark: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    cash_benchmark: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    secondary_benchmarks: dict[str, pd.Series] = field(default_factory=dict)

    # Metrics
    strategy_metrics: dict[str, float] = field(default_factory=dict)
    ideal_strategy_metrics: dict[str, float] = field(default_factory=dict)
    benchmark_metrics: dict[str, dict[str, float]] = field(default_factory=dict)
    robustness: dict[str, object] = field(default_factory=dict)
    quality_status: str = "VALID"
    execution_diagnostics: dict[str, object] = field(default_factory=dict)

    # Metadata
    num_weeks: int = 0
    num_reclusters: int = 0
    num_etfs_used: int = 0


@dataclass(frozen=True)
class ExecutionContext:
    """Read-only market data context shared across execution loop invocations.

    Contains only immutable market data indexes. Does NOT contain mutable
    account state (PortfolioExecutor, OrderManager).
    """

    bar_lookup: dict[tuple[str, str], dict]
    close_lookup: dict[tuple[str, str], float]
    adj_lookup: dict[tuple[str, str], float]
    adv_index: ADVIndex
    all_trade_dates: list[str]


@dataclass
class ExecutionProfiler:
    """Lightweight inclusive/exclusive profiler for run_execution_loop.

    Nesting: execute_with_capacity ⊃ {compute_adv20, cash_scaling}.
    Do NOT sum parent + children for total; use exclusive times.
    """

    # Exclusive accumulators (seconds)
    lookup_build: float = 0.0
    corporate_action: float = 0.0
    execute_with_capacity_inclusive: float = 0.0
    adv20_exclusive: float = 0.0
    cash_scaling_exclusive: float = 0.0
    position_snapshot: float = 0.0

    # Call counts
    execute_calls: int = 0
    adv20_calls: int = 0

    def report(self, label: str = "") -> dict:
        prefix = f"[profile {label}] " if label else "[profile] "
        data = {
            "lookup_build_s": round(self.lookup_build, 3),
            "corporate_action_s": round(self.corporate_action, 3),
            "execute_with_capacity_inclusive_s": round(self.execute_with_capacity_inclusive, 3),
            "adv20_exclusive_s": round(self.adv20_exclusive, 3),
            "cash_scaling_exclusive_s": round(self.cash_scaling_exclusive, 3),
            "position_snapshot_s": round(self.position_snapshot, 3),
            "execute_calls": self.execute_calls,
            "adv20_calls": self.adv20_calls,
        }
        logger.info(
            "%slookup=%.2fs corp=%.2fs exec(incl)=%.2fs adv=%.2fs scale=%.2fs snap=%.2fs calls=%d/%d",
            prefix, self.lookup_build, self.corporate_action,
            self.execute_with_capacity_inclusive, self.adv20_exclusive,
            self.cash_scaling_exclusive, self.position_snapshot,
            self.execute_calls, self.adv20_calls,
        )
        return data


def build_execution_context(
    fund_daily: pd.DataFrame,
    fund_adj: pd.DataFrame,
    config: FundRotationConfig,
    profiler: ExecutionProfiler | None = None,
) -> ExecutionContext:
    """Build read-only market data indexes for execution loop reuse."""
    _t0 = _time.perf_counter()
    fund_daily_copy = fund_daily.copy()
    fund_daily_copy["trade_date"] = fund_daily_copy["trade_date"].astype(str)
    fund_daily_copy["ts_code"] = fund_daily_copy["ts_code"].astype(str)

    all_trade_dates = sorted(fund_daily_copy["trade_date"].unique())

    _bar_cols = ["open", "close", "vol", "high", "low", "pre_close"]
    _available_cols = [c for c in _bar_cols if c in fund_daily_copy.columns]
    _bar_indexed = fund_daily_copy.set_index(["trade_date", "ts_code"])[_available_cols]
    _bar_indexed = _bar_indexed.fillna(0.0)
    bar_lookup: dict[tuple[str, str], dict] = {
        k: v for k, v in _bar_indexed.to_dict("index").items()
    }

    close_lookup: dict[tuple[str, str], float] = {
        k: v["close"] for k, v in bar_lookup.items() if v.get("close", 0.0) > 0
    }

    adv_grouped: dict[str, pd.DataFrame] = {
        code: group.reset_index(drop=True)
        for code, group in fund_daily_copy.groupby("ts_code", sort=False)
    }

    adj_lookup: dict[tuple[str, str], float] = {}
    if fund_adj is not None and not fund_adj.empty:
        adj_copy = fund_adj.copy()
        adj_copy["trade_date"] = adj_copy["trade_date"].astype(str)
        adj_copy["ts_code"] = adj_copy["ts_code"].astype(str)
        adj_copy = adj_copy[adj_copy["adj_factor"].notna() & (adj_copy["adj_factor"] > 0)]
        adj_series = adj_copy.set_index(["trade_date", "ts_code"])["adj_factor"]
        adj_lookup = {(td, tc): float(v) for (td, tc), v in adj_series.items()}

    # Build ADV index for fast causal lookup (included in lookup_build timing)
    adv_index = ADVIndex(
        adv_grouped, lookback=config.adv_lookback, min_obs=config.adv_min_observations,
    )

    if profiler:
        profiler.lookup_build += _time.perf_counter() - _t0

    return ExecutionContext(
        bar_lookup=bar_lookup,
        close_lookup=close_lookup,
        adj_lookup=adj_lookup,
        adv_index=adv_index,
        all_trade_dates=all_trade_dates,
    )


def first_actual_fill_date(result: PipelineResult, label: str) -> str:
    fills = [
        str(event.get("trade_date", ""))
        for event in result.trade_events
        if int(event.get("filled", 0)) > 0
        and event.get("status") in {"FILLED", "PARTIAL"}
    ]
    if not fills:
        raise ValueError(f"{label} had no executable first fill.")
    return min(fills)


def align_theoretical_to_common_dates(
    theoretical: pd.Series, common_dates: pd.Index,
) -> pd.Series:
    """Forward-fill weekly theoretical NAV onto the executable daily interval."""
    expanded = theoretical.reindex(theoretical.index.union(common_dates)).sort_index().ffill()
    aligned = expanded.reindex(common_dates)
    if aligned.empty or aligned.isna().any():
        raise ValueError("Theoretical strategy has no coverage at common interval start.")
    aligned.name = theoretical.name
    return aligned


def run_execution_loop(
    result: PipelineResult,
    config: FundRotationConfig,
    ctx: ExecutionContext,
    profiler: ExecutionProfiler | None = None,
    execution_targets: dict | None = None,
    evaluation_dates: list[str] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> None:
    """§12 — Run continuous-account execution with daily equity tracking.

    Anti-look-ahead: signal generated at signal_week close → execute at first
    trade day STRICTLY AFTER signal_week (typically next Monday). A
    pre-evaluation signal executes at the first evaluation trading day (§24).

    Integrates: ADV20 capacity, participation-rate slippage, OrderManager
    residual orders, daily mark-to-market equity, and fee impacts.

    §26.1: when ``should_cancel`` returns True the loop stops at the next
    daily checkpoint; events/equity collected so far are preserved and the
    caller decides the terminal state (legacy callers pass no token).
    """
    targets_map = execution_targets if execution_targets is not None else result.weekly_targets
    # §24: the evaluation calendar (trading days within [start_date, end_date])
    # is the single source passed by the caller; fall back to deriving it from
    # the context when not supplied.
    if evaluation_dates is None:
        evaluation_dates = [
            d for d in ctx.all_trade_dates
            if (not config.start_date or d >= config.start_date)
            and (not config.end_date or d <= config.end_date)
        ]
    if not targets_map:
        # No targets: hold cash (initial NAV) over the full evaluation interval
        # so equity and metrics are defined even without any trade (§32.1).
        result.executed_equity = pd.Series(
            1.0, index=list(evaluation_dates), name="executed_strategy",
        )
        return

    rules = ChinaETFExecutionRules(
        commission_rate=config.commission_rate,
        commission_min=config.commission_min,
        other_fee_rate=config.other_fee_rate,
    )
    executor = PortfolioExecutor(cash=config.initial_capital, rules=rules)
    order_mgr = OrderManager()

    # Use shared read-only context
    bar_lookup = ctx.bar_lookup
    close_lookup = ctx.close_lookup
    adj_lookup = ctx.adj_lookup

    # §24: build the execution schedule via the shared schedule_targets. A
    # pre-evaluation signal maps to the first evaluation trading day, and an
    # in-evaluation signal maps to the first trading day strictly after it.
    snapshots = [TargetSnapshot(pd.Timestamp(sw), tgts) for sw, tgts in targets_map.items()]
    schedule = schedule_targets(snapshots, [pd.Timestamp(d) for d in evaluation_dates])
    exec_schedule: list[tuple[str, str, dict[str, float]]] = [
        (snap.signal_date.strftime("%Y%m%d"), exec_date.strftime("%Y%m%d"), dict(snap.weights))
        for exec_date, snap in sorted(schedule.items())
    ]

    if not exec_schedule:
        # Targets exist but none are schedulable within the evaluation interval:
        # hold cash over the full interval.
        result.executed_equity = pd.Series(
            1.0, index=list(evaluation_dates), name="executed_strategy",
        )
        return

    # Track daily equity over the full evaluation interval (§24/§32.1). Days
    # before the first execution hold cash (initial NAV).
    equity_dates = list(evaluation_dates)

    # Map exec_date -> (signal_week, targets) for quick lookup
    exec_date_map: dict[str, tuple[str, dict[str, float]]] = {}
    for signal_week, exec_date, targets in exec_schedule:
        exec_date_map[exec_date] = (signal_week, targets)

    # Daily equity records
    equity_records: list[dict] = []
    event_counter = 0
    active_targets: dict[str, float] = {}
    event_context: dict[str, tuple[str, dict[str, float]]] = {}
    position_adj_factor: dict[str, float] = {}
    date_ordinal = {date: index for index, date in enumerate(equity_dates)}

    for trade_date in equity_dates:
        # §26.1 — cancellation checkpoint at each execution-day boundary.
        if should_cancel is not None and should_cancel():
            break
        # Apply the economic-share convention before today's open execution.
        _t_corp = _time.perf_counter()
        for code, pos in list(executor._positions.items()):
            new_factor = adj_lookup.get((trade_date, code))
            old_factor = position_adj_factor.get(code)
            if new_factor is None:
                continue
            if old_factor is not None and new_factor != old_factor:
                last_close_before = executor._last_close.get(code, 0.0)
                last_close_after = (
                    last_close_before * old_factor / new_factor
                    if last_close_before > 0 else 0.0
                )
                if last_close_after > 0:
                    executor._last_close[code] = last_close_after
                old_size = int(pos.get("size", 0))
                new_size, fractional = adjust_shares_for_factor_change(
                    old_size, old_factor, new_factor,
                )
                executor._positions[code] = {**pos, "size": new_size}
                scale = new_factor / old_factor
                corporate_action_id = f"CA-{trade_date}-{code}"
                order_mgr.adjust_for_factor(
                    code, scale, trade_date=trade_date,
                    corporate_action_id=corporate_action_id,
                )
                adjustment_price = float(
                    bar_lookup.get((trade_date, code), {}).get("open", 0.0)
                    or bar_lookup.get((trade_date, code), {}).get("close", 0.0)
                    or last_close_after
                )
                cash_in_lieu = fractional * adjustment_price
                executor.cash += cash_in_lieu
                result.trade_events.append({
                    "trade_date": trade_date, "signal_week": "",
                    "signal_event_id": "", "order_id": "", "attempt_id": "",
                    "event_type": "CORPORATE_ACTION",
                    "corporate_action_id": corporate_action_id,
                    "ts_code": code, "action": "SHARE_ADJUSTMENT", "status": "APPLIED",
                    "requested": old_size, "filled": new_size, "unfilled": 0,
                    "reason": "fund_adj_factor_change", "price": 0.0,
                    "commission": 0.0, "slippage_bps": 0.0, "adv20": 0.0,
                    "adv_observations": 0, "participation_rate": 0.0,
                    "post_holding": new_size, "remaining": 0,
                    "old_adj_factor": old_factor, "new_adj_factor": new_factor,
                    "fractional_remainder": fractional,
                    "cash_in_lieu": cash_in_lieu,
                    "last_close_before": last_close_before,
                    "last_close_after": last_close_after,
                    "last_valid_close_date": (
                        executor._last_close_date.get(code, "")
                        if executor._last_close_source.get(code) == "close" else ""
                    ),
                    "valuation_anchor_date": executor._last_close_date.get(code, ""),
                    "valuation_anchor_source": executor._last_close_source.get(code, ""),
                })
            position_adj_factor[code] = new_factor

        if profiler:
            profiler.corporate_action += _time.perf_counter() - _t_corp

        signal_week = ""
        if trade_date in exec_date_map:
            signal_week, targets = exec_date_map[trade_date]
            active_targets = targets
            event_counter += 1
            signal_event_id = f"SIG-{signal_week}-{event_counter:04d}"
            event_context[signal_event_id] = (signal_week, targets)

            # Compute deltas for OrderManager
            all_codes = set(targets) | set(executor._positions)
            bars = {
                code: bar_lookup[(trade_date, code)]
                for code in all_codes if (trade_date, code) in bar_lookup
            }
            pre_equity = executor._compute_equity_anchor(sorted(all_codes), bars)
            deltas: dict[str, int] = {}
            for code in all_codes:
                current_size = executor._positions.get(code, {}).get("size", 0)
                tw = targets.get(code, 0.0)
                price = bars.get(code, {}).get("open", 0)
                if price <= 0:
                    price = executor._last_close.get(code, 0)
                if price <= 0:
                    continue
                target_size = int(tw * pre_equity / price)
                delta = target_size - current_size
                if delta != 0:
                    deltas[code] = delta

            # Create orders (cancels previous residuals)
            order_mgr.create_orders(deltas, event_id=signal_event_id)

        # Every trading day retries the exact residual of active parent orders.
        pending = order_mgr.get_pending_orders()
        all_codes = {o.ts_code for o in pending} | set(executor._positions) | set(active_targets)
        bars = {
            code: bar_lookup[(trade_date, code)]
            for code in all_codes if (trade_date, code) in bar_lookup
        }
        for code, bar in bars.items():
            if bar.get("close", 0) > 0:
                executor._last_close[code] = bar["close"]
                executor._last_close_date[code] = trade_date
                executor._last_close_source[code] = "close"

        if pending:
            pre_sizes = {
                order.ts_code: int(
                    executor._positions.get(order.ts_code, {}).get("size", 0)
                )
                for order in pending
            }
            _t_exec = _time.perf_counter()
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
            if profiler:
                profiler.execute_with_capacity_inclusive += _time.perf_counter() - _t_exec
                profiler.execute_calls += 1

            for evt in rebalance_result.events:
                code = evt.get("code", "")
                order = order_mgr.get_order(code)
                event_id = order.event_id if order else ""
                order_signal_week = event_context.get(event_id, (signal_week, {}))[0]
                evt["trade_date"] = trade_date
                evt["signal_week"] = order_signal_week
                evt["signal_event_id"] = event_id
                evt["ts_code"] = evt.pop("code", "")
                evt["order_id"] = f"{event_id}-{evt['ts_code']}" if evt["ts_code"] else ""
                evt["attempt_id"] = f"{evt['order_id']}-A{len(order.attempts) if order else 0}"
                result.trade_events.append(evt)

            for code, pre_size in pre_sizes.items():
                post_size = int(executor._positions.get(code, {}).get("size", 0))
                sync_position_factor_basis(
                    position_adj_factor,
                    code=code,
                    pre_size=pre_size,
                    post_size=post_size,
                    current_factor=adj_lookup.get((trade_date, code)),
                )

        # Holdings snapshots are daily so adjustment and retry effects are auditable.
        _t_snap = _time.perf_counter()
        daily_equity = mark_to_market(executor, trade_date, close_lookup)
        holdings: list[dict] = []
        for code, pos in sorted(executor._positions.items()):
            mark_price = close_lookup.get((trade_date, code), executor._last_close.get(code, 0.0))
            market_value = float(pos.get("size", 0)) * mark_price
            anchor_date = executor._last_close_date.get(code, "")
            anchor_source = executor._last_close_source.get(code, "")
            holdings.append({
                "ts_code": code,
                "quantity": int(pos.get("size", 0)),
                "mark_price": mark_price,
                "market_value": market_value,
                "target_weight": float(active_targets.get(code, 0.0)),
                "actual_weight": market_value / daily_equity if daily_equity > 0 else 0.0,
                "adj_factor": adj_lookup.get((trade_date, code), position_adj_factor.get(code, 0.0)),
                "stale_days": (
                    date_ordinal.get(trade_date, 0) - date_ordinal.get(anchor_date, 0)
                    if anchor_date else 0
                ),
                "last_valid_close_date": anchor_date if anchor_source == "close" else "",
                "valuation_anchor_date": anchor_date,
                "valuation_anchor_source": anchor_source or "unavailable",
            })
        signal_cash = daily_equity * max(
            0.0, 1.0 - sum(max(weight, 0.0) for weight in active_targets.values()),
        )
        result.positions_history.append({
            "trade_date": trade_date,
            "positions": {k: v["size"] for k, v in executor._positions.items()},
            "holdings": holdings,
            "cash": executor.cash,
            "signal_cash": signal_cash,
            "execution_failure_cash": max(executor.cash - signal_cash, 0.0),
            "equity": daily_equity,
        })

        # Daily mark-to-market equity (every trading day)
        equity_records.append({"trade_date": trade_date, "equity": daily_equity})
        if profiler:
            profiler.position_snapshot += _time.perf_counter() - _t_snap

    # Build executed equity series (daily, normalized to 1.0)
    if equity_records:
        eq_df = pd.DataFrame(equity_records)
        result.executed_equity = pd.Series(
            eq_df["equity"].values / config.initial_capital,
            index=eq_df["trade_date"].values,
            name="executed_strategy",
        )

    # Collect all orders for orders.csv
    result.orders.extend(serialize_orders(order_mgr))


def serialize_orders(order_mgr: OrderManager) -> list[dict]:
    """Serialize immutable attempt facts beside the current adjusted parent state."""
    rows: list[dict] = []
    for order in order_mgr._history + list(order_mgr._active.values()):
        attempts = order.attempts or [{"filled": 0, "status": "NOT_ATTEMPTED"}]
        for attempt_number, attempt in enumerate(attempts, 1):
            rows.append({
                "order_id": f"{order.event_id}-{order.ts_code}",
                "event_id": order.event_id,
                "ts_code": order.ts_code,
                "direction": order.direction,
                "requested": abs(order.requested),
                "filled": order.filled,
                "attempt_number": attempt_number,
                "trade_date": attempt.get("trade_date", ""),
                "attempt_filled": int(attempt.get("filled", 0)),
                "attempt_status": attempt.get("status", ""),
                "reason": attempt.get("reason", ""),
                "cumulative_filled_at_attempt": int(attempt.get("cumulative_filled_after_attempt", 0)),
                "attempt_quantity_basis": float(attempt.get("quantity_basis", 1.0)),
                "current_quantity_basis": order.quantity_basis,
                "remaining": order.remaining,
                "final_status": order.status.value,
                "corporate_action_adjustments": json.dumps(
                    order.corporate_action_adjustments, ensure_ascii=False, sort_keys=True,
                ),
            })
    return rows


def execute_with_capacity(
    executor: PortfolioExecutor,
    order_mgr: OrderManager,
    targets: dict[str, float],
    bars: dict[str, dict],
    trade_date: str,
    config: FundRotationConfig,
    adv_index: ADVIndex,
    rules: ChinaETFExecutionRules,
    profiler: ExecutionProfiler | None = None,
    rules_by_code: dict[str, ChinaETFExecutionRules] | None = None,
) -> RebalanceResult:
    """Attempt exact residual quantities with causal ADV and common cash scaling."""
    events: list[dict] = []
    pending = order_mgr.get_pending_orders()
    all_codes = sorted({o.ts_code for o in pending} | set(executor._positions))
    pre_equity = executor._compute_equity_anchor(all_codes, bars)

    def execution_rules(code: str) -> ChinaETFExecutionRules:
        if rules_by_code is None:
            return rules
        return rules_by_code[code]

    def block(order, reason: str, adv=None) -> None:
        requested = order.remaining
        order_mgr.record_attempt(
            order.ts_code, 0, AttemptStatus.BLOCKED,
            {"trade_date": trade_date, "reason": reason},
        )
        events.append({
            "code": order.ts_code, "action": order.direction, "status": "BLOCKED",
            "requested": requested, "filled": 0, "unfilled": requested,
            "reason": reason, "price": 0.0, "commission": 0.0,
            "raw_open": float(bars.get(order.ts_code, {}).get("open", 0.0) or 0.0),
            "target_weight": float(targets.get(order.ts_code, 0.0)),
            "slippage_bps": 0.0, "adv20": adv.adv_value if adv else 0.0,
            "adv_observations": adv.observations if adv else 0,
            "participation_rate": 0.0,
            "post_holding": executor._positions.get(order.ts_code, {}).get("size", 0),
            "remaining": order.remaining,
        })

    pending_sells = [o for o in pending if o.direction == "SELL"]
    pending_buys = [o for o in pending if o.direction == "BUY"]

    # Cash-releasing reductions always precede increases.
    for order in pending_sells:
        code, requested = order.ts_code, order.remaining
        bar = bars.get(code, {})
        order_rules = execution_rules(code)
        if not order_rules.can_sell(bar):
            block(order, "market_blocked")
            continue
        entry_date = executor._positions.get(code, {}).get("entry_date", "")
        if not order_rules.can_sell_today(entry_date, trade_date):
            block(order, "t_plus_1")
            continue
        _t_adv = _time.perf_counter()
        adv = adv_index.get(code, trade_date)
        if profiler:
            profiler.adv20_exclusive += _time.perf_counter() - _t_adv
            profiler.adv20_calls += 1
        if not adv.is_valid:
            block(order, "insufficient_adv_history", adv)
            continue
        raw_price = executor._get_execution_price(code, bars)
        filled, participation, slippage = apply_capacity_and_slippage(
            requested, raw_price, adv.adv_value, config.max_participation_rate,
            order_rules.lot_size, config.base_slippage_bps, config.max_slippage_bps,
        )
        filled = min(
            order_rules.round_sell_size(filled),
            executor._positions.get(code, {}).get("size", 0),
        )
        if filled <= 0:
            block(order, "capacity_zero", adv)
            continue
        price = order_rules.apply_tick(raw_price * (1 - slippage / 10000.0), -1)
        commission = order_rules.calc_commission(filled, price)
        executor.cash += filled * price - commission
        pos = executor._positions[code]
        new_size = pos["size"] - filled
        if new_size <= 0:
            executor._positions.pop(code, None)
        else:
            executor._positions[code] = {**pos, "size": new_size}
        status = AttemptStatus.FILLED if filled >= requested else AttemptStatus.PARTIAL
        order_mgr.record_attempt(
            code, filled, status,
            {"trade_date": trade_date, "reason": ""},
        )
        events.append({
            "code": code, "action": "SELL", "status": status.value,
            "requested": requested, "filled": filled, "unfilled": requested - filled,
            "reason": "", "price": price, "commission": commission,
            "raw_open": raw_price, "target_weight": float(targets.get(code, 0.0)),
            "slippage_bps": slippage, "adv20": adv.adv_value,
            "adv_observations": adv.observations,
            "participation_rate": participation,
            "post_holding": max(new_size, 0), "remaining": order.remaining,
        })

    candidates: list[dict] = []
    for order in pending_buys:
        code, requested = order.ts_code, order.remaining
        bar = bars.get(code, {})
        order_rules = execution_rules(code)
        if not order_rules.can_buy(bar):
            block(order, "market_blocked")
            continue
        _t_adv = _time.perf_counter()
        adv = adv_index.get(code, trade_date)
        if profiler:
            profiler.adv20_exclusive += _time.perf_counter() - _t_adv
            profiler.adv20_calls += 1
        if not adv.is_valid:
            block(order, "insufficient_adv_history", adv)
            continue
        raw_price = executor._get_execution_price(code, bars)
        capacity_size, _, _ = apply_capacity_and_slippage(
            requested, raw_price, adv.adv_value, config.max_participation_rate,
            order_rules.lot_size, config.base_slippage_bps, config.max_slippage_bps,
        )
        if capacity_size <= 0:
            block(order, "capacity_zero", adv)
            continue
        candidates.append({
            "order": order, "requested": requested, "capacity_size": capacity_size,
            "raw_price": raw_price, "adv": adv, "rules": order_rules,
        })

    def buy_terms(item: dict, scale: float) -> tuple[int, float, float, float, float]:
        item_rules = item["rules"]
        size = item_rules.round_buy_size(item["capacity_size"] * scale)
        if size <= 0:
            return 0, 0.0, 0.0, 0.0, 0.0
        participation = size * item["raw_price"] / item["adv"].adv_value
        slippage = min(
            config.max_slippage_bps,
            config.base_slippage_bps + 200.0 * participation,
        )
        price = item_rules.apply_tick(item["raw_price"] * (1 + slippage / 10000.0), 1)
        commission = item_rules.calc_commission(size, price)
        return size, participation, slippage, price, commission

    def total_cost(scale: float) -> float:
        result = 0.0
        for item in candidates:
            size, _, _, price, commission = buy_terms(item, scale)
            if size > 0:
                result += size * price + commission
        return result

    scale = 1.0
    if candidates and total_cost(scale) > executor.cash:
        _t_scale = _time.perf_counter()
        lo, hi = 0.0, 1.0
        for _ in range(60):
            mid = (lo + hi) / 2.0
            if total_cost(mid) <= executor.cash:
                lo = mid
            else:
                hi = mid
        scale = lo
        if profiler:
            profiler.cash_scaling_exclusive += _time.perf_counter() - _t_scale

    for item in candidates:
        order = item["order"]
        code, requested = order.ts_code, item["requested"]
        filled, participation, slippage, price, commission = buy_terms(item, scale)
        if filled <= 0:
            block(order, "insufficient_cash_after_commission_and_lot", item["adv"])
            continue
        cost = filled * price + commission
        if cost > executor.cash + 1e-7:
            raise RuntimeError("common scaling invariant violated: negative cash")
        executor.cash -= cost
        pos = executor._positions.get(code, {"size": 0, "entry_date": trade_date})
        executor._positions[code] = {
            **pos, "size": pos.get("size", 0) + filled,
            "entry_date": pos.get("entry_date") or trade_date,
        }
        if pos.get("size", 0) <= 0 and float(bars.get(code, {}).get("close", 0.0) or 0.0) <= 0:
            executor._last_close[code] = price
            executor._last_close_date[code] = trade_date
            executor._last_close_source[code] = "execution_price"
        status = AttemptStatus.FILLED if filled >= requested else AttemptStatus.PARTIAL
        order_mgr.record_attempt(
            code, filled, status,
            {"trade_date": trade_date, "reason": ""},
        )
        events.append({
            "code": code, "action": "BUY", "status": status.value,
            "requested": requested, "filled": filled, "unfilled": requested - filled,
            "reason": "", "price": price, "commission": commission,
            "raw_open": item["raw_price"], "target_weight": float(targets.get(code, 0.0)),
            "slippage_bps": slippage, "adv20": item["adv"].adv_value,
            "adv_observations": item["adv"].observations,
            "participation_rate": participation,
            "post_holding": executor._positions[code]["size"],
            "remaining": order.remaining,
        })

    return RebalanceResult(
        pre_equity=pre_equity,
        cash=executor.cash,
        final_positions={k: dict(v) for k, v in executor._positions.items()},
        events=events,
        scale_factor=scale if candidates else 1.0,
    )


def mark_to_market(
    executor: PortfolioExecutor,
    trade_date: str,
    close_lookup: dict[tuple[str, str], float],
) -> float:
    """Compute total equity = cash + sum(shares * close_price) at end of day."""
    equity = executor.cash
    for code, pos in executor._positions.items():
        size = pos.get("size", 0)
        if size <= 0:
            continue
        price = close_lookup.get((trade_date, code), 0.0)
        if price <= 0:
            # Stale valuation: use last known close
            price = executor._last_close.get(code, 0.0)
        equity += size * price
    return equity
