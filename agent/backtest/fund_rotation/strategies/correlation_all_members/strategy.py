"""Correlation all-members strategy — migration baseline (design §32.2).

The session resolves point-in-time eligibility on every signal date. Clusters
remain stateful between reclustering dates, but listing/data/adjustment
eligibility is never cached as a run-wide pool.
"""

from __future__ import annotations

import pandas as pd
from pydantic import BaseModel

from backtest.fund_rotation.clustering import hierarchical_cluster
from backtest.fund_rotation.contracts import (
    DecisionKind,
    FundRotationStrategyDescriptor,
    QualityStatus,
    StrategyArtifact,
    StrategyDataRequirements,
    StrategyDecisionContext,
    StrategyDiagnostics,
    StrategyInitializationContext,
    TargetWeightDecision,
)
from backtest.fund_rotation.correlation import (
    compute_correlation_distance,
    iterative_exclude,
)
from backtest.fund_rotation.momentum import (
    compute_cluster_momentum,
)
from backtest.fund_rotation.signal_portfolio_risk import (
    ClusterCoveragePolicy,
    PortfolioPolicy,
    RiskPolicy,
    SelectionPolicy,
    SelectionState,
    compute_cluster_coverage,
    run_decision_pipeline,
    serialize_stage_records,
)
from backtest.fund_rotation.strategies.correlation_all_members.config import (
    CorrelationAllMembersConfig,
)
from backtest.fund_rotation.strategies.correlation_all_members.signals import (
    ensure_instrument_pool,
    iso_week_endings,
    signal_date_eligible,
)
from backtest.fund_rotation.universe import (
    ExclusionReason,
    ExclusionRecord,
    check_historical_eligibility,
)

DESCRIPTOR = FundRotationStrategyDescriptor(
    id="correlation_all_members",
    name="相关性聚类全成员等权",
    description=(
        "基线策略：PIT 周收益相关距离聚类，簇动量 Top-N，入选簇内全部 ETF 等权。"
        "作为迁移基准，封装首版固定流程行为。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)


class CorrelationAllMembersSession:
    """Per-run session for the baseline strategy."""

    def __init__(self, config: CorrelationAllMembersConfig) -> None:
        self._config = config
        self._week_index = 0
        self._clusters: dict[str, int] = {}
        self._selection_state: SelectionState | None = None
        self._last_recluster_week = -config.recluster_interval_weeks
        self._cluster_history: list[dict] = []
        self._exclusions: list = []

    def scheduled_dates(
        self,
        calendar: tuple[str, ...],
        simulation_start_date: str,
        evaluation_end_date: str,
    ) -> tuple[str, ...]:
        endings = iso_week_endings(calendar)
        return tuple(
            date
            for date in endings
            if simulation_start_date <= date <= evaluation_end_date
        )

    def _pool_at_signal(self, view) -> pd.DataFrame:
        cfg = self._config
        lookback_days = (
            max(cfg.min_training_weeks, cfg.correlation_lookback_weeks) + 1
        ) * 5 - 1
        return ensure_instrument_pool(
            view,
            lookback_trade_days=lookback_days,
        )

    def _eligible_at_signal(
        self,
        view,
        dim_pool: pd.DataFrame,
        signal_date: str,
    ) -> tuple[list[str], list]:
        eligible, _historical_excluded = check_historical_eligibility(
            dim_pool,
            signal_date,
        )
        kept, market_excluded = signal_date_eligible(
            view,
            eligible,
            signal_date,
        )
        return kept, market_excluded

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        cfg = self._config
        signal_date = context.signal_date
        view = context.data_view
        dim_pool = self._pool_at_signal(view)

        week_idx = self._week_index
        self._week_index += 1
        window = view.returns("weekly", cfg.correlation_lookback_weeks)

        weeks_since_recluster = week_idx - self._last_recluster_week
        reclustering = (
            weeks_since_recluster >= cfg.recluster_interval_weeks
            or not self._clusters
        )
        if reclustering:
            eligible_recluster, historical_excluded = (
                check_historical_eligibility(dim_pool, signal_date)
            )
            self._exclusions.extend(historical_excluded)
            kept_recluster, market_excluded = signal_date_eligible(
                view,
                eligible_recluster,
                signal_date,
            )
            self._exclusions.extend(market_excluded)

            valid_codes = [
                code for code in kept_recluster if code in window.columns
            ]
            if cfg.min_valid_weeks > 0 and valid_codes:
                counts = window[valid_codes].notna().sum()
                qualified_codes = [
                    code
                    for code in valid_codes
                    if counts.get(code, 0) >= cfg.min_valid_weeks
                ]
                for code in sorted(
                    set(valid_codes) - set(qualified_codes)
                ):
                    self._exclusions.append(
                        ExclusionRecord(
                            ts_code=code,
                            reason=(
                                ExclusionReason.INSUFFICIENT_VALID_WEEKS
                            ),
                            details=(
                                f"valid_weeks={int(counts.get(code, 0))}; "
                                f"required={cfg.min_valid_weeks}"
                            ),
                            signal_date=signal_date,
                        )
                    )
                valid_codes = qualified_codes
            if len(valid_codes) < cfg.k:
                raise ValueError(
                    f"Recluster at {signal_date}: only {len(valid_codes)} "
                    f"eligible ETFs, need at least K={cfg.k}."
                )
            distance = compute_correlation_distance(
                window[valid_codes],
                min_pairwise_weeks=cfg.min_pairwise_weeks,
            )
            kept_codes, pair_excluded = iterative_exclude(
                distance,
                k=cfg.k,
            )
            self._exclusions.extend(pair_excluded)
            sub_distance = distance.loc[kept_codes, kept_codes]
            self._clusters = hierarchical_cluster(
                sub_distance,
                k=cfg.k,
            )
            self._last_recluster_week = week_idx
            self._cluster_history.append(
                {
                    "week": signal_date,
                    "clusters": dict(self._clusters),
                    "num_etfs": len(self._clusters),
                }
            )

        eligible, market_excluded = self._eligible_at_signal(
            view,
            dim_pool,
            signal_date,
        )
        self._exclusions.extend(market_excluded)

        if not self._clusters:
            return TargetWeightDecision(
                decision_id=f"{signal_date}-correlation_all_members",
                signal_date=signal_date,
                action=DecisionKind.HOLD_TARGETS,
            )

        momentum_returns = window.iloc[
            -(cfg.momentum_window_weeks + 1):
        ]
        momentum = compute_cluster_momentum(
            momentum_returns,
            self._clusters,
            cfg.momentum_window_weeks,
        )
        cluster_members: dict[int, list[str]] = {}
        for code, cluster_id in self._clusters.items():
            cluster_members.setdefault(cluster_id, []).append(code)

        eligible_set = set(eligible)
        filtered_members = {
            cluster_id: [
                code for code in members if code in eligible_set
            ]
            for cluster_id, members in cluster_members.items()
        }
        all_cluster_members = {
            code for members in cluster_members.values() for code in members
        }
        coverage_reports = compute_cluster_coverage(
            weekly_returns=momentum_returns,
            cluster_members=cluster_members,
            eligible_by_week=_coverage_eligible_by_week(
                view,
                dim_pool,
                all_cluster_members,
                momentum_returns.index,
            ),
            policy=ClusterCoveragePolicy(
                min_weekly_coverage=self._config.min_weekly_coverage,
                max_low_coverage_weeks=self._config.max_low_coverage_weeks,
                minimum_valid_members=self._config.minimum_valid_members,
            ),
        )
        cycle_id = (
            str(self._cluster_history[-1]["week"])
            if self._cluster_history
            else "initial"
        )
        pipeline_decision = run_decision_pipeline(
            raw_signal_scores=dict(momentum),
            coverage_available={
                cluster_id: report.is_available
                for cluster_id, report in coverage_reports.items()
            },
            representatives=filtered_members,
            asset_metadata={
                code: {
                    "cluster_id": cluster_id,
                    "asset_class": "etf",
                }
                for cluster_id, members in filtered_members.items()
                for code in members
            },
            selection_policy=SelectionPolicy(
                top_n=cfg.top_n,
                minimum_entry_score=cfg.momentum_threshold,
            ),
            portfolio_policy=PortfolioPolicy(
                method="equal_weight_by_cluster_slot",
                target_cluster_slots=cfg.top_n,
            ),
            risk_policy=RiskPolicy(enabled=False),
            policy_versions={
                "signal": "correlation_all_members:momentum",
                "coverage": "correlation_all_members:coverage",
                "selection": "correlation_all_members:hysteresis",
                "representative": "correlation_all_members:all_members",
                "portfolio": (
                    "correlation_all_members:equal_weight_by_cluster_slot"
                ),
                "risk": "correlation_all_members:risk_identity",
            },
            selection_state=self._selection_state,
            cycle_id=cycle_id,
        )
        self._selection_state = pipeline_decision.next_selection_state
        execution_stage = pipeline_decision.stage_records[-1]
        execution_output = execution_stage.output
        targets = dict(execution_output["weights"])

        return TargetWeightDecision(
            decision_id=f"{signal_date}-correlation_all_members",
            signal_date=signal_date,
            action=DecisionKind.SET_TARGETS,
            target_weights=dict(targets),
            cash_weight=max(0.0, 1.0 - sum(targets.values())),
            quality_status=QualityStatus.VALID,
            diagnostics={
                "num_clusters": len(self._clusters),
                "eligible_codes": list(eligible),
                "signal_pipeline_stage_records": serialize_stage_records(
                    pipeline_decision.stage_records
                ),
                "signal_pipeline_reason_codes": list(
                    pipeline_decision.reason_codes
                ),
            },
        )

    def finalize(self) -> StrategyDiagnostics:
        return StrategyDiagnostics(
            artifacts=(
                StrategyArtifact(
                    role="cluster_history",
                    media_type="application/json",
                    payload=self._cluster_history,
                ),
                StrategyArtifact(
                    role="exclusions",
                    media_type="application/json",
                    payload=self._exclusions,
                ),
            )
        )


def _coverage_eligible_by_week(
    view,
    dim_pool: pd.DataFrame,
    codes: set[str],
    weeks,
) -> dict[object, set[str]]:
    list_dates = {
        str(row["ts_code"]): _week_key_to_yyyymmdd(row.get("list_date", ""))
        for _, row in dim_pool.iterrows()
    }
    eligible_by_week: dict[object, set[str]] = {}
    for week in weeks:
        week_date = _week_key_to_yyyymmdd(week)
        historically_eligible = [
            code
            for code in sorted(codes)
            if list_dates.get(code, "") <= week_date
        ]
        kept, _excluded = signal_date_eligible(
            view,
            historically_eligible,
            week_date,
        )
        eligible_by_week[week] = set(kept)
        eligible_by_week[pd.Timestamp(week_date)] = set(kept)
    return eligible_by_week


def _week_key_to_yyyymmdd(week) -> str:
    if isinstance(week, pd.Timestamp):
        return week.strftime("%Y%m%d")
    return pd.Timestamp(str(week)).strftime("%Y%m%d")


class CorrelationAllMembersStrategy:
    """Complete fund-rotation strategy plug-in (baseline)."""

    descriptor = DESCRIPTOR
    config_model = CorrelationAllMembersConfig
    artifact_roles: tuple[str, ...] = (
        "cluster_history",
        "exclusions",
    )

    def resolve_requirements(
        self,
        config: BaseModel,
    ) -> StrategyDataRequirements:
        cfg: CorrelationAllMembersConfig = config  # type: ignore[assignment]
        min_weeks = max(
            cfg.min_training_weeks,
            cfg.correlation_lookback_weeks,
        )
        warmup_trade_days = (min_weeks + 1) * 5 - 1
        return StrategyDataRequirements(
            required_datasets=(
                "fund",
                "fact_fund_adj",
                "dim_fund",
            ),
            required_fields=(
                "ts_code",
                "trade_date",
                "name",
                "list_date",
                "open",
                "close",
                "high",
                "low",
                "pre_close",
                "vol",
                "amount",
                "adj_factor",
            ),
            warmup_trade_days=warmup_trade_days,
            frequency=cfg.rebalance_freq,
            needs_benchmark=True,
        )

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> CorrelationAllMembersSession:
        return CorrelationAllMembersSession(config)  # type: ignore[arg-type]
