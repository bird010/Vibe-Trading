"""Round 42: R39 with half carry when breadth is exactly one."""

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
from backtest.fund_rotation.strategies.ai_rotation_r34_staged_reentry.strategy import (
    AiRotationR34StagedReentrySession,
    _append_reason,
)
from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import (
    AiRotationR39IncumbentCarrySession,
    AiRotationR39IncumbentCarryStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r41_breadth_gated_carry.strategy import (
    _classify_positive_targets,
    _r39_or_safe_baseline,
)


HALF_CARRY = 0.5

DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r42_single_incumbent_half_carry",
    name="单持续目标半量承接释放权重持续几何动量相关性代表ETF",
    description=(
        "完全沿用 R39；仅当正权重持续目标恰好一个且存在新目标时，"
        "承接释放权重的一半，其余留作现金；其余情况逐值沿用 R39。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)


def _finite_sum(values: list[float]) -> float | None:
    try:
        total = math.fsum(values)
    except (OverflowError, TypeError, ValueError):
        return None
    return total if math.isfinite(total) else None


def _positive_items(weights: object) -> list[tuple[str, float]]:
    if not isinstance(weights, Mapping):
        return []
    try:
        items = list(weights.items())
    except (AttributeError, TypeError, ValueError):
        return []
    result: list[tuple[str, float]] = []
    for code, raw_weight in items:
        if not isinstance(code, str):
            continue
        try:
            weight = float(raw_weight)
        except (OverflowError, TypeError, ValueError):
            continue
        if math.isfinite(weight) and weight > 0.0:
            result.append((code, weight))
    return result


def apply_single_incumbent_half_carry(
    previous_weights: object,
    staged_target_weights: object,
) -> tuple[dict[str, float], float, set[str], set[str], bool]:
    """Apply R39 except halve carry when exactly one incumbent receives it."""
    r39_targets, r39_cash, r39_staged, r39_incumbents = _r39_or_safe_baseline(
        previous_weights,
        staged_target_weights,
    )
    classified = _classify_positive_targets(previous_weights, staged_target_weights)
    if classified is None:
        return r39_targets, r39_cash, r39_staged, r39_incumbents, bool(
            r39_incumbents
        )

    staged, incumbents = classified
    if not staged or len(incumbents) != 1 or r39_incumbents != incumbents:
        return r39_targets, r39_cash, r39_staged, r39_incumbents, bool(
            r39_incumbents
        )

    released = _finite_sum(
        [weight for code, weight in _positive_items(staged_target_weights) if code in staged]
    )
    if released is None or released <= 0.0:
        return r39_targets, r39_cash, r39_staged, r39_incumbents, bool(
            r39_incumbents
        )

    code = next(iter(incumbents))
    reduction = released * (1.0 - HALF_CARRY)
    adjusted = dict(r39_targets)
    if code not in adjusted or not math.isfinite(adjusted[code]) or adjusted[code] < reduction:
        return r39_targets, r39_cash, r39_staged, r39_incumbents, bool(
            r39_incumbents
        )
    adjusted[code] -= reduction
    adjusted_cash = r39_cash + reduction
    total = _finite_sum(list(adjusted.values()) + [adjusted_cash])
    if not math.isfinite(adjusted_cash) or adjusted_cash < 0.0 or total is None:
        return r39_targets, r39_cash, r39_staged, r39_incumbents, bool(
            r39_incumbents
        )
    return adjusted, adjusted_cash, r39_staged, r39_incumbents, True


class AiRotationR42SingleIncumbentHalfCarrySession(AiRotationR39IncumbentCarrySession):
    """R39 session with a half-carry single-incumbent overlay."""

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        previous_weights = dict(self._previous_weights)
        decision = AiRotationR34StagedReentrySession.evaluate(self, context)
        target_weights, cash_weight, staged, incumbents, carry_applied = (
            apply_single_incumbent_half_carry(previous_weights, decision.target_weights)
        )
        diagnostics = dict(decision.diagnostics)
        diagnostics.update(
            {
                "staged_reentry_codes": sorted(staged),
                "incumbent_carry_codes": sorted(incumbents),
                "single_incumbent_half_carry": HALF_CARRY,
                "single_incumbent_half_carry_rule": (
                    "when_one_positive_incumbent_and_staged_target_carry_half_released_weight"
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
                "INCUMBENT_CARRY" if carry_applied else "",
            ),
            diagnostics=diagnostics,
        )
        self._patch_artifacts(decision)
        return decision


class AiRotationR42SingleIncumbentHalfCarryStrategy(AiRotationR39IncumbentCarryStrategy):
    """Complete round 42 strategy plug-in."""

    descriptor = DESCRIPTOR

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = super().describe_decision_pipeline(config)
        pipeline["selection_rule"] += (
            "; when exactly one positive incumbent and a staged target exist, carry half "
            "of released weight and retain the remainder as cash"
        )
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        return super().resolve_requirements(config)

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> AiRotationR42SingleIncumbentHalfCarrySession:
        del initialization
        return AiRotationR42SingleIncumbentHalfCarrySession(config)  # type: ignore[arg-type]
