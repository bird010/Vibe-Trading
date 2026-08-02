"""Pipeline orchestration — ties strategy modules into a complete backtest.

Data → Universe → Returns → Correlation → Clustering → Momentum →
Targets → Execution → Benchmarks → Metrics → Results
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import time as _time

import pandas as pd

logger = logging.getLogger(__name__)

from backtest.fund_rotation.config import FundRotationConfig
from backtest.fund_rotation.evaluation import (
    EvaluationContext,
    TargetSnapshot,
    schedule_targets,
    validate_equity_index,
)
from backtest.fund_rotation.universe import (
    ExclusionReason,
    ExclusionRecord,
    check_historical_eligibility,
    filter_etf_universe,
)
from backtest.fund_rotation.returns import compute_weekly_returns
from backtest.fund_rotation.correlation import compute_correlation_distance, iterative_exclude
from backtest.fund_rotation.clustering import hierarchical_cluster
from backtest.fund_rotation.momentum import compute_cluster_momentum, select_top_clusters, build_target_weights
from backtest.fund_rotation.metrics import compute_performance_metrics
from backtest.fund_rotation.robustness import compute_cluster_stability, time_block_bootstrap
from backtest.fund_rotation.executor import PortfolioExecutor, RebalanceResult
from backtest.fund_rotation.etf_rules import ChinaETFExecutionRules
from backtest.fund_rotation.capacity import compute_adv20, apply_capacity_and_slippage, ADVIndex
from backtest.fund_rotation.orders import OrderManager, AttemptStatus
from backtest.fund_rotation.share_adjustment import adjust_shares_for_factor_change
from backtest.fund_rotation.ideal_executor import run_daily_ideal_account
from backtest.fund_rotation.benchmarks import compute_equal_weight_theoretical_index


# Execution & valuation live in the common execution module (Phase 2 Task 2);
# the pipeline imports them under their legacy private names for compatibility.
from backtest.fund_rotation.execution import (
    ExecutionContext,
    ExecutionProfiler,
    PipelineResult,
    align_theoretical_to_common_dates as _align_theoretical_to_common_dates,
    build_execution_context as _build_execution_context,
    execute_with_capacity as _execute_with_capacity,
    first_actual_fill_date as _first_actual_fill_date,
    mark_to_market as _mark_to_market,
    run_execution_loop as _run_execution_loop,
    serialize_orders as _serialize_orders,
)


def run_signal_pipeline(
    config: FundRotationConfig,
    fund_daily: pd.DataFrame,
    fund_adj: pd.DataFrame,
    dim_fund: pd.DataFrame,
    trade_dates: list[str] | None = None,
    stage_callback: callable | None = None,
    profiler: ExecutionProfiler | None = None,
) -> PipelineResult:
    """Run the signal generation pipeline (no execution).

    This produces weekly target weights and benchmarks from raw data.
    Execution is handled separately by the PortfolioExecutor.

    Args:
        config: Backtest parameters.
        fund_daily: Columns [ts_code, trade_date, close, vol, amount].
        fund_adj: Columns [ts_code, trade_date, adj_factor].
        dim_fund: Columns [ts_code, name, list_date].
        trade_dates: Optional sorted list of all market trading dates.
        stage_callback: Optional callable(stage_name: str) for progress reporting.

    Returns:
        PipelineResult with targets, benchmarks, and metrics.
    """
    def _notify(stage: str) -> None:
        if stage_callback:
            stage_callback(stage)

    result = PipelineResult()

    # Step 1: Static ETF filter
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
    etf_codes = pool_codes
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

    # Step 2: Compute weekly returns (ETF pool only)
    _notify("PREPARING_RETURNS")
    end_date = config.end_date or fund_daily["trade_date"].max()
    weekly_returns = compute_weekly_returns(fund_daily, fund_adj, as_of_date=end_date)
    if weekly_returns.empty:
        raise ValueError("Weekly returns computation produced empty result (check fund_daily/fund_adj data)")

    all_weeks = list(weekly_returns.index)
    result.num_weeks = len(all_weeks)

    # Step 3: Determine rebalance weeks (weekly)
    # min_weeks_needed = max(min_training_weeks, correlation_lookback_weeks)
    # The momentum window is a subset of the lookback, not additive.
    # Design confirms 52 weeks (not 56).
    min_weeks_needed = max(config.min_training_weeks, config.correlation_lookback_weeks)
    if len(all_weeks) < min_weeks_needed:
        raise ValueError(
            f"Insufficient history: {len(all_weeks)} weeks available, "
            f"need at least {min_weeks_needed} (training={config.min_training_weeks}, "
            f"correlation_lookback={config.correlation_lookback_weeks})"
        )

    # Step 4: Dynamic clustering at recluster intervals
    _notify("CLUSTERING")
    current_clusters: dict[str, int] = {}
    last_recluster_week = -config.recluster_interval_weeks  # Force first recluster
    recluster_count = 0
    eligible_per_week: dict[str, list[str]] = {}  # §10: for dynamic equal-weight benchmark
    valid_close_keys = set(
        fund_daily.loc[
            pd.to_numeric(fund_daily["close"], errors="coerce").gt(0),
            ["trade_date", "ts_code"],
        ].astype(str).itertuples(index=False, name=None)
    )
    valid_adj_keys = set(
        fund_adj.loc[
            pd.to_numeric(fund_adj["adj_factor"], errors="coerce").gt(0),
            ["trade_date", "ts_code"],
        ].astype(str).itertuples(index=False, name=None)
    )

    def signal_date_eligible(codes: list[str], signal_date: str) -> tuple[list[str], list[ExclusionRecord]]:
        kept: list[str] = []
        rejected: list[ExclusionRecord] = []
        for code in codes:
            key = (str(signal_date), str(code))
            if key not in valid_close_keys:
                rejected.append(ExclusionRecord(
                    ts_code=code, reason=ExclusionReason.NO_VALID_CLOSE,
                    details="missing or non-positive close on signal date",
                    signal_date=signal_date,
                ))
            elif key not in valid_adj_keys:
                rejected.append(ExclusionRecord(
                    ts_code=code, reason=ExclusionReason.INSUFFICIENT_ADJ_COVERAGE,
                    details="missing or non-positive adj_factor on signal date",
                    signal_date=signal_date,
                ))
            else:
                kept.append(code)
        return kept, rejected

    for week_idx in range(min_weeks_needed, len(all_weeks)):
        signal_week = all_weeks[week_idx]
        weeks_since_recluster = week_idx - last_recluster_week

        # Recluster if needed
        if weeks_since_recluster >= config.recluster_interval_weeks or not current_clusters:
            # §32.1: the correlation window must contain exactly
            # correlation_lookback_weeks weekly-return observations, ending at
            # the signal week (inclusive) and never including future data.
            # Weekly returns need N+1 weekend prices for N returns, so the
            # window starts at week_idx - lookback + 1 (the first valid return
            # row), giving exactly `correlation_lookback_weeks` rows.
            lookback_start = max(0, week_idx - config.correlation_lookback_weeks + 1)
            window_returns = weekly_returns.iloc[lookback_start:week_idx + 1]

            # Historical eligibility at this signal date
            eligible_codes, excluded = check_historical_eligibility(etf_pool, signal_date=signal_week)
            result.exclusions.extend(excluded)
            eligible_codes, market_excluded = signal_date_eligible(eligible_codes, signal_week)
            result.exclusions.extend(market_excluded)

            # Filter to eligible codes present in returns
            valid_codes = [c for c in eligible_codes if c in window_returns.columns]

            # §9: min_valid_weeks gate — exclude ETFs with insufficient non-NaN weeks
            if config.min_valid_weeks > 0:
                valid_week_counts = window_returns[valid_codes].notna().sum()
                qualified_codes = [
                    c for c in valid_codes
                    if valid_week_counts.get(c, 0) >= config.min_valid_weeks
                ]
                for code in sorted(set(valid_codes) - set(qualified_codes)):
                    result.exclusions.append(ExclusionRecord(
                        ts_code=code,
                        reason=ExclusionReason.INSUFFICIENT_VALID_WEEKS,
                        details=(
                            f"valid_weeks={int(valid_week_counts.get(code, 0))}; "
                            f"required={config.min_valid_weeks}"
                        ),
                        signal_date=signal_week,
                    ))
                valid_codes = qualified_codes

            if len(valid_codes) < config.k:
                raise ValueError(
                    f"Recluster at {signal_week}: only {len(valid_codes)} eligible ETFs, "
                    f"need at least K={config.k}. Task failed per §18.1."
                )

            sub_returns = window_returns[valid_codes]

            # Correlation distance
            dist = compute_correlation_distance(sub_returns, min_pairwise_weeks=config.min_pairwise_weeks)

            # Iterative exclusion — raises ValueError if remaining < K
            kept_codes, pair_excluded = iterative_exclude(dist, k=config.k)
            result.exclusions.extend(pair_excluded)

            # Cluster
            sub_dist = dist.loc[kept_codes, kept_codes]
            current_clusters = hierarchical_cluster(sub_dist, k=config.k)
            last_recluster_week = week_idx
            recluster_count += 1

            result.cluster_history.append({
                "week": signal_week,
                "clusters": dict(current_clusters),
                "num_etfs": len(current_clusters),
            })

        if not current_clusters:
            continue

        # Step 5: Compute momentum and select clusters
        lookback_start = max(0, week_idx - config.momentum_window_weeks)
        momentum_returns = weekly_returns.iloc[lookback_start:week_idx + 1]

        momentum = compute_cluster_momentum(momentum_returns, current_clusters, config.momentum_window_weeks)

        # Build cluster members map
        cluster_members: dict[int, list[str]] = {}
        for code, cid in current_clusters.items():
            cluster_members.setdefault(cid, []).append(code)

        selected = select_top_clusters(
            momentum, top_n=config.top_n, threshold=config.momentum_threshold,
            cluster_members=cluster_members,
        )

        # Step 6: Build target weights
        # Filter members to those eligible at this signal date
        eligible_at_signal, _ = check_historical_eligibility(etf_pool, signal_date=signal_week)
        eligible_at_signal, market_excluded = signal_date_eligible(eligible_at_signal, signal_week)
        result.exclusions.extend(market_excluded)
        eligible_set = set(eligible_at_signal)
        filtered_members = {
            cid: [c for c in members if c in eligible_set]
            for cid, members in cluster_members.items()
        }

        targets = build_target_weights(selected, filtered_members, top_n=config.top_n)
        result.weekly_targets[signal_week] = targets

        # §10: Track eligible ETFs at each signal week for dynamic benchmark
        eligible_per_week[signal_week] = [
            c for c in eligible_at_signal if c in weekly_returns.columns
        ]

    result.num_reclusters = recluster_count
    result.num_etfs_used = len(set(
        code for targets in result.weekly_targets.values() for code in targets
    ))

    # §8/§24: Trim output signals to user-specified start_date, but preserve the
    # full target set (including the last pre-evaluation signal) for execution so
    # the initial position is built at the first evaluation trading day.
    _notify("GENERATING_TARGETS")
    execution_targets = dict(result.weekly_targets)
    if config.start_date:
        result.weekly_targets = {
            wk: tgts for wk, tgts in result.weekly_targets.items()
            if wk >= config.start_date
        }

    # Evaluation trading calendar (within [start_date, end_date]) shared by the
    # ideal and real executors so a pre-evaluation signal activates at the first
    # evaluation day (§24).
    all_dates_sorted = sorted(fund_daily["trade_date"].astype(str).unique())
    evaluation_dates = [
        d for d in all_dates_sorted
        if (not config.start_date or d >= config.start_date)
        and (not config.end_date or d <= config.end_date)
    ]

    # Step 7: Execute the ideal account at the first valid open after each
    # signal.  It ignores fees, capacity, and lot constraints, but preserves
    # the real signal timing and adjusted overnight/intraday return split.
    if execution_targets:
        result.strategy_cumulative = run_daily_ideal_account(
            execution_targets, fund_daily, fund_adj, evaluation_dates=evaluation_dates,
        )
        if config.start_date:
            result.strategy_cumulative = result.strategy_cumulative[
                result.strategy_cumulative.index >= config.start_date
            ]
    else:
        # No targets: the theoretical strategy holds cash (initial NAV) over the
        # full evaluation interval, so metrics are defined and zero (§32.1).
        result.strategy_cumulative = pd.Series(
            1.0, index=list(evaluation_dates), name="theoretical_strategy",
        )

    # Step 8: Execute the continuous account before benchmark computation.
    # This ordering is also the public task-state contract (§15.2).
    _notify("EXECUTING")
    exec_ctx = _build_execution_context(fund_daily, fund_adj, config, profiler=profiler)
    _run_execution_loop(result, config, exec_ctx, profiler=profiler,
                        execution_targets=execution_targets,
                        evaluation_dates=evaluation_dates)

    # Step 9: Benchmarks — §14.1
    _notify("COMPUTING_BENCHMARKS")
    benchmark_weeks = sorted(result.weekly_targets)
    local_codes = set(fund_daily["ts_code"].astype(str))
    has_510300 = "510300.SH" in local_codes

    if benchmark_weeks and not has_510300:
        raise ValueError("510300.SH benchmark data is missing from the local ETF dataset.")

    if benchmark_weeks and has_510300:
        first_week = benchmark_weeks[0]
        buy_hold_run = PipelineResult(
            weekly_targets={first_week: {"510300.SH": 1.0}},
        )
        _run_execution_loop(buy_hold_run, config, exec_ctx, profiler=profiler,
                            evaluation_dates=evaluation_dates)
        result.buy_hold_benchmark = buy_hold_run.executed_equity

        # §14.1.1: Dynamic equal-weight theoretical index (no execution costs).
        # Portfolio formed at signal week t earns returns at t+1.
        common_start = max(
            _first_actual_fill_date(result, "Strategy"),
            _first_actual_fill_date(buy_hold_run, "510300.SH benchmark"),
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
        result.equal_weight_benchmark = _align_theoretical_to_common_dates(
            ew_weekly, common_dates,
        )

        result.executed_equity = result.executed_equity.reindex(common_dates)
        result.strategy_cumulative = _align_theoretical_to_common_dates(
            result.strategy_cumulative, common_dates,
        )
        result.buy_hold_benchmark = result.buy_hold_benchmark.reindex(common_dates)
        result.cash_benchmark = pd.Series(
            1.0, index=common_dates, name="cash",
        )

    # Step 10: Metrics — §24/§32.1 formal evaluation context. The initial_nav
    # anchor (1.0) is the pre-interval principal; the first evaluation day's
    # return is measured against it. The strict index check validates the equity
    # series is well-formed (ordered, unique, within the evaluation interval).
    # (Task 7 extends the equity to the full [start_date, end_date] calendar.)
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
        result.strategy_metrics = compute_performance_metrics(
            result.strategy_cumulative, periods_per_year=244, initial_nav=initial_nav,
        )
    if not result.equal_weight_benchmark.empty:
        result.benchmark_metrics["equal_weight"] = compute_performance_metrics(
            result.equal_weight_benchmark.dropna(), periods_per_year=244, initial_nav=initial_nav,
        )
    if not result.buy_hold_benchmark.empty:
        result.benchmark_metrics["buy_hold_510300"] = compute_performance_metrics(
            result.buy_hold_benchmark.dropna(), periods_per_year=244, initial_nav=initial_nav,
        )
    if not result.cash_benchmark.empty:
        result.benchmark_metrics["cash"] = compute_performance_metrics(
            result.cash_benchmark, periods_per_year=244, initial_nav=initial_nav,
        )

    # Step 11: Robustness — cluster stability and bootstrap
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

    return result


