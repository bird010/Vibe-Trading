"""Cohort engine orchestrator: full pipeline from signals to published artifacts.

Implements design §27.16 step 7. Orchestrates Plan A components for a full strategy run.
"""

from __future__ import annotations

import bisect
from collections.abc import Callable
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from backtest.stockpred.cohort.aggregation import aggregate_cohorts
from backtest.stockpred.cohort.artifacts import publish_cohort_artifacts
from backtest.stockpred.cohort.benchmark import (
    ExitEvent,
    compute_liquidation_matched_benchmark,
    compute_target_horizon_benchmark,
)
from backtest.stockpred.cohort.contracts import (
    CohortResult,
    CohortStatus,
    ExecutionEvent,
    SignalSnapshot,
    compute_cohort_id,
    compute_evaluation_protocol_key,
)
from backtest.stockpred.cohort.eligibility import SignalEligibilityGate
from backtest.stockpred.cohort.ledger import CohortLedger
from backtest.stockpred.cohort.metrics import compute_cohort_result, compute_raw_signal_return
from backtest.stockpred.cohort.pit_assurance import classify_pit_assurance
from backtest.stockpred.cohort.targets import build_cohort_targets
from backtest.stockpred.execution.costs import DEFAULT_COST_POLICY
from backtest.stockpred.execution.adv import compute_causal_adv
from backtest.stockpred.execution.policy import ExecutionPolicy, MarketView, PositionInfo
from backtest.stockpred.execution.valuation import ValuationPolicy
from src.stockpred.graph.adjustment import apply_qfq

# Bump when eligibility gate semantics change (enters protocol fingerprint)
ELIGIBILITY_POLICY_VERSION = "eligibility_v2"


class CohortBacktestConfig(BaseModel):
    """Configuration for a cohort backtest run."""

    start: str
    end: str
    eval_step: int = 5
    holding_days: int = 5
    top_n: int = Field(default=50, ge=1)
    committed_capital_per_cohort: float = 10_000_000.0
    max_participation: float = 0.05
    adv_lookback_days: int = 20
    max_exit_extension_days: int = 20
    stale_price_limit_days: int = 5
    min_raw_label_coverage: float = Field(default=1.0, ge=0.0, le=1.0)
    benchmark_code: str = "H00300.CSI"
    strategy_id: str = ""
    strategy_version: str = ""
    data_snapshot_id: str = ""
    run_dir: Path = Field(default_factory=lambda: Path("."))


def cohort_config_from_strategy_config(
    config: Any, *, run_dir: str | Path, data_snapshot_id: str,
) -> CohortBacktestConfig:
    """Single translation used by both process and in-process cohort runners."""
    return CohortBacktestConfig(
        start=config.start, end=config.end, eval_step=config.eval_step,
        holding_days=config.forward_days, top_n=config.top_n,
        committed_capital_per_cohort=config.portfolio_capital,
        max_participation=config.max_participation,
        benchmark_code=config.benchmark_code,
        strategy_id=config.strategy_snapshot.descriptor.id,
        strategy_version=config.strategy_snapshot.strategy_version,
        data_snapshot_id=data_snapshot_id, run_dir=Path(run_dir),
    )


@dataclass
class CohortRunResult:
    """Result of a full cohort run."""

    cohort_results: list[CohortResult] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    aggregation: Any = None
    version_id: str = ""


ProgressCallback = Callable[[int, int, str], None]
PhaseCallback = Callable[[str], None]

# Engine phases per §16.4
PHASE_EVALUATING_SIGNALS = "EVALUATING_SIGNALS"
PHASE_BUILDING_COHORTS = "BUILDING_COHORTS"
PHASE_SIMULATING_EXECUTION = "SIMULATING_EXECUTION"
PHASE_CALCULATING_METRICS = "CALCULATING_METRICS"
PHASE_PUBLISHING_CHART_BUNDLE = "PUBLISHING_CHART_BUNDLE"


def _failed_cohort(
    cohort_id: str,
    reason: str,
    *,
    evaluation_date: str = "",
    raw_label_coverage: float = 0.0,
    raw_label_status: str = "insufficient_data",
    uses_stale_valuation: bool = False,
    max_stale_days: int = 0,
    **data_quality: Any,
) -> CohortResult:
    """Create an auditable failed cohort without invented zero returns."""
    return CohortResult(
        cohort_id=cohort_id,
        committed_capital_return=None,
        executed_capital_return=None,
        raw_signal_return=None,
        horizon_mark_return=None,
        liquidation_return=None,
        benchmark_return=None,
        target_horizon_excess_return=None,
        liquidation_policy_excess_return=None,
        fill_rate=0.0,
        idle_cash_ratio=1.0,
        cost_ratio=0.0,
        exit_delay_days=0,
        unliquidated_ratio=0.0,
        status=CohortStatus.FAILED_DATA,
        data_quality={"reason": reason, **data_quality},
        evaluation_date=evaluation_date,
        raw_label_coverage=raw_label_coverage,
        raw_label_status=raw_label_status,
        uses_stale_valuation=uses_stale_valuation,
        max_stale_days=max_stale_days,
    )


def _empty_cohort(cohort_id: str, *, evaluation_date: str, data_quality: dict[str, Any]) -> CohortResult:
    """Record a valid but empty target set separately from data failure."""
    return CohortResult(
        cohort_id=cohort_id,
        committed_capital_return=0.0,
        executed_capital_return=0.0,
        raw_signal_return=None,
        horizon_mark_return=0.0,
        liquidation_return=0.0,
        benchmark_return=0.0,
        target_horizon_excess_return=0.0,
        liquidation_policy_excess_return=0.0,
        fill_rate=0.0,
        idle_cash_ratio=1.0,
        cost_ratio=0.0,
        exit_delay_days=0,
        unliquidated_ratio=0.0,
        status=CohortStatus.LIQUIDATED,
        data_quality=data_quality,
        evaluation_date=evaluation_date,
    )


def compute_horizon_mark_value(
    *, ledger: CohortLedger, market: pd.DataFrame, target_exit_date: str,
    valuation_policy: ValuationPolicy | None = None,
    return_stale_days: bool = False,
) -> float | tuple[float, int]:
    """Mark positions via adjusted returns applied to immutable raw notionals."""
    policy = valuation_policy or ValuationPolicy()
    mkt = market.copy()
    mkt["ts_code"] = mkt["ts_code"].astype(str)
    mkt["trade_date"] = mkt["trade_date"].astype(str)
    value = ledger.available_cash
    max_stale_days = 0
    for code, quantity in ledger.positions.items():
        if quantity <= 0:
            continue
        entry_date = ledger.initial_entry_date(code)
        entry = mkt[(mkt["ts_code"] == code) & (mkt["trade_date"] == entry_date)]
        if entry.empty or "adj_open" not in entry.columns:
            raise ValueError(f"missing adjusted entry price for {code} on {entry_date}")
        entry_adj_open = pd.to_numeric(entry.iloc[0]["adj_open"], errors="coerce")
        mark = policy.horizon_mark(code, target_exit_date, mkt)
        if (
            mark.quality_failure
            or pd.isna(entry_adj_open)
            or not isfinite(float(entry_adj_open))
            or entry_adj_open <= 0
        ):
            raise ValueError(f"invalid horizon valuation for {code} on {target_exit_date}")
        max_stale_days = max(max_stale_days, mark.stale_days)
        original_notional = ledger.initial_entry_cost(code, quantity)
        value += original_notional * mark.price / float(entry_adj_open)
    value = float(value)
    return (value, max_stale_days) if return_stale_days else value


def make_terminal_exit_event(
    *, date: str, ledger: CohortLedger, code: str, quantity: int,
) -> ExitEvent:
    """Build the liquidation benchmark weight for a residual terminal mark."""
    proportion = (
        ledger.initial_entry_cost(code, quantity) / ledger.committed_capital
        if ledger.committed_capital > 0
        else 0.0
    )
    return ExitEvent(date=date, proportion=proportion, is_terminal=True)


def _terminal_position_value(
    *, code: str, quantity: int, market: pd.DataFrame, terminal_date: str,
    valuation_policy: ValuationPolicy, adv_lookback_days: int, trade_dates: list[str],
) -> tuple[float, int]:
    """Return policy-valued residual value using the last valid raw open."""
    stock = market[(market["ts_code"].astype(str) == code)].copy()
    stock["trade_date"] = stock["trade_date"].astype(str)
    stock = stock[(stock["trade_date"] <= terminal_date) & stock["open"].notna()]
    stock = stock[pd.to_numeric(stock["open"], errors="coerce") > 0].sort_values("trade_date")
    if stock.empty:
        return 0.0, 999
    last = stock.iloc[-1]
    price = float(last["open"])
    stale_days = valuation_policy._date_diff_days(str(last["trade_date"]), terminal_date)
    up_limit = pd.to_numeric(last.get("up_limit"), errors="coerce")
    down_limit = pd.to_numeric(last.get("down_limit"), errors="coerce")
    limit_band_rate = max(
        abs(float(up_limit) / price - 1.0) if pd.notna(up_limit) else 0.0,
        abs(float(down_limit) / price - 1.0) if pd.notna(down_limit) else 0.0,
    )
    adv = compute_causal_adv(
        market, code, as_of_date=str(last["trade_date"]),
        lookback=adv_lookback_days, min_observations=1, trade_dates=trade_dates,
    ).adv_value
    terminal = valuation_policy.terminal_value(
        quantity=quantity, last_valid_price=price, stale_days=stale_days,
        limit_band_rate=limit_band_rate, adv=adv,
    )
    return terminal.terminal_value, stale_days


def _execution_event_row(event: ExecutionEvent) -> dict[str, Any]:
    """Flatten a complete execution event into the auditable orders artifact."""
    fees = event.fee_components
    return {
        "order_id": event.order_id,
        "cohort_id": event.cohort_id,
        "trade_date": event.trade_date,
        "code": event.code,
        "side": event.side,
        "requested_quantity": event.requested_quantity,
        "requested_quantity_known": event.requested_quantity_known,
        "requested_value": event.requested_value,
        "executed_quantity": event.executed_quantity,
        "executed_value": event.executed_value,
        "price": event.price,
        "remaining_quantity": event.remaining_quantity,
        "status": event.status,
        "reason_code": event.reason_code,
        "commission": fees.get("commission", 0.0),
        "stamp_duty": fees.get("stamp_duty", 0.0),
        "transfer_fee": fees.get("transfer_fee", 0.0),
        "slippage": fees.get("slippage", 0.0),
        "market_impact": fees.get("market_impact", 0.0),
        "total_fees": event.total_fees,
    }


class CohortRunner:
    """Orchestrates the full cohort evaluation pipeline for one strategy."""

    def __init__(self, *, gateway: Any, strategy: Any) -> None:
        self.gateway = gateway
        self.strategy = strategy

    def run(
        self,
        config: CohortBacktestConfig,
        on_progress: ProgressCallback | None = None,
        on_phase: PhaseCallback | None = None,
    ) -> CohortRunResult:
        """Run the full cohort evaluation pipeline."""
        def _phase(name: str) -> None:
            if on_phase:
                on_phase(name)
        # Compute evaluation protocol key
        protocol_config = {
            "data_snapshot_id": config.data_snapshot_id,
            "start": config.start,
            "end": config.end,
            "holding_days": config.holding_days,
            "eval_step": config.eval_step,
            "top_n": config.top_n,
            "committed_capital_per_cohort": config.committed_capital_per_cohort,
            "max_participation": config.max_participation,
            "adv_lookback_days": config.adv_lookback_days,
            "benchmark_code": config.benchmark_code,
            "execution_policy_version": "exec_v1",
            "cost_policy_version": "cost_v1",
            "eligibility_policy_version": ELIGIBILITY_POLICY_VERSION,
            "max_exit_extension_days": config.max_exit_extension_days,
            "stale_price_limit_days": config.stale_price_limit_days,
            "min_raw_label_coverage": config.min_raw_label_coverage,
            "quality_gate": {"max_stale_valuation_ratio": 0.02},
        }
        protocol_key = compute_evaluation_protocol_key(protocol_config)
        # Add identity fields required by leaderboard (must be after key computation)
        protocol_config["evaluation_protocol_key"] = protocol_key
        protocol_config["strategy_id"] = config.strategy_id
        protocol_config["strategy_version"] = config.strategy_version

        # Get trade dates from market calendar (not stock data) and schedule
        all_trade_dates = self.gateway.trade_dates(config.start, config.end)
        scheduled_dates = all_trade_dates[:: config.eval_step]

        # Compute extended data window for ADV lookback and exit forward
        adv_warmup = config.adv_lookback_days + 20  # ADV20 needs extra buffer
        exit_forward = config.holding_days + config.max_exit_extension_days
        # Extend start backwards for ADV warmup
        extended_start_dates = self.gateway.trade_dates("19900101", config.start)
        if len(extended_start_dates) > adv_warmup:
            data_start = extended_start_dates[-adv_warmup - 1]
        else:
            data_start = extended_start_dates[0] if extended_start_dates else config.start
        # Extend end forwards for exit execution
        extended_end_dates = self.gateway.trade_dates(config.end, "20991231")
        if len(extended_end_dates) > exit_forward + 1:
            data_end = extended_end_dates[exit_forward + 1]
        else:
            data_end = extended_end_dates[-1] if extended_end_dates else config.end

        # Execution policy
        policy = ExecutionPolicy(
            cost_policy=DEFAULT_COST_POLICY,
            max_participation=config.max_participation,
            adv_lookback_days=config.adv_lookback_days,
            max_exit_extension_days=config.max_exit_extension_days,
            lot_size=100,
        )
        valuation_policy = ValuationPolicy(stale_price_limit_days=config.stale_price_limit_days)

        # Collect all codes for market data
        cohort_results: list[CohortResult] = []
        signal_codes: set[str] = set()
        total = len(scheduled_dates)
        signals_frames: list[pd.DataFrame] = []
        targets_frames: list[pd.DataFrame] = []
        benchmark_frames: list[pd.DataFrame] = []
        order_events: list[dict[str, Any]] = []  # Collect all execution events for charts
        cohort_eval_dates: list[tuple[str, CohortResult]] = []  # Track eval_date with results

        # Eligibility gate: validate stock pool before Top-N selection
        eligibility_gate = SignalEligibilityGate(
            min_listed_trade_days=60,
            exclude_st=True,
        )
        # Load static universe data for eligibility checks
        universe_df = self.gateway.stock_dimension() if hasattr(self.gateway, "stock_dimension") else pd.DataFrame()
        name_history_df = self.gateway.name_history() if hasattr(self.gateway, "name_history") else pd.DataFrame()

        for done, eval_date in enumerate(scheduled_dates, start=1):
            # 1. Signal
            _phase(PHASE_EVALUATING_SIGNALS)
            cohort_id = compute_cohort_id(
                evaluation_protocol_key=protocol_key,
                strategy_id=config.strategy_id,
                strategy_version=config.strategy_version,
                evaluation_date=eval_date,
            )
            try:
                result = self.strategy.evaluate(eval_date)
            except Exception as error:
                cohort_results.append(_failed_cohort(cohort_id, "signal_evaluation_failure", evaluation_date=eval_date, detail=str(error)))
                cohort_eval_dates.append((eval_date, cohort_results[-1]))
                if on_progress:
                    on_progress(done, total, eval_date)
                continue
            # Handle both StrategyScore (real adapters) and DataFrame (legacy/test mocks)
            scores = result.scores if hasattr(result, "scores") else result
            if scores.empty:
                cohort_results.append(_failed_cohort(cohort_id, "empty_signal_snapshot", evaluation_date=eval_date))
                cohort_eval_dates.append((eval_date, cohort_results[-1]))
                if on_progress:
                    on_progress(done, total, eval_date)
                continue

            # 1b. Eligibility gate: filter BEFORE building signals/targets (fail-closed)
            if universe_df.empty:
                # No universe data = cannot validate, record FAILED_DATA
                failed_cohort_id = compute_cohort_id(
                    evaluation_protocol_key=protocol_key,
                    strategy_id=config.strategy_id,
                    strategy_version=config.strategy_version,
                    evaluation_date=eval_date,
                )
                cohort_results.append(_failed_cohort(failed_cohort_id, "universe_data_unavailable", evaluation_date=eval_date))
                cohort_eval_dates.append((eval_date, cohort_results[-1]))
                if on_progress:
                    on_progress(done, total, eval_date)
                continue

            candidate_codes = scores["ts_code"].astype(str).tolist()
            candidate_universe = universe_df[universe_df["ts_code"].astype(str).isin(candidate_codes)]
            signal_prices = self.gateway.prices(eval_date, eval_date, candidate_codes)
            signal_factors = self.gateway.adjustment_factors(eval_date, eval_date, candidate_codes)
            signal_calendar = self.gateway.trade_dates("19900101", eval_date)
            eligibility = eligibility_gate.check(
                eval_date=eval_date,
                universe=candidate_universe,
                candidates=candidate_codes,
                prices=signal_prices,
                adjustment_factors=signal_factors,
                market_calendar=signal_calendar,
                name_history=name_history_df,
            )
            if eligibility.data_failure:
                failed_cohort_id = compute_cohort_id(
                    evaluation_protocol_key=protocol_key,
                    strategy_id=config.strategy_id,
                    strategy_version=config.strategy_version,
                    evaluation_date=eval_date,
                )
                cohort_results.append(_failed_cohort(
                    failed_cohort_id, "eligibility_data_failure", evaluation_date=eval_date, eligibility=eligibility.coverage_stats,
                ))
                cohort_eval_dates.append((eval_date, cohort_results[-1]))
                if on_progress:
                    on_progress(done, total, eval_date)
                continue

            # Always filter to eligible codes (empty set = reject all)
            eligible_set = set(eligibility.eligible_codes)
            scores = scores[scores["ts_code"].astype(str).isin(eligible_set)]
            if scores.empty:
                cohort_id = compute_cohort_id(
                    evaluation_protocol_key=protocol_key,
                    strategy_id=config.strategy_id,
                    strategy_version=config.strategy_version,
                    evaluation_date=eval_date,
                )
                cohort_results.append(_empty_cohort(
                    cohort_id,
                    evaluation_date=eval_date,
                    data_quality={"reason": "no_eligible_candidates", "eligibility": eligibility.coverage_stats},
                ))
                cohort_eval_dates.append((eval_date, cohort_results[-1]))
                if on_progress:
                    on_progress(done, total, eval_date)
                continue

            # 1c. Build SignalSnapshot AFTER filtering
            signals = SignalSnapshot(
                evaluation_date=eval_date,
                strategy_id=config.strategy_id,
                strategy_version=config.strategy_version,
                data_snapshot_id=config.data_snapshot_id,
                signals=scores.to_dict("records"),
                eligible_universe=sorted(eligible_set),
            )

            # Collect signals frame
            sig_df = scores.copy()
            sig_df["evaluation_date"] = eval_date
            signals_frames.append(sig_df)
            signal_codes.update(scores["ts_code"].astype(str))

            # 2. Targets
            cohort_id = compute_cohort_id(
                evaluation_protocol_key=protocol_key,
                strategy_id=config.strategy_id,
                strategy_version=config.strategy_version,
                evaluation_date=eval_date,
            )
            target = build_cohort_targets(
                signals,
                committed_capital=config.committed_capital_per_cohort,
                top_n=config.top_n,
                cohort_id=cohort_id,
            )

            # Collect targets frame
            if target.selected_codes:
                tgt_df = pd.DataFrame([
                    {"cohort_id": cohort_id, "evaluation_date": eval_date, "code": c,
                     "weight": target.target_weights.get(c, 0.0),
                     "target_value": target.target_values.get(c, 0.0)}
                    for c in target.selected_codes
                ])
                targets_frames.append(tgt_df)

            if not target.selected_codes:
                cohort_results.append(_failed_cohort(
                    cohort_id, "empty_target", evaluation_date=eval_date,
                ))
                cohort_eval_dates.append((eval_date, cohort_results[-1]))
                if on_progress:
                    on_progress(done, total, eval_date)
                continue

            # 3. Load market data for selected codes (extended window for ADV + exits)
            codes_list = sorted(target.selected_codes)
            market = self._load_market(codes_list, config, start=data_start, end=data_end)
            # Use market calendar dates (not stock-derived) to avoid suspension distortion
            trade_dates = self.gateway.trade_dates(data_start, data_end)

            if not trade_dates:
                cohort_results.append(_failed_cohort(cohort_id, "market_calendar_unavailable", evaluation_date=eval_date))
                cohort_eval_dates.append((eval_date, cohort_results[-1]))
                if on_progress:
                    on_progress(done, total, eval_date)
                continue

            # Detect truncation: if holding period extends beyond available calendar
            entry_idx_cal = bisect.bisect_right(trade_dates, eval_date)
            target_exit_idx = entry_idx_cal + config.holding_days
            is_truncated = target_exit_idx >= len(trade_dates)

            if is_truncated:
                cohort_results.append(_failed_cohort(cohort_id, "truncated_horizon", evaluation_date=eval_date))
                cohort_eval_dates.append((eval_date, cohort_results[-1]))
                if on_progress:
                    on_progress(done, total, eval_date)
                continue

            if target_exit_idx + config.max_exit_extension_days >= len(trade_dates):
                cohort_results.append(_failed_cohort(cohort_id, "truncated_exit_extension", evaluation_date=eval_date))
                cohort_eval_dates.append((eval_date, cohort_results[-1]))
                if on_progress:
                    on_progress(done, total, eval_date)
                continue

            view = MarketView(market=market, trade_dates=trade_dates)

            # 4. Execute entries + build ledger
            ledger = CohortLedger(
                cohort_id=cohort_id,
                committed_capital=config.committed_capital_per_cohort,
                evaluation_date=eval_date,
            )

            entry_date = ""
            for code in target.selected_codes:
                target_value = target.target_values.get(code, 0.0)
                event = policy.execute_entry(
                    code=code,
                    signal_date=eval_date,
                    cash_budget=ledger.available_cash,
                    target_value=target_value,
                    market_view=view,
                    cohort_id=cohort_id,
                )
                ledger.apply_entry(event)
                if event.executed_quantity > 0 and not entry_date:
                    entry_date = event.trade_date
                order_events.append(_execution_event_row(event))

            # 5. Compute horizon_mark_return BEFORE exits (independent snapshot)
            # Mark-to-market at target_exit_date using post-entry positions/cash
            target_exit_date = trade_dates[min(target_exit_idx, len(trade_dates) - 1)]
            try:
                horizon_mark_value, horizon_stale_days = compute_horizon_mark_value(
                    ledger=ledger,
                    market=market,
                    target_exit_date=target_exit_date,
                    valuation_policy=valuation_policy,
                    return_stale_days=True,
                )
            except ValueError as error:
                ledger.status = CohortStatus.FAILED_DATA
                cohort_results.append(compute_cohort_result(
                    ledger=ledger,
                    raw_signal_return=None,
                    horizon_mark_return=0.0,
                    target_horizon_benchmark_return=0.0,
                    liquidation_benchmark_return=0.0,
                    exit_delay_days=0,
                    unliquidated_ratio=0.0,
                    data_quality={
                        "reason": "horizon_valuation_failure",
                        "detail": str(error),
                    },
                ))
                cohort_eval_dates.append((eval_date, cohort_results[-1]))
                if on_progress:
                    on_progress(done, total, eval_date)
                continue
            horizon_mark_return = (horizon_mark_value - config.committed_capital_per_cohort) / config.committed_capital_per_cohort

            # 6. Execute exits (affects only liquidation return, not horizon)
            ledger.begin_exit()
            exit_events_for_bench: list[ExitEvent] = []

            for code, qty in list(ledger.positions.items()):
                if qty <= 0:
                    continue
                position = PositionInfo(
                    code=code, quantity=qty,
                    entry_date=entry_date, target_exit_date=target_exit_date,
                    cohort_id=cohort_id,
                )
                exit_evts = policy.execute_exit(position, market_view=view)
                for e in exit_evts:
                    accepted = ledger.apply_exit(e)
                    order_events.append(_execution_event_row(e))
                    if accepted and e.executed_quantity > 0:
                        initial_cost_proportion = (
                            ledger.initial_entry_cost(code, e.executed_quantity)
                            / config.committed_capital_per_cohort
                        )
                        exit_events_for_bench.append(
                            ExitEvent(date=e.trade_date, proportion=initial_cost_proportion)
                        )
            terminal_value = 0.0
            max_terminal_stale_days = 0
            terminal_idx = min(target_exit_idx + config.max_exit_extension_days, len(trade_dates) - 1)
            terminal_date = trade_dates[terminal_idx]
            for code, quantity in list(ledger.positions.items()):
                if quantity <= 0:
                    continue
                position_terminal_value, position_stale_days = _terminal_position_value(
                    code=code,
                    quantity=quantity,
                    market=market,
                    terminal_date=terminal_date,
                    valuation_policy=valuation_policy,
                    adv_lookback_days=config.adv_lookback_days,
                    trade_dates=trade_dates,
                )
                terminal_value += position_terminal_value
                max_terminal_stale_days = max(max_terminal_stale_days, position_stale_days)
                exit_events_for_bench.append(make_terminal_exit_event(
                    date=terminal_date, ledger=ledger, code=code, quantity=quantity,
                ))
            ledger.finalize_exit()

            if ledger.status == CohortStatus.FAILED_EXECUTION:
                result = compute_cohort_result(
                    ledger=ledger,
                    raw_signal_return=None,
                    horizon_mark_return=0.0,
                    target_horizon_benchmark_return=0.0,
                    liquidation_benchmark_return=0.0,
                    exit_delay_days=0,
                    unliquidated_ratio=0.0,
                    data_quality={"reason": "execution_event_invalid"},
                    uses_stale_valuation=max(horizon_stale_days, max_terminal_stale_days) > 0,
                    max_stale_days=max(horizon_stale_days, max_terminal_stale_days),
                )
                cohort_results.append(result)
                cohort_eval_dates.append((eval_date, result))
                if on_progress:
                    on_progress(done, total, eval_date)
                continue

            if max_terminal_stale_days > config.stale_price_limit_days:
                result = _failed_cohort(
                    cohort_id,
                    "terminal_valuation_stale",
                    evaluation_date=eval_date,
                    uses_stale_valuation=True,
                    max_stale_days=max_terminal_stale_days,
                    stale_price_limit_days=config.stale_price_limit_days,
                )
                cohort_results.append(result)
                cohort_eval_dates.append((eval_date, result))
                if on_progress:
                    on_progress(done, total, eval_date)
                continue

            # 6b. Real exit delay and unliquidated ratio
            # Find actual last exit date from exit events
            actual_last_exit_date = ""
            for evt in exit_events_for_bench:
                if evt.date > actual_last_exit_date:
                    actual_last_exit_date = evt.date
            # exit_delay_days = actual last exit - target exit (in calendar trade days)
            if actual_last_exit_date and target_exit_date:
                target_idx = trade_dates.index(target_exit_date) if target_exit_date in trade_dates else -1
                actual_idx = trade_dates.index(actual_last_exit_date) if actual_last_exit_date in trade_dates else -1
                real_exit_delay = max(0, actual_idx - target_idx) if target_idx >= 0 and actual_idx >= 0 else 0
            else:
                real_exit_delay = 0
            # unliquidated_ratio = residual position cost / committed capital
            residual_value = sum(
                ledger.initial_entry_cost(code, quantity)
                for code, quantity in ledger.positions.items()
            )
            real_unliquidated_ratio = residual_value / config.committed_capital_per_cohort if config.committed_capital_per_cohort > 0 else 0.0

            # 7. Raw label
            raw_result = compute_raw_signal_return(
                target, market, trade_dates, holding_days=config.holding_days,
                min_coverage=config.min_raw_label_coverage,
            )

            if raw_result.status != "ok":
                result = _failed_cohort(
                    cohort_id,
                    "raw_label_coverage_below_minimum",
                    evaluation_date=eval_date,
                    raw_label_coverage=raw_result.raw_label_coverage,
                    raw_label_status=raw_result.status,
                    min_raw_label_coverage=config.min_raw_label_coverage,
                )
                cohort_results.append(result)
                cohort_eval_dates.append((eval_date, result))
                if on_progress:
                    on_progress(done, total, eval_date)
                continue

            # 7. Benchmarks (use extended window for full exit coverage)
            idx_mkt = self.gateway.index_daily(config.benchmark_code, data_start, data_end)
            bench_target = compute_target_horizon_benchmark(
                index_market=idx_mkt, trade_dates=trade_dates,
                signal_date=eval_date, holding_days=config.holding_days,
                benchmark_code=config.benchmark_code,
            )
            bench_liq = compute_liquidation_matched_benchmark(
                index_market=idx_mkt, trade_dates=trade_dates,
                entry_date=entry_date or trade_dates[0],
                exit_events=exit_events_for_bench,
                benchmark_code=config.benchmark_code,
            )

            # Collect benchmark frame
            benchmark_frames.append(pd.DataFrame([{
                "cohort_id": cohort_id, "evaluation_date": eval_date,
                "target_horizon_return": bench_target.benchmark_return,
                "liquidation_matched_return": bench_liq.benchmark_return,
            }]))

            if bench_target.benchmark_return is None or bench_liq.benchmark_return is None:
                result = _failed_cohort(
                    cohort_id,
                    "benchmark_data_insufficient",
                    evaluation_date=eval_date,
                    target_horizon_available=bench_target.benchmark_return is not None,
                    liquidation_matched_available=bench_liq.benchmark_return is not None,
                    raw_label_coverage=raw_result.raw_label_coverage,
                    raw_label_status=raw_result.status,
                    uses_stale_valuation=max(horizon_stale_days, max_terminal_stale_days) > 0,
                    max_stale_days=max(horizon_stale_days, max_terminal_stale_days),
                )
                cohort_results.append(result)
                cohort_eval_dates.append((eval_date, result))
                if on_progress:
                    on_progress(done, total, eval_date)
                continue

            # 8. Cohort result (three separate return calibers)
            result = compute_cohort_result(
                ledger=ledger,
                raw_signal_return=raw_result.raw_signal_return,
                horizon_mark_return=horizon_mark_return,
                target_horizon_benchmark_return=bench_target.benchmark_return,
                liquidation_benchmark_return=bench_liq.benchmark_return,
                exit_delay_days=real_exit_delay,
                unliquidated_ratio=real_unliquidated_ratio,
                terminal_value=terminal_value if ledger.positions else 0.0,
                raw_label_coverage=raw_result.raw_label_coverage,
                raw_label_status=raw_result.status,
                uses_stale_valuation=max(horizon_stale_days, max_terminal_stale_days) > 0,
                max_stale_days=max(horizon_stale_days, max_terminal_stale_days),
            )
            cohort_results.append(result)
            cohort_eval_dates.append((eval_date, result))

            if on_progress:
                on_progress(done, total, eval_date)

        # 9. Aggregate
        _phase(PHASE_CALCULATING_METRICS)
        agg = aggregate_cohorts(
            cohort_results,
            holding_days=config.holding_days,
            eval_step=config.eval_step,
            evaluation_protocol_key=protocol_key,
        )

        # 9b. PIT assurance classification
        # Determine table dependencies from strategy (default: all core tables)
        table_deps = tuple(
            getattr(self.strategy, "dependencies", ())
            or getattr(getattr(self.strategy, "snapshot", None), "dependencies", ())
            or getattr(getattr(self.strategy, "descriptor", None), "metadata", {}).get("dependencies", ())
            or ("__unproven_strategy_dependencies__",)
        )
        # Merge engine-common dependencies (benchmark reads fact_index_daily, etc.)
        from src.stockpred.strategies.adapters import ENGINE_COMMON_DEPENDENCIES
        merged_deps = sorted(set(table_deps) | set(ENGINE_COMMON_DEPENDENCIES))
        pit_result = classify_pit_assurance(merged_deps)
        protocol_config["pit_assurance"] = pit_result.level

        # If snapshot_only, force ranking_eligible=false
        if pit_result.level == "snapshot_only":
            from backtest.stockpred.cohort.aggregation import QualityReport
            agg = type(agg)(
                metrics=agg.metrics,
                quality=QualityReport(
                    ranking_eligible=False,
                    valid_eval_ratio=agg.quality.valid_eval_ratio,
                    failures=agg.quality.failures + ["pit_assurance_snapshot_only"],
                ),
            )

        # 10. Publish artifacts + chart bundle (all inside atomic staging)
        _phase(PHASE_PUBLISHING_CHART_BUNDLE)
        run_dir = Path(config.run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)

        # Load chart market data (extended window covers full execution)
        signal_codes_list = sorted(signal_codes)
        chart_market = self._load_market(signal_codes_list, config, start=data_start, end=data_end) if signal_codes_list else None

        # Publish versioned artifacts with chart bundle included in staging
        period_breakdown = _compute_period_breakdown(cohort_eval_dates)
        chart_orders_df = pd.DataFrame(order_events)
        version_id = publish_cohort_artifacts(
            run_dir=run_dir,
            cohort_results=cohort_results,
            agg_result=agg,
            config=protocol_config,
            chart_market=chart_market,
            chart_codes=signal_codes_list,
            chart_orders=chart_orders_df,
            chart_start_date=data_start,
            chart_end_date=data_end,
            signals_frames=signals_frames,
            targets_frames=targets_frames,
            benchmark_frames=benchmark_frames,
            period_breakdown=period_breakdown,
        )

        # Build metrics dict for batch store
        metrics_dict = {
            "mean_return": agg.metrics.mean_return,
            "median_return": agg.metrics.median_return,
            "win_rate": agg.metrics.win_rate,
            "sharpe": agg.metrics.mean_return / agg.metrics.hac_se if agg.metrics.hac_se > 0 else 0.0,
            "valid_cohorts": agg.metrics.valid_cohort_count,
            "ranking_eligible": agg.quality.ranking_eligible,
        }

        return CohortRunResult(
            cohort_results=cohort_results,
            metrics=metrics_dict,
            aggregation=agg,
            version_id=version_id,
        )

    def _load_market(self, codes: list[str], config: CohortBacktestConfig, *, start: str | None = None, end: str | None = None) -> pd.DataFrame:
        """Load and merge market data for given codes.

        Args:
            codes: Stock codes to load.
            config: Backtest configuration.
            start: Override start date (defaults to config.start).
            end: Override end date (defaults to config.end).
        """
        load_start = start or config.start
        load_end = end or config.end
        raw = self.gateway.prices(load_start, load_end, codes)
        factors = self.gateway.adjustment_factors(load_start, load_end, codes)
        limits = self.gateway.stock_limits(load_start, load_end, codes)

        market = raw.copy()
        market["ts_code"] = market["ts_code"].astype(str)
        market["trade_date"] = market["trade_date"].astype(str)

        # Adjusted prices are return-only data.  Missing factors remain explicit
        # instead of being filled across dates or securities.
        if not factors.empty:
            factors = factors.copy()
            factors["ts_code"] = factors["ts_code"].astype(str)
            factors["trade_date"] = factors["trade_date"].astype(str)
        market = apply_qfq(market, factors)

        if not limits.empty:
            limits = limits.copy()
            limits["ts_code"] = limits["ts_code"].astype(str)
            limits["trade_date"] = limits["trade_date"].astype(str)
            market = market.merge(
                limits[["ts_code", "trade_date", "up_limit", "down_limit"]],
                on=["ts_code", "trade_date"],
                how="left",
            )

        return market


def _compute_period_breakdown(cohort_eval_dates: list[tuple[str, CohortResult]]) -> pd.DataFrame:
    """Compute year/quarter period breakdown from cohort results per §14.

    Args:
        cohort_eval_dates: List of (evaluation_date, CohortResult) tuples.
    """
    if not cohort_eval_dates:
        return pd.DataFrame(columns=["period", "count", "mean_return", "win_rate"])

    rows = []
    total = len(cohort_eval_dates)
    returns = [r.committed_capital_return for _, r in cohort_eval_dates if r.committed_capital_return is not None]
    n = len(returns)

    # Overall
    rows.append({
        "period": "all",
        "count": total,
        "mean_return": float(np.mean(returns)) if returns else None,
        "win_rate": float(sum(1 for r in returns if r > 0) / n) if n > 0 else 0.0,
    })

    # Group by year
    by_year: dict[str, list[float]] = {}
    by_quarter: dict[str, list[float]] = {}
    for eval_date, result in cohort_eval_dates:
        # eval_date format: YYYYMMDD
        year = eval_date[:4]
        month = int(eval_date[4:6])
        quarter = f"{year}Q{(month - 1) // 3 + 1}"
        by_year.setdefault(year, []).append(result.committed_capital_return)
        by_quarter.setdefault(quarter, []).append(result.committed_capital_return)

    # Year breakdown
    for year in sorted(by_year.keys()):
        rets = by_year[year]
        rows.append({
            "period": year,
            "count": len(rets),
            "mean_return": float(np.mean([r for r in rets if r is not None])) if any(r is not None for r in rets) else None,
            "win_rate": float(sum(1 for r in rets if r is not None and r > 0) / sum(1 for r in rets if r is not None)) if any(r is not None for r in rets) else 0.0,
        })

    # Quarter breakdown (only if multiple quarters)
    if len(by_quarter) > 1:
        for quarter in sorted(by_quarter.keys()):
            rets = by_quarter[quarter]
            rows.append({
                "period": quarter,
                "count": len(rets),
                "mean_return": float(np.mean([r for r in rets if r is not None])) if any(r is not None for r in rets) else None,
                "win_rate": float(sum(1 for r in rets if r is not None and r > 0) / sum(1 for r in rets if r is not None)) if any(r is not None for r in rets) else 0.0,
            })

    return pd.DataFrame(rows)
