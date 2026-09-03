"""R86: repaired R81 economic-role session with a 50% transition cap."""

from __future__ import annotations

from dataclasses import replace

from pydantic import BaseModel

from backtest.fund_rotation.contracts import (
    FundRotationStrategyDescriptor,
    StrategyDecisionContext,
    StrategyInitializationContext,
    TargetWeightDecision,
)
from backtest.fund_rotation.strategies.ai_rotation_r69_r39_transition_cap.strategy import (
    apply_transition_cap,
)
from backtest.fund_rotation.strategies.economic_role_rotation.strategy import (
    EconomicRoleSession,
    _EconomicRoleStrategy,
)


DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r86_r81_transition_cap_50",
    name="R81 动态代表单周新增风险 50% 上限",
    description=(
        "完全沿用修复后的 R81 经济角色动态代表、生命周期和防御资格；"
        "仅将单周正向目标暴露增量限制为 50%，超出部分释放为现金。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)


class EconomicRoleR81TransitionCap50Session(EconomicRoleSession):
    """R81 session with a post-decision-only positive-exposure cap."""

    CAP = 0.50
    STRATEGY_ID = DESCRIPTOR.id
    _R81_DESCRIPTOR_ID = "ai_rotation_r81_economic_role_dynamic_rep"

    def __init__(self, config) -> None:
        super().__init__(config, self._R81_DESCRIPTOR_ID)
        self.score_subject = "REPRESENTATIVE"
        self.representative_mode = "DYNAMIC"

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        previous_weights = dict(self._previous_weights)
        decision = super().evaluate(context)
        target_weights, cash_weight, positive_exposure, capped = apply_transition_cap(
            previous_weights, decision.target_weights, self.CAP
        )
        diagnostics = dict(decision.diagnostics)
        diagnostics["transition_cap"] = {
            "cap": self.CAP,
            "positive_target_exposure_change": positive_exposure,
            "capped": capped,
        }
        decision = replace(
            decision,
            decision_id=f"{decision.signal_date}-{self.STRATEGY_ID}",
            target_weights=target_weights,
            cash_weight=cash_weight,
            diagnostics=diagnostics,
        )
        self._patch_artifacts(decision)
        return decision

    def _patch_artifacts(self, decision: TargetWeightDecision) -> None:
        """Keep stateful R81 evidence aligned with the capped decision."""
        self._previous_weights = dict(decision.target_weights)
        decision_log = getattr(self, "_decision_log", None)
        if decision_log:
            decision_log[-1].update(
                {
                    "target_weights": dict(decision.target_weights),
                    "cash_weight": decision.cash_weight,
                    "diagnostics": dict(decision.diagnostics),
                }
            )
        decision_trace = getattr(self, "_decision_trace", None)
        if decision_trace:
            for row in decision_trace[-1].get("candidates", []):
                code = row.get("ts_code")
                if isinstance(code, str):
                    row["target_weight"] = float(
                        decision.target_weights.get(code, 0.0)
                    )


class AiRotationR86R81TransitionCap50Strategy(_EconomicRoleStrategy):
    descriptor = DESCRIPTOR
    score_subject = "REPRESENTATIVE"
    representative_mode = "DYNAMIC"

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = super().describe_decision_pipeline(config)
        pipeline["transition_cap_rule"] = (
            "one-week positive target exposure capped at 50%"
        )
        return pipeline

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> EconomicRoleR81TransitionCap50Session:
        del initialization
        return EconomicRoleR81TransitionCap50Session(config)
