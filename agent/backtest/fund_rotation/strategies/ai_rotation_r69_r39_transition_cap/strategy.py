"""Batch B: R39 with a cap on one-week positive target exposure changes."""

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
from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import (
    AiRotationR39IncumbentCarrySession,
    AiRotationR39IncumbentCarryStrategy,
)
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    CorrelationRepresentativeStrategy,
)


def apply_transition_cap(
    previous_weights: object,
    target_weights: object,
    cap: float,
) -> tuple[dict[str, float], float, float, bool]:
    """Scale only positive target increases when their sum exceeds ``cap``."""
    previous = previous_weights if isinstance(previous_weights, Mapping) else {}
    target = target_weights if isinstance(target_weights, Mapping) else {}
    try:
        cap_value = float(cap)
        if not math.isfinite(cap_value) or cap_value < 0.0:
            raise ValueError("invalid cap")
        previous_values = {
            str(code): max(0.0, float(weight)) for code, weight in previous.items()
        }
        target_values = {
            str(code): max(0.0, float(weight)) for code, weight in target.items()
        }
    except (AttributeError, TypeError, ValueError, OverflowError):
        return {}, 1.0, 0.0, False

    increases = {
        code: max(0.0, target_values.get(code, 0.0) - previous_values.get(code, 0.0))
        for code in sorted(set(previous_values) | set(target_values))
    }
    positive_exposure = math.fsum(increases.values())
    scale = min(1.0, cap_value / positive_exposure) if positive_exposure > 0.0 else 1.0
    adjusted = {}
    for code in sorted(set(previous_values) | set(target_values)):
        target_value = target_values.get(code, 0.0)
        previous_value = previous_values.get(code, 0.0)
        adjusted_value = (
            previous_value + (target_value - previous_value) * scale
            if target_value > previous_value
            else target_value
        )
        if adjusted_value > 0.0:
            adjusted[code] = adjusted_value
    total = math.fsum(adjusted.values())
    if total > 1.0 + 1e-12:
        return (
            target_values,
            max(0.0, 1.0 - math.fsum(target_values.values())),
            positive_exposure,
            False,
        )
    return adjusted, max(0.0, 1.0 - total), positive_exposure, scale < 1.0


class _TransitionCapSession(AiRotationR39IncumbentCarrySession):
    CAP: float = 0.5
    STRATEGY_ID: str = ""

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        previous_weights = dict(self._previous_weights)
        decision = super().evaluate(context)
        target_weights, cash_weight, positive_exposure, capped = apply_transition_cap(
            previous_weights, decision.target_weights, self.CAP
        )
        diagnostics = dict(decision.diagnostics)
        diagnostics.update(
            {
                "transition_cap": {
                    "cap": self.CAP,
                    "positive_target_exposure_change": positive_exposure,
                    "capped": capped,
                }
            }
        )
        decision = replace(
            decision,
            decision_id=f"{context.signal_date}-{self.STRATEGY_ID}",
            target_weights=target_weights,
            cash_weight=cash_weight,
            diagnostics=diagnostics,
        )
        self._patch_artifacts(decision)
        return decision


class AiRotationR69R39TransitionCap50Session(_TransitionCapSession):
    CAP = 0.50
    STRATEGY_ID = "ai_rotation_r69_r39_transition_cap_50"


class AiRotationR70R39TransitionCap25Session(_TransitionCapSession):
    CAP = 0.25
    STRATEGY_ID = "ai_rotation_r70_r39_transition_cap_25"


def _descriptor(strategy_id: str, name: str, cap: float) -> FundRotationStrategyDescriptor:
    return FundRotationStrategyDescriptor(
        id=strategy_id,
        name=name,
        description=(
            f"完全沿用 R39，仅将单周新增目标风险暴露限制为 {cap:.0%}，"
            "超出部分同比缩放，释放权重保留为现金。"
        ),
        interface_version="1.0",
        supported_universe=("etf",),
        deterministic=True,
    )


class _TransitionCapStrategy(AiRotationR39IncumbentCarryStrategy):
    session_class = _TransitionCapSession

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = super().describe_decision_pipeline(config)
        pipeline["transition_cap_rule"] = (
            f"one-week positive target exposure capped at "
            f"{self.session_class.CAP:.0%}"
        )
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        return CorrelationRepresentativeStrategy().resolve_requirements(config)

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> _TransitionCapSession:
        del initialization
        return self.session_class(config)


class AiRotationR69R39TransitionCap50Strategy(_TransitionCapStrategy):
    descriptor = _descriptor(
        "ai_rotation_r69_r39_transition_cap_50", "R39单周新增风险50%上限", 0.50
    )
    session_class = AiRotationR69R39TransitionCap50Session


class AiRotationR70R39TransitionCap25Strategy(_TransitionCapStrategy):
    descriptor = _descriptor(
        "ai_rotation_r70_r39_transition_cap_25", "R39单周新增风险25%上限", 0.25
    )
    session_class = AiRotationR70R39TransitionCap25Session
