"""Round 07 challenger: apply persistence only to the third slot.

The strategy reuses the correlation-representative session for clustering,
quality gates, representative locks, artifacts, and fixed-slot construction.
Its only signal change is requiring one-week-lagged four-week momentum for the
third current-momentum slot; the first two slots retain Champion semantics.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
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
    id="ai_rotation_r07_tail_persist",
    name="尾槽持续性相关性代表ETF",
    description=(
        "复用相关性聚类代表ETF流程，前两个当前动量槽保持 Champion 语义，"
        "仅要求第三个边界槽的一周滞后四周簇动量严格为正。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)


def compute_tail_persist_scores(
    weekly_returns: pd.DataFrame,
    clusters: Mapping[str, int],
    momentum_window: int,
) -> tuple[dict[int, StrategyScore], dict[int, float | None]]:
    """Return Champion current scores plus one-week-lagged diagnostic values."""
    score_model = ClusterMomentumScoreModel()
    cluster_ids = sorted(set(clusters.values()))
    if len(weekly_returns) < momentum_window:
        current_scores = score_model.from_values(
            {cluster_id: float("nan") for cluster_id in cluster_ids}
        )
        return current_scores, {cluster_id: None for cluster_id in cluster_ids}

    current_scores = score_model.score(
        weekly_returns.iloc[-momentum_window:],
        dict(clusters),
        momentum_window,
    )
    if len(weekly_returns) < momentum_window + 1:
        return current_scores, {cluster_id: None for cluster_id in cluster_ids}

    lagged_scores = score_model.score(
        weekly_returns.iloc[-(momentum_window + 1):-1],
        dict(clusters),
        momentum_window,
    )
    return current_scores, {
        cluster_id: score.value
        for cluster_id, score in lagged_scores.items()
    }


def select_tail_persist_clusters(
    ranked: Sequence[int],
    lagged_values: Mapping[int, float | None],
    top_n: int,
) -> list[int]:
    """Keep current top-two semantics and qualify only the final slot."""
    slot_count = max(top_n, 0)
    if slot_count == 0:
        return []

    core_count = max(slot_count - 1, 0)
    selected = list(ranked[:core_count])
    if len(selected) == slot_count:
        return selected

    for cluster_id in ranked[core_count:]:
        value = lagged_values.get(cluster_id)
        try:
            qualifies = (
                value is not None
                and math.isfinite(float(value))
                and value > 0
            )
        except (TypeError, ValueError):
            qualifies = False
        if qualifies:
            selected.append(cluster_id)
            break
    return selected[:slot_count]


class AiRotationR07TailPersistSession(CorrelationRepresentativeSession):
    """Isolated session with persistence applied only to the tail slot."""

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
            self._maintain_locks(view, window, eligible_set, signal_date)

        momentum_window = window.iloc[-(cfg.momentum_window_weeks + 1):]
        scores, lagged_values = compute_tail_persist_scores(
            momentum_window,
            self._clusters,
            cfg.momentum_window_weeks,
        )
        ranked = rank_scores(
            scores,
            cluster_members=self._frozen_members,
        )
        selected = select_tail_persist_clusters(
            ranked,
            lagged_values,
            cfg.top_n,
        )
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
            if value is None or not math.isfinite(float(value))
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
                "tail_persistence_gate": "third_slot_lagged_strictly_positive",
                "selected_clusters": selected,
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


class AiRotationR07TailPersistStrategy:
    """Complete round 07 strategy plug-in."""

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
            f"Champion Top {config.top_n - 1} plus the highest-ranked current-positive "
            "tail cluster with strictly positive one-week-lagged momentum"
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
    ) -> AiRotationR07TailPersistSession:
        return AiRotationR07TailPersistSession(config)  # type: ignore[arg-type]

