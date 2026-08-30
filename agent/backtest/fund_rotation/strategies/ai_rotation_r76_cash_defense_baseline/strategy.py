"""Round 76: cash remains the defense baseline."""

from __future__ import annotations

from dataclasses import replace

from pydantic import BaseModel

from backtest.fund_rotation.contracts import (
    FundRotationStrategyDescriptor,
    StrategyDecisionContext,
    StrategyInitializationContext,
    TargetWeightDecision,
)
from backtest.fund_rotation.risk_layers import apply_defense_asset
from backtest.fund_rotation.strategies.ai_rotation_r34_staged_reentry.strategy import _append_reason
from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import (
    AiRotationR39IncumbentCarrySession,
    AiRotationR39IncumbentCarryStrategy,
)


DEFENSE_ARM = "cash"
FIXED_SHORT_BOND = "511010.SH"

DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r76_cash_defense_baseline",
    name="R39 现金防御基线",
    description="完全沿用 R39；风险层只保留现金防御基线，不引入绝对动量。",
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)


class AiRotationR76CashDefenseBaselineSession(AiRotationR39IncumbentCarrySession):
    defense_code = None
    defense_arm = DEFENSE_ARM

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        decision = super().evaluate(context)
        weights, cash, diagnostics = apply_defense_asset(
            decision.target_weights,
            decision.cash_weight,
            defense_code=self.defense_code,
        )
        reason = (
            "CASH_DEFENSE_BASELINE"
            if self.defense_arm == DEFENSE_ARM
            else "FIXED_SHORT_BOND_DEFENSE"
        )
        diagnostics.update({"risk_layer": self.defense_arm, "defense_arm": self.defense_arm})
        decision = replace(
            decision,
            decision_id=f"{context.signal_date}-{DESCRIPTOR.id}",
            target_weights=weights,
            cash_weight=cash,
            reason_code=_append_reason(decision.reason_code, reason),
            diagnostics={**dict(decision.diagnostics), **diagnostics},
        )
        self._previous_weights = dict(weights)
        self._patch_artifacts(decision)
        return decision


class AiRotationR76FixedShortBondSession(AiRotationR76CashDefenseBaselineSession):
    defense_code = FIXED_SHORT_BOND
    defense_arm = "fixed_short_bond"


class AiRotationR76CashDefenseBaselineStrategy(AiRotationR39IncumbentCarryStrategy):
    descriptor = DESCRIPTOR

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = super().describe_decision_pipeline(config)
        pipeline["defense_layer"] = {"arm": DEFENSE_ARM, "fixed_short_bond": FIXED_SHORT_BOND}
        return pipeline

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> AiRotationR76CashDefenseBaselineSession:
        del initialization
        return AiRotationR76CashDefenseBaselineSession(config)  # type: ignore[arg-type]


FIXED_SHORT_BOND_DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r76_fixed_short_bond",
    name="R39 固定短债防御",
    description="完全沿用 R39；现金部分固定配置预注册短债，不引入绝对动量。",
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)


class AiRotationR76FixedShortBondStrategy(AiRotationR39IncumbentCarryStrategy):
    descriptor = FIXED_SHORT_BOND_DESCRIPTOR

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = super().describe_decision_pipeline(config)
        pipeline["defense_layer"] = {
            "arm": "fixed_short_bond",
            "asset": FIXED_SHORT_BOND,
        }
        return pipeline

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> AiRotationR76FixedShortBondSession:
        del initialization
        return AiRotationR76FixedShortBondSession(config)  # type: ignore[arg-type]
