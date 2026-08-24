"""Round 36: R34 with a full-sized new rank-three tail-slot entry."""

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
    AiRotationR11PersistGeomStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r34_staged_reentry.strategy import (
    _append_reason,
    AiRotationR34StagedReentrySession,
)
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    CorrelationRepresentativeStrategy,
)


DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r36_tail_slot_full_entry",
    name="尾槽排名第三新目标满仓持续几何动量相关性代表ETF",
    description=(
        "完全沿用 R34；仅将当期持续几何得分严格排名第 3 且上一期未持有的"
        "新代表 ETF 首周恢复为基础满槽权重。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)

_R34_STAGING_FRACTION = 0.5


def _is_strict_rank_three(value: object) -> bool:
    if isinstance(value, bool):
        return False
    try:
        rank = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(rank) and rank == 3.0


def apply_tail_slot_full_entry(
    previous_weights: Mapping[str, float],
    staged_target_weights: Mapping[str, float],
    representative_ranks: Mapping[str, object],
) -> tuple[dict[str, float], float, set[str], set[str]]:
    """Restore only a new representative's R34 half-size rank-three entry."""
    adjusted: dict[str, float] = {}
    staged: set[str] = set()
    full_size: set[str] = set()
    for code, weight in staged_target_weights.items():
        held = previous_weights.get(code, 0.0) > 0.0
        if held:
            adjusted[code] = float(weight)
            full_size.add(code)
        elif _is_strict_rank_three(representative_ranks.get(code)):
            adjusted[code] = float(weight) / _R34_STAGING_FRACTION
            full_size.add(code)
        else:
            adjusted[code] = float(weight)
            staged.add(code)
    return adjusted, max(0.0, 1.0 - sum(adjusted.values())), staged, full_size


def _trace_ranks(trace: Mapping[str, object] | None) -> dict[str, object]:
    if not isinstance(trace, Mapping):
        return {}
    candidates = trace.get("candidates")
    if not isinstance(candidates, list):
        return {}
    ranks: dict[str, object] = {}
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        code = candidate.get("ts_code")
        stages = candidate.get("stages")
        if isinstance(code, str) and isinstance(stages, Mapping):
            ranks[code] = stages.get("rank")
    return ranks


class AiRotationR36TailSlotFullEntrySession(AiRotationR34StagedReentrySession):
    """R34 session with a rank-three full-entry overlay."""

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        previous_weights = dict(self._previous_weights)
        decision = super().evaluate(context)
        trace = self._decision_trace[-1] if self._decision_trace else None
        representative_ranks = _trace_ranks(trace)
        target_weights, cash_weight, staged, full_size = apply_tail_slot_full_entry(
            previous_weights,
            decision.target_weights,
            representative_ranks,
        )
        tail_codes = sorted(
            code
            for code in full_size
            if previous_weights.get(code, 0.0) <= 0.0
            and _is_strict_rank_three(representative_ranks.get(code))
        )
        diagnostics = dict(decision.diagnostics)
        diagnostics.update(
            {
                "staged_reentry_codes": sorted(staged),
                "full_size_codes": sorted(full_size),
                "tail_slot_full_entry_codes": tail_codes,
                "tail_slot_full_entry_rank": 3,
                "staged_reentry_rule": (
                    "new_representative_target_weight_halved_once_except_"
                    "strict_rank_three_tail_slot"
                ),
            }
        )
        reason_code = _append_reason(
            decision.reason_code,
            "STAGED_REENTRY" if staged else "",
        )
        reason_code = _append_reason(
            reason_code,
            "TAIL_SLOT_FULL_ENTRY" if tail_codes else "",
        )
        decision = replace(
            decision,
            decision_id=f"{context.signal_date}-{DESCRIPTOR.id}",
            target_weights=target_weights,
            cash_weight=cash_weight,
            reason_code=reason_code,
            diagnostics=diagnostics,
        )
        self._patch_artifacts(decision)
        return decision


class AiRotationR36TailSlotFullEntryStrategy:
    """Complete round 36 strategy plug-in."""

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
        pipeline = AiRotationR11PersistGeomStrategy().describe_decision_pipeline(
            config
        )
        pipeline["selection_rule"] += (
            "; new rank-three representative entries use 100% size for one week, "
            "other new entries use 50% staging"
        )
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        return CorrelationRepresentativeStrategy().resolve_requirements(config)

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> AiRotationR36TailSlotFullEntrySession:
        del initialization
        return AiRotationR36TailSlotFullEntrySession(config)  # type: ignore[arg-type]
