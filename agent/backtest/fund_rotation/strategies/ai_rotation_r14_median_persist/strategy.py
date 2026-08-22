"""Round 14 challenger: median persistent momentum for representative ETFs."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import replace

import numpy as np
import pandas as pd
from pydantic import BaseModel

from backtest.fund_rotation.contracts import (
    DecisionKind, FundRotationStrategyDescriptor, QualityStatus,
    StrategyDataRequirements, StrategyDecisionContext,
    StrategyInitializationContext, TargetWeightDecision,
)
from backtest.fund_rotation.scoring.contracts import StrategyScore, rank_scores
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    CorrelationRepresentativeSession, CorrelationRepresentativeStrategy,
    _SIGNAL_INFORMATION_CUTOFF, _momentum_diagnostics, _serialize_scores,
    build_slot_weights,
)

DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r14_median_persist", name="持续中位数动量相关性代表ETF",
    description=("复用相关性聚类代表ETF流程，要求当前及向后错一周的四周簇动量"
                 "均严格为正，并以四周簇收益中位数的几何持续得分排名。"),
    interface_version="1.0", supported_universe=("etf",), deterministic=True,
)
_SCORE_MODEL_ID = "median_persistent_cluster_momentum"
_SCORE_MODEL_LABEL = "Median Persistent Cluster Momentum"
_SCORE_MODEL_VERSION = "1"


def _finite_or_none(value: object) -> float | None:
    try:
        number = float(value) if value is not None else math.nan
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def median_persistent_score(current_momentum: float | None, lagged_momentum: float | None) -> StrategyScore:
    current, lagged = _finite_or_none(current_momentum), _finite_or_none(lagged_momentum)
    eligible = (current is not None and lagged is not None and current > 0.0 and lagged > 0.0)
    geometric = math.sqrt((1.0 + current) * (1.0 + lagged)) - 1.0 if eligible else None
    return StrategyScore(
        value=geometric, eligible=eligible, subject_id=None,
        display_label="持续中位数簇动量", model_label=_SCORE_MODEL_LABEL,
        frequency="WEEKLY", scope="CLUSTER", model_id=_SCORE_MODEL_ID,
        model_version=_SCORE_MODEL_VERSION,
        components={"current_momentum": current, "lagged_momentum": lagged,
                    "median_persistent_momentum": geometric},
    )


def _cluster_median_scores(weekly_returns: pd.DataFrame, clusters: Mapping[str, int], momentum_window: int) -> dict[int, float | None]:
    observations: dict[int, list[float]] = {cid: [] for cid in sorted(set(clusters.values()))}
    for _, row in weekly_returns.iloc[-momentum_window:].iterrows():
        per_cluster: dict[int, list[float]] = {cid: [] for cid in observations}
        for fund_code, cluster_id in clusters.items():
            value = _finite_or_none(row.get(fund_code))
            if value is not None:
                per_cluster[cluster_id].append(value)
        for cluster_id, values in per_cluster.items():
            if values:
                observations[cluster_id].append(float(np.mean(values)))
    return {cid: (float(np.median(values)) if len(values) == momentum_window else None)
            for cid, values in observations.items()}


def compute_median_persist_scores(weekly_returns: pd.DataFrame, clusters: Mapping[str, int], momentum_window: int) -> tuple[dict[int, StrategyScore], dict[int, float | None], dict[int, float | None], dict[int, float | None], dict[int, float | None]]:
    cluster_ids = sorted(set(clusters.values()))
    if len(weekly_returns) < momentum_window + 1:
        current = lagged = {cid: None for cid in cluster_ids}
    else:
        signal_window = weekly_returns.iloc[-(momentum_window + 1):]
        current = _cluster_median_scores(signal_window.iloc[-momentum_window:], clusters, momentum_window)
        lagged = _cluster_median_scores(signal_window.iloc[:momentum_window], clusters, momentum_window)
    delta = {cid: (current.get(cid) - lagged.get(cid) if current.get(cid) is not None and lagged.get(cid) is not None else None) for cid in cluster_ids}
    scores = {cid: median_persistent_score(current.get(cid), lagged.get(cid)) for cid in cluster_ids}
    return scores, current, lagged, delta, {cid: score.value for cid, score in scores.items()}


class AiRotationR14MedianPersistSession(CorrelationRepresentativeSession):
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
        scores, current_raw, lagged_raw, delta_raw, median_raw = compute_median_persist_scores(window.iloc[-(cfg.momentum_window_weeks + 1):], self._clusters, cfg.momentum_window_weeks)
        ranked = rank_scores(scores, cluster_members=self._frozen_members)
        weights, filled, vacant, cash_weight = build_slot_weights(ranked[:max(cfg.top_n, 0)], self._representatives, cfg.top_n)
        def diagnostics(values):
            return _momentum_diagnostics({k: v if v is not None else math.nan for k, v in values.items()})
        current_values, current_unavailable = diagnostics(current_raw)
        lagged_values, lagged_unavailable = diagnostics(lagged_raw)
        delta_values, delta_unavailable = diagnostics(delta_raw)
        median_values, median_unavailable = diagnostics(median_raw)
        unavailable = sorted(set(current_unavailable) | set(lagged_unavailable) | set(delta_unavailable) | set(median_unavailable))
        quality = QualityStatus.VALID if self._last_gate_overall.value == "PASS" else QualityStatus.DEGRADED
        decision = TargetWeightDecision(
            decision_id=f"{signal_date}-{DESCRIPTOR.id}", signal_date=signal_date,
            action=DecisionKind.SET_TARGETS, target_weights=dict(weights), cash_weight=cash_weight,
            reason_code="CLUSTER_QUALITY_REJECTED" if self._last_gate_overall.value == "REJECT" else "",
            quality_status=quality,
            diagnostics={"filled_slots": filled, "vacant_slots": vacant, "momentum": current_values,
                "momentum_status": "PARTIAL" if unavailable else "COMPLETE",
                "momentum_unavailable_clusters": unavailable,
                "momentum_available_cluster_count": len(scores) - len(unavailable),
                "momentum_total_cluster_count": len(scores), "lagged_momentum": lagged_values,
                "lagged_momentum_unavailable_clusters": lagged_unavailable,
                "median_delta": delta_values, "median_delta_unavailable_clusters": delta_unavailable,
                "median_momentum": median_values, "median_momentum_unavailable_clusters": median_unavailable,
                "lagged_momentum_window_weeks": cfg.momentum_window_weeks,
                "persistence_gate": "current_and_lagged_strictly_positive",
                "score_model": {"id": _SCORE_MODEL_ID, "label": _SCORE_MODEL_LABEL, "version": _SCORE_MODEL_VERSION, "direction": "HIGHER_BETTER"},
                "strategy_scores": _serialize_scores(scores), "num_clusters": len(self._clusters),
                "signal_information_cutoff": _SIGNAL_INFORMATION_CUTOFF},
        )
        self._log_decision(decision, scores=scores, ranked_subjects=ranked)
        return decision

    def _recluster(self, view, window, kept, eligible_set, signal_date):
        decision = super()._recluster(view, window, kept, eligible_set, signal_date)
        return replace(decision, decision_id=f"{signal_date}-{DESCRIPTOR.id}") if decision is not None else None


class AiRotationR14MedianPersistStrategy:
    descriptor = DESCRIPTOR
    config_model = CorrelationRepresentativeStrategy.config_model
    artifact_roles = ("cluster_history", "gates", "representatives", "exclusions", "decisions")

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = CorrelationRepresentativeStrategy().describe_decision_pipeline(config)
        pipeline["selection_rule"] = f"Top {config.top_n} clusters with strictly positive current and one-week-lagged momentum, ranked by geometric mean of median momentum"
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        return CorrelationRepresentativeStrategy().resolve_requirements(config)

    def create_session(self, initialization: StrategyInitializationContext, config: BaseModel) -> AiRotationR14MedianPersistSession:
        return AiRotationR14MedianPersistSession(config)  # type: ignore[arg-type]
