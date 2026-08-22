"""Round 05 challenger: require persistent positive cluster momentum.

The strategy reuses the correlation-representative session for clustering,
quality gates, representative locks, artifacts, and fixed-slot construction.
Its only signal change is requiring both the current and one-week-lagged
four-week cluster momentum to be strictly positive.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import replace

import pandas as pd
from pydantic import BaseModel

from backtest.fund_rotation.contracts import (
    DecisionKind,
    FundRotationStrategyDescriptor,
    QualityStatus,
    StrategyDataRequirements,
    StrategyDecisionContext,
    StrategyInitializationContext,
    TargetWeightDecision,
)
from backtest.fund_rotation.scoring.cluster_momentum import (
    ClusterMomentumScoreModel,
)
from backtest.fund_rotation.scoring.contracts import StrategyScore, rank_scores
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    CorrelationRepresentativeSession,
    CorrelationRepresentativeStrategy,
    _SIGNAL_INFORMATION_CUTOFF,
    _momentum_diagnostics,
    _serialize_scores,
    build_slot_weights,
)


DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r05_mom_persist",
    name="持续动量相关性代表ETF",
    description=(
        "复用相关性聚类代表ETF流程，仅要求当前及向后错一周的四周簇动量"
        "均严格为正后再进入当前动量排名。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)


def compute_persistent_scores(
    weekly_returns: pd.DataFrame,
    clusters: Mapping[str, int],
    momentum_window: int,
) -> tuple[dict[int, StrategyScore], dict[int, float | None]]:
    """Score M0 and require same-length lagged M1 to be strictly positive."""
    score_model = ClusterMomentumScoreModel()
    cluster_ids = sorted(set(clusters.values()))
    if len(weekly_returns) < momentum_window + 1:
        current_scores = score_model.from_values(
            {cluster_id: float("nan") for cluster_id in cluster_ids}
        )
        return current_scores, {cluster_id: None for cluster_id in cluster_ids}

    signal_window = weekly_returns.iloc[-(momentum_window + 1):]
    current_scores = score_model.score(
        signal_window,
        dict(clusters),
        momentum_window,
    )
    lagged_scores = score_model.score(
        signal_window.iloc[:-1],
        dict(clusters),
        momentum_window,
    )
    lagged_values = {
        cluster_id: score.value
        for cluster_id, score in lagged_scores.items()
    }
    persistent_scores: dict[int, StrategyScore] = {}
    for cluster_id, score in current_scores.items():
        lagged = lagged_scores[cluster_id]
        persistent_scores[cluster_id] = replace(
            score,
            eligible=bool(score.eligible and lagged.eligible),
        )
    return persistent_scores, lagged_values


class AiRotationR05MomPersistSession(CorrelationRepresentativeSession):
    """Isolated session with the round 05 persistence qualification gate."""

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        cfg = self._config
        signal_date = context.signal_date
        view = context.data_view
        week_index = self._week_index
        self._week_index += 1

        dim_pool = self._pool_at_signal(view)
        window = view.returns("weekly", cfg.correlation_lookback_weeks)

        from backtest.fund_rotation.strategies.correlation_all_members.signals import (
            signal_date_eligible,
        )
        from backtest.fund_rotation.universe import check_historical_eligibility

        historically_eligible, historical_excluded = (
            check_historical_eligibility(dim_pool, signal_date)
        )
        kept, market_excluded = signal_date_eligible(
            view,
            historically_eligible,
            signal_date,
        )
        self._exclusions.extend(market_excluded)
        eligible_set = set(kept)

        reclustering = (
            week_index - self._last_recluster_week
            >= cfg.recluster_interval_weeks
            or not self._clusters
        )
        if reclustering:
            self._exclusions.extend(historical_excluded)
            decision = self._recluster(
                view,
                window,
                kept,
                eligible_set,
                signal_date,
            )
            if decision is not None:
                self._log_decision(decision)
                return decision
        else:
            self._maintain_locks(
                view,
                window,
                eligible_set,
                signal_date,
            )

        momentum_window = window.iloc[-(cfg.momentum_window_weeks + 1):]
        scores, lagged_values = compute_persistent_scores(
            momentum_window,
            self._clusters,
            cfg.momentum_window_weeks,
        )
        ranked = rank_scores(
            scores,
            cluster_members=self._frozen_members,
        )
        selected = ranked[: max(cfg.top_n, 0)]
        weights, filled, vacant, cash_weight = build_slot_weights(
            selected,
            self._representatives,
            cfg.top_n,
        )
        current_values, current_unavailable = _momentum_diagnostics(
            {
                cluster_id: (
                    score.value if score.value is not None else math.nan
                )
                for cluster_id, score in scores.items()
            }
        )
        lagged_unavailable = sorted(
            cluster_id
            for cluster_id, value in lagged_values.items()
            if value is None
        )
        rejected = self._last_gate_overall.value == "REJECT"
        quality = (
            QualityStatus.VALID
            if self._last_gate_overall.value == "PASS"
            else QualityStatus.DEGRADED
        )
        decision = TargetWeightDecision(
            decision_id=f"{signal_date}-{DESCRIPTOR.id}",
            signal_date=signal_date,
            action=DecisionKind.SET_TARGETS,
            target_weights=dict(weights),
            cash_weight=cash_weight,
            reason_code=("CLUSTER_QUALITY_REJECTED" if rejected else ""),
            quality_status=quality,
            diagnostics={
                "filled_slots": filled,
                "vacant_slots": vacant,
                "momentum": current_values,
                "momentum_status": (
                    "PARTIAL"
                    if current_unavailable or lagged_unavailable
                    else "COMPLETE"
                ),
                "momentum_unavailable_clusters": current_unavailable,
                "momentum_available_cluster_count": (
                    len(scores) - len(current_unavailable)
                ),
                "momentum_total_cluster_count": len(scores),
                "lagged_momentum": {
                    str(cluster_id): value
                    for cluster_id, value in sorted(lagged_values.items())
                },
                "lagged_momentum_unavailable_clusters": lagged_unavailable,
                "lagged_momentum_window_weeks": cfg.momentum_window_weeks,
                "persistence_gate": "current_and_lagged_strictly_positive",
                "score_model": {
                    "id": ClusterMomentumScoreModel.id,
                    "label": ClusterMomentumScoreModel.label,
                    "version": ClusterMomentumScoreModel.version,
                    "direction": "HIGHER_BETTER",
                },
                "strategy_scores": _serialize_scores(scores),
                "num_clusters": len(self._clusters),
                "signal_information_cutoff": _SIGNAL_INFORMATION_CUTOFF,
            },
        )
        self._log_decision(decision, scores=scores, ranked_subjects=ranked)
        return decision

    def _recluster(
        self,
        view,
        window: pd.DataFrame,
        kept: list[str],
        eligible_set: set,
        signal_date: str,
    ) -> TargetWeightDecision | None:
        decision = super()._recluster(
            view,
            window,
            kept,
            eligible_set,
            signal_date,
        )
        if decision is None:
            return None
        return replace(
            decision,
            decision_id=f"{signal_date}-{DESCRIPTOR.id}",
        )


class AiRotationR05MomPersistStrategy:
    """Complete round 05 strategy plug-in."""

    descriptor = DESCRIPTOR
    config_model = CorrelationRepresentativeStrategy.config_model
    artifact_roles: tuple[str, ...] = (
        "cluster_history",
        "gates",
        "representatives",
        "exclusions",
        "decisions",
    )

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = CorrelationRepresentativeStrategy().describe_decision_pipeline(
            config,
        )
        pipeline["selection_rule"] = (
            f"Top {config.top_n} after current and lagged positive momentum gate"
        )
        return pipeline

    def resolve_requirements(
        self,
        config: BaseModel,
    ) -> StrategyDataRequirements:
        return CorrelationRepresentativeStrategy().resolve_requirements(config)

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> AiRotationR05MomPersistSession:
        return AiRotationR05MomPersistSession(config)  # type: ignore[arg-type]
