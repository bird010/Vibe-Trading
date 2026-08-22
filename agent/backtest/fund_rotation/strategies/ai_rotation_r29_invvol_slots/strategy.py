"""Round 29 challenger: inverse-volatility weights within fixed slots."""

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
    compute_persist_geom_scores,
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
    id="ai_rotation_r29_invvol_slots",
    name="代表ETF逆波动槽位持续几何动量",
    description=(
        "完全沿用持续几何动量的选簇和代表锁定流程，仅按已锁定代表"
        "最近八个完整周收益的固定逆波动因子调整有效槽位权重。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)
_SCORE_MODEL_ID = "persistent_geometric_cluster_momentum"
_VOLATILITY_WINDOW_WEEKS = 8


def _finite_or_none(value: object) -> float | None:
    try:
        number = float(value) if value is not None else math.nan
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def build_inverse_volatility_slot_weights(
    selected_cluster_ids: list[int],
    representatives: Mapping[int, str | None],
    weekly_returns: pd.DataFrame,
    top_n: int,
    window_weeks: int = _VOLATILITY_WINDOW_WEEKS,
    quality_gate: str = "PASS",
) -> tuple[dict[str, float], list[int], list[int], float, dict[str, object]]:
    """Adjust only filled Champion slots using causal representative volatility."""
    base_weights, filled, vacant, base_cash = build_slot_weights(
        selected_cluster_ids, representatives, top_n
    )
    diagnostics: dict[str, object] = {
        "window_weeks": window_weeks,
        "weight_mode": "champion_equal_slot",
        "volatility": {},
        "inverse_volatility_factor": {},
        "fallback_reason": None,
    }
    if not filled:
        diagnostics["fallback_reason"] = "no_filled_slots"
        return base_weights, filled, vacant, base_cash, diagnostics
    gate_value = getattr(quality_gate, "value", quality_gate)
    if gate_value != "PASS":
        diagnostics["fallback_reason"] = "quality_gate_rejected"
        return base_weights, filled, vacant, base_cash, diagnostics
    if window_weeks <= 0 or len(weekly_returns) < window_weeks:
        diagnostics["fallback_reason"] = "insufficient_window"
        return base_weights, filled, vacant, base_cash, diagnostics

    recent = weekly_returns.iloc[-window_weeks:]
    factors: dict[str, float] = {}
    volatilities: dict[str, float] = {}
    for cluster_id in filled:
        representative = representatives.get(cluster_id)
        if not representative or representative not in recent.columns:
            diagnostics["fallback_reason"] = "representative_window_unavailable"
            return base_weights, filled, vacant, base_cash, diagnostics
        values = [_finite_or_none(value) for value in recent[representative].tolist()]
        if len(values) != window_weeks or any(value is None for value in values):
            diagnostics["fallback_reason"] = "representative_window_invalid"
            return base_weights, filled, vacant, base_cash, diagnostics
        finite_values = [value for value in values if value is not None]
        mean = sum(finite_values) / len(finite_values)
        sigma = math.sqrt(
            sum((value - mean) ** 2 for value in finite_values) / len(finite_values)
        )
        factor = 1.0 / (1.0 + sigma)
        if not math.isfinite(sigma) or not math.isfinite(factor):
            diagnostics["fallback_reason"] = "representative_volatility_invalid"
            return base_weights, filled, vacant, base_cash, diagnostics
        volatilities[str(representative)] = sigma
        factors[str(representative)] = factor

    mean_factor = sum(factors[str(representatives[cid])] for cid in filled) / len(filled)
    if not math.isfinite(mean_factor) or mean_factor <= 0.0:
        diagnostics["fallback_reason"] = "inverse_volatility_mean_invalid"
        return base_weights, filled, vacant, base_cash, diagnostics

    weights: dict[str, float] = {}
    slot_weight = 1.0 / top_n
    for cluster_id in filled:
        representative = representatives[cluster_id]
        assert representative is not None
        weights[representative] = weights.get(representative, 0.0) + (
            slot_weight * factors[representative] / mean_factor
        )
    cash_weight = max(0.0, 1.0 - sum(weights.values()))
    diagnostics.update(
        {
            "weight_mode": "inverse_volatility_with_fixed_cash_slots",
            "volatility": dict(sorted(volatilities.items())),
            "inverse_volatility_factor": dict(sorted(factors.items())),
        }
    )
    return weights, filled, vacant, cash_weight, diagnostics


class AiRotationR29InvvolSlotsSession(CorrelationRepresentativeSession):
    """Champion session with only filled-slot weighting changed."""

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

        historically_eligible, historical_excluded = check_historical_eligibility(
            dim_pool, signal_date
        )
        kept, market_excluded = signal_date_eligible(
            view, historically_eligible, signal_date
        )
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
            window, self._clusters, cfg.momentum_window_weeks
        )
        ranked = rank_scores(scores, cluster_members=self._frozen_members)
        selected = ranked[: max(cfg.top_n, 0)]
        weights, filled, vacant, cash_weight, weight_diagnostics = (
            build_inverse_volatility_slot_weights(
                selected,
                self._representatives,
                window,
                cfg.top_n,
                quality_gate=getattr(self._last_gate_overall, "value", self._last_gate_overall),
            )
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
        unavailable = sorted(
            set(current_unavailable) | set(lagged_unavailable) | set(geometric_unavailable)
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
            reason_code="CLUSTER_QUALITY_REJECTED" if rejected else "",
            quality_status=quality,
            diagnostics={
                "filled_slots": filled,
                "vacant_slots": vacant,
                "momentum": current_values,
                "momentum_status": "PARTIAL" if unavailable else "COMPLETE",
                "momentum_unavailable_clusters": unavailable,
                "momentum_available_cluster_count": len(scores) - len(unavailable),
                "momentum_total_cluster_count": len(scores),
                "lagged_momentum": lagged_values,
                "lagged_momentum_unavailable_clusters": lagged_unavailable,
                "geometric_momentum": geometric_values,
                "geometric_momentum_unavailable_clusters": geometric_unavailable,
                "representative_inverse_volatility": weight_diagnostics,
                "lagged_momentum_window_weeks": cfg.momentum_window_weeks,
                "persistence_gate": "current_and_lagged_strictly_positive",
                "score_model": {
                    "id": _SCORE_MODEL_ID,
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

    def _recluster(self, view, window, kept, eligible_set, signal_date):
        decision = super()._recluster(view, window, kept, eligible_set, signal_date)
        return None if decision is None else replace(
            decision, decision_id=f"{signal_date}-{DESCRIPTOR.id}"
        )


class AiRotationR29InvvolSlotsStrategy:
    """Complete round 29 strategy plug-in."""

    descriptor = DESCRIPTOR
    config_model = CorrelationRepresentativeStrategy.config_model
    artifact_roles = (
        "cluster_history", "gates", "representatives", "exclusions", "decisions",
    )

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = CorrelationRepresentativeStrategy().describe_decision_pipeline(config)
        pipeline["selection_rule"] = (
            f"Top {config.top_n} clusters with Champion persistent geometric momentum; "
            "filled locked-representative slots use fixed eight-week inverse volatility"
        )
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        return CorrelationRepresentativeStrategy().resolve_requirements(config)

    def create_session(self, initialization: StrategyInitializationContext, config: BaseModel):
        return AiRotationR29InvvolSlotsSession(config)  # type: ignore[arg-type]
