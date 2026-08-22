"""Round 25 challenger: persistent momentum of locked representatives."""

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
from backtest.fund_rotation.scoring.contracts import StrategyScore, rank_scores
from backtest.fund_rotation.strategies.ai_rotation_r11_persist_geom.strategy import (
    persistent_geometric_score,
)
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    CorrelationRepresentativeSession, CorrelationRepresentativeStrategy,
    _SIGNAL_INFORMATION_CUTOFF, _momentum_diagnostics, _serialize_scores,
    build_slot_weights,
)


DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r25_rep_persist_geom",
    name="代表ETF持续几何动量相关性代表ETF",
    description=(
        "沿用冻结相关性聚类和代表锁定流程，以当前锁定代表ETF的当前及"
        "向后错一周四周复合收益均严格为正，并按等权几何增长均值排名。"
    ),
    interface_version="1.0", supported_universe=("etf",), deterministic=True,
)
_SCORE_MODEL_ID = "persistent_geometric_representative_momentum"
_SCORE_MODEL_LABEL = "Persistent Geometric Representative Momentum"
_SCORE_MODEL_VERSION = "1"


def _finite_or_none(value: object) -> float | None:
    try:
        number = float(value) if value is not None else math.nan
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _compound(values: list[object], window: int) -> float | None:
    if len(values) != window:
        return None
    finite = [_finite_or_none(value) for value in values]
    if any(value is None for value in finite):
        return None
    result = 1.0
    for value in finite:
        result *= 1.0 + value  # type: ignore[operator]
    return result - 1.0 if math.isfinite(result) else None


def representative_persistent_geometric_score(
    current_momentum: object, lagged_momentum: object,
) -> StrategyScore:
    """Apply H25's strict-positive gate and geometric ranking score."""
    current = _finite_or_none(current_momentum)
    lagged = _finite_or_none(lagged_momentum)
    base = persistent_geometric_score(current, lagged)
    return StrategyScore(
        value=base.value, eligible=base.eligible, subject_id=None,
        display_label="代表ETF持续几何动量", model_label=_SCORE_MODEL_LABEL,
        frequency="WEEKLY", scope="CLUSTER", model_id=_SCORE_MODEL_ID,
        model_version=_SCORE_MODEL_VERSION,
        components={
            "representative_current_momentum": current,
            "representative_lagged_momentum": lagged,
            "persistent_geometric_representative_momentum": base.value,
        },
    )


def compute_rep_persist_geom_scores(
    weekly_returns: pd.DataFrame,
    clusters: Mapping[str, int],
    representatives: Mapping[int, str | None],
    momentum_window: int,
) -> tuple[dict[int, StrategyScore], dict[int, float | None],
           dict[int, float | None], dict[int, float | None]]:
    """Compute H25 scores from the current epoch's locked representatives."""
    cluster_ids = sorted(set(clusters.values()))
    current: dict[int, float | None] = {}
    lagged: dict[int, float | None] = {}
    for cluster_id in cluster_ids:
        representative = representatives.get(cluster_id)
        if (representative is None or representative not in weekly_returns.columns
                or len(weekly_returns) < momentum_window + 1):
            current[cluster_id] = None
            lagged[cluster_id] = None
            continue
        signal_window = weekly_returns.iloc[-(momentum_window + 1):]
        values = signal_window[representative].tolist()
        current[cluster_id] = _compound(values[-momentum_window:], momentum_window)
        lagged[cluster_id] = _compound(values[:momentum_window], momentum_window)
    scores = {
        cluster_id: representative_persistent_geometric_score(
            current.get(cluster_id), lagged.get(cluster_id)
        ) for cluster_id in cluster_ids
    }
    return scores, current, lagged, {
        cluster_id: score.value for cluster_id, score in scores.items()
    }


class AiRotationR25RepPersistGeomSession(CorrelationRepresentativeSession):
    """Isolated session with representative-aligned persistent ranking."""

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        cfg, signal_date, view = self._config, context.signal_date, context.data_view
        week_index = self._week_index
        self._week_index += 1
        dim_pool = self._pool_at_signal(view)
        window = view.returns("weekly", cfg.correlation_lookback_weeks)
        from backtest.fund_rotation.strategies.correlation_all_members.signals import signal_date_eligible
        from backtest.fund_rotation.universe import check_historical_eligibility
        historically_eligible, historical_excluded = check_historical_eligibility(dim_pool, signal_date)
        kept, market_excluded = signal_date_eligible(view, historically_eligible, signal_date)
        self._exclusions.extend(market_excluded)
        eligible_set = set(kept)
        reclustering = (week_index - self._last_recluster_week >= cfg.recluster_interval_weeks
                        or not self._clusters)
        if reclustering:
            self._exclusions.extend(historical_excluded)
            decision = self._recluster(view, window, kept, eligible_set, signal_date)
            if decision is not None:
                self._log_decision(decision)
                return decision
        else:
            self._maintain_locks(view, window, eligible_set, signal_date)
        scores, current_raw, lagged_raw, geometric_raw = compute_rep_persist_geom_scores(
            window, self._clusters, self._representatives, cfg.momentum_window_weeks
        )
        ranked = rank_scores(scores, cluster_members=self._frozen_members)
        selected = ranked[:max(cfg.top_n, 0)]
        weights, filled, vacant, cash_weight = build_slot_weights(
            selected, self._representatives, cfg.top_n
        )
        current_values, current_unavailable = _momentum_diagnostics(
            {cid: value if value is not None else math.nan for cid, value in current_raw.items()}
        )
        lagged_values, lagged_unavailable = _momentum_diagnostics(
            {cid: value if value is not None else math.nan for cid, value in lagged_raw.items()}
        )
        geometric_values, geometric_unavailable = _momentum_diagnostics(
            {cid: value if value is not None else math.nan for cid, value in geometric_raw.items()}
        )
        unavailable = sorted(set(current_unavailable) | set(lagged_unavailable) | set(geometric_unavailable))
        rejected = self._last_gate_overall.value == "REJECT"
        quality = QualityStatus.VALID if self._last_gate_overall.value == "PASS" else QualityStatus.DEGRADED
        decision = TargetWeightDecision(
            decision_id=f"{signal_date}-{DESCRIPTOR.id}", signal_date=signal_date,
            action=DecisionKind.SET_TARGETS, target_weights=dict(weights), cash_weight=cash_weight,
            reason_code="CLUSTER_QUALITY_REJECTED" if rejected else "", quality_status=quality,
            diagnostics={
                "filled_slots": filled, "vacant_slots": vacant, "momentum": current_values,
                "momentum_status": "PARTIAL" if unavailable else "COMPLETE",
                "momentum_unavailable_clusters": unavailable,
                "momentum_available_cluster_count": len(scores) - len(unavailable),
                "momentum_total_cluster_count": len(scores), "lagged_momentum": lagged_values,
                "lagged_momentum_unavailable_clusters": lagged_unavailable,
                "representative_geometric_momentum": geometric_values,
                "representative_geometric_momentum_unavailable_clusters": geometric_unavailable,
                "lagged_momentum_window_weeks": cfg.momentum_window_weeks,
                "persistence_gate": "representative_current_and_lagged_strictly_positive",
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
        return None if decision is None else replace(decision, decision_id=f"{signal_date}-{DESCRIPTOR.id}")


class AiRotationR25RepPersistGeomStrategy:
    """Complete round 25 strategy plug-in."""
    descriptor = DESCRIPTOR
    config_model = CorrelationRepresentativeStrategy.config_model
    artifact_roles: tuple[str, ...] = ("cluster_history", "gates", "representatives", "exclusions", "decisions")

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = CorrelationRepresentativeStrategy().describe_decision_pipeline(config)
        pipeline["selection_rule"] = (
            f"Top {config.top_n} clusters with strictly positive current and one-week-lagged "
            "four-week momentum of each locked representative ETF, ranked by geometric growth mean"
        )
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        return CorrelationRepresentativeStrategy().resolve_requirements(config)

    def create_session(self, initialization: StrategyInitializationContext, config: BaseModel):
        return AiRotationR25RepPersistGeomSession(config)  # type: ignore[arg-type]
