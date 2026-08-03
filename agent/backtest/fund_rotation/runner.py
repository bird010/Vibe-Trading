"""Strategy-neutral fund-rotation backtest Runner.

The Runner consumes an immutable resolved strategy requirement, starts strategy
evaluation at the planned decision boundary, executes all targets through one
common account model, enforces the exact evaluation calendar and produces
strategy, benchmark and execution evidence.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from backtest.fund_rotation.causal_data import (
    CausalDataView,
    UndeclaredStrategyDataAccess,
)
from backtest.fund_rotation.config import FundRotationConfig
from backtest.fund_rotation.contracts import (
    DecisionKind,
    StrategyContractViolation,
    StrategyDataRequirements,
    StrategyDecisionContext,
    StrategyDiagnostics,
    StrategyInitializationContext,
    TargetWeightDecision,
    validate_target_decision,
)
from backtest.fund_rotation.evaluation import EvaluationContext, iso_week_endings
from backtest.fund_rotation.execution import (
    PipelineResult,
    build_execution_context,
    run_execution_loop,
)
from backtest.fund_rotation.metrics import compute_performance_metrics
from backtest.fund_rotation.returns import compute_adjusted_close

if TYPE_CHECKING:
    from backtest.fund_rotation.contracts import FundRotationStrategy
    from src.stockpred.fund_rotation.data_snapshot import PinnedFundDataSnapshot


class CancellationToken:
    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled


class ExecutionConfig(BaseModel):
    """Common execution parameters shared by all strategy variants."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    initial_capital: float = Field(default=1_000_000.0, gt=0)
    commission_rate: float = Field(default=0.00025, ge=0)
    commission_min: float = Field(default=5.0, ge=0)
    other_fee_rate: float = Field(default=0.0, ge=0)
    max_participation_rate: float = Field(default=0.05, gt=0, le=1)
    adv_lookback: int = Field(default=20, ge=1)
    adv_min_observations: int = Field(default=10, ge=1)
    base_slippage_bps: float = Field(default=5.0, ge=0)
    max_slippage_bps: float = Field(default=30.0, ge=0)
    lot_size: int = Field(default=100, ge=1)

    def model_post_init(self, __context: object) -> None:
        if self.adv_min_observations > self.adv_lookback:
            raise ValueError(
                "adv_min_observations must be <= adv_lookback"
            )
        if self.max_slippage_bps < self.base_slippage_bps:
            raise ValueError(
                "max_slippage_bps must be >= base_slippage_bps"
            )


class SubRunStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


@dataclass(frozen=True)
class FundRotationRunResult:
    status: SubRunStatus
    error_code: str = ""
    error_message: str = ""
    decisions: tuple[TargetWeightDecision, ...] = ()
    weekly_targets: dict[str, dict[str, float]] = field(default_factory=dict)
    executed_equity: pd.Series = field(
        default_factory=lambda: pd.Series(dtype=float)
    )
    trade_events: list[dict] = field(default_factory=list)
    orders: list[dict] = field(default_factory=list)
    positions_history: list[dict] = field(default_factory=list)
    strategy_metrics: dict[str, float] = field(default_factory=dict)
    benchmark_equity: dict[str, pd.Series] = field(default_factory=dict)
    benchmark_metrics: dict[str, dict[str, float]] = field(default_factory=dict)
    execution_diagnostics: dict[str, float] = field(default_factory=dict)
    diagnostics: StrategyDiagnostics | None = None
    quality_status: str = "VALID"


class FundRotationBacktestRunner:
    def __init__(
        self,
        fund_daily: pd.DataFrame,
        fund_adj: pd.DataFrame,
        dim_fund: pd.DataFrame,
        *,
        run_id: str | None = None,
    ) -> None:
        self._fund_daily = fund_daily
        self._fund_adj = fund_adj
        self._dim_fund = dim_fund
        self._run_id = run_id or uuid.uuid4().hex[:12]

    def run(
        self,
        strategy: "FundRotationStrategy",
        config: BaseModel,
        snapshot: "PinnedFundDataSnapshot",
        evaluation: EvaluationContext,
        execution: ExecutionConfig,
        cancellation: CancellationToken,
        *,
        decision_start_date: str | None = None,
        resolved_requirements: StrategyDataRequirements | None = None,
        run_id: str | None = None,
        simulation_start_date: str | None = None,
    ) -> FundRotationRunResult:
        if cancellation.is_cancelled:
            return FundRotationRunResult(
                status=SubRunStatus.CANCELED,
                error_code="CANCELED",
            )
        if (
            decision_start_date is not None
            and simulation_start_date is not None
            and str(decision_start_date) != str(simulation_start_date)
        ):
            return FundRotationRunResult(
                status=SubRunStatus.FAILED,
                error_code=StrategyContractViolation.code,
                error_message=(
                    "decision_start_date and deprecated simulation_start_date "
                    "must match when both are supplied"
                ),
            )

        requirements = (
            resolved_requirements or strategy.resolve_requirements(config)
        )
        all_trade_dates = sorted(
            {
                str(date)
                for date in self._fund_daily["trade_date"].astype(str).unique()
            }
        )
        warmup = int(requirements.warmup_trade_days)
        planned_start = decision_start_date or simulation_start_date
        if planned_start is not None:
            decision_start = str(planned_start)
            if decision_start not in all_trade_dates:
                return FundRotationRunResult(
                    status=SubRunStatus.FAILED,
                    error_code=StrategyContractViolation.code,
                    error_message=(
                        f"planned decision_start_date {decision_start!r} "
                        "is not an available trading day"
                    ),
                )
        elif str(requirements.frequency).upper().startswith("W"):
            week_endings = iso_week_endings(all_trade_dates)
            needed_endings = warmup // 5 + 1
            if len(week_endings) < needed_endings:
                return FundRotationRunResult(
                    status=SubRunStatus.FAILED,
                    error_code="INSUFFICIENT_HISTORY",
                    error_message=(
                        f"need at least {needed_endings} week-endings, "
                        f"have {len(week_endings)}"
                    ),
                )
            decision_start = week_endings[needed_endings - 1]
        else:
            if len(all_trade_dates) <= warmup:
                return FundRotationRunResult(
                    status=SubRunStatus.FAILED,
                    error_code="INSUFFICIENT_HISTORY",
                    error_message=(
                        f"need more than {warmup} trading days, "
                        f"have {len(all_trade_dates)}"
                    ),
                )
            decision_start = all_trade_dates[warmup]

        evaluation_dates = [
            date.strftime("%Y%m%d") for date in evaluation.trading_dates
        ]
        if not evaluation_dates:
            return FundRotationRunResult(
                status=SubRunStatus.FAILED,
                error_code="EMPTY_EVALUATION_CALENDAR",
            )
        evaluation_end = evaluation_dates[-1]

        session = strategy.create_session(
            StrategyInitializationContext(
                run_id=run_id or self._run_id,
                evaluation_calendar=tuple(evaluation_dates),
            ),
            config,
        )
        try:
            scheduled = tuple(
                session.scheduled_dates(
                    tuple(all_trade_dates),
                    decision_start,
                    evaluation_end,
                )
            )
            self._check_schedule(
                scheduled,
                set(all_trade_dates),
                decision_start,
                evaluation_end,
            )
        except StrategyContractViolation as exc:
            return FundRotationRunResult(
                status=SubRunStatus.FAILED,
                error_code=StrategyContractViolation.code,
                error_message=str(exc),
            )

        universe = frozenset(str(code) for code in snapshot.universe_codes)
        decisions: list[TargetWeightDecision] = []
        targets_map: dict[str, dict[str, float]] = {}
        current_targets: dict[str, float] = {}
        seen_decision_ids: set[str] = set()

        def fail(
            error_code: str,
            error_message: str = "",
            *,
            executed_equity: pd.Series | None = None,
            trade_events: list[dict] | None = None,
            orders: list[dict] | None = None,
            positions_history: list[dict] | None = None,
            strategy_metrics: dict[str, float] | None = None,
            benchmark_equity: dict[str, pd.Series] | None = None,
            benchmark_metrics: dict[str, dict[str, float]] | None = None,
            execution_diagnostics: dict[str, float] | None = None,
            quality_status: str = "VALID",
        ) -> FundRotationRunResult:
            return FundRotationRunResult(
                status=SubRunStatus.FAILED,
                error_code=error_code,
                error_message=error_message,
                decisions=tuple(decisions),
                weekly_targets={
                    date: dict(weights)
                    for date, weights in targets_map.items()
                },
                executed_equity=(
                    executed_equity
                    if executed_equity is not None
                    else pd.Series(dtype=float)
                ),
                trade_events=trade_events or [],
                orders=orders or [],
                positions_history=positions_history or [],
                strategy_metrics=strategy_metrics or {},
                benchmark_equity=benchmark_equity or {},
                benchmark_metrics=benchmark_metrics or {},
                execution_diagnostics=execution_diagnostics or {},
                quality_status=quality_status,
            )

        for signal_date in scheduled:
            if cancellation.is_cancelled:
                return FundRotationRunResult(
                    status=SubRunStatus.CANCELED,
                    error_code="CANCELED",
                    decisions=tuple(decisions),
                    weekly_targets={
                        date: dict(weights)
                        for date, weights in targets_map.items()
                    },
                )
            view = CausalDataView(
                self._fund_daily,
                self._fund_adj,
                self._dim_fund,
                requirements,
                pd.Timestamp(signal_date),
                universe,
            )
            context = StrategyDecisionContext(
                signal_date=signal_date,
                data_view=view,
                previous_target_weights=dict(current_targets),
            )
            try:
                decision = session.evaluate(context)
            except UndeclaredStrategyDataAccess as exc:
                return fail(exc.code, str(exc))
            except Exception as exc:
                return fail("STRATEGY_EVALUATION_ERROR", str(exc))

            try:
                if not isinstance(decision, TargetWeightDecision):
                    raise StrategyContractViolation(
                        "strategy must return a TargetWeightDecision instance"
                    )
                if str(decision.signal_date) != str(signal_date):
                    raise StrategyContractViolation(
                        f"decision signal_date {decision.signal_date!r} does not "
                        f"match scheduled date {signal_date!r}"
                    )
                eligible_codes = universe
                if (
                    decision.action is DecisionKind.SET_TARGETS
                    and decision.target_weights
                ):
                    eligible_codes = frozenset(
                        instrument.ts_code
                        for instrument in view.eligible_universe()
                    )
                validate_target_decision(
                    decision,
                    eligible_codes,
                    seen_decision_ids,
                )
            except UndeclaredStrategyDataAccess as exc:
                return fail(exc.code, str(exc))
            except StrategyContractViolation as exc:
                return fail(StrategyContractViolation.code, str(exc))

            seen_decision_ids.add(decision.decision_id)
            decisions.append(decision)
            if decision.action is DecisionKind.INVALID:
                return fail(decision.reason_code or "INVALID")
            if decision.action is DecisionKind.SET_TARGETS:
                current_targets = dict(decision.target_weights)
                targets_map[signal_date] = dict(current_targets)

        if cancellation.is_cancelled:
            return FundRotationRunResult(
                status=SubRunStatus.CANCELED,
                error_code="CANCELED",
                decisions=tuple(decisions),
                weekly_targets={
                    date: dict(weights)
                    for date, weights in targets_map.items()
                },
            )

        legacy_execution = FundRotationConfig(
            initial_capital=execution.initial_capital,
            commission_rate=execution.commission_rate,
            commission_min=execution.commission_min,
            other_fee_rate=execution.other_fee_rate,
            max_participation_rate=execution.max_participation_rate,
            adv_lookback=execution.adv_lookback,
            adv_min_observations=execution.adv_min_observations,
            base_slippage_bps=execution.base_slippage_bps,
            max_slippage_bps=execution.max_slippage_bps,
            lot_size=execution.lot_size,
            start_date=evaluation_dates[0],
            end_date=evaluation_dates[-1],
        )
        pipeline_result = PipelineResult(weekly_targets=targets_map)
        execution_context = build_execution_context(
            self._fund_daily,
            self._fund_adj,
            legacy_execution,
        )
        run_execution_loop(
            pipeline_result,
            legacy_execution,
            execution_context,
            evaluation_dates=evaluation_dates,
            should_cancel=lambda: cancellation.is_cancelled,
        )
        if cancellation.is_cancelled:
            return FundRotationRunResult(
                status=SubRunStatus.CANCELED,
                error_code="CANCELED",
                decisions=tuple(decisions),
                weekly_targets={
                    date: dict(weights)
                    for date, weights in targets_map.items()
                },
                executed_equity=pipeline_result.executed_equity,
                trade_events=pipeline_result.trade_events,
                orders=pipeline_result.orders,
                positions_history=pipeline_result.positions_history,
                execution_diagnostics=_execution_diagnostics(
                    pipeline_result,
                    execution,
                ),
            )

        actual_dates = pd.Index(
            [str(value) for value in pipeline_result.executed_equity.index]
        )
        expected_dates = pd.Index(evaluation_dates)
        if not actual_dates.equals(expected_dates):
            return fail(
                "EVALUATION_CALENDAR_MISMATCH",
                "executed equity index must exactly equal the evaluation calendar",
                executed_equity=pipeline_result.executed_equity,
                trade_events=pipeline_result.trade_events,
                orders=pipeline_result.orders,
                positions_history=pipeline_result.positions_history,
                execution_diagnostics=_execution_diagnostics(
                    pipeline_result,
                    execution,
                ),
            )

        benchmark_equity = self._public_benchmarks(
            evaluation_dates,
            execution,
        )
        benchmark_metrics = {
            name: compute_performance_metrics(
                series,
                periods_per_year=244,
                initial_nav=evaluation.initial_nav,
            )
            for name, series in benchmark_equity.items()
            if not series.empty and not series.isna().all()
        }
        strategy_metrics = compute_performance_metrics(
            pipeline_result.executed_equity,
            periods_per_year=244,
            initial_nav=evaluation.initial_nav,
        )
        strategy_metrics.update(
            _relative_metrics(
                pipeline_result.executed_equity,
                benchmark_equity.get("equal_weight_etf"),
            )
        )
        execution_diagnostics = _execution_diagnostics(
            pipeline_result,
            execution,
        )

        overall_quality = _worst_quality_status(decisions)
        try:
            diagnostics = session.finalize()
        except Exception as exc:
            return fail(
                "FINALIZE_FAILED",
                str(exc),
                executed_equity=pipeline_result.executed_equity,
                trade_events=pipeline_result.trade_events,
                orders=pipeline_result.orders,
                positions_history=pipeline_result.positions_history,
                strategy_metrics=strategy_metrics,
                benchmark_equity=benchmark_equity,
                benchmark_metrics=benchmark_metrics,
                execution_diagnostics=execution_diagnostics,
                quality_status=overall_quality,
            )

        return FundRotationRunResult(
            status=SubRunStatus.SUCCEEDED,
            decisions=tuple(decisions),
            weekly_targets={
                date: dict(weights)
                for date, weights in targets_map.items()
            },
            executed_equity=pipeline_result.executed_equity,
            trade_events=pipeline_result.trade_events,
            orders=pipeline_result.orders,
            positions_history=pipeline_result.positions_history,
            strategy_metrics=strategy_metrics,
            benchmark_equity=benchmark_equity,
            benchmark_metrics=benchmark_metrics,
            execution_diagnostics=execution_diagnostics,
            diagnostics=diagnostics,
            quality_status=overall_quality,
        )

    def _public_benchmarks(
        self,
        evaluation_dates: list[str],
        execution: ExecutionConfig,
    ) -> dict[str, pd.Series]:
        """Build common daily benchmarks from the same pinned market frames."""
        index = pd.Index(evaluation_dates)
        cash = pd.Series(1.0, index=index, name="cash")
        adjusted = compute_adjusted_close(
            self._fund_daily,
            self._fund_adj,
            evaluation_dates[-1],
        )
        if adjusted.empty:
            return {
                "cash": cash,
                "equal_weight_etf": cash.rename("equal_weight_etf"),
                "510300.SH": pd.Series(float("nan"), index=index, name="510300.SH"),
            }
        adjusted = adjusted.copy()
        adjusted.index = pd.Index([str(value) for value in adjusted.index])
        adjusted = adjusted.sort_index()
        returns = adjusted.pct_change(fill_method=None)

        list_dates: dict[str, str] = {}
        if not self._dim_fund.empty:
            for _, row in self._dim_fund.iterrows():
                code = str(row.get("ts_code", ""))
                listed = str(row.get("list_date", ""))
                if code:
                    list_dates[code] = listed

        equal_values = [1.0]
        nav = 1.0
        for position in range(1, len(evaluation_dates)):
            date = evaluation_dates[position]
            prior_date = evaluation_dates[position - 1]
            if date not in returns.index:
                equal_values.append(nav)
                continue
            eligible = [
                code
                for code in returns.columns
                if list_dates.get(str(code), "99999999") <= prior_date
            ]
            period = (
                returns.loc[date, eligible]
                if eligible
                else pd.Series(dtype=float)
            )
            if isinstance(period, pd.DataFrame):
                period = period.iloc[0]
            values = pd.to_numeric(period, errors="coerce").dropna()
            period_return = float(values.mean()) if not values.empty else 0.0
            nav *= 1.0 + period_return
            equal_values.append(nav)
        equal_weight = pd.Series(
            equal_values,
            index=index,
            name="equal_weight_etf",
        )

        benchmark_code = "510300.SH"
        buy_hold = pd.Series(
            float("nan"),
            index=index,
            name=benchmark_code,
        )
        if benchmark_code in adjusted.columns:
            prices = pd.to_numeric(
                adjusted[benchmark_code].reindex(index),
                errors="coerce",
            ).ffill()
            first_price = prices.iloc[0] if not prices.empty else float("nan")
            if pd.notna(first_price) and float(first_price) > 0:
                buy_hold = (
                    prices / float(first_price) * (1.0 - execution.commission_rate)
                )
                buy_hold.name = benchmark_code
        return {
            "cash": cash,
            "equal_weight_etf": equal_weight,
            benchmark_code: buy_hold,
        }

    @staticmethod
    def _check_schedule(
        scheduled: tuple[str, ...],
        trading_days: set[str],
        decision_start: str,
        evaluation_end: str,
    ) -> None:
        previous = ""
        for date in scheduled:
            date = str(date)
            if date not in trading_days:
                raise StrategyContractViolation(
                    f"scheduled decision date {date!r} is not an actual trading day"
                )
            if date < decision_start:
                raise StrategyContractViolation(
                    f"scheduled decision date {date!r} is inside the pure "
                    f"warmup period before {decision_start!r}"
                )
            if date > evaluation_end:
                raise StrategyContractViolation(
                    f"scheduled decision date {date!r} is after evaluation end "
                    f"{evaluation_end!r}"
                )
            if date <= previous:
                raise StrategyContractViolation(
                    f"scheduled decision dates must be strictly increasing at {date!r}"
                )
            previous = date


_QUALITY_ORDER = {"VALID": 0, "DEGRADED": 1, "INVALID": 2, "FAILED": 3}


def _worst_quality_status(decisions: list) -> str:
    if not decisions:
        return "VALID"
    worst = max(
        decisions,
        key=lambda decision: _QUALITY_ORDER.get(
            str(decision.quality_status.value)
            if hasattr(decision.quality_status, "value")
            else str(decision.quality_status),
            0,
        ),
    )
    value = worst.quality_status
    return str(value.value) if hasattr(value, "value") else str(value)


def _execution_diagnostics(
    result: PipelineResult,
    execution: ExecutionConfig,
) -> dict[str, float]:
    trades = [
        event
        for event in result.trade_events
        if str(event.get("event_type", "")) != "CORPORATE_ACTION"
    ]
    requested = sum(
        max(float(event.get("requested", 0) or 0), 0.0)
        for event in trades
    )
    filled = sum(
        max(float(event.get("filled", 0) or 0), 0.0)
        for event in trades
    )
    notionals = [
        abs(float(event.get("filled", 0) or 0))
        * max(float(event.get("price", 0) or 0), 0.0)
        for event in trades
    ]
    total_notional = float(sum(notionals))
    commission = float(
        sum(float(event.get("commission", 0) or 0) for event in trades)
    )
    slippage_cost = float(
        sum(
            notional
            * max(float(event.get("slippage_bps", 0) or 0), 0.0)
            / 10_000.0
            for event, notional in zip(trades, notionals)
        )
    )
    participation = [
        float(event.get("participation_rate", 0) or 0)
        for event in trades
        if float(event.get("filled", 0) or 0) > 0
    ]
    blocked = sum(
        1
        for event in trades
        if float(event.get("requested", 0) or 0) > 0
        and float(event.get("filled", 0) or 0) <= 0
    )
    return {
        "turnover": total_notional / execution.initial_capital,
        "total_notional": total_notional,
        "total_commission": commission,
        "total_slippage_cost": slippage_cost,
        "fill_rate": filled / requested if requested > 0 else 1.0,
        "blocked_order_count": float(blocked),
        "trade_count": float(sum(1 for value in notionals if value > 0)),
        "average_participation_rate": (
            float(sum(participation) / len(participation))
            if participation
            else 0.0
        ),
    }


def _relative_metrics(
    strategy: pd.Series,
    benchmark: pd.Series | None,
) -> dict[str, float]:
    if benchmark is None or benchmark.empty or benchmark.isna().all():
        return {}
    aligned = pd.concat(
        [strategy.rename("strategy"), benchmark.rename("benchmark")],
        axis=1,
        join="inner",
    ).dropna()
    if aligned.empty:
        return {}
    strategy_returns = aligned["strategy"].pct_change(fill_method=None)
    benchmark_returns = aligned["benchmark"].pct_change(fill_method=None)
    active = (strategy_returns - benchmark_returns).dropna()
    tracking_error = float(active.std(ddof=1) * math.sqrt(244)) if len(active) > 1 else 0.0
    annualized_excess = float(active.mean() * 244) if not active.empty else 0.0
    information_ratio = (
        annualized_excess / tracking_error
        if tracking_error > 0
        else 0.0
    )
    strategy_total = float(aligned["strategy"].iloc[-1] / aligned["strategy"].iloc[0] - 1.0)
    benchmark_total = float(aligned["benchmark"].iloc[-1] / aligned["benchmark"].iloc[0] - 1.0)
    relative_nav = aligned["strategy"] / aligned["benchmark"]
    relative_peak = relative_nav.cummax()
    relative_drawdown = relative_nav / relative_peak - 1.0
    return {
        "excess_total_return": strategy_total - benchmark_total,
        "annualized_excess_return": annualized_excess,
        "tracking_error": tracking_error,
        "information_ratio": float(information_ratio),
        "relative_max_drawdown": float(relative_drawdown.min()),
    }
