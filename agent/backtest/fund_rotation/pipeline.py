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

    # Metrics
    strategy_metrics: dict[str, float] = field(default_factory=dict)
    benchmark_metrics: dict[str, dict[str, float]] = field(default_factory=dict)
    robustness: dict[str, object] = field(default_factory=dict)

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


def _build_execution_context(
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


@dataclass
class ExecutionProfiler:
    """Lightweight inclusive/exclusive profiler for _run_execution_loop.

    Nesting: _execute_with_capacity ⊃ {compute_adv20, cash_scaling}.
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
            # Use data up to signal week for correlation
            lookback_start = max(0, week_idx - config.correlation_lookback_weeks)
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

    # §8: Trim signals to user-specified start_date.
    # Training uses extended history, but output signals/trades/equity must
    # not begin before start_date.
    _notify("GENERATING_TARGETS")
    if config.start_date:
        result.weekly_targets = {
            wk: tgts for wk, tgts in result.weekly_targets.items()
            if wk >= config.start_date
        }

    # Step 7: Execute the ideal account at the first valid open after each
    # signal.  It ignores fees, capacity, and lot constraints, but preserves
    # the real signal timing and adjusted overnight/intraday return split.
    if result.weekly_targets:
        result.strategy_cumulative = run_daily_ideal_account(
            result.weekly_targets, fund_daily, fund_adj,
        )
        if config.start_date:
            result.strategy_cumulative = result.strategy_cumulative[
                result.strategy_cumulative.index >= config.start_date
            ]

    # Step 8: Execute the continuous account before benchmark computation.
    # This ordering is also the public task-state contract (§15.2).
    _notify("EXECUTING")
    exec_ctx = _build_execution_context(fund_daily, fund_adj, config, profiler=profiler)
    _run_execution_loop(result, config, exec_ctx, profiler=profiler)

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
        _run_execution_loop(buy_hold_run, config, exec_ctx, profiler=profiler)
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

    # Step 10: Metrics
    if not result.strategy_cumulative.empty:
        result.strategy_metrics = compute_performance_metrics(
            result.strategy_cumulative, periods_per_year=244,
        )
    if not result.equal_weight_benchmark.empty:
        result.benchmark_metrics["equal_weight"] = compute_performance_metrics(
            result.equal_weight_benchmark.dropna(), periods_per_year=244,
        )
    if not result.buy_hold_benchmark.empty:
        result.benchmark_metrics["buy_hold_510300"] = compute_performance_metrics(
            result.buy_hold_benchmark.dropna(), periods_per_year=244,
        )
    if not result.cash_benchmark.empty:
        result.benchmark_metrics["cash"] = compute_performance_metrics(
            result.cash_benchmark, periods_per_year=244,
        )

    # Step 11: Robustness — cluster stability and bootstrap
    if result.cluster_history:
        result.robustness["cluster_stability"] = compute_cluster_stability(result.cluster_history)
    if not result.strategy_cumulative.empty:
        strat_returns = result.strategy_cumulative.pct_change().dropna()
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


def _first_actual_fill_date(result: PipelineResult, label: str) -> str:
    fills = [
        str(event.get("trade_date", ""))
        for event in result.trade_events
        if int(event.get("filled", 0)) > 0
        and event.get("status") in {"FILLED", "PARTIAL"}
    ]
    if not fills:
        raise ValueError(f"{label} had no executable first fill.")
    return min(fills)


def _align_theoretical_to_common_dates(
    theoretical: pd.Series, common_dates: pd.Index,
) -> pd.Series:
    """Forward-fill weekly theoretical NAV onto the executable daily interval."""
    expanded = theoretical.reindex(theoretical.index.union(common_dates)).sort_index().ffill()
    aligned = expanded.reindex(common_dates)
    if aligned.empty or aligned.isna().any():
        raise ValueError("Theoretical strategy has no coverage at common interval start.")
    aligned.name = theoretical.name
    return aligned


def _run_execution_loop(
    result: PipelineResult,
    config: FundRotationConfig,
    ctx: ExecutionContext,
    profiler: ExecutionProfiler | None = None,
) -> None:
    """§12 — Run continuous-account execution with daily equity tracking.

    Anti-look-ahead: signal generated at signal_week close → execute at first
    trade day STRICTLY AFTER signal_week (typically next Monday).

    Integrates: ADV20 capacity, participation-rate slippage, OrderManager
    residual orders, daily mark-to-market equity, and fee impacts.
    """
    if not result.weekly_targets:
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
    all_trade_dates = ctx.all_trade_dates

    # Determine execution schedule: signal_week -> first trade date strictly after
    signal_weeks_sorted = sorted(result.weekly_targets.keys())
    exec_schedule: list[tuple[str, str, dict[str, float]]] = []  # (signal_week, exec_date, targets)
    for signal_week in signal_weeks_sorted:
        exec_date = _find_first_trade_date_after(all_trade_dates, signal_week)
        if exec_date is None:
            continue
        exec_schedule.append((signal_week, exec_date, result.weekly_targets[signal_week]))

    if not exec_schedule:
        return

    # Determine the full date range for daily equity
    first_exec_date = exec_schedule[0][1]
    last_date = all_trade_dates[-1] if all_trade_dates else first_exec_date
    # Only track equity from first execution date onward
    equity_dates = [d for d in all_trade_dates if d >= first_exec_date]

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
            _t_exec = _time.perf_counter()
            rebalance_result = _execute_with_capacity(
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

        for code in executor._positions:
            factor = adj_lookup.get((trade_date, code))
            if factor is not None:
                position_adj_factor.setdefault(code, factor)

        # Holdings snapshots are daily so adjustment and retry effects are auditable.
        _t_snap = _time.perf_counter()
        daily_equity = _mark_to_market(executor, trade_date, close_lookup)
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
    result.orders.extend(_serialize_orders(order_mgr))


def _serialize_orders(order_mgr: OrderManager) -> list[dict]:
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


def _execute_with_capacity(
    executor: PortfolioExecutor,
    order_mgr: OrderManager,
    targets: dict[str, float],
    bars: dict[str, dict],
    trade_date: str,
    config: FundRotationConfig,
    adv_index: ADVIndex,
    rules: ChinaETFExecutionRules,
    profiler: ExecutionProfiler | None = None,
) -> RebalanceResult:
    """Attempt exact residual quantities with causal ADV and common cash scaling."""
    events: list[dict] = []
    pending = order_mgr.get_pending_orders()
    all_codes = sorted({o.ts_code for o in pending} | set(executor._positions))
    pre_equity = executor._compute_equity_anchor(all_codes, bars)

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
        if not rules.can_sell(bar):
            block(order, "market_blocked")
            continue
        entry_date = executor._positions.get(code, {}).get("entry_date", "")
        if not rules.can_sell_today(entry_date, trade_date):
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
            rules.lot_size, config.base_slippage_bps, config.max_slippage_bps,
        )
        filled = min(
            rules.round_sell_size(filled),
            executor._positions.get(code, {}).get("size", 0),
        )
        if filled <= 0:
            block(order, "capacity_zero", adv)
            continue
        price = rules.apply_tick(raw_price * (1 - slippage / 10000.0), -1)
        commission = rules.calc_commission(filled, price)
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
        if not rules.can_buy(bar):
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
            rules.lot_size, config.base_slippage_bps, config.max_slippage_bps,
        )
        if capacity_size <= 0:
            block(order, "capacity_zero", adv)
            continue
        candidates.append({
            "order": order, "requested": requested, "capacity_size": capacity_size,
            "raw_price": raw_price, "adv": adv,
        })

    def buy_terms(item: dict, scale: float) -> tuple[int, float, float, float, float]:
        size = rules.round_buy_size(item["capacity_size"] * scale)
        if size <= 0:
            return 0, 0.0, 0.0, 0.0, 0.0
        participation = size * item["raw_price"] / item["adv"].adv_value
        slippage = min(
            config.max_slippage_bps,
            config.base_slippage_bps + 200.0 * participation,
        )
        price = rules.apply_tick(item["raw_price"] * (1 + slippage / 10000.0), 1)
        commission = rules.calc_commission(size, price)
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


def _mark_to_market(
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


def _find_first_trade_date_after(trade_dates: list[str], target: str) -> str | None:
    """Find the first trade date STRICTLY AFTER target from a sorted list.

    Anti-look-ahead: signal at week_ending (Friday close) → execute next
    trading day (typically Monday).
    """
    for d in trade_dates:
        if d > target:
            return d
    return None
