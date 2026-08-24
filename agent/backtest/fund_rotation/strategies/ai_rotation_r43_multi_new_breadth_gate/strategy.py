"""Round 43: cancel R39 carry only for one incumbent and two new targets."""

from __future__ import annotations

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
    _r34_baseline,
    _r39_or_safe_baseline,
)


DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r43_multi_new_breadth_gate",
    name="单持续目标多新目标取消承接释放权重持续几何动量相关性代表ETF",
    description=(
        "完全沿用 R39；仅当正权重持续目标恰好一个且正权重新目标至少两个时，"
        "取消 carry 并保留 R34 staged 输出，其余情况逐值沿用 R39。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)


def apply_multi_new_breadth_gate(
    previous_weights: object,
    staged_target_weights: object,
) -> tuple[dict[str, float], float, set[str], set[str], bool]:
    """Apply R39 except when one incumbent would absorb at least two new slots."""
    r39_result = _r39_or_safe_baseline(previous_weights, staged_target_weights)
    classified = _classify_positive_targets(previous_weights, staged_target_weights)
    if classified is None:
        return r39_result[0], r39_result[1], r39_result[2], r39_result[3], bool(r39_result[3])
    staged, incumbents = classified
    if staged and len(staged) >= 2 and len(incumbents) == 1 and r39_result[3] == incumbents:
        targets, cash, gated_staged = _r34_baseline(previous_weights, staged_target_weights)
        return targets, cash, gated_staged, set(), False
    return r39_result[0], r39_result[1], r39_result[2], r39_result[3], bool(r39_result[3])


class AiRotationR43MultiNewBreadthGateSession(AiRotationR39IncumbentCarrySession):
    """R39 session with a narrow multi-new breadth gate."""

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        previous_weights = dict(self._previous_weights)
        decision = AiRotationR34StagedReentrySession.evaluate(self, context)
        target_weights, cash_weight, staged, incumbents, carry_applied = apply_multi_new_breadth_gate(
            previous_weights, decision.target_weights
        )
        classified = _classify_positive_targets(previous_weights, decision.target_weights)
        diagnostics = dict(decision.diagnostics)
        diagnostics.update(
            {
                "staged_reentry_codes": sorted(staged),
                "incumbent_carry_codes": sorted(incumbents),
                "multi_new_breadth_gate_triggered": bool(
                    classified and len(classified[0]) >= 2 and len(classified[1]) == 1
                ),
                "multi_new_breadth_gate_rule": (
                    "cancel_carry_when_one_positive_incumbent_and_at_least_two_staged_targets"
                ),
            }
        )
        decision = replace(
            decision,
            decision_id=f"{context.signal_date}-{DESCRIPTOR.id}",
            target_weights=target_weights,
            cash_weight=cash_weight,
            reason_code=_append_reason(
                decision.reason_code, "INCUMBENT_CARRY" if carry_applied else ""
            ),
            diagnostics=diagnostics,
        )
        self._patch_artifacts(decision)
        return decision


class AiRotationR43MultiNewBreadthGateStrategy(AiRotationR39IncumbentCarryStrategy):
    """Complete round 43 strategy plug-in."""

    descriptor = DESCRIPTOR

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = super().describe_decision_pipeline(config)
        pipeline["selection_rule"] += (
            "; cancel R39 carry only when exactly one positive incumbent and at least two "
            "staged positive new targets are present"
        )
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        return super().resolve_requirements(config)

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> AiRotationR43MultiNewBreadthGateSession:
        del initialization
        return AiRotationR43MultiNewBreadthGateSession(config)  # type: ignore[arg-type]
