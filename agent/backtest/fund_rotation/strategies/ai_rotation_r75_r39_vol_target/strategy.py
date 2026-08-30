"""Round 75: R39 with one fixed, non-levered volatility target."""

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
from backtest.fund_rotation.risk_layers import apply_volatility_target, compute_portfolio_volatility
from backtest.fund_rotation.strategies.ai_rotation_r34_staged_reentry.strategy import _append_reason
from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import (
    AiRotationR39IncumbentCarrySession,
    AiRotationR39IncumbentCarryStrategy,
)


TARGET_VOLATILITY = 0.15

DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r75_r39_vol_target",
    name="R39 固定目标波动率风险层",
    description=(
        "完全沿用 R39 选基、staging、carry 和执行规则，仅按一个预注册目标波动率"
        "缩放已有多头目标；exposure=min(1,target/portfolio_volatility)，不使用杠杆。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)


class AiRotationR75R39VolTargetSession(AiRotationR39IncumbentCarrySession):
    """R39 session with a fixed, fail-closed exposure cap."""

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        decision = super().evaluate(context)
        try:
            weekly_returns = context.data_view.returns(
                "weekly", self._config.correlation_lookback_weeks
            )
        except (AttributeError, KeyError, TypeError, ValueError):
            weekly_returns = None
        volatility = compute_portfolio_volatility(weekly_returns, decision.target_weights)
        weights, cash, diagnostics = apply_volatility_target(
            decision.target_weights,
            portfolio_volatility=volatility,
            target_volatility=TARGET_VOLATILITY,
        )
        diagnostics.update(
            {
                "risk_layer": "fixed_target_volatility",
                "target_volatility": TARGET_VOLATILITY,
                "no_leverage": True,
            }
        )
        decision = replace(
            decision,
            decision_id=f"{context.signal_date}-{DESCRIPTOR.id}",
            target_weights=weights,
            cash_weight=cash,
            reason_code=_append_reason(
                decision.reason_code,
                "VOLATILITY_TARGET_UNAVAILABLE" if diagnostics["reason"] else "",
            ),
            diagnostics={**dict(decision.diagnostics), **diagnostics},
        )
        self._previous_weights = dict(weights)
        self._patch_artifacts(decision)
        return decision


class AiRotationR75R39VolTargetStrategy(AiRotationR39IncumbentCarryStrategy):
    descriptor = DESCRIPTOR

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = super().describe_decision_pipeline(config)
        pipeline["risk_layer"] = {
            "target_volatility": TARGET_VOLATILITY,
            "formula": "min(1, target_volatility / portfolio_volatility)",
            "leverage": False,
        }
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        return super().resolve_requirements(config)

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> AiRotationR75R39VolTargetSession:
        del initialization
        return AiRotationR75R39VolTargetSession(config)  # type: ignore[arg-type]
