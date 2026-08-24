"""Round 49: preserve a micro cash floor after R39 carry."""

from __future__ import annotations

import math
from dataclasses import replace

from pydantic import BaseModel

from backtest.fund_rotation.contracts import FundRotationStrategyDescriptor, StrategyDataRequirements, StrategyDecisionContext, StrategyInitializationContext, TargetWeightDecision
from backtest.fund_rotation.strategies.ai_rotation_r34_staged_reentry.strategy import AiRotationR34StagedReentrySession, _append_reason
from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import AiRotationR39IncumbentCarrySession, AiRotationR39IncumbentCarryStrategy
from backtest.fund_rotation.strategies.ai_rotation_r41_breadth_gated_carry.strategy import _classify_positive_targets, _r34_baseline, _r39_or_safe_baseline

CASH_FLOOR = 1 / 48

DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r49_cash_floor_micro",
    name="承接释放权重保留四十八分之一现金持续几何动量相关性代表ETF",
    description=("完全沿用 R39；仅当 carry 后现金低于 1/48 时按原 carry 比例削减 carry，使现金回到 1/48，其余情况逐值沿用 R39。"),
    interface_version="1.0", supported_universe=("etf",), deterministic=True,
)


def apply_cash_floor_carry(previous_weights: object, staged_target_weights: object) -> tuple[dict[str, float], float, set[str], set[str], bool]:
    r39_targets, r39_cash, r39_staged, r39_incumbents = _r39_or_safe_baseline(previous_weights, staged_target_weights)
    if r39_cash >= CASH_FLOOR:
        return r39_targets, r39_cash, r39_staged, r39_incumbents, bool(r39_incumbents)
    classified = _classify_positive_targets(previous_weights, staged_target_weights)
    if classified is None:
        return r39_targets, r39_cash, r39_staged, r39_incumbents, bool(r39_incumbents)
    staged, incumbents = classified
    baseline_targets, _, _ = _r34_baseline(previous_weights, staged_target_weights)
    carry_by_code = {code: r39_targets.get(code, 0.0) - baseline_targets.get(code, 0.0) for code in incumbents}
    total_carry = math.fsum(value for value in carry_by_code.values() if value > 0.0)
    reduction = CASH_FLOOR - r39_cash
    if not staged or not incumbents or total_carry <= 0.0 or reduction <= 0.0 or reduction > total_carry + 1e-9:
        return r39_targets, r39_cash, r39_staged, r39_incumbents, bool(r39_incumbents)
    adjusted = dict(r39_targets)
    for code, carry in carry_by_code.items():
        if carry > 0.0:
            adjusted[code] -= reduction * carry / total_carry
    if not math.isfinite(math.fsum(adjusted.values())):
        return r39_targets, r39_cash, r39_staged, r39_incumbents, bool(r39_incumbents)
    return adjusted, CASH_FLOOR, r39_staged, r39_incumbents, True


class AiRotationR49CashFloorMicroSession(AiRotationR39IncumbentCarrySession):
    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        previous_weights = dict(self._previous_weights)
        decision = AiRotationR34StagedReentrySession.evaluate(self, context)
        target_weights, cash_weight, staged, incumbents, carry_applied = apply_cash_floor_carry(previous_weights, decision.target_weights)
        diagnostics = dict(decision.diagnostics)
        diagnostics.update({"staged_reentry_codes": sorted(staged), "incumbent_carry_codes": sorted(incumbents), "cash_floor": CASH_FLOOR, "cash_floor_rule": "preserve_one_forty_eighth_cash_after_r39_carry_only_when_floor_breached"})
        decision = replace(decision, decision_id=f"{context.signal_date}-{DESCRIPTOR.id}", target_weights=target_weights, cash_weight=cash_weight, reason_code=_append_reason(decision.reason_code, "INCUMBENT_CARRY" if carry_applied else ""), diagnostics=diagnostics)
        self._patch_artifacts(decision)
        return decision


class AiRotationR49CashFloorMicroStrategy(AiRotationR39IncumbentCarryStrategy):
    descriptor = DESCRIPTOR

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = super().describe_decision_pipeline(config)
        pipeline["selection_rule"] += "; preserve a fixed 1/48 cash floor after R39 carry only when breached"
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        return super().resolve_requirements(config)

    def create_session(self, initialization: StrategyInitializationContext, config: BaseModel) -> AiRotationR49CashFloorMicroSession:
        del initialization
        return AiRotationR49CashFloorMicroSession(config)  # type: ignore[arg-type]
