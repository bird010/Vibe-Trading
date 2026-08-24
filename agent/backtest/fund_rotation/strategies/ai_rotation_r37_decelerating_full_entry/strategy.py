"""Round 37: R34 with a full-sized decelerating-momentum new entry."""

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
    id="ai_rotation_r37_decelerating_full_entry",
    name="减速正动量新目标满仓持续几何动量相关性代表ETF",
    description=(
        "完全沿用 R34；仅将上一期未持有且当前四周动量不高于滞后四周动量、"
        "两者均严格为正的唯一入选簇新目标首周恢复为基础满槽权重。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)

_R34_STAGING_FRACTION = 0.5


def _finite_positive(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0.0 else None


def _unique_cluster_id(value: object) -> object | None:
    if isinstance(value, (list, tuple, set, frozenset)):
        if len(value) != 1:
            return None
        value = next(iter(value))
    if isinstance(value, Mapping) or value is None or isinstance(value, bool):
        return None
    try:
        hash(value)
    except TypeError:
        return None
    return value


def _lookup_signal(
    values: object, cluster_id: object,
) -> object | None:
    if not isinstance(values, Mapping):
        return None
    matches = [
        value
        for key, value in values.items()
        if key == cluster_id or str(key) == str(cluster_id)
    ]
    return matches[0] if len(matches) == 1 else None


def _is_decelerating_positive_cluster(
    cluster_id: object,
    current_momentum: Mapping[object, object],
    lagged_momentum: Mapping[object, object],
) -> bool:
    current = _finite_positive(_lookup_signal(current_momentum, cluster_id))
    lagged = _finite_positive(_lookup_signal(lagged_momentum, cluster_id))
    return (
        current is not None
        and lagged is not None
        and current <= lagged
    )


def apply_decelerating_full_entry(
    previous_weights: Mapping[str, float],
    staged_target_weights: Mapping[str, float],
    cluster_ids_by_code: Mapping[str, object],
    current_momentum: Mapping[object, object],
    lagged_momentum: Mapping[object, object],
) -> tuple[dict[str, float], float, set[str], set[str]]:
    """Restore R34's half-size only for a valid decelerating new target."""
    adjusted: dict[str, float] = {}
    staged: set[str] = set()
    full_size: set[str] = set()
    for code, weight in staged_target_weights.items():
        held = previous_weights.get(code, 0.0) > 0.0
        cluster_id = _unique_cluster_id(
            cluster_ids_by_code.get(code)
            if isinstance(cluster_ids_by_code, Mapping)
            else None
        )
        if held:
            adjusted[code] = float(weight)
            full_size.add(code)
        elif (
            cluster_id is not None
            and _is_decelerating_positive_cluster(
                cluster_id, current_momentum, lagged_momentum
            )
        ):
            adjusted[code] = float(weight) / _R34_STAGING_FRACTION
            full_size.add(code)
        else:
            adjusted[code] = float(weight)
            staged.add(code)
    return adjusted, max(0.0, 1.0 - sum(adjusted.values())), staged, full_size


def _trace_representative_clusters(
    trace: Mapping[str, object] | None,
    target_codes: set[str],
) -> dict[str, set[object]]:
    if not isinstance(trace, Mapping):
        return {}
    candidates = trace.get("candidates")
    if not isinstance(candidates, list):
        return {}
    clusters: dict[str, set[object]] = {}
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        code = candidate.get("ts_code")
        stages = candidate.get("stages")
        if (
            not isinstance(code, str)
            or code not in target_codes
            or not isinstance(stages, Mapping)
            or stages.get("cluster_representative") is not True
            or "cluster_id" not in stages
        ):
            continue
        try:
            hash(stages["cluster_id"])
        except TypeError:
            continue
        clusters.setdefault(code, set()).add(stages["cluster_id"])
    return clusters


class AiRotationR37DeceleratingFullEntrySession(AiRotationR34StagedReentrySession):
    """R34 session with a signal-close decelerating-momentum overlay."""

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        previous_weights = dict(self._previous_weights)
        decision = super().evaluate(context)
        trace = self._decision_trace[-1] if self._decision_trace else None
        diagnostics = dict(decision.diagnostics)
        target_weights, cash_weight, staged, full_size = (
            apply_decelerating_full_entry(
                previous_weights,
                decision.target_weights,
                _trace_representative_clusters(
                    trace, set(decision.target_weights)
                ),
                diagnostics.get("momentum", {}),
                diagnostics.get("lagged_momentum", {}),
            )
        )
        decelerating_codes = sorted(
            code
            for code in full_size
            if previous_weights.get(code, 0.0) <= 0.0
        )
        diagnostics.update(
            {
                "staged_reentry_codes": sorted(staged),
                "full_size_codes": sorted(full_size),
                "decelerating_full_entry_codes": decelerating_codes,
                "decelerating_full_entry_condition": "0<M0<=M1",
                "staged_reentry_rule": (
                    "new_representative_target_weight_halved_once_except_"
                    "positive_decelerating_unique_cluster"
                ),
            }
        )
        reason_code = _append_reason(
            decision.reason_code,
            "DECELERATING_FULL_ENTRY" if decelerating_codes else "",
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


class AiRotationR37DeceleratingFullEntryStrategy:
    """Complete round 37 strategy plug-in."""

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
            "; new targets with finite positive decelerating cluster momentum "
            "use 100% size for one week, other new targets use 50% staging"
        )
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        return CorrelationRepresentativeStrategy().resolve_requirements(config)

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> AiRotationR37DeceleratingFullEntrySession:
        del initialization
        return AiRotationR37DeceleratingFullEntrySession(config)  # type: ignore[arg-type]
