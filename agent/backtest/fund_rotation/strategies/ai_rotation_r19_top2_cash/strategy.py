"""Round 19 challenger: cap active persistent-momentum slots at two."""

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
from backtest.fund_rotation.scoring.contracts import rank_scores
from backtest.fund_rotation.strategies.ai_rotation_r11_persist_geom.strategy import (
    AiRotationR11PersistGeomSession,
    AiRotationR11PersistGeomStrategy,
    compute_persist_geom_scores,
    persistent_geometric_score,
)
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    CorrelationRepresentativeStrategy,
    _SIGNAL_INFORMATION_CUTOFF,
    _momentum_diagnostics,
    _serialize_scores,
)


DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r19_top2_cash",
    name="前二名持续动量现金缓冲相关性代表ETF",
    description=(
        "复用持续几何动量相关性代表ETF流程，仅持有排名前两簇；每个活动槽固定"
        "配置三分之一，其余资金保持现金，不以第三名补位。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)

ACTIVE_SLOT_COUNT = 2
_SLOT_DENOMINATOR = 3


def build_top2_cash_slot_weights(
    ranked_cluster_ids: list[int],
    representatives: Mapping[int, str | None],
) -> tuple[dict[str, float], list[int], list[int], float]:
    """Map only the first two fixed slots; vacant slots are never backfilled."""
    slot_weight = 1.0 / _SLOT_DENOMINATOR
    weights: dict[str, float] = {}
    filled: list[int] = []
    vacant: list[int] = []
    for cluster_id in ranked_cluster_ids[:ACTIVE_SLOT_COUNT]:
        representative = representatives.get(cluster_id)
        if representative:
            weights[representative] = weights.get(representative, 0.0) + slot_weight
            filled.append(cluster_id)
        else:
            vacant.append(cluster_id)
    return weights, filled, vacant, max(0.0, 1.0 - sum(weights.values()))


class AiRotationR19Top2CashSession(AiRotationR11PersistGeomSession):
    """R11 causal session with a fixed two-slot active cap."""

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        cfg = self._config
        signal_date = context.signal_date
        view = context.data_view
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
        reclustering = (
            week_index - self._last_recluster_week >= cfg.recluster_interval_weeks
            or not self._clusters
        )
        if reclustering:
            self._exclusions.extend(historical_excluded)
            decision = self._recluster(view, window, kept, eligible_set, signal_date)
            if decision is not None:
                self._log_decision(decision)
                return decision
        else:
            self._maintain_locks(view, window, eligible_set, signal_date)

        scores, current_raw, lagged_raw, geometric_raw = compute_persist_geom_scores(
            window.iloc[-(cfg.momentum_window_weeks + 1):],
            self._clusters,
            cfg.momentum_window_weeks,
        )
        ranked = rank_scores(scores, cluster_members=self._frozen_members)
        weights, filled, vacant, cash_weight = build_top2_cash_slot_weights(
            ranked, self._representatives,
        )
        current_values, current_unavailable = _momentum_diagnostics(
            {cluster_id: value if value is not None else math.nan for cluster_id, value in current_raw.items()}
        )
        lagged_values, lagged_unavailable = _momentum_diagnostics(
            {cluster_id: value if value is not None else math.nan for cluster_id, value in lagged_raw.items()}
        )
        geometric_values, geometric_unavailable = _momentum_diagnostics(
            {cluster_id: value if value is not None else math.nan for cluster_id, value in geometric_raw.items()}
        )
        unavailable = sorted(set(current_unavailable) | set(lagged_unavailable) | set(geometric_unavailable))
        quality = QualityStatus.VALID if self._last_gate_overall.value == "PASS" else QualityStatus.DEGRADED
        decision = TargetWeightDecision(
            decision_id=f"{signal_date}-{DESCRIPTOR.id}",
            signal_date=signal_date,
            action=DecisionKind.SET_TARGETS,
            target_weights=dict(weights),
            cash_weight=cash_weight,
            reason_code="CLUSTER_QUALITY_REJECTED" if self._last_gate_overall.value == "REJECT" else "",
            quality_status=quality,
            diagnostics={
                "filled_slots": filled,
                "vacant_slots": vacant,
                "active_slot_count": ACTIVE_SLOT_COUNT,
                "slot_denominator": _SLOT_DENOMINATOR,
                "momentum": current_values,
                "momentum_status": "PARTIAL" if unavailable else "COMPLETE",
                "momentum_unavailable_clusters": unavailable,
                "momentum_available_cluster_count": len(scores) - len(unavailable),
                "momentum_total_cluster_count": len(scores),
                "lagged_momentum": lagged_values,
                "lagged_momentum_unavailable_clusters": lagged_unavailable,
                "geometric_momentum": geometric_values,
                "geometric_momentum_unavailable_clusters": geometric_unavailable,
                "lagged_momentum_window_weeks": cfg.momentum_window_weeks,
                "persistence_gate": "current_and_lagged_strictly_positive",
                "score_model": {
                    "id": "persistent_geometric_cluster_momentum",
                    "label": "Persistent Geometric Cluster Momentum",
                    "version": "1",
                    "direction": "HIGHER_BETTER",
                },
                "strategy_scores": _serialize_scores(scores),
                "num_clusters": len(self._clusters),
                "signal_information_cutoff": _SIGNAL_INFORMATION_CUTOFF,
            },
        )
        self._log_decision(decision, scores=scores, ranked_subjects=ranked)
        return decision

    def _recluster(self, view, window: pd.DataFrame, kept: list[str], eligible_set: set, signal_date: str):
        decision = super()._recluster(view, window, kept, eligible_set, signal_date)
        return replace(decision, decision_id=f"{signal_date}-{DESCRIPTOR.id}") if decision is not None else None


class AiRotationR19Top2CashStrategy:
    """Complete round 19 strategy plug-in."""

    descriptor = DESCRIPTOR
    config_model = CorrelationRepresentativeStrategy.config_model
    artifact_roles = ("cluster_history", "gates", "representatives", "exclusions", "decisions")

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = AiRotationR11PersistGeomStrategy().describe_decision_pipeline(config)
        pipeline["selection_rule"] = (
            "Top 2 of the persistent geometric-momentum ranking with fixed 1/3 active-slot weights; "
            "vacant slots are not backfilled and the remainder stays cash"
        )
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        return CorrelationRepresentativeStrategy().resolve_requirements(config)

    def create_session(self, initialization: StrategyInitializationContext, config: BaseModel) -> AiRotationR19Top2CashSession:
        return AiRotationR19Top2CashSession(config)  # type: ignore[arg-type]
