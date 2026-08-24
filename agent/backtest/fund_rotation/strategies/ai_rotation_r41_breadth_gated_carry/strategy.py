"""Round 41: R39 carry disabled for a single incumbent target."""

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
from backtest.fund_rotation.strategies.ai_rotation_r34_staged_reentry.strategy import (
    AiRotationR34StagedReentrySession,
    _append_reason,
)
from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import (
    AiRotationR39IncumbentCarrySession,
    AiRotationR39IncumbentCarryStrategy,
    _r34_baseline,
    _validated_state,
    apply_incumbent_carry,
)


DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r41_breadth_gated_carry",
    name="单持续目标取消承接释放权重持续几何动量相关性代表ETF",
    description=(
        "完全沿用 R39；仅当正权重持续目标恰好一个且存在正权重新目标时，"
        "取消 carry 并保留 R34 staged 输出，其余情况逐值沿用 R39。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)


def _classify_positive_targets(
    previous_weights: object,
    staged_target_weights: object,
) -> tuple[set[str], set[str]] | None:
    try:
        previous_state = _validated_state(previous_weights)
        target_state = _validated_state(staged_target_weights)
    except (OverflowError, TypeError, ValueError):
        return None
    if previous_state is None or target_state is None:
        return None

    previous_positive = {
        code for code, weight in previous_state if weight > 0.0
    }
    staged = {
        code
        for code, weight in target_state
        if code not in previous_positive and weight > 0.0
    }
    incumbents = {
        code
        for code, weight in target_state
        if code in previous_positive and weight > 0.0
    }
    return staged, incumbents


def _r39_or_safe_baseline(
    previous_weights: object,
    staged_target_weights: object,
) -> tuple[dict[str, float], float, set[str], set[str]]:
    try:
        return apply_incumbent_carry(previous_weights, staged_target_weights)
    except (OverflowError, TypeError, ValueError):
        try:
            baseline_targets, baseline_cash, baseline_staged = _r34_baseline(
                previous_weights,
                staged_target_weights,
            )
        except (OverflowError, TypeError, ValueError):
            return {}, 1.0, set(), set()
        return baseline_targets, baseline_cash, baseline_staged, set()


def apply_breadth_gated_carry(
    previous_weights: object,
    staged_target_weights: object,
) -> tuple[dict[str, float], float, set[str], set[str], bool]:
    """Apply R39 except when one incumbent would receive all released weight."""
    r39_targets, r39_cash, r39_staged, r39_incumbents = _r39_or_safe_baseline(
        previous_weights,
        staged_target_weights,
    )
    classified = _classify_positive_targets(previous_weights, staged_target_weights)
    if classified is None:
        return r39_targets, r39_cash, r39_staged, r39_incumbents, bool(
            r39_incumbents
        )

    staged, incumbents = classified
    if (
        staged
        and len(incumbents) == 1
        and r39_incumbents == incumbents
    ):
        baseline_targets, baseline_cash, baseline_staged = _r34_baseline(
            previous_weights,
            staged_target_weights,
        )
        return baseline_targets, baseline_cash, baseline_staged, set(), False

    return r39_targets, r39_cash, r39_staged, r39_incumbents, bool(
        r39_incumbents
    )


class AiRotationR41BreadthGatedCarrySession(AiRotationR39IncumbentCarrySession):
    """R39 session with a single-incumbent breadth gate."""

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        previous_weights = dict(self._previous_weights)
        decision = AiRotationR34StagedReentrySession.evaluate(self, context)
        target_weights, cash_weight, staged, incumbents, carry_applied = (
            apply_breadth_gated_carry(previous_weights, decision.target_weights)
        )
        classified = _classify_positive_targets(
            previous_weights,
            decision.target_weights,
        )
        breadth_gate_incumbents = classified[1] if classified else set()
        r39_result = _r39_or_safe_baseline(
            previous_weights,
            decision.target_weights,
        )
        breadth_gate_triggered = bool(
            classified
            and classified[0]
            and len(classified[1]) == 1
            and r39_result[3] == classified[1]
        )

        diagnostics = dict(decision.diagnostics)
        diagnostics.update(
            {
                "staged_reentry_codes": sorted(staged),
                "incumbent_carry_codes": sorted(incumbents),
                "breadth_gate_incumbent_codes": sorted(breadth_gate_incumbents),
                "breadth_gate_triggered": breadth_gate_triggered,
                "breadth_gate_rule": (
                    "cancel_carry_when_one_positive_incumbent_and_staged_target"
                ),
                "incumbent_carry_rule": (
                    "released_new_target_weight_proportional_to_"
                    "continuous_base_target_weight_when_breadth_at_least_two"
                ),
                "staged_reentry_rule": (
                    "new_representative_target_weight_halved_once_"
                    "then_release_is_gated_by_incumbent_breadth"
                ),
            }
        )
        decision = replace(
            decision,
            decision_id=f"{context.signal_date}-{DESCRIPTOR.id}",
            target_weights=target_weights,
            cash_weight=cash_weight,
            reason_code=_append_reason(
                decision.reason_code,
                "INCUMBENT_CARRY" if carry_applied else "",
            ),
            diagnostics=diagnostics,
        )
        self._patch_artifacts(decision)
        return decision


class AiRotationR41BreadthGatedCarryStrategy(AiRotationR39IncumbentCarryStrategy):
    """Complete round 41 strategy plug-in."""

    descriptor = DESCRIPTOR

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = super().describe_decision_pipeline(config)
        pipeline["selection_rule"] += (
            "; apply a breadth gate and cancel R39 carry when exactly one positive incumbent and a staged "
            "positive new target are present"
        )
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        return super().resolve_requirements(config)

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> AiRotationR41BreadthGatedCarrySession:
        del initialization
        return AiRotationR41BreadthGatedCarrySession(config)  # type: ignore[arg-type]
