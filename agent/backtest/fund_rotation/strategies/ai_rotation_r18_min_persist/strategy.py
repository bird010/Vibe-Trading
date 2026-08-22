"""Round 18 challenger: weakest-window persistent momentum ranking."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
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
    id="ai_rotation_r18_min_persist", name="弱窗口持续动量相关性代表ETF",
    description=("复用相关性聚类代表ETF流程，要求当前及向后错一周的四周簇动量"
                 "均严格为正，并以两个动量窗口的较小值排名。"),
    interface_version="1.0", supported_universe=("etf",), deterministic=True,
)
_SCORE_MODEL_ID = "minimum_persistent_cluster_momentum"
_SCORE_MODEL_LABEL = "Minimum Persistent Cluster Momentum"
_SCORE_MODEL_VERSION = "1"


def _finite_or_none(value: object) -> float | None:
    try:
        number = float(value) if value is not None else math.nan
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def min_persistent_score(current_momentum: object, lagged_momentum: object) -> StrategyScore:
    current, lagged = _finite_or_none(current_momentum), _finite_or_none(lagged_momentum)
    eligible = (current is not None and lagged is not None and current > 0.0 and lagged > 0.0)
    minimum = min(current, lagged) if eligible else None
    return StrategyScore(
        value=minimum, eligible=eligible, subject_id=None,
        display_label="弱窗口持续簇动量", model_label=_SCORE_MODEL_LABEL,
        frequency="WEEKLY", scope="CLUSTER", model_id=_SCORE_MODEL_ID,
        model_version=_SCORE_MODEL_VERSION,
        components={"current_momentum": current, "lagged_momentum": lagged,
                     "min_persistent_momentum": minimum},
    )


def compute_min_persist_scores(
    values_or_returns,
    members_or_clusters,
    momentum_window: int | None = None,
    *,
    cluster_members: Mapping[int, Sequence[str]] | None = None,
):
    """Compute direct scores, or causal current/lagged momentum from weekly returns."""
    if isinstance(values_or_returns, pd.DataFrame):
        weekly_returns, clusters = values_or_returns, members_or_clusters
        cluster_ids = sorted(set(clusters.values()))
        if momentum_window is None or len(weekly_returns) < momentum_window + 1:
            current = lagged = {cid: None for cid in cluster_ids}
        else:
            model = ClusterMomentumScoreModel()
            signal_window = weekly_returns.iloc[-(momentum_window + 1):]
            current = {cid: score.value for cid, score in model.score(
                signal_window.iloc[-momentum_window:], dict(clusters), momentum_window).items()}
            lagged = {cid: score.value for cid, score in model.score(
                signal_window.iloc[:momentum_window], dict(clusters), momentum_window).items()}
        values = {cid: (current.get(cid), lagged.get(cid)) for cid in cluster_ids}
        scores = compute_min_persist_scores(values, cluster_members or {cid: () for cid in cluster_ids})
        return scores, current, lagged, {cid: score.value for cid, score in scores.items()}
    values, members = values_or_returns, members_or_clusters
    return {cid: min_persistent_score(pair[0], pair[1]) for cid, pair in values.items()}


class AiRotationR18MinPersistSession(CorrelationRepresentativeSession):
    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        cfg, signal_date, view = self._config, context.signal_date, context.data_view
        week_index, self._week_index = self._week_index, self._week_index + 1
        dim_pool = self._pool_at_signal(view)
        window = view.returns("weekly", cfg.correlation_lookback_weeks)
        from backtest.fund_rotation.strategies.correlation_all_members.signals import signal_date_eligible
        from backtest.fund_rotation.universe import check_historical_eligibility
        historically_eligible, historical_excluded = check_historical_eligibility(dim_pool, signal_date)
        kept, market_excluded = signal_date_eligible(view, historically_eligible, signal_date)
        self._exclusions.extend(market_excluded)
        eligible_set = set(kept)
        reclustering = week_index - self._last_recluster_week >= cfg.recluster_interval_weeks or not self._clusters
        if reclustering:
            self._exclusions.extend(historical_excluded)
            decision = self._recluster(view, window, kept, eligible_set, signal_date)
            if decision is not None:
                self._log_decision(decision)
                return decision
        else:
            self._maintain_locks(view, window, eligible_set, signal_date)
        scores, current_raw, lagged_raw, minimum_raw = compute_min_persist_scores(
            window.iloc[-(cfg.momentum_window_weeks + 1):], self._clusters,
            cfg.momentum_window_weeks, cluster_members=self._frozen_members)
        ranked = rank_scores(scores, cluster_members=self._frozen_members)
        weights, filled, vacant, cash_weight = build_slot_weights(
            ranked[:max(cfg.top_n, 0)], self._representatives, cfg.top_n)
        current_values, current_unavailable = _momentum_diagnostics({k: v if v is not None else math.nan for k, v in current_raw.items()})
        lagged_values, lagged_unavailable = _momentum_diagnostics({k: v if v is not None else math.nan for k, v in lagged_raw.items()})
        minimum_values, minimum_unavailable = _momentum_diagnostics({k: v if v is not None else math.nan for k, v in minimum_raw.items()})
        unavailable = sorted(set(current_unavailable) | set(lagged_unavailable) | set(minimum_unavailable))
        quality = QualityStatus.VALID if self._last_gate_overall.value == "PASS" else QualityStatus.DEGRADED
        decision = TargetWeightDecision(
            decision_id=f"{signal_date}-{DESCRIPTOR.id}", signal_date=signal_date,
            action=DecisionKind.SET_TARGETS, target_weights=dict(weights), cash_weight=cash_weight,
            reason_code="CLUSTER_QUALITY_REJECTED" if self._last_gate_overall.value == "REJECT" else "",
            quality_status=quality,
            diagnostics={
                "filled_slots": filled, "vacant_slots": vacant, "momentum": current_values,
                "momentum_status": "PARTIAL" if unavailable else "COMPLETE",
                "momentum_unavailable_clusters": unavailable,
                "momentum_available_cluster_count": len(scores) - len(unavailable),
                "momentum_total_cluster_count": len(scores), "lagged_momentum": lagged_values,
                "lagged_momentum_unavailable_clusters": lagged_unavailable,
                "min_persistent_momentum": minimum_values,
                "min_persistent_momentum_unavailable_clusters": minimum_unavailable,
                "lagged_momentum_window_weeks": cfg.momentum_window_weeks,
                "persistence_gate": "current_and_lagged_strictly_positive",
                "score_model": {"id": _SCORE_MODEL_ID, "label": _SCORE_MODEL_LABEL,
                                "version": _SCORE_MODEL_VERSION, "direction": "HIGHER_BETTER"},
                "strategy_scores": _serialize_scores(scores), "num_clusters": len(self._clusters),
                "signal_information_cutoff": _SIGNAL_INFORMATION_CUTOFF,
            },
        )
        self._log_decision(decision, scores=scores, ranked_subjects=ranked)
        return decision

    def _recluster(self, view, window, kept, eligible_set, signal_date):
        decision = super()._recluster(view, window, kept, eligible_set, signal_date)
        return replace(decision, decision_id=f"{signal_date}-{DESCRIPTOR.id}") if decision is not None else None


class AiRotationR18MinPersistStrategy:
    descriptor = DESCRIPTOR
    config_model = CorrelationRepresentativeStrategy.config_model
    artifact_roles = ("cluster_history", "gates", "representatives", "exclusions", "decisions")

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = CorrelationRepresentativeStrategy().describe_decision_pipeline(config)
        pipeline["selection_rule"] = (
            f"Top {config.top_n} clusters with strictly positive current and one-week-lagged momentum, "
            "ranked by the minimum of the two momentum windows"
        )
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        return CorrelationRepresentativeStrategy().resolve_requirements(config)

    def create_session(self, initialization: StrategyInitializationContext, config: BaseModel) -> AiRotationR18MinPersistSession:
        return AiRotationR18MinPersistSession(config)  # type: ignore[arg-type]
