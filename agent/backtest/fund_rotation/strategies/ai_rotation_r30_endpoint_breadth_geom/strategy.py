"""Round 30 challenger: endpoint-breadth-adjusted persistent geometry."""

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
from backtest.fund_rotation.scoring.contracts import StrategyScore, rank_scores
from backtest.fund_rotation.strategies.ai_rotation_r11_persist_geom.strategy import (
    persistent_geometric_score,
)
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    CorrelationRepresentativeSession,
    CorrelationRepresentativeStrategy,
    _SIGNAL_INFORMATION_CUTOFF,
    _momentum_diagnostics,
    _serialize_scores,
    build_slot_weights,
)


DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r30_endpoint_breadth_geom",
    name="端点成员广度持续几何动量相关性代表ETF",
    description=(
        "沿用双正持续几何动量与相关性代表ETF流程，以当前及滞后窗口端点的"
        "成员正收益一致性调节合格簇得分。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)
_SCORE_MODEL_ID = "endpoint_breadth_adjusted_persistent_cluster_momentum"
_SCORE_MODEL_LABEL = "Endpoint-Breadth-Adjusted Persistent Geometric Cluster Momentum"
_SCORE_MODEL_VERSION = "1"


def _finite_or_none(value: object) -> float | None:
    try:
        number = float(value) if value is not None else math.nan
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _complete_window(
    weekly_returns: pd.DataFrame,
    members: Sequence[str],
    window: int,
) -> bool:
    if len(weekly_returns) != window or not members:
        return False
    if any(member not in weekly_returns.columns for member in members):
        return False
    return all(
        _finite_or_none(value) is not None
        for value in weekly_returns[list(members)].to_numpy().ravel()
    )


def _weekly_mean_compounded_momentum(
    weekly_returns: pd.DataFrame,
    members: Sequence[str],
    window: int,
) -> float | None:
    if not _complete_window(weekly_returns, members, window):
        return None
    weekly_mean = weekly_returns[list(members)].mean(axis=1, skipna=False)
    if weekly_mean.isna().any():
        return None
    result = float((1.0 + weekly_mean.to_numpy()).prod() - 1.0)
    return result if math.isfinite(result) else None


def _endpoint_positive_breadth(
    endpoint_returns: Sequence[object],
) -> float | None:
    values = [_finite_or_none(value) for value in endpoint_returns]
    if not values or any(value is None for value in values):
        return None
    breadth = sum(value > 0.0 for value in values if value is not None) / len(values)
    return breadth if math.isfinite(breadth) else None


def endpoint_breadth_geometric_score(
    current_momentum: object,
    lagged_momentum: object,
    current_endpoint_members: Sequence[object] | None,
    lagged_endpoint_members: Sequence[object] | None,
) -> StrategyScore:
    """Apply strict persistence and P30 = G * sqrt(B0 * B1)."""
    current = _finite_or_none(current_momentum)
    lagged = _finite_or_none(lagged_momentum)
    current_breadth = (
        None
        if current_endpoint_members is None
        else _endpoint_positive_breadth(current_endpoint_members)
    )
    lagged_breadth = (
        None
        if lagged_endpoint_members is None
        else _endpoint_positive_breadth(lagged_endpoint_members)
    )
    same_member_count = (
        current_endpoint_members is not None
        and lagged_endpoint_members is not None
        and len(current_endpoint_members) == len(lagged_endpoint_members)
    )
    eligible = (
        current is not None
        and lagged is not None
        and current > 0.0
        and lagged > 0.0
        and same_member_count
        and current_breadth is not None
        and lagged_breadth is not None
    )
    adjustment = score = None
    if eligible:
        adjustment = math.sqrt(current_breadth * lagged_breadth)
        geometric = persistent_geometric_score(current, lagged).value
        score = geometric * adjustment if geometric is not None else None
        eligible = score is not None and math.isfinite(score)
    return StrategyScore(
        value=score if eligible else None,
        eligible=eligible,
        subject_id=None,
        display_label="端点成员广度持续几何簇动量",
        model_label=_SCORE_MODEL_LABEL,
        frequency="WEEKLY",
        scope="CLUSTER",
        model_id=_SCORE_MODEL_ID,
        model_version=_SCORE_MODEL_VERSION,
        components={
            "current_momentum": current,
            "lagged_momentum": lagged,
            "current_positive_breadth": current_breadth,
            "lagged_positive_breadth": lagged_breadth,
            "endpoint_breadth_adjustment": adjustment,
            "endpoint_breadth_adjusted_persistent_momentum": (
                score if eligible else None
            ),
        },
    )


def compute_endpoint_breadth_geom_scores(
    weekly_returns: pd.DataFrame,
    clusters: Mapping[str, int],
    frozen_members: Mapping[int, Sequence[str]],
    momentum_window: int,
) -> tuple[
    dict[int, StrategyScore],
    dict[int, float | None],
    dict[int, float | None],
    dict[int, float | None],
]:
    """Compute causal M0, M1, B0, B1 and P30 from one frozen epoch."""
    if momentum_window != 4:
        raise ValueError("round 30 requires four-week momentum windows")
    cluster_ids = sorted(set(clusters.values()))
    current = {cid: None for cid in cluster_ids}
    lagged = {cid: None for cid in cluster_ids}
    adjustments = {cid: None for cid in cluster_ids}
    current_endpoints: dict[int, list[object] | None] = {
        cid: None for cid in cluster_ids
    }
    lagged_endpoints: dict[int, list[object] | None] = {
        cid: None for cid in cluster_ids
    }
    if len(weekly_returns) >= momentum_window + 1:
        signal_window = weekly_returns.iloc[-(momentum_window + 1) :]
        current_window = signal_window.iloc[-momentum_window:]
        lagged_window = signal_window.iloc[:momentum_window]
        for cid in cluster_ids:
            members = tuple(sorted(frozen_members.get(cid, ())))
            if not _complete_window(current_window, members, momentum_window):
                continue
            if not _complete_window(lagged_window, members, momentum_window):
                continue
            current_value = _weekly_mean_compounded_momentum(
                current_window, members, momentum_window
            )
            lagged_value = _weekly_mean_compounded_momentum(
                lagged_window, members, momentum_window
            )
            current[cid] = current_value
            lagged[cid] = lagged_value
            current_endpoints[cid] = signal_window[list(members)].iloc[-1].tolist()
            lagged_endpoints[cid] = signal_window[list(members)].iloc[-2].tolist()
    scores = {
        cid: endpoint_breadth_geometric_score(
            current[cid],
            lagged[cid],
            current_endpoints[cid],
            lagged_endpoints[cid],
        )
        for cid in cluster_ids
    }
    for cid, score in scores.items():
        adjustments[cid] = score.components["endpoint_breadth_adjustment"]
    return scores, current, lagged, adjustments


class AiRotationR30EndpointBreadthGeomSession(CorrelationRepresentativeSession):
    """Session retaining Champion clustering, locks, slots and execution."""

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
        reclustering = week_index - self._last_recluster_week >= cfg.recluster_interval_weeks or not self._clusters
        if reclustering:
            self._exclusions.extend(historical_excluded)
            decision = self._recluster(view, window, kept, eligible_set, signal_date)
            if decision is not None:
                self._log_decision(decision)
                return decision
        else:
            self._maintain_locks(view, window, eligible_set, signal_date)
        scores, current_raw, lagged_raw, adjustment_raw = compute_endpoint_breadth_geom_scores(
            window, self._clusters, self._frozen_members, cfg.momentum_window_weeks
        )
        ranked = rank_scores(scores, cluster_members=self._frozen_members)
        selected = ranked[: max(cfg.top_n, 0)]
        weights, filled, vacant, cash_weight = build_slot_weights(selected, self._representatives, cfg.top_n)
        current_values, current_unavailable = _momentum_diagnostics({cid: v if v is not None else math.nan for cid, v in current_raw.items()})
        lagged_values, lagged_unavailable = _momentum_diagnostics({cid: v if v is not None else math.nan for cid, v in lagged_raw.items()})
        adjustment_values, adjustment_unavailable = _momentum_diagnostics({cid: v if v is not None else math.nan for cid, v in adjustment_raw.items()})
        unavailable = sorted(set(current_unavailable) | set(lagged_unavailable) | set(adjustment_unavailable))
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
                "endpoint_breadth_adjustment": adjustment_values,
                "endpoint_breadth_adjustment_unavailable_clusters": adjustment_unavailable,
                "lagged_momentum_window_weeks": cfg.momentum_window_weeks,
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
        return None if decision is None else replace(decision, decision_id=f"{signal_date}-{DESCRIPTOR.id}")


class AiRotationR30EndpointBreadthGeomStrategy:
    """Complete round 30 strategy plug-in."""

    descriptor = DESCRIPTOR
    config_model = CorrelationRepresentativeStrategy.config_model
    artifact_roles = ("cluster_history", "gates", "representatives", "exclusions", "decisions")

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = CorrelationRepresentativeStrategy().describe_decision_pipeline(config)
        pipeline["selection_rule"] = (
            f"Top {config.top_n} clusters with strictly positive current and one-week-lagged "
            "four-week momentum, ranked by geometric growth times endpoint positive breadth"
        )
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        return CorrelationRepresentativeStrategy().resolve_requirements(config)

    def create_session(self, initialization: StrategyInitializationContext, config: BaseModel):
        return AiRotationR30EndpointBreadthGeomSession(config)  # type: ignore[arg-type]
