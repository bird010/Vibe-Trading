"""Round 31: R11 with a one-week negative-return exit brake."""

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
from backtest.fund_rotation.momentum import compute_cluster_momentum
from backtest.fund_rotation.strategies.ai_rotation_r11_persist_geom.strategy import (
    AiRotationR11PersistGeomSession,
    AiRotationR11PersistGeomStrategy,
)
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    CorrelationRepresentativeStrategy,
)


DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r31_fast_exit",
    name="一周风险刹车持续几何动量相关性代表ETF",
    description=(
        "完全沿用 R11 入场与评分，仅对已持有簇增加一周簇收益非正时的"
        "退出刹车，以缩短趋势反转的反应时间。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)


def apply_fast_exit(
    previous_weights: Mapping[str, float],
    target_weights: Mapping[str, float],
    cluster_by_code: Mapping[str, int],
    one_week_cluster_returns: Mapping[int, object],
    top_n: int,
) -> tuple[dict[str, float], float, set[int]]:
    """Remove already-held clusters whose latest weekly return is non-positive."""
    del top_n  # The helper preserves R11 fixed-slot weights for all survivors.
    exited: set[int] = set()
    for code, weight in previous_weights.items():
        if weight <= 0.0:
            continue
        cluster_id = cluster_by_code.get(code)
        if cluster_id is None:
            continue
        raw_return = one_week_cluster_returns.get(cluster_id)
        try:
            weekly_return = float(raw_return) if raw_return is not None else math.nan
        except (TypeError, ValueError):
            weekly_return = math.nan
        if math.isfinite(weekly_return) and weekly_return <= 0.0:
            exited.add(cluster_id)

    adjusted = {
        code: float(weight)
        for code, weight in target_weights.items()
        if cluster_by_code.get(code) not in exited
    }
    return adjusted, max(0.0, 1.0 - sum(adjusted.values())), exited


def _append_reason(existing: str, addition: str) -> str:
    return f"{existing}|{addition}" if existing and addition else existing or addition


class AiRotationR31FastExitSession(AiRotationR11PersistGeomSession):
    """R11 session with a post-selection one-week exit brake."""

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        previous_weights = dict(self._previous_weights)
        decision = super().evaluate(context)
        if not previous_weights or not decision.target_weights:
            decision = replace(
                decision,
                decision_id=f"{context.signal_date}-{DESCRIPTOR.id}",
            )
            self._patch_artifacts(decision)
            return decision

        weekly_returns = context.data_view.returns(
            "weekly",
            self._config.correlation_lookback_weeks,
        )
        one_week_cluster_returns = compute_cluster_momentum(
            weekly_returns.tail(1),
            self._clusters,
            1,
        )
        target_weights, cash_weight, exited = apply_fast_exit(
            previous_weights,
            decision.target_weights,
            self._clusters,
            one_week_cluster_returns,
            self._config.top_n,
        )
        diagnostics = dict(decision.diagnostics)
        diagnostics.update(
            {
                "fast_exit_rule": "held_cluster_one_week_return_non_positive",
                "fast_exit_clusters": sorted(exited),
                "fast_exit_one_week_cluster_returns": {
                    str(cluster_id): (
                        float(value)
                        if value is not None and math.isfinite(float(value))
                        else None
                    )
                    for cluster_id, value in sorted(one_week_cluster_returns.items())
                },
            }
        )
        decision = replace(
            decision,
            decision_id=f"{context.signal_date}-{DESCRIPTOR.id}",
            target_weights=target_weights,
            cash_weight=cash_weight,
            reason_code=_append_reason(
                decision.reason_code,
                "FAST_EXIT_1W_NEGATIVE" if exited else "",
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


class AiRotationR31FastExitStrategy:
    """Complete round 31 strategy plug-in."""

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
        pipeline["selection_rule"] += (
            "; held clusters exit when one-week cluster return is non-positive"
        )
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        return CorrelationRepresentativeStrategy().resolve_requirements(config)

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> AiRotationR31FastExitSession:
        del initialization
        return AiRotationR31FastExitSession(config)  # type: ignore[arg-type]
