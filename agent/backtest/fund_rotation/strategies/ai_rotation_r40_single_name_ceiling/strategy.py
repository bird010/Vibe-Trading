"""Round 40: R39 with a fixed single-name target ceiling."""

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


SINGLE_NAME_CEILING = 0.5
_CONSERVATION_TOLERANCE = 1e-9

DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r40_single_name_ceiling",
    name="持续目标承接释放权重单标的半仓上限持续几何动量相关性代表ETF",
    description=(
        "完全沿用 R39；仅将 signal-close 后超过 1/2 的单标的目标权重截断，"
        "全部超额留作现金，不重分配、不归一化。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)


def _validated_weights(weights: object) -> list[tuple[str, float]] | None:
    if not isinstance(weights, Mapping):
        return None
    try:
        items = list(weights.items())
    except (AttributeError, TypeError, ValueError):
        return None

    validated: list[tuple[str, float]] = []
    seen: set[str] = set()
    for code, raw_weight in items:
        if not isinstance(code, str) or not code or code in seen:
            return None
        if isinstance(raw_weight, bool):
            return None
        try:
            weight = float(raw_weight)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(weight) or weight < 0.0 or weight > 1.0:
            return None
        seen.add(code)
        validated.append((code, weight))
    return validated


def _finite_sum(values: list[float]) -> float | None:
    try:
        total = math.fsum(values)
    except (OverflowError, TypeError, ValueError):
        return None
    return total if math.isfinite(total) else None


def _baseline_targets(weights: object) -> dict[str, float]:
    if isinstance(weights, Mapping):
        try:
            return dict(weights.items())
        except (AttributeError, TypeError, ValueError):
            return {}
    return {}


def apply_single_name_ceiling(
    target_weights: object,
    cash_weight: object,
) -> tuple[dict[str, float], object]:
    """Cap valid R39 targets at 1/2, returning invalid inputs unchanged."""
    baseline_targets = _baseline_targets(target_weights)
    baseline_cash = cash_weight
    state = _validated_weights(target_weights)
    if isinstance(cash_weight, bool):
        return baseline_targets, baseline_cash
    try:
        cash = float(cash_weight)
    except (TypeError, ValueError, OverflowError):
        return baseline_targets, baseline_cash
    if not math.isfinite(cash) or cash < 0.0 or cash > 1.0:
        return baseline_targets, baseline_cash
    if state is None:
        return baseline_targets, baseline_cash

    target_total = _finite_sum([weight for _, weight in state])
    if target_total is None or target_total > 1.0 + _CONSERVATION_TOLERANCE:
        return baseline_targets, baseline_cash
    total = _finite_sum([target_total, cash])
    if total is None or not math.isclose(
        total, 1.0, rel_tol=0.0, abs_tol=_CONSERVATION_TOLERANCE
    ):
        return baseline_targets, baseline_cash

    adjusted: dict[str, float] = {}
    excess_values: list[float] = []
    for code, weight in state:
        capped = min(weight, SINGLE_NAME_CEILING)
        adjusted[code] = capped
        excess_values.append(weight - capped)
    excess = _finite_sum(excess_values)
    if excess is None:
        return baseline_targets, baseline_cash
    adjusted_cash = cash + excess
    if not math.isfinite(adjusted_cash) or adjusted_cash < 0.0:
        return baseline_targets, baseline_cash
    adjusted_total = _finite_sum(list(adjusted.values()) + [adjusted_cash])
    if adjusted_total is None or not math.isclose(
        adjusted_total, 1.0, rel_tol=0.0, abs_tol=_CONSERVATION_TOLERANCE
    ):
        return baseline_targets, baseline_cash
    return adjusted, adjusted_cash


class AiRotationR40SingleNameCeilingSession(AiRotationR39IncumbentCarrySession):
    """R39 session with a fail-closed single-name ceiling overlay."""

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        decision = super().evaluate(context)
        target_weights, cash_weight = apply_single_name_ceiling(
            decision.target_weights,
            decision.cash_weight,
        )
        diagnostics = dict(decision.diagnostics)
        diagnostics.update(
            {
                "single_name_ceiling": SINGLE_NAME_CEILING,
                "single_name_ceiling_applied": (
                    target_weights != decision.target_weights
                    or cash_weight != decision.cash_weight
                ),
                "single_name_ceiling_rule": (
                    "cap_r39_signal_close_target_at_half_and_keep_excess_cash"
                ),
            }
        )
        decision = replace(
            decision,
            decision_id=f"{context.signal_date}-{DESCRIPTOR.id}",
            target_weights=target_weights,
            cash_weight=cash_weight,
            diagnostics=diagnostics,
        )
        self._patch_artifacts(decision)
        return decision


class AiRotationR40SingleNameCeilingStrategy(AiRotationR39IncumbentCarryStrategy):
    """Complete round 40 strategy plug-in."""

    descriptor = DESCRIPTOR

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = super().describe_decision_pipeline(config)
        pipeline["selection_rule"] += (
            "; after R39 signal-close sizing, apply a single-name cap of fixed 1/2 "
            "and keep excess weight as cash"
        )
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        return super().resolve_requirements(config)

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> AiRotationR40SingleNameCeilingSession:
        del initialization
        return AiRotationR40SingleNameCeilingSession(config)  # type: ignore[arg-type]
