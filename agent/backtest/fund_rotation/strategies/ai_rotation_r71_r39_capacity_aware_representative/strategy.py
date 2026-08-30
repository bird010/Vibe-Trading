"""Round 71: capacity-aware representative fallback over the frozen R39 path."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace

from pydantic import BaseModel

from backtest.fund_rotation.capacity import select_capacity_aware_representative
from backtest.fund_rotation.contracts import (
    FundRotationStrategyDescriptor,
    StrategyDataRequirements,
    StrategyDecisionContext,
    StrategyInitializationContext,
    TargetWeightDecision,
)
from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import (
    AiRotationR39IncumbentCarrySession,
    AiRotationR39IncumbentCarryStrategy,
)
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    CorrelationRepresentativeStrategy,
)


DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r71_r39_capacity_aware_representative",
    name="R39容量感知代表解锁与确定性fallback",
    description=(
        "完全沿用 R39；仅在决策 cutoff 可见且容量证据足够时承接当前代表，"
        "否则按同簇同身份的确定性候选顺序 fallback，全部不可用时转现金。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)


@dataclass(frozen=True)
class CapacityOverlayResult:
    target_weights: dict[str, float]
    cash_weight: float
    diagnostics: dict[str, object]


def apply_capacity_overlay(
    base_target_weights: object,
    *,
    candidates: object,
    target_quantity: object,
    market_observation: object,
    prior_representative: object,
) -> CapacityOverlayResult:
    """Replace at most one R39 representative, preserving its target weight."""
    if not isinstance(base_target_weights, Mapping):
        return CapacityOverlayResult({}, 1.0, {"capacity_status": "unavailable"})
    try:
        base = {str(code): float(weight) for code, weight in base_target_weights.items()}
        total = math.fsum(base.values())
    except (AttributeError, TypeError, ValueError, OverflowError):
        return CapacityOverlayResult({}, 1.0, {"capacity_status": "unavailable"})
    if any(not math.isfinite(weight) or weight < 0.0 for weight in base.values()):
        return CapacityOverlayResult({}, 1.0, {"capacity_status": "unavailable"})
    if candidates is None or market_observation is None:
        return CapacityOverlayResult(
            base, max(0.0, 1.0 - total),
            {"capacity_status": "unavailable", "capacity_reason": "CAPACITY_EVIDENCE_UNAVAILABLE"},
        )
    selection = select_capacity_aware_representative(
        candidates, target_quantity, market_observation, prior_representative
    )
    diagnostics = dict(selection.diagnostics)
    diagnostics.update(
        {
            "capacity_status": "selected" if selection.selected_representative else "cash",
            "capacity_reason": selection.reason_code,
            "capacity_selected_representative": selection.selected_representative,
            "capacity_used_fallback": selection.used_fallback,
        }
    )
    if selection.selected_representative is None:
        old_code = prior_representative if isinstance(prior_representative, str) else None
        if old_code not in base and len(base) == 1:
            old_code = next(iter(base))
        if old_code not in base:
            return CapacityOverlayResult(base, max(0.0, 1.0 - total), diagnostics)
        adjusted = dict(base)
        released = adjusted.pop(old_code)
        return CapacityOverlayResult(
            adjusted,
            max(0.0, 1.0 - total + released),
            diagnostics,
        )
    selected = selection.selected_representative
    old_code = prior_representative if isinstance(prior_representative, str) else None
    if old_code not in base and len(base) == 1:
        old_code = next(iter(base))
    if old_code is None or old_code not in base or selected == old_code:
        return CapacityOverlayResult(base, max(0.0, 1.0 - total), diagnostics)
    adjusted = dict(base)
    weight = adjusted.pop(old_code)
    adjusted[selected] = adjusted.get(selected, 0.0) + weight
    adjusted_total = math.fsum(adjusted.values())
    if adjusted_total > 1.0 + 1e-12:
        return CapacityOverlayResult(base, max(0.0, 1.0 - total), {**diagnostics, "capacity_status": "unavailable"})
    return CapacityOverlayResult(adjusted, max(0.0, 1.0 - adjusted_total), diagnostics)


class AiRotationR71R39CapacityAwareRepresentativeSession(
    AiRotationR39IncumbentCarrySession
):
    """R39 session that is inert unless explicit capacity evidence is present."""

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        decision = super().evaluate(context)
        observation = getattr(context.data_view, "capacity_observation", None)
        if callable(observation):
            observation = observation()
        if not isinstance(observation, Mapping):
            observation = None
        candidates = observation.get("candidates") if observation else None
        market_observation = observation.get("market_observation") if observation else None
        prior = observation.get("prior_representative") if observation else None
        if prior is None and len(decision.target_weights) == 1:
            prior = next(iter(decision.target_weights))
        overlay = apply_capacity_overlay(
            decision.target_weights,
            candidates=candidates,
            target_quantity=observation.get("target_quantity") if observation else None,
            market_observation=market_observation,
            prior_representative=prior,
        )
        diagnostics = dict(decision.diagnostics)
        diagnostics["capacity_overlay"] = overlay.diagnostics
        decision = replace(
            decision,
            decision_id=f"{context.signal_date}-{DESCRIPTOR.id}",
            target_weights=overlay.target_weights,
            cash_weight=overlay.cash_weight,
            diagnostics=diagnostics,
        )
        self._patch_artifacts(decision)
        return decision


class AiRotationR71R39CapacityAwareRepresentativeStrategy(
    AiRotationR39IncumbentCarryStrategy
):
    descriptor = DESCRIPTOR
    session_class = AiRotationR71R39CapacityAwareRepresentativeSession

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = super().describe_decision_pipeline(config)
        pipeline["capacity_overlay"] = (
            "carry when causal capacity is sufficient; deterministic same-cluster/identity fallback; cash otherwise"
        )
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        return CorrelationRepresentativeStrategy().resolve_requirements(config)

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> AiRotationR71R39CapacityAwareRepresentativeSession:
        del initialization
        return self.session_class(config)
