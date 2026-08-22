"""Round 06 challenger: retain one positive rank-4 cluster in an epoch.

The strategy reuses the correlation-representative session for clustering,
quality gates, representative locks, artifacts, and fixed-slot construction.
Its only signal change is a fixed one-slot exit buffer for the prior selected
cluster set while the clustering epoch remains unchanged.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
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
from backtest.fund_rotation.scoring.cluster_momentum import ClusterMomentumScoreModel
from backtest.fund_rotation.scoring.contracts import rank_scores
from backtest.fund_rotation.strategies.correlation_all_members.signals import (
    signal_date_eligible,
)
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    CorrelationRepresentativeSession,
    CorrelationRepresentativeStrategy,
    _SIGNAL_INFORMATION_CUTOFF,
    _momentum_diagnostics,
    _serialize_scores,
    build_slot_weights,
)
from backtest.fund_rotation.universe import check_historical_eligibility


DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r06_rank_buffer",
    name="排名缓冲相关性代表ETF",
    description=(
        "复用相关性聚类代表ETF流程，在同一聚类 epoch 内保留当前仍为正动量且"
        "排名不超过 Top-N+1 的上一期入选簇；重聚类立即重置。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)

_EXIT_BUFFER_CLUSTERS = 1


def select_rank_buffered_clusters(
    ranked: Sequence[int],
    *,
    previous_selected: Sequence[int],
    top_n: int,
    epoch_reset: bool,
) -> tuple[list[int], list[int]]:
    """Select current ranks while retaining eligible prior slots in one epoch."""
    ranked_list = list(ranked)
    if epoch_reset:
        return ranked_list[: max(top_n, 0)], []

    rank_by_cluster = {
        cluster_id: rank
        for rank, cluster_id in enumerate(ranked_list, start=1)
    }
    retained = sorted(
        {
            cluster_id
            for cluster_id in previous_selected
            if cluster_id in rank_by_cluster
            and rank_by_cluster[cluster_id] <= top_n + _EXIT_BUFFER_CLUSTERS
        },
        key=rank_by_cluster.__getitem__,
    )
    retained_set = set(retained)
    fillers = [cluster_id for cluster_id in ranked_list if cluster_id not in retained_set]
    return (retained + fillers)[: max(top_n, 0)], retained


class AiRotationR06RankBufferSession(CorrelationRepresentativeSession):
    """Isolated session with a fixed one-cluster rank exit buffer."""

    def __init__(self, config) -> None:
        super().__init__(config)
        self._previous_selected_clusters: list[int] = []

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        cfg = self._config
        signal_date = context.signal_date
        view = context.data_view
        week_index = self._week_index
        self._week_index += 1

        dim_pool = self._pool_at_signal(view)
        window = view.returns("weekly", cfg.correlation_lookback_weeks)
        historically_eligible, historical_excluded = check_historical_eligibility(
            dim_pool,
            signal_date,
        )
        kept, market_excluded = signal_date_eligible(
            view,
            historically_eligible,
            signal_date,
        )
        self._exclusions.extend(market_excluded)
        eligible_set = set(kept)

        reclustering = (
            week_index - self._last_recluster_week >= cfg.recluster_interval_weeks
            or not self._clusters
        )
        if reclustering:
            self._previous_selected_clusters = []
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
            self._maintain_locks(view, window, eligible_set, signal_date)

        momentum_window = window.iloc[-(cfg.momentum_window_weeks + 1):]
        score_model = ClusterMomentumScoreModel()
        scores = score_model.score(
            momentum_window,
            self._clusters,
            cfg.momentum_window_weeks,
        )
        ranked = rank_scores(scores, cluster_members=self._frozen_members)
        selected, retained = select_rank_buffered_clusters(
            ranked,
            previous_selected=self._previous_selected_clusters,
            top_n=cfg.top_n,
            epoch_reset=reclustering,
        )
        rank_by_cluster = {
            str(cluster_id): rank
            for rank, cluster_id in enumerate(ranked, start=1)
        }
        self._previous_selected_clusters = list(selected)

        weights, filled, vacant, cash_weight = build_slot_weights(
            selected,
            self._representatives,
            cfg.top_n,
        )
        momentum_values, unavailable_clusters = _momentum_diagnostics(
            {
                cluster_id: score.value if score.value is not None else math.nan
                for cluster_id, score in scores.items()
            }
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
                "momentum": momentum_values,
                "momentum_status": "PARTIAL" if unavailable_clusters else "COMPLETE",
                "momentum_unavailable_clusters": unavailable_clusters,
                "momentum_available_cluster_count": (
                    len(scores) - len(unavailable_clusters)
                ),
                "momentum_total_cluster_count": len(scores),
                "ranked_clusters": list(ranked),
                "rank_buffer_clusters": list(retained),
                "rank_buffer_candidate_ranks": rank_by_cluster,
                "rank_buffer_size": _EXIT_BUFFER_CLUSTERS,
                "rank_buffer_epoch_reset": reclustering,
                "score_model": {
                    "id": score_model.id,
                    "label": score_model.label,
                    "version": score_model.version,
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


class AiRotationR06RankBufferStrategy:
    """Complete round 06 strategy plug-in."""

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
            f"Top {config.top_n} with one-cluster same-epoch exit buffer"
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
    ) -> AiRotationR06RankBufferSession:
        return AiRotationR06RankBufferSession(config)  # type: ignore[arg-type]
