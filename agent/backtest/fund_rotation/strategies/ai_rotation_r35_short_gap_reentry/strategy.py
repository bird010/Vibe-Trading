"""Round 35: R34 staging with causal short-gap re-entry recovery."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
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
from backtest.fund_rotation.strategies.ai_rotation_r34_staged_reentry.strategy import (
    _append_reason,
)
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    CorrelationRepresentativeStrategy,
)


DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r35_short_gap_reentry",
    name="短空档重入满仓持续几何动量相关性代表ETF",
    description=(
        "完全沿用 R11/R34；仅曾出现在更早基础目标且连续缺席 2–3 个再平衡决策的"
        "代表 ETF 重入时恢复基础满槽权重，其余新目标继续半仓试探。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)

STAGING_FRACTION = 0.5
FULL_SIZE_REENTRY_GAP_DECISIONS = (2, 3)


def _finite_positive_weights(weights: Mapping[str, float]) -> dict[str, float]:
    result: dict[str, float] = {}
    for raw_code, raw_weight in sorted(weights.items(), key=lambda item: str(item[0])):
        try:
            weight = float(raw_weight)
        except (TypeError, ValueError):
            continue
        if math.isfinite(weight) and weight > 0.0:
            result[str(raw_code)] = weight
    return result


def _positive_codes(weights: Mapping[str, float]) -> set[str]:
    return set(_finite_positive_weights(weights))


def apply_short_gap_reentry(
    target_history: Sequence[Mapping[str, float]],
    target_weights: Mapping[str, float],
    staging_fraction: float = STAGING_FRACTION,
    full_size_reentry_gap_decisions: Sequence[int] = FULL_SIZE_REENTRY_GAP_DECISIONS,
) -> tuple[dict[str, float], float, set[str], set[str], set[str], dict[str, int]]:
    """Apply R35 staging using only prior base-target decisions.

    ``target_history`` contains one base target mapping per prior strategy
    evaluation. Invalid or non-positive weights do not count as appearances.
    The returned categories are staged codes, all full-size codes, short-gap
    re-entry codes, and their finite integer gaps.
    """
    history_sets = [_positive_codes(targets) for targets in target_history]
    previous_codes = history_sets[-1] if history_sets else set()
    last_seen: dict[str, int] = {}
    for index, codes in enumerate(history_sets):
        for code in sorted(codes):
            last_seen[code] = index

    current_index = len(history_sets)
    allowed_gaps = {int(gap) for gap in full_size_reentry_gap_decisions}
    current_weights = _finite_positive_weights(target_weights)
    adjusted: dict[str, float] = {}
    staged: set[str] = set()
    full_size: set[str] = set()
    reentries: set[str] = set()
    gaps: dict[str, int] = {}

    for code, weight in current_weights.items():
        if code in previous_codes:
            full_size.add(code)
            multiplier = 1.0
        else:
            prior_index = last_seen.get(code)
            gap = (
                current_index - prior_index - 1
                if prior_index is not None
                else None
            )
            if gap is not None and gap in allowed_gaps:
                full_size.add(code)
                reentries.add(code)
                gaps[code] = int(gap)
                multiplier = 1.0
            else:
                staged.add(code)
                multiplier = float(staging_fraction)
        adjusted[code] = float(weight) * multiplier

    return (
        adjusted,
        max(0.0, 1.0 - sum(adjusted.values())),
        staged,
        full_size,
        reentries,
        dict(sorted(gaps.items())),
    )


class AiRotationR35ShortGapReentrySession(AiRotationR11PersistGeomSession):
    """R11 session with history-based short-gap re-entry sizing."""

    def __init__(self, config) -> None:
        super().__init__(config)
        self._base_target_history: list[dict[str, float]] = []

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        decision = AiRotationR11PersistGeomSession.evaluate(self, context)
        base_targets = dict(decision.target_weights)
        (
            target_weights,
            cash_weight,
            staged,
            full_size,
            reentries,
            gaps,
        ) = apply_short_gap_reentry(self._base_target_history, base_targets)
        self._base_target_history.append(_finite_positive_weights(base_targets))

        diagnostics = dict(decision.diagnostics)
        diagnostics.update(
            {
                "staged_reentry_fraction": STAGING_FRACTION,
                "staged_reentry_codes": sorted(staged),
                "full_size_codes": sorted(full_size),
                "short_gap_reentry_codes": sorted(reentries),
                "short_gap_reentry_gaps": gaps,
                "short_gap_reentry_gap_decisions": list(
                    FULL_SIZE_REENTRY_GAP_DECISIONS
                ),
                "staged_reentry_rule": (
                    "new_representative_target_weight_halved_except_"
                    "short_gap_reentry"
                ),
            }
        )
        reason_code = _append_reason(
            decision.reason_code,
            "STAGED_REENTRY" if staged else "",
        )
        reason_code = _append_reason(
            reason_code,
            "SHORT_GAP_REENTRY" if reentries else "",
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


class AiRotationR35ShortGapReentryStrategy:
    """Complete round 35 strategy plug-in."""

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
            "; first entries and re-entries outside the 2-3 decision short gap "
            "use 50% staging, while short-gap re-entries use full size"
        )
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        return CorrelationRepresentativeStrategy().resolve_requirements(config)

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> AiRotationR35ShortGapReentrySession:
        del initialization
        return AiRotationR35ShortGapReentrySession(config)  # type: ignore[arg-type]

