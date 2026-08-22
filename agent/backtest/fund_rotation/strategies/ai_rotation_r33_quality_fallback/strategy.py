"""Round 33: bounded broad-ETF fallback after a cluster-quality reject."""

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
    id="ai_rotation_r33_quality_fallback",
    name="质量拒绝宽基回退持续几何动量相关性代表ETF",
    description=(
        "完全沿用 R11，仅在聚类质量拒绝、510300.SH 四周收益为正且存在"
        "空槽现金时，用一个固定槽位回退至宽基 ETF。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)

_BENCHMARK_CODE = "510300.SH"


def apply_quality_fallback(
    target_weights: Mapping[str, float],
    cash_weight: float,
    *,
    quality_rejected: bool,
    benchmark_return: object,
    benchmark_code: str,
    top_n: int,
) -> tuple[dict[str, float], float, bool]:
    """Use at most one vacant slot as a broad-ETF fallback under risk-on quality reject."""
    try:
        value = float(benchmark_return) if benchmark_return is not None else math.nan
    except (TypeError, ValueError):
        value = math.nan
    fallback_weight = (
        min(float(cash_weight), 1.0 / top_n)
        if quality_rejected
        and math.isfinite(value)
        and value > 0.0
        and cash_weight > 0.0
        else 0.0
    )
    adjusted = {code: float(weight) for code, weight in target_weights.items()}
    if fallback_weight <= 0.0:
        return adjusted, max(0.0, 1.0 - sum(adjusted.values())), False
    adjusted[benchmark_code] = adjusted.get(benchmark_code, 0.0) + fallback_weight
    return adjusted, max(0.0, 1.0 - sum(adjusted.values())), True


def _benchmark_momentum(weekly_returns, code: str, window: int) -> float | None:
    if code not in weekly_returns.columns or len(weekly_returns) < window:
        return None
    try:
        values = [float(value) for value in weekly_returns[code].tail(window).tolist()]
    except (TypeError, ValueError):
        return None
    if any(not math.isfinite(value) for value in values):
        return None
    result = float(math.prod(1.0 + value for value in values) - 1.0)
    return result if math.isfinite(result) else None


def _append_reason(existing: str, addition: str) -> str:
    return f"{existing}|{addition}" if existing and addition else existing or addition


class AiRotationR33QualityFallbackSession(AiRotationR11PersistGeomSession):
    """R11 session with a bounded risk-on fallback for rejected clusters."""

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
        quality_rejected = decision.reason_code == "CLUSTER_QUALITY_REJECTED"
        target_weights, cash_weight, used = apply_quality_fallback(
            decision.target_weights,
            decision.cash_weight,
            quality_rejected=quality_rejected,
            benchmark_return=benchmark_return,
            benchmark_code=_BENCHMARK_CODE,
            top_n=self._config.top_n,
        )
        diagnostics = dict(decision.diagnostics)
        diagnostics.update(
            {
                "quality_fallback_benchmark": _BENCHMARK_CODE,
                "quality_fallback_four_week_return": benchmark_return,
                "quality_fallback_used": used,
                "quality_fallback_rule": "one_slot_only_under_quality_reject_and_positive_benchmark",
            }
        )
        decision = replace(
            decision,
            decision_id=f"{context.signal_date}-{DESCRIPTOR.id}",
            target_weights=target_weights,
            cash_weight=cash_weight,
            reason_code=_append_reason(
                decision.reason_code,
                "QUALITY_REJECTED_BROAD_FALLBACK" if used else "",
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


class AiRotationR33QualityFallbackStrategy:
    """Complete round 33 strategy plug-in."""

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
        pipeline["selection_rule"] += "; one 510300.SH slot may fill vacant cash after a rejected quality gate"
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        return CorrelationRepresentativeStrategy().resolve_requirements(config)

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> AiRotationR33QualityFallbackSession:
        del initialization
        return AiRotationR33QualityFallbackSession(config)  # type: ignore[arg-type]
