"""Round 39: R34 staging with incumbent carry of released weight."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import replace

from pydantic import BaseModel

from backtest.fund_rotation.contracts import (
    FundRotationStrategyDescriptor,
    StrategyDataRequirements,
    StrategyDecisionContext,
    StrategyInitializationContext,
    TargetWeightDecision,
)
from backtest.fund_rotation.strategies.ai_rotation_r11_persist_geom.strategy import (
    AiRotationR11PersistGeomStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r34_staged_reentry.strategy import (
    AiRotationR34StagedReentrySession,
    _append_reason,
)
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    CorrelationRepresentativeStrategy,
)


DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r39_incumbent_carry",
    name="持续目标承接释放权重半仓试探持续几何动量相关性代表ETF",
    description=(
        "完全沿用 R34；当期存在有效持续目标时，将新目标半槽释放的权重按"
        "持续目标基础权重比例转配给持续目标，否则精确回退 R34。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)

def _validated_state(weights: object) -> list[tuple[str, float]] | None:
    if not isinstance(weights, Mapping):
        return None
    try:
        items = list(weights.items())
    except (AttributeError, TypeError, ValueError):
        return None

    validated: list[tuple[str, float]] = []
    seen: set[str] = set()
    for code, raw_weight in items:
        if not isinstance(code, str) or not code or code in seen:
            return None
        if isinstance(raw_weight, bool):
            return None
        try:
            weight = float(raw_weight)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(weight) or weight < 0.0:
            return None
        seen.add(code)
        validated.append((code, weight))
    return validated


def _r34_baseline(
    previous_weights: object,
    staged_target_weights: object,
) -> tuple[dict[str, float], float, set[str]]:
    previous = previous_weights if isinstance(previous_weights, Mapping) else {}
    target = staged_target_weights if isinstance(staged_target_weights, Mapping) else {}
    try:
        adjusted = {
            code: float(weight)
            for code, weight in target.items()
        }
        staged = {
            code for code in adjusted
            if previous.get(code, 0.0) <= 0.0
        }
        return adjusted, max(0.0, 1.0 - sum(adjusted.values())), staged
    except (AttributeError, TypeError, ValueError):
        return {}, 1.0, set()


def _finite_sum(values: list[float]) -> float | None:
    try:
        total = math.fsum(values)
    except (OverflowError, TypeError, ValueError):
        return None
    return total if math.isfinite(total) else None


def apply_incumbent_carry(
    previous_weights: object,
    staged_target_weights: object,
) -> tuple[dict[str, float], float, set[str], set[str]]:
    """Carry R34's released new-entry weight to valid continuous targets."""
    baseline_targets, baseline_cash, baseline_staged = _r34_baseline(
        previous_weights, staged_target_weights
    )
    previous_state = _validated_state(previous_weights)
    target_state = _validated_state(staged_target_weights)
    if previous_state is None or target_state is None:
        return baseline_targets, baseline_cash, baseline_staged, set()

    previous_positive = {
        code for code, weight in previous_state if weight > 0.0
    }
    staged = {
        code for code, weight in target_state
        if code not in previous_positive and weight > 0.0
    }
    incumbents = {
        code for code, weight in target_state
        if code in previous_positive and weight > 0.0
    }
    released_weight = _finite_sum(
        [weight for code, weight in target_state if code in staged]
    )
    denominator = _finite_sum(
        [weight for code, weight in target_state if code in incumbents]
    )
    if (
        not staged
        or not incumbents
        or released_weight is None
        or released_weight <= 0.0
        or denominator is None
        or denominator <= 0.0
    ):
        return baseline_targets, baseline_cash, baseline_staged, set()

    adjusted = dict(baseline_targets)
    for code in incumbents:
        adjusted[code] += released_weight * adjusted[code] / denominator
    total = _finite_sum(list(adjusted.values()))
    if total is None or total > 1.0:
        return baseline_targets, baseline_cash, baseline_staged, set()
    return adjusted, max(0.0, 1.0 - total), staged, incumbents


class AiRotationR39IncumbentCarrySession(AiRotationR34StagedReentrySession):
    """R34 session with a fail-closed incumbent-carry overlay."""

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        previous_weights = dict(self._previous_weights)
        decision = super().evaluate(context)
        target_weights, cash_weight, staged, incumbents = apply_incumbent_carry(
            previous_weights, decision.target_weights
        )
        diagnostics = dict(decision.diagnostics)
        diagnostics.update(
            {
                "staged_reentry_codes": sorted(staged),
                "incumbent_carry_codes": sorted(incumbents),
                "incumbent_carry_rule": (
                    "released_new_target_weight_proportional_to_"
                    "continuous_base_target_weight"
                ),
                "staged_reentry_rule": (
                    "new_representative_target_weight_halved_once_"
                    "then_released_weight_carried_to_incumbents"
                ),
            }
        )
        decision = replace(
            decision,
            decision_id=f"{context.signal_date}-{DESCRIPTOR.id}",
            target_weights=target_weights,
            cash_weight=cash_weight,
            reason_code=_append_reason(
                decision.reason_code,
                "INCUMBENT_CARRY" if incumbents else "",
            ),
            diagnostics=diagnostics,
        )
        self._patch_artifacts(decision)
        return decision


class AiRotationR39IncumbentCarryStrategy:
    """Complete round 39 strategy plug-in."""

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
        pipeline = AiRotationR11PersistGeomStrategy().describe_decision_pipeline(
            config
        )
        pipeline["selection_rule"] += (
            "; new representative entries use 50% staging and release is carried "
            "to incumbent continuous targets by base-weight proportion"
        )
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        return CorrelationRepresentativeStrategy().resolve_requirements(config)

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> AiRotationR39IncumbentCarrySession:
        del initialization
        return AiRotationR39IncumbentCarrySession(config)  # type: ignore[arg-type]
