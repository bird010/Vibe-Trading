"""Round 38: R34 with full-sized replacement entries."""

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
    AiRotationR34StagedReentrySession,
    _append_reason,
)
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    CorrelationRepresentativeStrategy,
)


DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r38_replacement_full_entry",
    name="替代目标满仓入场持续几何动量相关性代表ETF",
    description=(
        "完全沿用 R34；仅当上一期至少一个正权重目标退出当前基础目标时，"
        "当期新代表 ETF 首周恢复为基础满槽权重。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)

_R34_STAGING_FRACTION = 0.5


def _validated_state(weights: object) -> list[tuple[str, float]] | None:
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
        except (TypeError, ValueError):
            return None
        if not math.isfinite(weight) or weight < 0.0:
            return None
        seen.add(code)
        validated.append((code, weight))
    return validated


def _r34_baseline(
    previous_weights: object,
    target_weights: object,
) -> tuple[dict[str, float], float, set[str]]:
    previous = previous_weights if isinstance(previous_weights, Mapping) else {}
    target = target_weights if isinstance(target_weights, Mapping) else {}
    try:
        adjusted = {
            code: float(weight)
            for code, weight in target.items()
        }
        staged = {
            code for code in adjusted
            if previous.get(code, 0.0) <= 0.0
        }
        return adjusted, max(0.0, 1.0 - sum(adjusted.values())), staged
    except (AttributeError, TypeError, ValueError):
        return {}, 1.0, set()


def apply_replacement_full_entry(
    previous_weights: object,
    staged_target_weights: object,
) -> tuple[dict[str, float], float, set[str], set[str]]:
    """Restore R34's half-size only for a confirmed replacement entry.

    Invalid, incomplete, duplicate, or non-finite state returns the R34
    baseline without applying the replacement overlay.
    """
    previous_state = _validated_state(previous_weights)
    target_state = _validated_state(staged_target_weights)
    baseline_targets, baseline_cash, baseline_staged = _r34_baseline(
        previous_weights, staged_target_weights
    )
    if previous_state is None or target_state is None:
        return baseline_targets, baseline_cash, baseline_staged, set()

    previous_positive = {
        code for code, weight in previous_state if weight > 0.0
    }
    current_codes = {code for code, weight in target_state if weight > 0.0}
    replacement_event = bool(previous_positive - current_codes)
    adjusted = dict(baseline_targets)
    staged = set(baseline_staged)
    full_size = set(adjusted) - staged
    if replacement_event:
        replacement_codes = set(staged)
        for code in replacement_codes:
            adjusted[code] = float(adjusted[code]) / _R34_STAGING_FRACTION
        staged.difference_update(replacement_codes)
        full_size.update(replacement_codes)

    return adjusted, max(0.0, 1.0 - sum(adjusted.values())), staged, full_size


class AiRotationR38ReplacementFullEntrySession(AiRotationR34StagedReentrySession):
    """R34 session with a previous-target replacement overlay."""

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        previous_weights = dict(self._previous_weights)
        decision = super().evaluate(context)
        (
            target_weights,
            cash_weight,
            staged,
            full_size,
        ) = apply_replacement_full_entry(previous_weights, decision.target_weights)
        replacement_codes = sorted(
            code
            for code in full_size
            if previous_weights.get(code, 0.0) <= 0.0
            and code not in staged
        )
        diagnostics = dict(decision.diagnostics)
        diagnostics.update(
            {
                "staged_reentry_codes": sorted(staged),
                "full_size_codes": sorted(full_size),
                "replacement_full_entry_codes": replacement_codes,
                "replacement_full_entry_condition": (
                    "positive_previous_target_exited_current_base_target"
                ),
                "staged_reentry_rule": (
                    "new_representative_target_weight_halved_once_except_"
                    "replacement_entry"
                ),
            }
        )
        reason_code = _append_reason(
            decision.reason_code,
            "STAGED_REENTRY" if staged else "",
        )
        reason_code = _append_reason(
            reason_code,
            "REPLACEMENT_FULL_ENTRY" if replacement_codes else "",
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


class AiRotationR38ReplacementFullEntryStrategy:
    """Complete round 38 strategy plug-in."""

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
            "; replacement new targets use 100% size for one week, other new "
            "targets use 50% staging"
        )
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        return CorrelationRepresentativeStrategy().resolve_requirements(config)

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> AiRotationR38ReplacementFullEntrySession:
        del initialization
        return AiRotationR38ReplacementFullEntrySession(config)  # type: ignore[arg-type]
