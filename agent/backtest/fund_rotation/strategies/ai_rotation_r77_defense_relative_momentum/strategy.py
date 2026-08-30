"""Round 77: a frozen defense pool ranked by causal relative momentum."""

from __future__ import annotations

import math
from dataclasses import replace

from pydantic import BaseModel

from backtest.fund_rotation.contracts import (
    FundRotationStrategyDescriptor,
    StrategyDecisionContext,
    StrategyInitializationContext,
    TargetWeightDecision,
)
from backtest.fund_rotation.risk_layers import apply_defense_asset, select_defense_asset
from backtest.fund_rotation.strategies.ai_rotation_r34_staged_reentry.strategy import _append_reason
from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import (
    AiRotationR39IncumbentCarrySession,
    AiRotationR39IncumbentCarryStrategy,
)


DEFENSE_POOL = ("511010.SH", "511880.SH", "518880.SH")
FIXED_SHORT_BOND = DEFENSE_POOL[0]
DEFENSE_WINDOW_WEEKS = 4

DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r77_defense_relative_momentum",
    name="R39 防御资产相对动量",
    description=(
        "完全沿用 R39；只有现金部分从冻结防御池中按因果相对动量选择，"
        "无有效分数时回退现金，不使用事后最优资产回填。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)


def compute_defense_relative_momentum(weekly_returns: object) -> dict[str, float | None]:
    result: dict[str, float | None] = {code: None for code in DEFENSE_POOL}
    if not hasattr(weekly_returns, "iloc") or not hasattr(weekly_returns, "columns"):
        return result
    try:
        recent = weekly_returns.iloc[-DEFENSE_WINDOW_WEEKS:]
        for code in DEFENSE_POOL:
            if code not in recent.columns or len(recent) < DEFENSE_WINDOW_WEEKS:
                continue
            values = [float(value) for value in recent[code].tolist()]
            if any(not math.isfinite(value) for value in values):
                continue
            score = math.prod(1.0 + value for value in values) - 1.0
            result[code] = score if math.isfinite(score) else None
    except (AttributeError, KeyError, TypeError, ValueError):
        return result
    return result


class AiRotationR77DefenseRelativeMomentumSession(AiRotationR39IncumbentCarrySession):
    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        decision = super().evaluate(context)
        try:
            weekly_returns = context.data_view.returns(
                "weekly", self._config.correlation_lookback_weeks
            )
        except (AttributeError, KeyError, TypeError, ValueError):
            weekly_returns = None
        relative_scores = compute_defense_relative_momentum(weekly_returns)
        defense_code = select_defense_asset(
            "relative_momentum",
            fixed_short_bond=FIXED_SHORT_BOND,
            relative_scores=relative_scores,
        )
        weights, cash, diagnostics = apply_defense_asset(
            decision.target_weights,
            decision.cash_weight,
            defense_code=defense_code,
        )
        diagnostics.update(
            {
                "risk_layer": "defense_relative_momentum",
                "defense_arm": "relative_momentum",
                "defense_pool": list(DEFENSE_POOL),
                "relative_momentum": relative_scores,
            }
        )
        decision = replace(
            decision,
            decision_id=f"{context.signal_date}-{DESCRIPTOR.id}",
            target_weights=weights,
            cash_weight=cash,
            reason_code=_append_reason(
                decision.reason_code,
                "DEFENSE_CASH_FALLBACK" if defense_code is None else "DEFENSE_RELATIVE_MOMENTUM",
            ),
            diagnostics={**dict(decision.diagnostics), **diagnostics},
        )
        self._previous_weights = dict(weights)
        self._patch_artifacts(decision)
        return decision


class AiRotationR77DefenseRelativeMomentumStrategy(AiRotationR39IncumbentCarryStrategy):
    descriptor = DESCRIPTOR

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = super().describe_decision_pipeline(config)
        pipeline["defense_layer"] = {
            "arm": "relative_momentum",
            "pool": list(DEFENSE_POOL),
            "window_weeks": DEFENSE_WINDOW_WEEKS,
            "fallback": "cash",
        }
        return pipeline

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> AiRotationR77DefenseRelativeMomentumSession:
        del initialization
        return AiRotationR77DefenseRelativeMomentumSession(config)  # type: ignore[arg-type]
