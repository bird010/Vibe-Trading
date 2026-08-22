"""Round 17 challenger: winsorized persistent geometric momentum."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import replace

import numpy as np
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
    id="ai_rotation_r17_winsor_geom", name="截尾持续几何动量相关性代表ETF",
    description=("复用相关性聚类代表ETF流程，要求当前及向后错一周的四周簇动量"
                 "均严格为正，并在合格簇横截面内分别进行10%/90%截尾后按几何均值排名。"),
    interface_version="1.0", supported_universe=("etf",), deterministic=True,
)
_SCORE_MODEL_ID = "winsorized_persistent_geometric_cluster_momentum"
_SCORE_MODEL_LABEL = "Winsorized Persistent Geometric Cluster Momentum"
_SCORE_MODEL_VERSION = "1"
_LOWER_Q = 0.10
_UPPER_Q = 0.90


def _finite_or_none(value: object) -> float | None:
    try:
        number = float(value) if value is not None else math.nan
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _bounds(values: Sequence[float]) -> tuple[float, float] | None:
    if not values:
        return None
    lower, upper = np.quantile(np.asarray(values, dtype=float), [_LOWER_Q, _UPPER_Q], method="linear")
    return float(lower), float(upper)


def _clip(value: float | None, bounds: tuple[float, float] | None) -> float | None:
    if value is None or bounds is None:
        return None
    return min(max(value, bounds[0]), bounds[1])


def winsor_geometric_score(current_momentum: object, lagged_momentum: object, *,
                           current_bounds: tuple[float, float] | None,
                           lagged_bounds: tuple[float, float] | None) -> StrategyScore:
    current, lagged = _finite_or_none(current_momentum), _finite_or_none(lagged_momentum)
    eligible = current is not None and lagged is not None and current > 0.0 and lagged > 0.0
    clipped_current = _clip(current, current_bounds) if eligible else None
    clipped_lagged = _clip(lagged, lagged_bounds) if eligible else None
    geometric = (math.sqrt((1.0 + clipped_current) * (1.0 + clipped_lagged)) - 1.0
                 if clipped_current is not None and clipped_lagged is not None
                 and 1.0 + clipped_current > 0.0 and 1.0 + clipped_lagged > 0.0 else None)
    eligible = eligible and geometric is not None
    return StrategyScore(
        value=geometric if eligible else None, eligible=eligible, subject_id=None,
        display_label="截尾持续几何簇动量", model_label=_SCORE_MODEL_LABEL,
        frequency="WEEKLY", scope="CLUSTER", model_id=_SCORE_MODEL_ID,
        model_version=_SCORE_MODEL_VERSION,
        components={"current_momentum": current, "lagged_momentum": lagged,
                     "winsorized_current_momentum": clipped_current,
                     "winsorized_lagged_momentum": clipped_lagged,
                     "winsorized_geometric_momentum": geometric},
    )


def compute_winsor_geom_scores(values_or_returns, clusters_or_members, momentum_window: int | None = None, *,
                               cluster_members: Mapping[int, Sequence[str]] | None = None):
    """Compute direct scores or causal M0/M1 from a frozen weekly window."""
    if isinstance(values_or_returns, pd.DataFrame):
        weekly_returns, clusters = values_or_returns, clusters_or_members
        cluster_ids = sorted(set(clusters.values()))
        if momentum_window is None or len(weekly_returns) < momentum_window + 1:
            current = {cid: None for cid in cluster_ids}
            lagged = {cid: None for cid in cluster_ids}
        else:
            model = ClusterMomentumScoreModel()
            signal_window = weekly_returns.iloc[-(momentum_window + 1):]
            current = {cid: score.value for cid, score in model.score(signal_window.iloc[-momentum_window:], dict(clusters), momentum_window).items()}
            lagged = {cid: score.value for cid, score in model.score(signal_window.iloc[:momentum_window], dict(clusters), momentum_window).items()}
        scores = compute_winsor_geom_scores({cid: (current.get(cid), lagged.get(cid)) for cid in cluster_ids},
                                             cluster_members or {cid: () for cid in cluster_ids})
        return scores, current, lagged, {cid: score.value for cid, score in scores.items()}
    values, members = values_or_returns, clusters_or_members
    valid = {cid: (_finite_or_none(pair[0]), _finite_or_none(pair[1])) for cid, pair in values.items()
             if _finite_or_none(pair[0]) is not None and _finite_or_none(pair[1]) is not None
             and float(pair[0]) > 0.0 and float(pair[1]) > 0.0}
    current_bounds = _bounds([pair[0] for pair in valid.values()])
    lagged_bounds = _bounds([pair[1] for pair in valid.values()])
    return {cid: winsor_geometric_score(pair[0], pair[1], current_bounds=current_bounds,
                                        lagged_bounds=lagged_bounds) for cid, pair in values.items()}


class AiRotationR17WinsorGeomSession(CorrelationRepresentativeSession):
    """Session retaining the shared representative and execution contract."""

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
        scores, current_raw, lagged_raw, geometric_raw = compute_winsor_geom_scores(
            window.iloc[-(cfg.momentum_window_weeks + 1):], self._clusters,
            cfg.momentum_window_weeks, cluster_members=self._frozen_members)
        ranked = rank_scores(scores, cluster_members=self._frozen_members)
        weights, filled, vacant, cash_weight = build_slot_weights(ranked[:max(cfg.top_n, 0)], self._representatives, cfg.top_n)
        def diagnostics(values):
            return _momentum_diagnostics({k: v if v is not None else math.nan for k, v in values.items()})
        current_values, current_unavailable = diagnostics(current_raw)
        lagged_values, lagged_unavailable = diagnostics(lagged_raw)
        geometric_values, geometric_unavailable = diagnostics(geometric_raw)
        unavailable = sorted(set(current_unavailable) | set(lagged_unavailable) | set(geometric_unavailable))
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
                "winsorized_geometric_momentum": geometric_values,
                "winsorized_geometric_momentum_unavailable_clusters": geometric_unavailable,
                "winsor_quantiles": {"lower": _LOWER_Q, "upper": _UPPER_Q},
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


class AiRotationR17WinsorGeomStrategy:
    descriptor = DESCRIPTOR
    config_model = CorrelationRepresentativeStrategy.config_model
    artifact_roles = ("cluster_history", "gates", "representatives", "exclusions", "decisions")

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = CorrelationRepresentativeStrategy().describe_decision_pipeline(config)
        pipeline["selection_rule"] = (f"Top {config.top_n} clusters with strictly positive current and one-week-lagged momentum, "
                                       "ranked by geometric mean after independent 10%/90% cross-sectional winsorization")
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        return CorrelationRepresentativeStrategy().resolve_requirements(config)

    def create_session(self, initialization: StrategyInitializationContext, config: BaseModel) -> AiRotationR17WinsorGeomSession:
        return AiRotationR17WinsorGeomSession(config)  # type: ignore[arg-type]
