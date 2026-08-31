"""Round 11 challenger: rank clusters by persistent geometric momentum.

The strategy reuses the correlation-representative implementation for
point-in-time eligibility, clustering, quality gates, representative locks,
artifacts, and fixed-slot construction. Its sole signal change is requiring
both the current and one-week-lagged four-week momentum to be strictly
positive, then ranking eligible clusters by their equal-weight geometric
growth mean.
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
from backtest.fund_rotation.scoring.cluster_momentum import ClusterMomentumScoreModel
from backtest.fund_rotation.scoring.contracts import StrategyScore, rank_scores
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    CorrelationRepresentativeSession,
    CorrelationRepresentativeStrategy,
    _SIGNAL_INFORMATION_CUTOFF,
    _cluster_state_diagnostics,
    _momentum_diagnostics,
    _serialize_scores,
    build_slot_weights,
)


DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r11_persist_geom",
    name="持续几何动量相关性代表ETF",
    description=(
        "复用相关性聚类代表ETF流程，要求当前及向后错一周的四周簇动量"
        "均严格为正，并按两者增长因子的等权几何均值排名。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)

_SCORE_MODEL_ID = "persistent_geometric_cluster_momentum"
_SCORE_MODEL_LABEL = "Persistent Geometric Cluster Momentum"
_SCORE_MODEL_VERSION = "1"


def _finite_or_none(value: object) -> float | None:
    try:
        number = float(value) if value is not None else math.nan
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def persistent_geometric_score(
    current_momentum: float | None,
    lagged_momentum: float | None,
) -> StrategyScore:
    """Build the persistent score after applying both strict-positive gates."""
    current = _finite_or_none(current_momentum)
    lagged = _finite_or_none(lagged_momentum)
    eligible = (
        current is not None
        and lagged is not None
        and current > 0.0
        and lagged > 0.0
    )
    geometric = (
        math.sqrt((1.0 + current) * (1.0 + lagged)) - 1.0
        if eligible
        else None
    )
    return StrategyScore(
        value=geometric,
        eligible=eligible,
        subject_id=None,
        display_label="持续几何簇动量",
        model_label=_SCORE_MODEL_LABEL,
        frequency="WEEKLY",
        scope="CLUSTER",
        model_id=_SCORE_MODEL_ID,
        model_version=_SCORE_MODEL_VERSION,
        components={
            "current_momentum": current,
            "lagged_momentum": lagged,
            "persistent_geometric_momentum": geometric,
        },
    )


def compute_persist_geom_scores(
    weekly_returns: pd.DataFrame,
    clusters: Mapping[str, int],
    momentum_window: int,
) -> tuple[
    dict[int, StrategyScore],
    dict[int, float | None],
    dict[int, float | None],
    dict[int, float | None],
]:
    """Compute M0, M1, and P from one frozen epoch and causal windows."""
    cluster_ids = sorted(set(clusters.values()))
    current_values: dict[int, float | None]
    lagged_values: dict[int, float | None]

    if len(weekly_returns) < momentum_window + 1:
        current_values = {cluster_id: None for cluster_id in cluster_ids}
        lagged_values = {cluster_id: None for cluster_id in cluster_ids}
    else:
        score_model = ClusterMomentumScoreModel()
        signal_window = weekly_returns.iloc[-(momentum_window + 1):]
        current_scores = score_model.score(
            signal_window.iloc[-momentum_window:],
            dict(clusters),
            momentum_window,
        )
        lagged_scores = score_model.score(
            signal_window.iloc[:momentum_window],
            dict(clusters),
            momentum_window,
        )
        current_values = {
            cluster_id: score.value
            for cluster_id, score in current_scores.items()
        }
        lagged_values = {
            cluster_id: score.value
            for cluster_id, score in lagged_scores.items()
        }

    scores = {
        cluster_id: persistent_geometric_score(
            current_values.get(cluster_id),
            lagged_values.get(cluster_id),
        )
        for cluster_id in cluster_ids
    }
    geometric_values = {
        cluster_id: score.value
        for cluster_id, score in scores.items()
    }
    return scores, current_values, lagged_values, geometric_values


class AiRotationR11PersistGeomSession(CorrelationRepresentativeSession):
    """Isolated session with round 11 persistent-geometry ranking."""

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
        scores, current_raw, lagged_raw, geometric_raw = compute_persist_geom_scores(
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
                cluster_id: value if value is not None else math.nan
                for cluster_id, value in current_raw.items()
            }
        )
        lagged_values, lagged_unavailable = _momentum_diagnostics(
            {
                cluster_id: value if value is not None else math.nan
                for cluster_id, value in lagged_raw.items()
            }
        )
        geometric_values, geometric_unavailable = _momentum_diagnostics(
            {
                cluster_id: value if value is not None else math.nan
                for cluster_id, value in geometric_raw.items()
            }
        )
        unavailable = sorted(
            set(current_unavailable)
            | set(lagged_unavailable)
            | set(geometric_unavailable)
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
                "momentum_status": "PARTIAL" if unavailable else "COMPLETE",
                "momentum_unavailable_clusters": unavailable,
                "momentum_available_cluster_count": (
                    len(scores) - len(unavailable)
                ),
                "momentum_total_cluster_count": len(scores),
                "lagged_momentum": lagged_values,
                "lagged_momentum_unavailable_clusters": lagged_unavailable,
                "geometric_momentum": geometric_values,
                "geometric_momentum_unavailable_clusters": geometric_unavailable,
                "lagged_momentum_window_weeks": cfg.momentum_window_weeks,
                "persistence_gate": "current_and_lagged_strictly_positive",
                "score_model": {
                    "id": _SCORE_MODEL_ID,
                    "label": _SCORE_MODEL_LABEL,
                    "version": _SCORE_MODEL_VERSION,
                    "direction": "HIGHER_BETTER",
                },
                "strategy_scores": _serialize_scores(scores),
                **_cluster_state_diagnostics(
                    self._clusters, self._representatives,
                ),
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


class AiRotationR11PersistGeomStrategy:
    """Complete round 11 strategy plug-in."""

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
            f"Top {config.top_n} clusters with strictly positive current and "
            "one-week-lagged momentum, ranked by their geometric growth mean"
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
    ) -> AiRotationR11PersistGeomSession:
        return AiRotationR11PersistGeomSession(config)  # type: ignore[arg-type]
