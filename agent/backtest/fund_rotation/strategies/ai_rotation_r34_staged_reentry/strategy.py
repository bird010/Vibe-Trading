"""Round 34: R11 with half-sized new representative entries."""

from __future__ import annotations

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
    id="ai_rotation_r34_staged_reentry",
    name="半仓试探再入场持续几何动量相关性代表ETF",
    description=(
        "完全沿用 R11，仅将相对上一周新出现的代表 ETF 目标权重减半，"
        "下一周仍被选中时恢复完整权重。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)


def apply_staged_reentry(
    previous_weights: Mapping[str, float],
    target_weights: Mapping[str, float],
    staging_fraction: float = 0.5,
) -> tuple[dict[str, float], float, set[str]]:
    """Half-size only representatives with no positive weight in the prior target."""
    adjusted: dict[str, float] = {}
    staged: set[str] = set()
    for code, weight in target_weights.items():
        if previous_weights.get(code, 0.0) > 0.0:
            adjusted[code] = float(weight)
        else:
            adjusted[code] = float(weight) * staging_fraction
            staged.add(code)
    return adjusted, max(0.0, 1.0 - sum(adjusted.values())), staged


def _append_reason(existing: str, addition: str) -> str:
    return f"{existing}|{addition}" if existing and addition else existing or addition


class AiRotationR34StagedReentrySession(AiRotationR11PersistGeomSession):
    """R11 session with a fixed half-size new-entry overlay."""

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        previous_weights = dict(self._previous_weights)
        decision = super().evaluate(context)
        target_weights, cash_weight, staged = apply_staged_reentry(
            previous_weights,
            decision.target_weights,
        )
        diagnostics = dict(decision.diagnostics)
        diagnostics.update(
            {
                "staged_reentry_fraction": 0.5,
                "staged_reentry_codes": sorted(staged),
                "staged_reentry_rule": "new_representative_target_weight_halved_once",
            }
        )
        decision = replace(
            decision,
            decision_id=f"{context.signal_date}-{DESCRIPTOR.id}",
            target_weights=target_weights,
            cash_weight=cash_weight,
            reason_code=_append_reason(
                decision.reason_code,
                "STAGED_REENTRY" if staged else "",
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


class AiRotationR34StagedReentryStrategy:
    """Complete round 34 strategy plug-in."""

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
        pipeline["selection_rule"] += "; new representative entries use 50% size for one week"
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        return CorrelationRepresentativeStrategy().resolve_requirements(config)

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> AiRotationR34StagedReentrySession:
        del initialization
        return AiRotationR34StagedReentrySession(config)  # type: ignore[arg-type]
