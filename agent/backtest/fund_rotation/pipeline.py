"""Legacy fixed pipeline — reduced to a compatibility adapter (Phase 2 Task 6).

Design §13.1/§32.3: ``run_signal_pipeline`` keeps its public name and result
shape, but no longer contains strategy algorithms, order matching, or
valuation. It now performs:

1. static data preparation (ETF name filter, adj-coverage pool pre-filter);
2. parameter conversion to the strategy config/execution config;
3. one call into the strategy-neutral Runner driving the baseline strategy;
4. legacy-shape extras (ideal account, benchmarks, robustness) built from the
   common execution/metric modules.

Signal generation lives in ``strategies.correlation_all_members``; execution
and valuation live in ``execution.py``; scheduling lives in the Runner.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

import pandas as pd

from backtest.fund_rotation.config import FundRotationConfig
from backtest.fund_rotation.evaluation import (
    EvaluationContext,
    validate_equity_index,
)
from backtest.fund_rotation.universe import (
    ExclusionReason,
    ExclusionRecord,
    filter_etf_universe,
)
from backtest.fund_rotation.returns import compute_weekly_returns
from backtest.fund_rotation.metrics import compute_performance_metrics
from backtest.fund_rotation.robustness import compute_cluster_stability, time_block_bootstrap
from backtest.fund_rotation.ideal_executor import run_daily_ideal_account
from backtest.fund_rotation.benchmarks import compute_equal_weight_theoretical_index
from backtest.fund_rotation.market_rules import (
    FundInstrumentVersion,
    MarketRuleResolver,
)
from backtest.fund_rotation.pit_universe import PITQueryMode
from backtest.fund_rotation.oos_validation import (
    DEFAULT_BENCHMARK_POLICY,
    BenchmarkPolicy,
)

from backtest.fund_rotation.runner import (
    CancellationToken,
    ExecutionConfig,
    FundRotationBacktestRunner,
    SubRunStatus,
)
from backtest.fund_rotation.strategies.correlation_all_members.config import (
    CorrelationAllMembersConfig,
)
from backtest.fund_rotation.strategies.correlation_all_members.strategy import (
    CorrelationAllMembersStrategy,
)
from src.stockpred.fund_rotation.data_snapshot import PinnedFundDataSnapshot


# Execution & valuation live in the common execution module (Phase 2 Task 2);
# the adapter uses the public names and re-exports the legacy private aliases
# for compatibility with existing callers/tests.
from backtest.fund_rotation.execution import (
    ExecutionContext,  # noqa: F401  (re-exported for legacy callers)
    ExecutionProfiler,
    PipelineResult,
    align_theoretical_to_common_dates,
    build_execution_context,
    execute_with_capacity as _execute_with_capacity,  # noqa: F401  (re-exported)
    first_actual_fill_date,
    mark_to_market as _mark_to_market,  # noqa: F401  (re-exported)
    run_execution_loop,
    serialize_orders as _serialize_orders,  # noqa: F401  (re-exported)
)

# Legacy private aliases retained for existing test/caller imports.
_align_theoretical_to_common_dates = align_theoretical_to_common_dates
_build_execution_context = build_execution_context
_first_actual_fill_date = first_actual_fill_date
_run_execution_loop = run_execution_loop

logger = logging.getLogger(__name__)


def run_signal_pipeline(
    config: FundRotationConfig,
    fund_daily: pd.DataFrame,
    fund_adj: pd.DataFrame,
    dim_fund: pd.DataFrame,
    trade_dates: list[str] | None = None,
    stage_callback: callable | None = None,
    profiler: ExecutionProfiler | None = None,
    market_rule_resolver: MarketRuleResolver | None = None,
    market_rule_instruments: Mapping[str, FundInstrumentVersion] | None = None,
    market_rule_mode: PITQueryMode = PITQueryMode.AS_WAS_KNOWN,
    market_rule_snapshot_version: int | None = None,
    pit_universe_resolver: object | None = None,
    require_verified_pit: bool = False,
    formal_benchmarks: bool = False,
    benchmark_policy: BenchmarkPolicy | None = None,
    data_snapshot: PinnedFundDataSnapshot | None = None,
) -> PipelineResult:
    """Run the legacy-shape fund-rotation backtest via the common Runner.

    Signal generation is delegated to the baseline strategy driven by the
    strategy-neutral Runner; this adapter keeps the historical result fields
    (theoretical account, benchmarks, robustness) intact for existing callers.

    Args:
        config: Backtest parameters.
        fund_daily: Columns [ts_code, trade_date, close, vol, amount].
        fund_adj: Columns [ts_code, trade_date, adj_factor].
        dim_fund: Columns [ts_code, name, list_date].
        trade_dates: Optional sorted list of all market trading dates.
        stage_callback: Optional callable(stage_name: str) for progress reporting.
        market_rule_resolver: Explicit PIT market-rule resolver for Runner execution.
        market_rule_instruments: Explicit PIT instrument mapping for Runner execution.
        market_rule_mode: PIT market-rule query mode.
        market_rule_snapshot_version: Explicit market-rule snapshot version.
        pit_universe_resolver: Explicit PIT universe adapter for formal runs.
        require_verified_pit: Fail closed when the PIT universe is not verified.
        formal_benchmarks: Use Runner PIT-aware benchmark outputs in the result.
        data_snapshot: Pinned data snapshot required by formal runs.

    Returns:
        PipelineResult with targets, benchmarks, and metrics.
    """
    formal_requested = require_verified_pit or formal_benchmarks
    if formal_requested and pit_universe_resolver is None:
        raise ValueError(
            "formal pipeline requires an explicit PIT universe resolver; "
            "static dim_fund/list_date fallback is research-only"
        )
    if formal_requested and benchmark_policy is None:
        raise ValueError("formal pipeline requires an explicit BenchmarkPolicy")
    effective_benchmark_policy = benchmark_policy or DEFAULT_BENCHMARK_POLICY
    if formal_requested and (
        market_rule_resolver is None or not market_rule_instruments or market_rule_snapshot_version is None
    ):
        raise ValueError(
            "formal pipeline requires an explicit versioned PIT market-rule source"
        )
    if formal_requested and data_snapshot is None:
        raise ValueError(
            "formal pipeline requires an explicit pinned fund data snapshot"
        )
    def _notify(stage: str) -> None:
        if stage_callback:
            stage_callback(stage)

    result = PipelineResult()

    # ── Static data preparation: ETF pool and exclusions ──
    etf_pool = filter_etf_universe(dim_fund)
    if etf_pool.empty:
        raise ValueError("ETF pool is empty after static name filtering (no ETFs in dim_fund)")

    pool_codes = set(etf_pool["ts_code"].astype(str))
    for row in dim_fund.itertuples(index=False):
        code = str(getattr(row, "ts_code"))
        if code in pool_codes:
            continue
        name = str(getattr(row, "name", ""))
        if "QDII" in name:
            reason = ExclusionReason.QDII
        elif "LOF" in name:
            reason = ExclusionReason.LOF
        elif "联接" in name:
            reason = ExclusionReason.FEEDER
        else:
            reason = ExclusionReason.NOT_ETF_NAME
        result.exclusions.append(ExclusionRecord(
            ts_code=code, reason=reason, details=f"name={name}",
        ))

    # Filter fund_daily and fund_adj to ETF pool codes BEFORE computing returns
    # (avoids processing 10,000+ non-ETF funds)
    # Formal PIT runs retain the complete pinned ETF snapshot, including
    # instruments that are absent from today's static dim_fund pool but may be
    # needed by historical coverage denominators.
    etf_codes = (
        set(
            str(code)
            for code in (
                data_snapshot.historical_candidate_codes
                or data_snapshot.universe_codes
            )
        )
        if formal_requested and data_snapshot is not None
        else pool_codes
    )
    fund_daily = fund_daily[fund_daily["ts_code"].astype(str).isin(etf_codes)].copy()
    fund_adj = fund_adj[fund_adj["ts_code"].astype(str).isin(etf_codes)].copy()

    if fund_adj.empty:
        raise ValueError("fact_fund_adj has no usable rows for the ETF universe")
    daily_keys = fund_daily[["ts_code", "trade_date"]].astype(str).drop_duplicates()
    adj_keys = fund_adj[["ts_code", "trade_date"]].astype(str).drop_duplicates()
    coverage = daily_keys.merge(adj_keys, on=["ts_code", "trade_date"], how="left", indicator=True)
    incomplete_codes = sorted(coverage.loc[coverage["_merge"] == "left_only", "ts_code"].unique())
    for code in incomplete_codes:
        result.exclusions.append(ExclusionRecord(
            ts_code=code,
            reason=ExclusionReason.INSUFFICIENT_ADJ_COVERAGE,
            details="fund_adj does not cover every loaded fund_daily record",
        ))
    if incomplete_codes:
        complete_codes = etf_codes - set(incomplete_codes)
        etf_pool = etf_pool[etf_pool["ts_code"].astype(str).isin(complete_codes)].reset_index(drop=True)
        fund_daily = fund_daily[fund_daily["ts_code"].astype(str).isin(complete_codes)]
        fund_adj = fund_adj[fund_adj["ts_code"].astype(str).isin(complete_codes)]
    if fund_daily.empty:
        raise ValueError("fact_fund_adj coverage is systematically insufficient for all ETFs")

    _notify("PREPARING_DATA")
    end_date = config.end_date or fund_daily["trade_date"].max()
    weekly_returns = compute_weekly_returns(fund_daily, fund_adj, as_of_date=end_date)
    if weekly_returns.empty:
        raise ValueError("Weekly returns computation produced empty result (check fund_daily/fund_adj data)")

    all_weeks = list(weekly_returns.index)
    result.num_weeks = len(all_weeks)

    # min_weeks_needed = max(min_training_weeks, correlation_lookback_weeks).
    # The momentum window is a subset of the lookback, not additive.
    min_weeks_needed = max(config.min_training_weeks, config.correlation_lookback_weeks)
    if len(all_weeks) < min_weeks_needed:
        raise ValueError(
            f"Insufficient history: {len(all_weeks)} weeks available, "
            f"need at least {min_weeks_needed} (training={config.min_training_weeks}, "
            f"correlation_lookback={config.correlation_lookback_weeks})"
        )

    # Evaluation trading calendar (within [start_date, end_date]) shared by the
    # ideal and real executors so a pre-evaluation signal activates at the
    # first evaluation day (§24).
    all_dates_sorted = sorted(fund_daily["trade_date"].astype(str).unique())
    evaluation_dates = [
        d for d in all_dates_sorted
        if (not config.start_date or d >= config.start_date)
        and (not config.end_date or d <= config.end_date)
    ]
    if formal_requested:
        if data_snapshot is None:
            raise ValueError("formal pipeline requires an explicit pinned fund data snapshot")
        if not data_snapshot.fingerprint or data_snapshot.fingerprint == "legacy-compat":
            raise ValueError("formal pipeline requires an auditable pinned snapshot fingerprint")
        snapshot_candidate_codes = set(
            data_snapshot.historical_candidate_codes
            or data_snapshot.universe_codes
        )
        if not set(etf_codes).issubset(snapshot_candidate_codes):
            raise ValueError("pinned snapshot universe does not cover the loaded ETF universe")
        if not set(evaluation_dates).issubset(set(data_snapshot.trading_dates)):
            raise ValueError("pinned snapshot calendar does not cover evaluation dates")

    # ── Drive the baseline strategy through the strategy-neutral Runner ──
    _notify("GENERATING_SIGNALS")
    strategy_config = CorrelationAllMembersConfig.from_legacy(config)
    snapshot = data_snapshot or PinnedFundDataSnapshot(
        fund_version=0,
        fund_adj_version=0,
        dim_version=int(market_rule_snapshot_version or 0),
        universe_codes=tuple(sorted(str(c) for c in etf_codes)),
        trading_dates=tuple(all_dates_sorted),
        fingerprint="legacy-compat",
    )
    evaluation = EvaluationContext.from_range(
        all_dates_sorted,
        config.start_date or all_dates_sorted[0],
        config.end_date or all_dates_sorted[-1],
    )
    execution_config = ExecutionConfig(
        initial_capital=config.initial_capital,
        commission_rate=config.commission_rate,
        commission_min=config.commission_min,
        other_fee_rate=config.other_fee_rate,
        max_participation_rate=config.max_participation_rate,
        adv_lookback=config.adv_lookback,
        adv_min_observations=config.adv_min_observations,
        base_slippage_bps=config.base_slippage_bps,
        max_slippage_bps=config.max_slippage_bps,
    )
    runner = FundRotationBacktestRunner(
        fund_daily,
        fund_adj,
        dim_fund,
        market_rule_resolver=market_rule_resolver,
        market_rule_instruments=market_rule_instruments,
        market_rule_mode=market_rule_mode,
        pit_universe_resolver=pit_universe_resolver,
        strict_pit_benchmarks=formal_requested,
        benchmark_policy=effective_benchmark_policy,
    )
    run_result = runner.run(
        strategy=CorrelationAllMembersStrategy(),
        config=strategy_config,
        snapshot=snapshot,
        evaluation=evaluation,
        execution=execution_config,
        cancellation=CancellationToken(),
    )

    result.quality_status = run_result.quality_status
    result.execution_diagnostics = dict(run_result.execution_diagnostics)
    if formal_requested and result.quality_status not in {"VALID", "VERIFIED"}:
        raise ValueError(
            "formal pipeline requires a verified PIT universe; "
            f"got quality_status={result.quality_status}"
        )

    diagnostics = run_result.diagnostics
    artifacts = {a.role: a.payload for a in (diagnostics.artifacts if diagnostics else ())}
    result.cluster_history = list(artifacts.get("cluster_history", []))
    result.exclusions.extend(artifacts.get("exclusions", []))

    if run_result.status is SubRunStatus.FAILED and run_result.error_code == "INSUFFICIENT_HISTORY":
        # Too few week-endings for one complete window: the legacy loop emits
        # no signal here (not an error).
        execution_targets: dict[str, dict[str, float]] = {}
        eligible_per_week: dict[str, list[str]] = {}
        succeeded = False
    elif run_result.status is SubRunStatus.SUCCEEDED:
        execution_targets = dict(run_result.weekly_targets)
        eligible_per_week = {
            d.signal_date: list(d.diagnostics.get("eligible_codes", []))
            for d in run_result.decisions
        }
        succeeded = True
    else:
        raise ValueError(
            f"Strategy sub-run failed: {run_result.error_code}: {run_result.error_message}"
        )

    # §8/§24: output signals are trimmed to user-specified start_date, but the
    # full target set (including the last pre-evaluation signal) drives
    # execution so the initial position is built at the first evaluation day.
    result.weekly_targets = (
        {wk: tgts for wk, tgts in execution_targets.items() if wk >= config.start_date}
        if config.start_date else dict(execution_targets)
    )
    result.num_reclusters = len(result.cluster_history)
    result.num_etfs_used = len(set(
        code for targets in execution_targets.values() for code in targets
    ))

    # ── Execution evidence (continuous account, daily equity) ──
    _notify("EXECUTING")
    if succeeded:
        result.executed_equity = run_result.executed_equity
        result.trade_events = run_result.trade_events
        result.orders = run_result.orders
        result.positions_history = run_result.positions_history
    else:
        # No targets: hold cash (initial NAV) over the full evaluation
        # interval so equity and metrics remain defined (§32.1).
        empty = PipelineResult()
        empty_ctx = build_execution_context(fund_daily, fund_adj, config, profiler=profiler)
        run_execution_loop(
            empty, config, empty_ctx, profiler=profiler,
            execution_targets={}, evaluation_dates=evaluation_dates,
        )
        result.executed_equity = empty.executed_equity

    # ── Ideal account (theoretical strategy, no costs) ──
    if execution_targets:
        result.strategy_cumulative = run_daily_ideal_account(
            execution_targets, fund_daily, fund_adj, evaluation_dates=evaluation_dates,
        )
        if config.start_date:
            result.strategy_cumulative = result.strategy_cumulative[
                result.strategy_cumulative.index >= config.start_date
            ]
    else:
        # No targets: the theoretical strategy holds cash (initial NAV) over
        # the full evaluation interval, so metrics are defined and zero (§32.1).
        result.strategy_cumulative = pd.Series(
            1.0, index=list(evaluation_dates), name="theoretical_strategy",
        )

    # ── Benchmarks — §14.1 ──
    _notify("COMPUTING_BENCHMARKS")
    benchmark_weeks = sorted(result.weekly_targets)
    local_codes = set(fund_daily["ts_code"].astype(str))
    benchmark_code = str(effective_benchmark_policy.primary_benchmark)
    has_primary_benchmark = benchmark_code in local_codes

    if not formal_requested and benchmark_weeks and not has_primary_benchmark:
        raise ValueError(
            f"{benchmark_code} benchmark data is missing from the local ETF dataset."
        )

    if not formal_requested and benchmark_weeks and has_primary_benchmark:
        exec_ctx = build_execution_context(fund_daily, fund_adj, config, profiler=profiler)
        first_week = benchmark_weeks[0]
        buy_hold_run = PipelineResult(
            weekly_targets={first_week: {benchmark_code: 1.0}},
        )
        run_execution_loop(buy_hold_run, config, exec_ctx, profiler=profiler,
                           evaluation_dates=evaluation_dates)
        result.buy_hold_benchmark = buy_hold_run.executed_equity

        # §14.1.1: Dynamic equal-weight theoretical index (no execution costs).
        # Portfolio formed at signal week t earns returns at t+1.
        common_start = max(
            first_actual_fill_date(result, "Strategy"),
            first_actual_fill_date(buy_hold_run, f"{benchmark_code} benchmark"),
        )
        ew_weekly = compute_equal_weight_theoretical_index(
            weekly_returns, eligible_per_week, benchmark_weeks, common_start,
        )
        if ew_weekly.empty:
            raise ValueError("Dynamic equal-weight theoretical index produced no values.")

        # Common dates: strategy ∩ buy_hold, starting from common_start
        common_dates = result.executed_equity.index.intersection(
            buy_hold_run.executed_equity.index
        )
        common_dates = common_dates[common_dates.astype(str) >= common_start]
        if common_dates.empty:
            raise ValueError("No common executable coverage across strategy and benchmarks.")

        # Align equal-weight weekly index to daily common dates via forward-fill
        result.equal_weight_benchmark = align_theoretical_to_common_dates(
            ew_weekly, common_dates,
        )

        result.executed_equity = result.executed_equity.reindex(common_dates)
        result.strategy_cumulative = align_theoretical_to_common_dates(
            result.strategy_cumulative, common_dates,
        )
        result.buy_hold_benchmark = result.buy_hold_benchmark.reindex(common_dates)
        result.cash_benchmark = pd.Series(
            1.0, index=common_dates, name="cash",
        )

    # ── Metrics — §24/§32.1 formal evaluation context ──
    if not result.executed_equity.empty:
        eval_context = EvaluationContext(
            trading_dates=tuple(pd.Timestamp(d) for d in result.executed_equity.index),
            initial_nav=1.0,
        )
        validate_equity_index(result.executed_equity, eval_context)
        initial_nav = eval_context.initial_nav
    else:
        initial_nav = 1.0

    if not result.strategy_cumulative.empty:
        result.ideal_strategy_metrics = compute_performance_metrics(
            result.strategy_cumulative, periods_per_year=244, initial_nav=initial_nav,
        )
        result.strategy_metrics = dict(result.ideal_strategy_metrics)
    if not result.equal_weight_benchmark.empty:
        result.benchmark_metrics["equal_weight"] = compute_performance_metrics(
            result.equal_weight_benchmark.dropna(), periods_per_year=244, initial_nav=initial_nav,
        )
    if not result.buy_hold_benchmark.empty:
        result.benchmark_metrics[f"buy_hold_{benchmark_code}"] = compute_performance_metrics(
            result.buy_hold_benchmark.dropna(), periods_per_year=244, initial_nav=initial_nav,
        )
    if not result.cash_benchmark.empty:
        result.benchmark_metrics["cash"] = compute_performance_metrics(
            result.cash_benchmark, periods_per_year=244, initial_nav=initial_nav,
        )

    # ── Robustness — cluster stability and bootstrap ──
    if result.cluster_history:
        result.robustness["cluster_stability"] = compute_cluster_stability(result.cluster_history)
    if not result.strategy_cumulative.empty:
        strat_returns = result.strategy_cumulative.pct_change(fill_method=None).dropna()
        if len(strat_returns) >= 24:
            result.robustness["bootstrap"] = time_block_bootstrap(strat_returns)
        else:
            result.robustness["bootstrap"] = {
                "status": "SKIPPED",
                "reason": "insufficient_common_interval",
                "observations": len(strat_returns),
                "minimum_observations": 24,
            }

    if formal_requested:
        result.strategy_metrics = compute_performance_metrics(
            result.executed_equity,
            periods_per_year=244,
            initial_nav=initial_nav,
        )
        result.execution_diagnostics["ideal_strategy_metrics"] = dict(
            result.ideal_strategy_metrics
        )
        primary_benchmark = str(effective_benchmark_policy.primary_benchmark)
        cash_benchmark = str(effective_benchmark_policy.cash_benchmark)
        equal_weight_benchmark = str(
            effective_benchmark_policy.universe_equal_weight_benchmark
        )
        result.equal_weight_benchmark = run_result.benchmark_equity.get(
            equal_weight_benchmark,
            pd.Series(dtype=float, name=equal_weight_benchmark),
        )
        result.buy_hold_benchmark = run_result.benchmark_equity.get(
            primary_benchmark,
            pd.Series(dtype=float, name=primary_benchmark),
        )
        result.cash_benchmark = run_result.benchmark_equity.get(
            cash_benchmark,
            pd.Series(dtype=float, name=cash_benchmark),
        )
        secondary_names = tuple(
            str(name)
            for name in effective_benchmark_policy.secondary_benchmarks
        )
        result.secondary_benchmarks = {
            name: run_result.benchmark_equity[name]
            for name in secondary_names
            if name in run_result.benchmark_equity
        }
        result.benchmark_metrics = {
            name: compute_performance_metrics(
                series,
                periods_per_year=244,
                initial_nav=1.0,
            )
            for name, series in {
                "equal_weight": result.equal_weight_benchmark,
                f"buy_hold_{primary_benchmark}": result.buy_hold_benchmark,
                "cash": result.cash_benchmark,
            }.items()
            if not series.empty and not series.isna().all()
        }
        result.benchmark_metrics.update({
            f"buy_hold_{name}": compute_performance_metrics(
                series,
                periods_per_year=244,
                initial_nav=1.0,
            )
            for name, series in result.secondary_benchmarks.items()
            if not series.empty and not series.isna().all()
        })

    return result
