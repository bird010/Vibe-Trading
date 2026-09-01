"""Strategy-neutral fund-rotation backtest Runner.

The Runner consumes an immutable resolved strategy requirement, starts strategy
evaluation at the planned decision boundary, executes all targets through one
common account model, enforces the exact evaluation calendar and produces
strategy, benchmark and execution evidence.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

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
from backtest.fund_rotation.execution_ledger_v2 import compute_execution_diagnostics_v2
from backtest.fund_rotation.market_rules import (
    FundInstrumentVersion,
    MarketRuleResolver,
    PITInvalidMarketRule,
    UnknownExecutionRule,
)
from backtest.fund_rotation.metrics import compute_performance_metrics
from backtest.fund_rotation.native_execution import (
    FundRotationExecutionEngine,
    NativeExecutionRequest,
    NativeExecutionResult,
)
from backtest.fund_rotation.pit_universe import PITQueryMode
from backtest.fund_rotation.oos_validation import (
    DEFAULT_BENCHMARK_POLICY,
    BenchmarkPolicy,
)
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
    execution_diagnostics: dict[str, Any] = field(default_factory=dict)
    diagnostics: StrategyDiagnostics | None = None
    decision_trace: tuple[dict[str, Any], ...] = ()
    quality_status: str = "VALID"
    benchmark_quality_status: str = "VALID"


class FundRotationBacktestRunner:
    def __init__(
        self,
        fund_daily: pd.DataFrame,
        fund_adj: pd.DataFrame,
        dim_fund: pd.DataFrame,
        *,
        execution_engine: FundRotationExecutionEngine | None = None,
        market_rule_resolver: MarketRuleResolver | None = None,
        market_rule_instruments: Mapping[str, FundInstrumentVersion] | None = None,
        market_rule_mode: PITQueryMode = PITQueryMode.AS_WAS_KNOWN,
        pit_universe_resolver: object | None = None,
        strict_pit_benchmarks: bool = False,
        benchmark_policy: BenchmarkPolicy | None = None,
        run_id: str | None = None,
    ) -> None:
        self._fund_daily = fund_daily
        self._fund_adj = fund_adj
        self._dim_fund = dim_fund
        self._execution_engine = execution_engine or FundRotationExecutionEngine()
        self._market_rule_resolver = market_rule_resolver
        self._market_rule_instruments = (
            dict(market_rule_instruments) if market_rule_instruments is not None else None
        )
        self._market_rule_mode = market_rule_mode
        self._pit_universe_resolver = pit_universe_resolver
        self._strict_pit_benchmarks = strict_pit_benchmarks
        if strict_pit_benchmarks and benchmark_policy is None:
            raise ValueError("formal benchmarks require an explicit BenchmarkPolicy")
        if strict_pit_benchmarks and pit_universe_resolver is None:
            raise ValueError("formal benchmarks require an explicit PIT universe resolver")
        self._benchmark_policy = benchmark_policy or DEFAULT_BENCHMARK_POLICY
        self._run_id = run_id or uuid.uuid4().hex[:12]
        self._benchmark_quality_status = "VALID"
        self._benchmark_snapshot_version = 0

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

        candidate_resolver = getattr(
            strategy, "resolve_candidate_universe_codes", None
        )
        if self._pit_universe_resolver is None and callable(candidate_resolver):
            candidate_codes = candidate_resolver(snapshot)
        elif self._pit_universe_resolver is None:
            candidate_codes = snapshot.universe_codes
        else:
            candidate_codes = (
                getattr(snapshot, "historical_candidate_codes", ())
                or snapshot.universe_codes
            )
        fallback_universe = frozenset(str(code) for code in candidate_codes)
        universe_diagnostics_by_date: dict[str, dict[str, Any]] = {}
        universe_codes_by_date: dict[str, frozenset[str]] = {}
        pit_quality_status = "VERIFIED"
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
            execution_diagnostics: dict[str, Any] | None = None,
            quality_status: str | None = None,
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
                quality_status=(
                    pit_quality_status if quality_status is None else quality_status
                ),
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
                    quality_status=pit_quality_status,
                )
            pit_evidence = _resolve_pit_universe_for_signal(
                self._pit_universe_resolver,
                snapshot=snapshot,
                signal_date=signal_date,
                fallback_universe=fallback_universe,
            )
            universe = pit_evidence.universe
            universe_codes_by_date[signal_date] = universe
            universe_diagnostics_by_date[signal_date] = dict(pit_evidence.diagnostics)
            pit_quality_status = _combine_pit_quality_status(
                pit_quality_status,
                pit_evidence.quality_status,
            )

            def historical_pit_universe(historical_date: str) -> frozenset[str]:
                nonlocal pit_quality_status
                evidence = _resolve_pit_universe_for_signal(
                    self._pit_universe_resolver,
                    snapshot=snapshot,
                    signal_date=str(historical_date),
                    fallback_universe=fallback_universe,
                )
                pit_quality_status = _combine_pit_quality_status(
                    pit_quality_status,
                    evidence.quality_status,
                )
                universe_diagnostics_by_date[str(historical_date)] = dict(
                    evidence.diagnostics
                )
                return evidence.universe

            view = CausalDataView(
                self._fund_daily,
                self._fund_adj,
                self._dim_fund,
                requirements,
                pd.Timestamp(signal_date),
                universe,
                pit_universe_lookup=(
                    historical_pit_universe
                    if self._pit_universe_resolver is not None
                    else None
                ),
                historical_candidate_codes=frozenset(fallback_universe),
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
                quality_status=pit_quality_status,
            )

        native_execution_config = FundRotationConfig(
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
        rules_error = self._validate_native_rule_inputs()
        if rules_error is not None:
            return fail(*rules_error)

        try:
            native_request = NativeExecutionRequest(
                targets={
                    signal_date: dict(weights)
                    for signal_date, weights in targets_map.items()
                },
                evaluation_dates=tuple(evaluation_dates),
                fund_daily=self._fund_daily,
                fund_adj=self._fund_adj,
                execution=native_execution_config,
                initial_capital=execution.initial_capital,
                knowledge_cutoff=_native_knowledge_cutoff(targets_map, evaluation_dates),
                knowledge_cutoffs={
                    trade_date: _decision_knowledge_cutoff(trade_date)
                    for trade_date in evaluation_dates
                },
                snapshot_version=_native_snapshot_version(snapshot),
                run_id=run_id or self._run_id,
                rule_resolver=self._market_rule_resolver,
                instrument_versions=dict(self._market_rule_instruments or {}),
                rule_mode=self._market_rule_mode,
                decision_ids={
                    str(decision.signal_date): str(decision.decision_id)
                    for decision in decisions
                    if decision.action is DecisionKind.SET_TARGETS
                },
            )
        except (ValueError, TypeError) as exc:
            return fail("CONTRACT_ERROR", str(exc))

        try:
            native_result = self._execution_engine.execute(
                native_request,
                should_cancel=lambda: cancellation.is_cancelled,
            )
        except (UnknownExecutionRule, PITInvalidMarketRule) as exc:
            return fail("EXECUTION_RULES_UNAVAILABLE", str(exc))
        except (ValueError, TypeError) as exc:
            return fail("ENGINE_EXECUTION_ERROR", str(exc))

        execution_diagnostics = _formal_execution_diagnostics(
            native_result,
            execution,
            universe_diagnostics_by_date,
            snapshot=snapshot,
            run_id=run_id or self._run_id,
            rule_mode=self._market_rule_mode,
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
                executed_equity=native_result.executed_equity,
                trade_events=native_result.trade_events,
                orders=native_result.orders,
                positions_history=native_result.positions_history,
                execution_diagnostics=execution_diagnostics,
                quality_status=pit_quality_status,
            )

        actual_dates = pd.Index(
            [str(value) for value in native_result.executed_equity.index]
        )
        expected_dates = pd.Index(evaluation_dates)
        if not actual_dates.equals(expected_dates):
            return fail(
                "EVALUATION_CALENDAR_MISMATCH",
                "executed equity index must exactly equal the evaluation calendar",
                executed_equity=native_result.executed_equity,
                trade_events=native_result.trade_events,
                orders=native_result.orders,
                positions_history=native_result.positions_history,
                execution_diagnostics=execution_diagnostics,
            )

        benchmark_universes = dict(universe_codes_by_date)
        for benchmark_date in evaluation_dates:
            if benchmark_date in benchmark_universes:
                continue
            benchmark_evidence = _resolve_pit_universe_for_signal(
                self._pit_universe_resolver,
                snapshot=snapshot,
                signal_date=benchmark_date,
                fallback_universe=fallback_universe,
            )
            benchmark_universes[benchmark_date] = benchmark_evidence.universe
            universe_diagnostics_by_date[benchmark_date] = dict(
                benchmark_evidence.diagnostics
            )
            pit_quality_status = _combine_pit_quality_status(
                pit_quality_status,
                benchmark_evidence.quality_status,
            )

        execution_diagnostics["universe"] = _summarize_universe_diagnostics(
            universe_diagnostics_by_date
        )

        self._benchmark_snapshot_version = _native_snapshot_version(snapshot)
        benchmark_equity = self._public_benchmarks(
            evaluation_dates,
            execution,
            benchmark_universes,
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
            native_result.executed_equity,
            periods_per_year=244,
            initial_nav=evaluation.initial_nav,
        )
        equal_weight_name = str(
            getattr(
                self._benchmark_policy,
                "universe_equal_weight_benchmark",
                "equal_weight_etf",
            )
        )
        strategy_metrics.update(
            _relative_metrics(
                native_result.executed_equity,
                benchmark_equity.get(equal_weight_name),
            )
        )

        overall_quality = _combine_run_quality_status(
            _worst_quality_status(decisions),
            pit_quality_status,
        )
        overall_quality = _combine_run_quality_status(
            overall_quality,
            self._benchmark_quality_status,
        )
        execution_diagnostics["benchmark_quality_status"] = self._benchmark_quality_status
        if self._benchmark_policy is not None:
            execution_diagnostics["benchmark_policy"] = (
                self._benchmark_policy.to_identity_dict()
            )
        try:
            diagnostics = session.finalize()
        except Exception as exc:
            return fail(
                "FINALIZE_FAILED",
                str(exc),
                executed_equity=native_result.executed_equity,
                trade_events=native_result.trade_events,
                orders=native_result.orders,
                positions_history=native_result.positions_history,
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
            executed_equity=native_result.executed_equity,
            trade_events=native_result.trade_events,
            orders=native_result.orders,
            positions_history=native_result.positions_history,
            strategy_metrics=strategy_metrics,
            benchmark_equity=benchmark_equity,
            benchmark_metrics=benchmark_metrics,
            execution_diagnostics=execution_diagnostics,
            diagnostics=diagnostics,
            decision_trace=tuple(diagnostics.decision_trace),
            quality_status=overall_quality,
        )

    def _validate_native_rule_inputs(self) -> tuple[str, str] | None:
        if self._market_rule_resolver is None:
            return (
                "EXECUTION_RULES_UNAVAILABLE",
                "explicit PIT market rule resolver is required for native execution",
            )
        if not self._market_rule_instruments:
            return (
                "EXECUTION_RULES_UNAVAILABLE",
                "explicit PIT market rule instrument mapping is required for native execution",
            )
        return None

    def _public_benchmarks(
        self,
        evaluation_dates: list[str],
        execution: ExecutionConfig,
        universe_codes_by_signal_date: Mapping[str, frozenset[str]] | None = None,
    ) -> dict[str, pd.Series]:
        """Build common daily benchmarks from the same pinned market frames."""
        self._benchmark_quality_status = "VALID"
        benchmark_code = str(self._benchmark_policy.primary_benchmark)
        cash_name = str(self._benchmark_policy.cash_benchmark)
        equal_weight_name = str(
            self._benchmark_policy.universe_equal_weight_benchmark
        )
        index = pd.Index(evaluation_dates)
        cash = pd.Series(1.0, index=index, name=cash_name)
        adjusted = compute_adjusted_close(
            self._fund_daily,
            self._fund_adj,
            evaluation_dates[-1],
        )
        if adjusted.empty:
            return {
                cash_name: cash,
                equal_weight_name: cash.rename(equal_weight_name),
                benchmark_code: pd.Series(float("nan"), index=index, name=benchmark_code),
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
                if self._strict_pit_benchmarks and universe_codes_by_signal_date is not None:
                    self._benchmark_quality_status = _combine_pit_quality_status(
                        self._benchmark_quality_status,
                        "PIT_INVALID",
                    )
                    equal_values.append(float("nan"))
                    continue
                equal_values.append(nav)
                continue
            pit_codes = _latest_pit_universe(
                universe_codes_by_signal_date or {},
                prior_date,
            )
            if self._strict_pit_benchmarks and universe_codes_by_signal_date is not None and pit_codes is None:
                self._benchmark_quality_status = _combine_pit_quality_status(
                    self._benchmark_quality_status,
                    "PIT_INVALID",
                )
                equal_values.append(float("nan"))
                continue
            eligible = [
                code
                for code in returns.columns
                if (
                    str(code) in pit_codes
                    if pit_codes is not None
                    else list_dates.get(str(code), "99999999") <= prior_date
                )
            ]
            period = (
                returns.loc[date, eligible]
                if eligible
                else pd.Series(dtype=float)
            )
            if isinstance(period, pd.DataFrame):
                period = period.iloc[0]
            values = pd.to_numeric(period, errors="coerce").dropna()
            if values.empty:
                if self._strict_pit_benchmarks and universe_codes_by_signal_date is not None:
                    self._benchmark_quality_status = _combine_pit_quality_status(
                        self._benchmark_quality_status,
                        "PIT_INVALID",
                    )
                    equal_values.append(float("nan"))
                    continue
                period_return = 0.0
            else:
                period_return = float(values.mean())
            nav *= 1.0 + period_return
            equal_values.append(nav)
        equal_weight = pd.Series(
            equal_values,
            index=index,
            name=equal_weight_name,
        )

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
        policy_secondary: dict[str, pd.Series] = {}
        if self._strict_pit_benchmarks and self._market_rule_resolver is not None:
            for code in dict.fromkeys(
                (
                    benchmark_code,
                    *self._benchmark_policy.secondary_benchmarks,
                )
            ):
                series = self._execute_native_benchmark(
                    str(code), evaluation_dates, execution
                )
                if str(code) == benchmark_code:
                    buy_hold = series.rename(benchmark_code)
                else:
                    policy_secondary[str(code)] = series.rename(str(code))
        return {
            cash_name: cash,
            equal_weight_name: equal_weight,
            benchmark_code: buy_hold,
            **policy_secondary,
        }

    def _execute_native_benchmark(
        self,
        code: str,
        evaluation_dates: list[str],
        execution: ExecutionConfig,
    ) -> pd.Series:
        if self._market_rule_instruments is None or code not in self._market_rule_instruments:
            raise UnknownExecutionRule(
                f"UNKNOWN_EXECUTION_RULE: benchmark instrument {code} is missing"
            )
        native_config = FundRotationConfig(
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
        request = NativeExecutionRequest(
            targets={evaluation_dates[0]: {code: 1.0}},
            evaluation_dates=tuple(evaluation_dates),
            fund_daily=self._fund_daily,
            fund_adj=self._fund_adj,
            execution=native_config,
            initial_capital=execution.initial_capital,
            knowledge_cutoff=f"{evaluation_dates[0]}T15:00:00",
            knowledge_cutoffs={date: f"{date}T15:00:00" for date in evaluation_dates},
            snapshot_version=self._benchmark_snapshot_version,
            run_id=f"{self._run_id}:benchmark:{code}",
            rule_resolver=self._market_rule_resolver,
            instrument_versions=dict(self._market_rule_instruments),
            rule_mode=self._market_rule_mode,
            decision_ids={evaluation_dates[0]: f"benchmark:{code}:decision"},
        )
        return self._execution_engine.execute(request).executed_equity

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


@dataclass(frozen=True)
class _PITUniverseEvidence:
    universe: frozenset[str]
    quality_status: str
    diagnostics: dict[str, Any]


def _resolve_pit_universe_for_signal(
    resolver: object | None,
    *,
    snapshot: object,
    signal_date: str,
    fallback_universe: frozenset[str],
) -> _PITUniverseEvidence:
    if resolver is None:
        return _missing_pit_master_evidence(fallback_universe)

    if hasattr(resolver, "resolve_universe"):
        resolution = resolver.resolve_universe(
            snapshot=snapshot,
            signal_date=signal_date,
            knowledge_cutoff=_decision_knowledge_cutoff(signal_date),
            fallback_universe=fallback_universe,
        )
    elif callable(resolver):
        resolution = resolver(
            snapshot=snapshot,
            signal_date=signal_date,
            knowledge_cutoff=_decision_knowledge_cutoff(signal_date),
            fallback_universe=fallback_universe,
        )
    else:
        raise TypeError("pit_universe_resolver must be callable or expose resolve_universe")

    universe = _extract_universe_codes(resolution)
    diagnostics = _extract_universe_diagnostics(resolution)
    quality_status = _quality_status_value(
        _extract_resolution_value(resolution, "quality_status", "VERIFIED")
    )
    diagnostics["quality_status"] = quality_status
    diagnostics.setdefault("reason_code", "")
    diagnostics.setdefault("details", "")
    return _PITUniverseEvidence(
        universe=frozenset(universe),
        quality_status=quality_status,
        diagnostics=diagnostics,
    )


def _missing_pit_master_evidence(
    fallback_universe: frozenset[str],
) -> _PITUniverseEvidence:
    diagnostics = {
        "quality_status": "RESEARCH_ONLY_UNVERIFIED_UNIVERSE",
        "reason_code": "PIT_MASTER_MISSING",
        "details": (
            "snapshot does not provide PIT fund master and no PIT resolver was injected"
        ),
    }
    return _PITUniverseEvidence(
        universe=frozenset(fallback_universe),
        quality_status="RESEARCH_ONLY_UNVERIFIED_UNIVERSE",
        diagnostics=diagnostics,
    )


def _extract_universe_codes(resolution: object) -> tuple[str, ...]:
    raw = _extract_resolution_value(resolution, "universe_codes", None)
    if raw is None:
        raw = _extract_resolution_value(resolution, "eligible_codes", None)
    if raw is None:
        raw = _extract_resolution_value(resolution, "eligible", ())
    codes = []
    for item in raw or ():
        codes.append(str(getattr(item, "ts_code", item)))
    return tuple(codes)


def _extract_universe_diagnostics(resolution: object) -> dict[str, Any]:
    raw = _extract_resolution_value(resolution, "diagnostics", None)
    if raw is None:
        raw = _extract_resolution_value(resolution, "audit_metrics", None)
    diagnostics = dict(raw or {})
    for key in (
        "identity_hash",
        "snapshot_fingerprint",
        "coverage_diagnostics",
    ):
        value = _extract_resolution_value(resolution, key, None)
        if value not in (None, "", {}):
            diagnostics[key] = dict(value) if key == "coverage_diagnostics" else value
    return diagnostics


def _extract_resolution_value(
    resolution: object,
    key: str,
    default: object,
) -> object:
    if isinstance(resolution, dict):
        return resolution.get(key, default)
    return getattr(resolution, key, default)


def _latest_pit_universe(
    universes_by_signal_date: Mapping[str, frozenset[str]],
    as_of_date: str,
) -> frozenset[str] | None:
    eligible_dates = [
        signal_date
        for signal_date in universes_by_signal_date
        if str(signal_date) <= str(as_of_date)
    ]
    if not eligible_dates:
        return None
    return universes_by_signal_date[max(eligible_dates)]


def _quality_status_value(value: object) -> str:
    return str(value.value) if hasattr(value, "value") else str(value)


def _decision_knowledge_cutoff(signal_date: str) -> str:
    return f"{pd.Timestamp(signal_date).strftime('%Y%m%d')}T15:00:00"


_PIT_QUALITY_ORDER = {
    "VERIFIED": 0,
    "VALID": 0,
    "KNOWLEDGE_TIME_UNVERIFIED": 1,
    "PIT_UNVERIFIED": 1,
    "RESEARCH_ONLY_UNVERIFIED_UNIVERSE": 1,
    "PIT_INVALID": 2,
}


def _combine_pit_quality_status(current: str, candidate: str) -> str:
    return max(
        (current, candidate),
        key=lambda value: _PIT_QUALITY_ORDER.get(str(value), 1),
    )


def _combine_run_quality_status(strategy_quality: str, pit_quality: str) -> str:
    if pit_quality in {"VERIFIED", "VALID"}:
        return strategy_quality
    if _QUALITY_ORDER.get(strategy_quality, 0) >= _QUALITY_ORDER["INVALID"]:
        return strategy_quality
    return pit_quality


def _native_knowledge_cutoff(
    targets_map: dict[str, dict[str, float]],
    evaluation_dates: list[str],
) -> str:
    if targets_map:
        return _decision_knowledge_cutoff(max(str(signal_date) for signal_date in targets_map))
    return _decision_knowledge_cutoff(evaluation_dates[0])


def _native_snapshot_version(snapshot: object) -> int:
    version = getattr(snapshot, "snapshot_version", None)
    if version is None:
        version = getattr(snapshot, "dim_version", None)
    if version is None:
        raise ValueError("snapshot identity must expose snapshot_version or dim_version")
    return int(version)


def _formal_execution_diagnostics(
    result: NativeExecutionResult,
    execution: ExecutionConfig,
    universe_diagnostics_by_date: dict[str, dict[str, Any]],
    *,
    snapshot: object,
    run_id: str,
    rule_mode: PITQueryMode,
) -> dict[str, Any]:
    equity = result.executed_equity
    if equity.empty:
        average_portfolio_nav = float(execution.initial_capital)
        evaluation_days = None
    else:
        average_portfolio_nav = float(equity.mean()) * float(execution.initial_capital)
        evaluation_days = int(len(equity))
    diagnostics = compute_execution_diagnostics_v2(
        result.ledger,
        average_portfolio_nav=average_portfolio_nav,
        evaluation_days=evaluation_days,
    )
    diagnostics["universe"] = _summarize_universe_diagnostics(
        universe_diagnostics_by_date,
    )
    diagnostics["execution_identity"] = _native_execution_identity(
        result,
        snapshot=snapshot,
        run_id=run_id,
        rule_mode=rule_mode,
    )
    return diagnostics


def _native_execution_identity(
    result: NativeExecutionResult,
    *,
    snapshot: object,
    run_id: str,
    rule_mode: PITQueryMode,
) -> dict[str, Any]:
    rule_versions = sorted(
        {
            str(row.get("rule_version", ""))
            for row in result.orders
            if str(row.get("rule_version", ""))
        }
    )
    source_record_ids = sorted(
        {
            str(row.get("source_record_id", ""))
            for row in result.orders
            if str(row.get("source_record_id", ""))
        }
    )
    return {
        "run_id": run_id,
        "snapshot_version": _native_snapshot_version(snapshot),
        "snapshot_fingerprint": str(getattr(snapshot, "fingerprint", "")),
        "rule_mode": str(rule_mode.value if hasattr(rule_mode, "value") else rule_mode),
        "rule_versions": rule_versions,
        "source_record_ids": source_record_ids,
    }


def _summarize_universe_diagnostics(
    universe_diagnostics_by_date: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if not universe_diagnostics_by_date:
        return {}
    latest_date = sorted(universe_diagnostics_by_date)[-1]
    latest = dict(universe_diagnostics_by_date[latest_date])
    latest.pop("signal_date", None)
    if len(universe_diagnostics_by_date) > 1:
        latest["by_date"] = {
            date: dict(diagnostics)
            for date, diagnostics in sorted(universe_diagnostics_by_date.items())
        }
    return latest


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
