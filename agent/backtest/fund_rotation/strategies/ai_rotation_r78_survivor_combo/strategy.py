"""Round 78: compose only mechanisms that independently passed promotion gates."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence

from pydantic import BaseModel

from backtest.fund_rotation.contracts import (
    DecisionKind,
    FundRotationStrategyDescriptor,
    QualityStatus,
    StrategyDecisionContext,
    StrategyInitializationContext,
    TargetWeightDecision,
)
from backtest.fund_rotation.strategies.ai_rotation_r34_staged_reentry.strategy import _append_reason
from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import (
    AiRotationR39IncumbentCarrySession,
    AiRotationR39IncumbentCarryStrategy,
)


@dataclass(frozen=True)
class MechanismProvenance:
    mechanism_id: str
    stage: str
    source_sha256: str
    promotion_allowed: bool
    review_p0: int
    review_p1: int


_STAGE_ORDER = {"ranking": 0, "risk": 1, "defense": 2}


def select_survivor_layers(
    candidates: Sequence[MechanismProvenance],
) -> tuple[MechanismProvenance, ...]:
    seen: set[str] = set()
    selected = []
    for candidate in candidates:
        if candidate.mechanism_id in seen:
            raise ValueError(f"duplicate mechanism: {candidate.mechanism_id}")
        seen.add(candidate.mechanism_id)
        if candidate.promotion_allowed and candidate.review_p0 == 0 and candidate.review_p1 == 0:
            selected.append(candidate)
    return tuple(
        sorted(
            selected,
            key=lambda item: (_STAGE_ORDER.get(item.stage, 99), item.mechanism_id),
        )
    )


def compose_survivor_layers(
    layers: Sequence[MechanismProvenance],
) -> dict[str, object]:
    seen: set[str] = set()
    for layer in layers:
        if layer.mechanism_id in seen:
            raise ValueError(f"duplicate mechanism: {layer.mechanism_id}")
        seen.add(layer.mechanism_id)
        if not layer.promotion_allowed or layer.review_p0 != 0 or layer.review_p1 != 0:
            return {"status": "UNAVAILABLE_INPUTS", "mechanism_ids": [], "source_sha256": {}}
    ordered = sorted(
        layers,
        key=lambda item: (_STAGE_ORDER.get(item.stage, 99), item.mechanism_id),
    )
    return {
        "status": "READY" if ordered else "UNAVAILABLE_INPUTS",
        "mechanism_ids": [item.mechanism_id for item in ordered],
        "source_sha256": {item.mechanism_id: item.source_sha256 for item in ordered},
    }


DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r78_survivor_combo",
    name="幸存机制组合（证据门控）",
    description=(
        "只组合 promotion_allowed=true 且独立审查 P0/P1 均为零的冻结机制；"
        "无合格 survivor 时 fail-closed，不新增可调参数。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)


class AiRotationR78SurvivorComboSession(AiRotationR39IncumbentCarrySession):
    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        decision = super().evaluate(context)
        diagnostics = dict(decision.diagnostics)
        diagnostics.update(
            {
                "survivor_combo": compose_survivor_layers(()),
                "combination_rule": "only independently promoted frozen mechanisms",
            }
        )
        return replace(
            decision,
            decision_id=f"{context.signal_date}-{DESCRIPTOR.id}",
            action=DecisionKind.INVALID,
            target_weights={},
            cash_weight=1.0,
            reason_code=_append_reason(decision.reason_code, "SURVIVOR_COMBO_UNAVAILABLE"),
            quality_status=QualityStatus.FAILED,
            diagnostics=diagnostics,
        )


class AiRotationR78SurvivorComboStrategy(AiRotationR39IncumbentCarryStrategy):
    descriptor = DESCRIPTOR

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = super().describe_decision_pipeline(config)
        pipeline["survivor_combo"] = {
            "selection": "promotion_allowed and review P0/P1 both zero",
            "new_parameters": False,
            "empty_selection": "UNAVAILABLE_INPUTS",
        }
        return pipeline

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> AiRotationR78SurvivorComboSession:
        del initialization
        return AiRotationR78SurvivorComboSession(config)  # type: ignore[arg-type]
