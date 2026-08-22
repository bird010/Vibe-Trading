"""Round 15 challenger: recent-weighted persistent geometric momentum."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import replace

import pandas as pd
from pydantic import BaseModel

from backtest.fund_rotation.contracts import (
    DecisionKind, FundRotationStrategyDescriptor, QualityStatus,
    StrategyDataRequirements, StrategyDecisionContext,
    StrategyInitializationContext, TargetWeightDecision,
)
from backtest.fund_rotation.scoring.cluster_momentum import ClusterMomentumScoreModel
from backtest.fund_rotation.scoring.contracts import StrategyScore, rank_scores
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    CorrelationRepresentativeSession, CorrelationRepresentativeStrategy,
    _SIGNAL_INFORMATION_CUTOFF, _momentum_diagnostics, _serialize_scores,
    build_slot_weights,
)

DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r15_weighted_persist", name="近期加权持续动量相关性代表ETF",
    description="复用相关性聚类代表ETF流程，要求当前及向后错一周的四周簇动量均严格为正，并以近期加权几何增长估计排名。",
    interface_version="1.0", supported_universe=("etf",), deterministic=True,
)
_WEIGHTS = (0.4, 0.3, 0.2, 0.1)
_SCORE_MODEL_ID = "weighted_persistent_geometric_cluster_momentum"
_SCORE_MODEL_LABEL = "Recency-Weighted Persistent Geometric Cluster Momentum"
_SCORE_MODEL_VERSION = "1"


def _finite_or_none(value: object) -> float | None:
    try:
        number = float(value) if value is not None else math.nan
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def weighted_persistent_score(current_momentum: float | None, lagged_momentum: float | None) -> StrategyScore:
    current, lagged = _finite_or_none(current_momentum), _finite_or_none(lagged_momentum)
    eligible = current is not None and lagged is not None and current > 0 and lagged > 0
    geometric = math.sqrt((1 + current) * (1 + lagged)) - 1 if eligible else None
    return StrategyScore(
        value=geometric, eligible=eligible, subject_id=None,
        display_label="近期加权持续几何簇动量", model_label=_SCORE_MODEL_LABEL,
        frequency="WEEKLY", scope="CLUSTER", model_id=_SCORE_MODEL_ID,
        model_version=_SCORE_MODEL_VERSION,
        components={"current_momentum": current, "lagged_momentum": lagged,
                     "weighted_persistent_momentum": geometric},
    )


def _weighted_cluster_values(frame: pd.DataFrame, clusters: Mapping[str, int], window: int) -> dict[int, float | None]:
    cluster_ids = sorted(set(clusters.values()))
    if len(frame) != window:
        return {cid: None for cid in cluster_ids}
    # The public dataframe is oldest -> newest; fixed weights are newest -> oldest.
    newest_first = frame.iloc[::-1]
    model = ClusterMomentumScoreModel()
    weekly = [model.score(newest_first.iloc[i:i + 1], dict(clusters), 1) for i in range(window)]
    values: dict[int, float | None] = {}
    for cid in cluster_ids:
        series = [weekly[i].get(cid).value for i in range(window)]
        if any(v is None or not math.isfinite(float(v)) or 1 + float(v) <= 0 for v in series):
            values[cid] = None
        else:
            values[cid] = math.exp(sum(w * math.log1p(float(v)) for w, v in zip(_WEIGHTS, series))) - 1
    return values


def compute_weighted_persist_scores(
    weekly_returns: pd.DataFrame, clusters: Mapping[str, int], momentum_window: int,
) -> tuple[dict[int, StrategyScore], dict[int, float | None], dict[int, float | None], dict[int, float | None]]:
    cluster_ids = sorted(set(clusters.values()))
    if momentum_window != 4 or len(weekly_returns) < momentum_window + 1:
        current = lagged = {cid: None for cid in cluster_ids}
    else:
        signal_window = weekly_returns.iloc[-(momentum_window + 1):]
        current = _weighted_cluster_values(signal_window.iloc[-momentum_window:], clusters, momentum_window)
        lagged = _weighted_cluster_values(signal_window.iloc[:momentum_window], clusters, momentum_window)
    scores = {cid: weighted_persistent_score(current.get(cid), lagged.get(cid)) for cid in cluster_ids}
    return scores, current, lagged, {cid: score.value for cid, score in scores.items()}


class AiRotationR15WeightedPersistSession(CorrelationRepresentativeSession):
    """Session retaining the shared representative, quality, and execution contract."""

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        signal_date, view = context.signal_date, context.data_view
        week_index = self._week_index
        self._week_index += 1
        dim_pool = self._pool_at_signal(view)
        window = view.returns("weekly", self._config.correlation_lookback_weeks)
        from backtest.fund_rotation.strategies.correlation_all_members.signals import signal_date_eligible
        from backtest.fund_rotation.universe import check_historical_eligibility
        historically_eligible, historical_excluded = check_historical_eligibility(dim_pool, signal_date)
        kept, market_excluded = signal_date_eligible(view, historically_eligible, signal_date)
        self._exclusions.extend(market_excluded)
        eligible_set = set(kept)
        reclustering = week_index - self._last_recluster_week >= self._config.recluster_interval_weeks or not self._clusters
        if reclustering:
            self._exclusions.extend(historical_excluded)
            decision = self._recluster(view, window, kept, eligible_set, signal_date)
            if decision is not None:
                self._log_decision(decision)
                return decision
        else:
            self._maintain_locks(view, window, eligible_set, signal_date)
        scores, current_raw, lagged_raw, weighted_raw = compute_weighted_persist_scores(
            window.iloc[-(self._config.momentum_window_weeks + 1):], self._clusters,
            self._config.momentum_window_weeks,
        )
        ranked = rank_scores(scores, cluster_members=self._frozen_members)
        selected = ranked[:max(self._config.top_n, 0)]
        weights, filled, vacant, cash_weight = build_slot_weights(selected, self._representatives, self._config.top_n)
        def diag(values):
            return _momentum_diagnostics({cid: value if value is not None else math.nan for cid, value in values.items()})
        current_values, current_unavailable = diag(current_raw)
        lagged_values, lagged_unavailable = diag(lagged_raw)
        weighted_values, weighted_unavailable = diag(weighted_raw)
        unavailable = sorted(set(current_unavailable) | set(lagged_unavailable) | set(weighted_unavailable))
        rejected = self._last_gate_overall.value == "REJECT"
        decision = TargetWeightDecision(
            decision_id=f"{signal_date}-{DESCRIPTOR.id}", signal_date=signal_date,
            action=DecisionKind.SET_TARGETS, target_weights=dict(weights), cash_weight=cash_weight,
            reason_code="CLUSTER_QUALITY_REJECTED" if rejected else "",
            quality_status=QualityStatus.VALID if self._last_gate_overall.value == "PASS" else QualityStatus.DEGRADED,
            diagnostics={
                "filled_slots": filled, "vacant_slots": vacant, "momentum": current_values,
                "momentum_status": "PARTIAL" if unavailable else "COMPLETE",
                "momentum_unavailable_clusters": unavailable,
                "momentum_available_cluster_count": len(scores) - len(unavailable),
                "momentum_total_cluster_count": len(scores), "lagged_momentum": lagged_values,
                "lagged_momentum_unavailable_clusters": lagged_unavailable,
                "weighted_momentum": weighted_values,
                "weighted_momentum_unavailable_clusters": weighted_unavailable,
                "weighted_momentum_weights": list(_WEIGHTS),
                "lagged_momentum_window_weeks": self._config.momentum_window_weeks,
                "persistence_gate": "current_and_lagged_strictly_positive",
                "score_model": {"id": _SCORE_MODEL_ID, "label": _SCORE_MODEL_LABEL, "version": _SCORE_MODEL_VERSION, "direction": "HIGHER_BETTER"},
                "strategy_scores": _serialize_scores(scores), "num_clusters": len(self._clusters),
                "signal_information_cutoff": _SIGNAL_INFORMATION_CUTOFF,
            },
        )
        self._log_decision(decision, scores=scores, ranked_subjects=ranked)
        return decision

    def _recluster(self, view, window, kept, eligible_set, signal_date):
        decision = super()._recluster(view, window, kept, eligible_set, signal_date)
        return replace(decision, decision_id=f"{signal_date}-{DESCRIPTOR.id}") if decision is not None else None


class AiRotationR15WeightedPersistStrategy:
    descriptor = DESCRIPTOR
    config_model = CorrelationRepresentativeStrategy.config_model
    artifact_roles = ("cluster_history", "gates", "representatives", "exclusions", "decisions")

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = CorrelationRepresentativeStrategy().describe_decision_pipeline(config)
        pipeline["selection_rule"] = (
            f"Top {config.top_n} clusters with strictly positive current and one-week-lagged momentum, "
            "ranked by geometric mean of 0.4/0.3/0.2/0.1 recent-weighted four-week growth"
        )
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        return CorrelationRepresentativeStrategy().resolve_requirements(config)

    def create_session(self, initialization: StrategyInitializationContext, config: BaseModel) -> AiRotationR15WeightedPersistSession:
        return AiRotationR15WeightedPersistSession(config)  # type: ignore[arg-type]
