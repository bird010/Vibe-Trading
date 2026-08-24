"""Round 47: apply the tight cash floor only with incumbent breadth."""

from __future__ import annotations

from dataclasses import replace

from pydantic import BaseModel

from backtest.fund_rotation.contracts import FundRotationStrategyDescriptor, StrategyDataRequirements, StrategyDecisionContext, StrategyInitializationContext, TargetWeightDecision
from backtest.fund_rotation.strategies.ai_rotation_r34_staged_reentry.strategy import AiRotationR34StagedReentrySession, _append_reason
from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import AiRotationR39IncumbentCarrySession, AiRotationR39IncumbentCarryStrategy
from backtest.fund_rotation.strategies.ai_rotation_r41_breadth_gated_carry.strategy import _classify_positive_targets, _r39_or_safe_baseline
from backtest.fund_rotation.strategies.ai_rotation_r45_cash_floor_carry.strategy_r46_cash_floor_tight import apply_cash_floor_carry

CASH_FLOOR = 1 / 12

DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r47_breadth_tight_floor",
    name="多持续目标时保留十二分之一现金承接释放权重持续几何动量相关性代表ETF",
    description=(
        "完全沿用 R39；仅当至少两个 incumbent 且 carry 后现金低于 1/12 时按比例削减 carry，"
        "单一 incumbent 或其他状态逐值沿用 R39。"
    ),
    interface_version="1.0", supported_universe=("etf",), deterministic=True,
)


def apply_breadth_tight_floor(previous_weights: object, staged_target_weights: object) -> tuple[dict[str, float], float, set[str], set[str], bool]:
    r39 = _r39_or_safe_baseline(previous_weights, staged_target_weights)
    classified = _classify_positive_targets(previous_weights, staged_target_weights)
    if classified is None or len(classified[1]) < 2:
        return r39[0], r39[1], r39[2], r39[3], bool(r39[3])
    return apply_cash_floor_carry(previous_weights, staged_target_weights)


class AiRotationR47BreadthTightFloorSession(AiRotationR39IncumbentCarrySession):
    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        previous_weights = dict(self._previous_weights)
        decision = AiRotationR34StagedReentrySession.evaluate(self, context)
        target_weights, cash_weight, staged, incumbents, carry_applied = apply_breadth_tight_floor(previous_weights, decision.target_weights)
        diagnostics = dict(decision.diagnostics)
        diagnostics.update({"staged_reentry_codes": sorted(staged), "incumbent_carry_codes": sorted(incumbents), "cash_floor": CASH_FLOOR, "cash_floor_rule": "preserve_one_twelfth_cash_only_when_at_least_two_incumbents_and_floor_breached"})
        decision = replace(decision, decision_id=f"{context.signal_date}-{DESCRIPTOR.id}", target_weights=target_weights, cash_weight=cash_weight, reason_code=_append_reason(decision.reason_code, "INCUMBENT_CARRY" if carry_applied else ""), diagnostics=diagnostics)
        self._patch_artifacts(decision)
        return decision


class AiRotationR47BreadthTightFloorStrategy(AiRotationR39IncumbentCarryStrategy):
    descriptor = DESCRIPTOR

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = super().describe_decision_pipeline(config)
        pipeline["selection_rule"] += "; preserve a fixed 1/12 cash floor only when at least two incumbents remain"
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        return super().resolve_requirements(config)

    def create_session(self, initialization: StrategyInitializationContext, config: BaseModel) -> AiRotationR47BreadthTightFloorSession:
        del initialization
        return AiRotationR47BreadthTightFloorSession(config)  # type: ignore[arg-type]
