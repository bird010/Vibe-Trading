"""Round 44: R39 carry only to incumbents held for two prior signals."""

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
from backtest.fund_rotation.strategies.ai_rotation_r34_staged_reentry.strategy import AiRotationR34StagedReentrySession, _append_reason
from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import (
    AiRotationR39IncumbentCarrySession,
    AiRotationR39IncumbentCarryStrategy,
    _r34_baseline,
    _validated_state,
)
from backtest.fund_rotation.strategies.ai_rotation_r41_breadth_gated_carry.strategy import (
    _classify_positive_targets,
    _r39_or_safe_baseline,
)


DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r44_persistent_incumbent_carry",
    name="连续两期持续目标承接释放权重持续几何动量相关性代表ETF",
    description=(
        "完全沿用 R39；仅允许已连续存在于前两次有效目标中的 incumbent 承接释放权重，"
        "其他 incumbent 对应预算留作现金。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)


def _safe_baseline(previous: object, staged: object) -> tuple[dict[str, float], float, set[str]]:
    try:
        return _r34_baseline(previous, staged)
    except (OverflowError, TypeError, ValueError):
        return {}, 1.0, set()


def apply_persistent_incumbent_carry(
    previous_weights: object,
    older_weights: object,
    staged_target_weights: object,
) -> tuple[dict[str, float], float, set[str], set[str], bool]:
    """Apply R39 carry only to incumbents present in the prior two states."""
    fallback = _r39_or_safe_baseline(previous_weights, staged_target_weights)
    try:
        previous_state = _validated_state(previous_weights)
        older_state = _validated_state(older_weights)
        target_state = _validated_state(staged_target_weights)
    except (OverflowError, TypeError, ValueError):
        return fallback[0], fallback[1], fallback[2], fallback[3], bool(fallback[3])
    classified = _classify_positive_targets(previous_weights, staged_target_weights)
    if previous_state is None or older_state is None or target_state is None or classified is None:
        return fallback[0], fallback[1], fallback[2], fallback[3], bool(fallback[3])

    staged, incumbents = classified
    previous_positive = {code for code, weight in previous_state if weight > 0.0}
    older_positive = {code for code, weight in older_state if weight > 0.0}
    eligible = incumbents & previous_positive & older_positive
    baseline_targets, baseline_cash, baseline_staged = _safe_baseline(
        previous_weights, staged_target_weights
    )
    if not staged or not eligible:
        return baseline_targets, baseline_cash, baseline_staged, set(), False
    released = math.fsum(
        weight for code, weight in target_state if code in staged
    )
    denominator = math.fsum(
        baseline_targets.get(code, 0.0) for code in eligible
    )
    if not math.isfinite(released) or not math.isfinite(denominator) or released <= 0.0 or denominator <= 0.0:
        return baseline_targets, baseline_cash, baseline_staged, set(), False
    adjusted = dict(baseline_targets)
    for code in eligible:
        adjusted[code] += released * adjusted[code] / denominator
    total = math.fsum(adjusted.values())
    if not math.isfinite(total) or total > 1.0:
        return baseline_targets, baseline_cash, baseline_staged, set(), False
    return adjusted, max(0.0, 1.0 - total), staged, eligible, True


class AiRotationR44PersistentIncumbentCarrySession(AiRotationR39IncumbentCarrySession):
    """R39 session with a two-period incumbent persistence gate."""

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        previous_weights = dict(self._previous_weights)
        older_weights = dict(getattr(self, "_older_weights", {}))
        decision = AiRotationR34StagedReentrySession.evaluate(self, context)
        target_weights, cash_weight, staged, incumbents, carry_applied = apply_persistent_incumbent_carry(
            previous_weights, older_weights, decision.target_weights
        )
        diagnostics = dict(decision.diagnostics)
        diagnostics.update(
            {
                "staged_reentry_codes": sorted(staged),
                "incumbent_carry_codes": sorted(incumbents),
                "persistent_incumbent_rule": "carry_only_to_targets_present_in_previous_two_states",
            }
        )
        decision = replace(
            decision,
            decision_id=f"{context.signal_date}-{DESCRIPTOR.id}",
            target_weights=target_weights,
            cash_weight=cash_weight,
            reason_code=_append_reason(decision.reason_code, "INCUMBENT_CARRY" if carry_applied else ""),
            diagnostics=diagnostics,
        )
        self._patch_artifacts(decision)
        self._older_weights = previous_weights
        return decision


class AiRotationR44PersistentIncumbentCarryStrategy(AiRotationR39IncumbentCarryStrategy):
    """Complete round 44 strategy plug-in."""

    descriptor = DESCRIPTOR

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = super().describe_decision_pipeline(config)
        pipeline["selection_rule"] += "; carry is restricted to incumbents present in two prior target states"
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        return super().resolve_requirements(config)

    def create_session(self, initialization: StrategyInitializationContext, config: BaseModel) -> AiRotationR44PersistentIncumbentCarrySession:
        del initialization
        return AiRotationR44PersistentIncumbentCarrySession(config)  # type: ignore[arg-type]
