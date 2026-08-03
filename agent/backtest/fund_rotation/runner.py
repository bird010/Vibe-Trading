"""Strategy-neutral fund-rotation backtest Runner — Phase 2 Task 3.

Design §5/§6/§7/§24/§26/§32.3. The Runner drives any ``FundRotationStrategy``
through the common life cycle without knowing its algorithm:

1. resolve data requirements and build a per-signal ``CausalDataView``;
2. create an isolated session and drive ``evaluate`` on its scheduled dates;
3. validate every ``TargetWeightDecision`` against the public contract;
4. schedule SET_TARGETS decisions via the shared ``schedule_targets`` rule and
   drive the common execution module (sell-before-buy, ADV capacity, valuation);
5. finalize the session and report metrics.

The Runner recognizes only the three public decision actions — never a
per-strategy branch. Cancellation is checked at decision-day and execution
checkpoints (§26.1).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest.fund_rotation.causal_data import CausalDataView, UndeclaredStrategyDataAccess
from backtest.fund_rotation.config import FundRotationConfig
from backtest.fund_rotation.contracts import (
    DecisionKind,
    StrategyContractViolation,
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

if TYPE_CHECKING:  # type-only: the Runner stays independent of the src layer
    from backtest.fund_rotation.contracts import FundRotationStrategy
    from src.stockpred.fund_rotation.data_snapshot import PinnedFundDataSnapshot


class CancellationToken:
    """Cooperative cancellation token (§26.1).

    The Runner checks it at decision-day and execution checkpoints; setting it
    never raises, it only transitions the sub-run to CANCELED.
    """

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled


class ExecutionConfig(BaseModel):
    """Common execution parameters shared by all strategies (§25).

    Deliberately contains NO strategy parameters (k, lookbacks, ...): execution
    rules are identical for every variant in a batch.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    initial_capital: float = 1_000_000.0
    commission_rate: float = 0.00025
    commission_min: float = 5.0
    other_fee_rate: float = 0.0
    max_participation_rate: float = 0.05
    adv_lookback: int = 20
    adv_min_observations: int = 10
    base_slippage_bps: float = 5.0
    max_slippage_bps: float = 30.0


class SubRunStatus(str, Enum):
    """§26 — terminal states of one strategy sub-run."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


@dataclass(frozen=True)
class FundRotationRunResult:
    """Outcome of one strategy sub-run.

    Failed/canceled runs still carry every decision and event collected before
    the failure — partial evidence is preserved but never faked as success.
    """

    status: SubRunStatus
    error_code: str = ""
    error_message: str = ""
    decisions: tuple[TargetWeightDecision, ...] = ()
    weekly_targets: dict[str, dict[str, float]] = field(default_factory=dict)
    executed_equity: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    trade_events: list[dict] = field(default_factory=list)
    orders: list[dict] = field(default_factory=list)
    positions_history: list[dict] = field(default_factory=list)
    strategy_metrics: dict[str, float] = field(default_factory=dict)
    diagnostics: StrategyDiagnostics | None = None
    quality_status: str = "VALID"  # worst across all decisions (§9/§27)


class FundRotationBacktestRunner:
    """Strategy-neutral driver for one fund-rotation sub-run (§32.3)."""

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

    # ── public entry point ──

    def run(
        self,
        strategy: "FundRotationStrategy",
        config: BaseModel,
        snapshot: "PinnedFundDataSnapshot",
        evaluation: EvaluationContext,
        execution: ExecutionConfig,
        cancellation: CancellationToken,
        *,
        simulation_start_date: str | None = None,
        run_id: str | None = None,
    ) -> FundRotationRunResult:
        if cancellation.is_cancelled:
            return FundRotationRunResult(status=SubRunStatus.CANCELED, error_code="CANCELED")

        requirements = strategy.resolve_requirements(config)
        all_trade_dates = sorted(
            {str(d) for d in self._fund_daily["trade_date"].astype(str).unique()}
        )
        warmup = int(requirements.warmup_trade_days)
        if simulation_start_date is not None:
            simulation_start = str(simulation_start_date)
            if simulation_start not in all_trade_dates:
                return FundRotationRunResult(
                    status=SubRunStatus.FAILED,
                    error_code=StrategyContractViolation.code,
                    error_message=(
                        f"planned simulation_start_date {simulation_start!r} "
                        "is not an available trading day"
                    ),
                )
        elif str(requirements.frequency).upper().startswith("W"):
            # §6 weekly cadence: align the warmup boundary with ISO
            # week-endings (N weekly returns need N+1 week-endings) so
            # holiday-shortened weeks cannot shift the first decision date.
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
            simulation_start = week_endings[needed_endings - 1]
        else:
            if len(all_trade_dates) <= warmup:
                return FundRotationRunResult(
                    status=SubRunStatus.FAILED,
                    error_code="INSUFFICIENT_HISTORY",
                    error_message=(
                        f"need more than {warmup} trading days, have {len(all_trade_dates)}"
                    ),
                )
            # §6: first date at which the declared warmup is fully satisfied.
            simulation_start = all_trade_dates[warmup]
        evaluation_dates = [d.strftime("%Y%m%d") for d in evaluation.trading_dates]
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
                session.scheduled_dates(tuple(all_trade_dates), simulation_start, evaluation_end)
            )
            self._check_schedule(scheduled, set(all_trade_dates), simulation_start, evaluation_end)
        except StrategyContractViolation as exc:
            return FundRotationRunResult(
                status=SubRunStatus.FAILED,
                error_code=StrategyContractViolation.code,
                error_message=str(exc),
            )

        universe = frozenset(str(c) for c in snapshot.universe_codes)
        decisions: list[TargetWeightDecision] = []
        targets_map: dict[str, dict[str, float]] = {}
        current_targets: dict[str, float] = {}
        seen_decision_ids: set[str] = set()

        def _fail(error_code: str, error_message: str = "") -> FundRotationRunResult:
            return FundRotationRunResult(
                status=SubRunStatus.FAILED,
                error_code=error_code,
                error_message=error_message,
                decisions=tuple(decisions),
                weekly_targets={d: dict(w) for d, w in targets_map.items()},
            )

        # §6/§7 — decision loop (pure warmup dates are never evaluated because
        # scheduled_dates starts at the strategy's own warmup boundary).
        for signal_date in scheduled:
            if cancellation.is_cancelled:
                return FundRotationRunResult(
                    status=SubRunStatus.CANCELED,
                    error_code="CANCELED",
                    decisions=tuple(decisions),
                    weekly_targets={d: dict(w) for d, w in targets_map.items()},
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
                return _fail(exc.code, str(exc))
            except Exception as exc:
                return _fail("STRATEGY_EVALUATION_ERROR", str(exc))

            try:
                if not isinstance(decision, TargetWeightDecision):
                    raise StrategyContractViolation(
                        "strategy must return a TargetWeightDecision instance"
                    )
                if str(decision.signal_date) != str(signal_date):
                    raise StrategyContractViolation(
                        f"decision signal_date {decision.signal_date!r} does not match "
                        f"the scheduled date {signal_date!r}"
                    )
                eligible_codes = universe
                if decision.action is DecisionKind.SET_TARGETS and decision.target_weights:
                    eligible_codes = frozenset(
                        instrument.ts_code for instrument in view.eligible_universe()
                    )
                validate_target_decision(decision, eligible_codes, seen_decision_ids)
            except UndeclaredStrategyDataAccess as exc:
                return _fail(exc.code, str(exc))
            except StrategyContractViolation as exc:
                return _fail(StrategyContractViolation.code, str(exc))

            seen_decision_ids.add(decision.decision_id)
            decisions.append(decision)

            if decision.action is DecisionKind.INVALID:
                # §7.3 — any INVALID after warmup terminates the sub-run.
                return _fail(decision.reason_code or "INVALID")
            if decision.action is DecisionKind.SET_TARGETS:
                # §7.1 — full replacement (empty weights = 100% cash).
                current_targets = dict(decision.target_weights)
                targets_map[signal_date] = dict(current_targets)
            # §7.2 — HOLD_TARGETS: no new target event, no order interaction.

        if cancellation.is_cancelled:
            return FundRotationRunResult(
                status=SubRunStatus.CANCELED,
                error_code="CANCELED",
                decisions=tuple(decisions),
                weekly_targets={d: dict(w) for d, w in targets_map.items()},
            )

        # §24/§12 — common execution over the formal evaluation calendar.
        exec_config = FundRotationConfig(
            initial_capital=execution.initial_capital,
            commission_rate=execution.commission_rate,
            commission_min=execution.commission_min,
            other_fee_rate=execution.other_fee_rate,
            max_participation_rate=execution.max_participation_rate,
            adv_lookback=execution.adv_lookback,
            adv_min_observations=execution.adv_min_observations,
            base_slippage_bps=execution.base_slippage_bps,
            max_slippage_bps=execution.max_slippage_bps,
            start_date=evaluation_dates[0],
            end_date=evaluation_dates[-1],
        )
        pipeline_result = PipelineResult(weekly_targets=targets_map)
        exec_ctx = build_execution_context(self._fund_daily, self._fund_adj, exec_config)
        run_execution_loop(
            pipeline_result, exec_config, exec_ctx,
            evaluation_dates=evaluation_dates,
            should_cancel=lambda: cancellation.is_cancelled,
        )
        if cancellation.is_cancelled:
            # §26.1 — canceled during execution: keep collected evidence, never
            # publish it as a successful run.
            return FundRotationRunResult(
                status=SubRunStatus.CANCELED,
                error_code="CANCELED",
                decisions=tuple(decisions),
                weekly_targets={d: dict(w) for d, w in targets_map.items()},
                executed_equity=pipeline_result.executed_equity,
                trade_events=pipeline_result.trade_events,
                orders=pipeline_result.orders,
                positions_history=pipeline_result.positions_history,
            )
        strategy_metrics = compute_performance_metrics(
            pipeline_result.executed_equity,
            periods_per_year=244,
            initial_nav=evaluation.initial_nav,
        )

        # §9/§27 — aggregate worst research quality across all decisions.
        overall_quality = _worst_quality_status(decisions)

        # Finalize failure fails the sub-run but preserves all prior evidence.
        try:
            diagnostics = session.finalize()
        except Exception as exc:
            return FundRotationRunResult(
                status=SubRunStatus.FAILED,
                error_code="FINALIZE_FAILED",
                error_message=str(exc),
                decisions=tuple(decisions),
                weekly_targets={d: dict(w) for d, w in targets_map.items()},
                executed_equity=pipeline_result.executed_equity,
                trade_events=pipeline_result.trade_events,
                orders=pipeline_result.orders,
                positions_history=pipeline_result.positions_history,
                strategy_metrics=strategy_metrics,
                quality_status=overall_quality,
            )

        return FundRotationRunResult(
            status=SubRunStatus.SUCCEEDED,
            decisions=tuple(decisions),
            weekly_targets={d: dict(w) for d, w in targets_map.items()},
            executed_equity=pipeline_result.executed_equity,
            trade_events=pipeline_result.trade_events,
            orders=pipeline_result.orders,
            positions_history=pipeline_result.positions_history,
            strategy_metrics=strategy_metrics,
            diagnostics=diagnostics,
            quality_status=overall_quality,
        )

    # ── internal helpers ──

    @staticmethod
    def _check_schedule(
        scheduled: tuple[str, ...],
        trading_days: set[str],
        simulation_start: str,
        evaluation_end: str,
    ) -> None:
        """§6 — decision calendar: strictly increasing actual trading days
        within [warmup boundary, evaluation end]."""
        previous = ""
        for date in scheduled:
            date = str(date)
            if date not in trading_days:
                raise StrategyContractViolation(
                    f"scheduled decision date {date!r} is not an actual trading day"
                )
            if date < simulation_start:
                raise StrategyContractViolation(
                    f"scheduled decision date {date!r} is inside the pure warmup "
                    f"period (before {simulation_start!r})"
                )
            if date > evaluation_end:
                raise StrategyContractViolation(
                    f"scheduled decision date {date!r} is after the evaluation end "
                    f"({evaluation_end!r})"
                )
            if date <= previous:
                raise StrategyContractViolation(
                    f"scheduled decision dates must be strictly increasing at {date!r}"
                )
            previous = date


_QUALITY_ORDER = {"VALID": 0, "DEGRADED": 1, "INVALID": 2, "FAILED": 3}


def _worst_quality_status(decisions: list) -> str:
    """Return the worst research quality across all decisions (§9/§27)."""
    if not decisions:
        return "VALID"
    worst = max(
        decisions,
        key=lambda d: _QUALITY_ORDER.get(
            str(d.quality_status.value) if hasattr(d.quality_status, "value") else str(d.quality_status),
            0,
        ),
    )
    val = worst.quality_status
    return str(val.value) if hasattr(val, "value") else str(val)
