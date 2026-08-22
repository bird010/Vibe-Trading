"""Round 32: R11 with a broad-market risk-off filter."""

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
from backtest.fund_rotation.strategies.ai_rotation_r11_persist_geom.strategy import (
    AiRotationR11PersistGeomSession,
    AiRotationR11PersistGeomStrategy,
)
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    CorrelationRepresentativeStrategy,
)


DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r32_market_regime",
    name="市场状态过滤持续几何动量相关性代表ETF",
    description=(
        "完全沿用 R11，在 510300.SH 四周复合收益非正时暂停所有风险资产"
        "目标，宽基数据不可用时保持 R11 行为。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)

_BENCHMARK_CODE = "510300.SH"


def apply_market_regime(
    target_weights: Mapping[str, float],
    benchmark_return: object,
) -> tuple[dict[str, float], float, bool]:
    """Set all targets to cash only when the finite benchmark return is non-positive."""
    try:
        value = float(benchmark_return) if benchmark_return is not None else math.nan
    except (TypeError, ValueError):
        value = math.nan
    if not math.isfinite(value) or value > 0.0:
        adjusted = {code: float(weight) for code, weight in target_weights.items()}
        return adjusted, max(0.0, 1.0 - sum(adjusted.values())), False
    return {}, 1.0, True


def _benchmark_momentum(weekly_returns, code: str, window: int) -> float | None:
    if code not in weekly_returns.columns or len(weekly_returns) < window:
        return None
    values = weekly_returns[code].tail(window).tolist()
    try:
        numeric = [float(value) for value in values]
    except (TypeError, ValueError):
        return None
    if any(not math.isfinite(value) for value in numeric):
        return None
    result = float(math.prod(1.0 + value for value in numeric) - 1.0)
    return result if math.isfinite(result) else None


def _append_reason(existing: str, addition: str) -> str:
    return f"{existing}|{addition}" if existing and addition else existing or addition


class AiRotationR32MarketRegimeSession(AiRotationR11PersistGeomSession):
    """R11 session with a causal 510300.SH risk-off filter."""

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        decision = super().evaluate(context)
        weekly_returns = context.data_view.returns(
            "weekly",
            self._config.correlation_lookback_weeks,
        )
        benchmark_return = _benchmark_momentum(
            weekly_returns,
            _BENCHMARK_CODE,
            self._config.momentum_window_weeks,
        )
        target_weights, cash_weight, risk_off = apply_market_regime(
            decision.target_weights,
            benchmark_return,
        )
        diagnostics = dict(decision.diagnostics)
        diagnostics.update(
            {
                "market_regime_benchmark": _BENCHMARK_CODE,
                "market_regime_four_week_return": benchmark_return,
                "market_regime": "RISK_OFF" if risk_off else (
                    "RISK_ON" if benchmark_return is not None else "UNAVAILABLE"
                ),
                "market_regime_rule": "510300_SH_four_week_return_non_positive",
            }
        )
        decision = replace(
            decision,
            decision_id=f"{context.signal_date}-{DESCRIPTOR.id}",
            target_weights=target_weights,
            cash_weight=cash_weight,
            reason_code=_append_reason(
                decision.reason_code,
                "MARKET_REGIME_RISK_OFF" if risk_off else "",
            ),
            diagnostics=diagnostics,
        )
        self._patch_artifacts(decision)
        return decision

    def _patch_artifacts(self, decision: TargetWeightDecision) -> None:
        if self._decision_log:
            self._decision_log[-1].update(
                {
                    "target_weights": dict(decision.target_weights),
                    "cash_weight": decision.cash_weight,
                    "reason_code": decision.reason_code,
                    "diagnostics": dict(decision.diagnostics),
                }
            )
        if self._decision_trace:
            for candidate in self._decision_trace[-1].get("candidates", []):
                code = candidate.get("ts_code")
                candidate["target_weight"] = float(
                    decision.target_weights.get(code, 0.0)
                )
                candidate["stages"]["portfolio_selected"] = (
                    code in decision.target_weights
                )
        self._previous_weights = dict(decision.target_weights)


class AiRotationR32MarketRegimeStrategy:
    """Complete round 32 strategy plug-in."""

    descriptor = DESCRIPTOR
    config_model = CorrelationRepresentativeStrategy.config_model
    artifact_roles: tuple[str, ...] = (
        "cluster_history",
        "gates",
        "representatives",
        "exclusions",
        "decisions",
    )

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = AiRotationR11PersistGeomStrategy().describe_decision_pipeline(config)
        pipeline["selection_rule"] += "; cash-only when 510300.SH four-week return is non-positive"
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        return CorrelationRepresentativeStrategy().resolve_requirements(config)

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> AiRotationR32MarketRegimeSession:
        del initialization
        return AiRotationR32MarketRegimeSession(config)  # type: ignore[arg-type]
