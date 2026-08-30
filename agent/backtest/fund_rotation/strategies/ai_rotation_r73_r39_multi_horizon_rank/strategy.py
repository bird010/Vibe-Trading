"""Round 73: R39 with equal-weight R60/R120/R240 relative-momentum ranks."""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping, Sequence
from dataclasses import replace

import pandas as pd
from pydantic import BaseModel

from backtest.fund_rotation.contracts import (
    DecisionKind,
    FundRotationStrategyDescriptor,
    QualityStatus,
    StrategyDataRequirements,
    StrategyDecisionContext,
    StrategyInitializationContext,
    TargetWeightDecision,
)
from backtest.fund_rotation.scoring.contracts import ScoreDirection, StrategyScore, rank_scores
from backtest.fund_rotation.strategies.ai_rotation_r11_persist_geom.strategy import (
    _SIGNAL_INFORMATION_CUTOFF,
)
from backtest.fund_rotation.strategies.ai_rotation_r34_staged_reentry.strategy import (
    _append_reason,
    apply_staged_reentry,
)
from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import (
    AiRotationR39IncumbentCarrySession,
    AiRotationR39IncumbentCarryStrategy,
    apply_incumbent_carry,
)
from backtest.fund_rotation.strategies.correlation_all_members.signals import (
    signal_date_eligible,
)
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    CorrelationRepresentativeSession,
    CorrelationRepresentativeStrategy,
    build_slot_weights,
)
from backtest.fund_rotation.causal_data import UndeclaredStrategyDataAccess
from backtest.fund_rotation.universe import check_historical_eligibility


HORIZONS = (60, 120, 240)
_WINDOW_SIZE = max(HORIZONS) + 1
_SCORE_MODEL_ID = "equal_weight_rank_r60_r120_r240"

DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r73_r39_multi_horizon_rank",
    name="R73 R39 多周期相对动量等权排名",
    description=(
        "完全沿用 R39 的聚类、代表、staging、carry、槽位和执行规则，"
        "仅将排名替换为 rank(R60)+rank(R120)+rank(R240) 等权聚合，不加入 R20。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)


def _finite(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _tie_code(cluster_id: object, cluster_members: Mapping[object, Sequence[str]]) -> str:
    members = cluster_members.get(cluster_id, ())
    return min((str(member) for member in members), default=str(cluster_id))


def rank_period_scores(
    values: Mapping[int, object],
    *,
    cluster_members: Mapping[int, Sequence[str]],
) -> dict[int, int]:
    """Return deterministic descending ranks for finite period values."""
    valid = [
        (cluster_id, number)
        for cluster_id, raw in values.items()
        if (number := _finite(raw)) is not None
    ]
    ordered = sorted(
        valid,
        key=lambda item: (-item[1], _tie_code(item[0], cluster_members), str(item[0])),
    )
    return {cluster_id: rank for rank, (cluster_id, _) in enumerate(ordered, start=1)}


def aggregate_multi_horizon_rank_scores(
    period_scores: Mapping[int, Mapping[int, object]],
    *,
    cluster_members: Mapping[int, Sequence[str]],
) -> dict[int, StrategyScore]:
    """Aggregate exactly the three pre-registered horizon ranks."""
    cluster_ids = sorted(
        {cluster_id for values in period_scores.values() for cluster_id in values}
    )
    complete_cluster_ids = {
        cluster_id
        for cluster_id in cluster_ids
        if all(
            _finite(period_scores.get(horizon, {}).get(cluster_id)) is not None
            for horizon in HORIZONS
        )
    }
    rank_maps = {
        horizon: rank_period_scores(
            {
                cluster_id: period_scores[horizon][cluster_id]
                for cluster_id in complete_cluster_ids
            },
            cluster_members=cluster_members,
        )
        for horizon in HORIZONS
    }
    output: dict[int, StrategyScore] = {}
    for cluster_id in cluster_ids:
        missing = tuple(
            horizon
            for horizon in HORIZONS
            if _finite(period_scores.get(horizon, {}).get(cluster_id)) is None
        )
        components: dict[str, object] = {
            f"rank_{horizon}": rank_maps[horizon].get(cluster_id)
            for horizon in HORIZONS
        }
        components["missing_horizons"] = missing[0] if len(missing) == 1 else missing
        total = (
            float(sum(components[f"rank_{horizon}"] for horizon in HORIZONS))
            if cluster_id in complete_cluster_ids
            else None
        )
        output[cluster_id] = StrategyScore(
            value=total,
            eligible=not missing,
            subject_id=f"cluster:{cluster_id}",
            display_label="R60/R120/R240 等权排名和",
            model_label="Equal-Weight Multi-Horizon Rank",
            frequency="WEEKLY",
            scope="CLUSTER",
            direction=ScoreDirection.LOWER_BETTER,
            model_id=_SCORE_MODEL_ID,
            model_version="1",
            components=components,  # type: ignore[arg-type]
        )
    return output


def _causal_frame(adjusted_closes: object, signal_date: str) -> pd.DataFrame | None:
    if not isinstance(adjusted_closes, pd.DataFrame):
        return None
    try:
        signal_key = pd.Timestamp(signal_date).strftime("%Y%m%d")
        rows = [
            index for index in adjusted_closes.index
            if pd.Timestamp(index).strftime("%Y%m%d") <= signal_key
        ]
        rows.sort(key=lambda index: pd.Timestamp(index))
        return adjusted_closes.loc[rows]
    except (TypeError, ValueError, OverflowError, KeyError):
        return None


def compute_multi_horizon_returns(
    adjusted_closes: object,
    *,
    signal_date: str,
    codes: Sequence[str],
) -> dict[int, dict[str, float | None]]:
    frame = _causal_frame(adjusted_closes, signal_date)
    result = {horizon: {str(code): None for code in codes} for horizon in HORIZONS}
    if frame is None:
        return result
    for code in tuple(result[HORIZONS[0]]):
        if code not in frame.columns:
            continue
        values = pd.to_numeric(frame[code], errors="coerce")
        for horizon in HORIZONS:
            if len(values) < horizon + 1:
                continue
            window = values.iloc[-(horizon + 1):]
            numbers = [_finite(value) for value in window]
            if any(value is None or value <= 0.0 for value in numbers):
                continue
            start = numbers[0]
            end = numbers[-1]
            assert start is not None and end is not None
            change = end / start - 1.0
            result[horizon][code] = change if math.isfinite(change) else None
    return result


def compute_cluster_multi_horizon_returns(
    adjusted_closes: object,
    *,
    signal_date: str,
    clusters: Mapping[str, int],
) -> dict[int, dict[int, float | None]]:
    per_code = compute_multi_horizon_returns(
        adjusted_closes,
        signal_date=signal_date,
        codes=tuple(clusters),
    )
    result: dict[int, dict[int, float | None]] = {horizon: {} for horizon in HORIZONS}
    for horizon in HORIZONS:
        for cluster_id in sorted(set(clusters.values())):
            values = [
                per_code[horizon][code]
                for code, value in clusters.items()
                if value == cluster_id
            ]
            if values and len(values) == sum(value is not None for value in values):
                average = sum(values) / len(values)
                result[horizon][cluster_id] = (
                    average if _finite(average) is not None else None
                )
            else:
                result[horizon][cluster_id] = None
    return result


class AiRotationR73R39MultiHorizonRankSession(AiRotationR39IncumbentCarrySession):
    """R39 lifecycle with only the cluster ranking score replaced."""

    def __init__(self, config) -> None:
        super().__init__(config)
        self._previous_ranked_clusters: list[int] = []

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        state_before = copy.deepcopy(self.__dict__)
        try:
            return self._evaluate_transaction(context)
        except BaseException:
            self.__dict__.clear()
            self.__dict__.update(state_before)
            raise

    def _evaluate_transaction(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        cfg = self._config
        signal_date = context.signal_date
        view = context.data_view
        week_index = self._week_index
        self._week_index += 1
        previous_weights = dict(self._previous_weights)
        dim_pool = self._pool_at_signal(view)
        weekly_window = view.returns("weekly", cfg.correlation_lookback_weeks)
        historically_eligible, historical_excluded = check_historical_eligibility(
            dim_pool, signal_date
        )
        kept, market_excluded = signal_date_eligible(view, historically_eligible, signal_date)
        self._exclusions.extend(market_excluded)
        eligible_set = set(kept)
        reclustering = (
            week_index - self._last_recluster_week >= cfg.recluster_interval_weeks
            or not self._clusters
        )
        if reclustering:
            self._previous_ranked_clusters = []
            self._exclusions.extend(historical_excluded)
            invalid = self._recluster(view, weekly_window, kept, eligible_set, signal_date)
            if invalid is not None:
                staged_weights, _, staged = apply_staged_reentry(
                    previous_weights, invalid.target_weights
                )
                final_weights, final_cash, staged, incumbents = apply_incumbent_carry(
                    previous_weights, staged_weights
                )
                diagnostics = dict(invalid.diagnostics)
                diagnostics.update(
                    {
                        "staged_reentry_fraction": 0.5,
                        "staged_reentry_codes": sorted(staged),
                        "staged_reentry_rule": (
                            "new_representative_target_weight_halved_once_"
                            "then_released_weight_carried_to_incumbents"
                        ),
                        "incumbent_carry_codes": sorted(incumbents),
                        "incumbent_carry_rule": (
                            "released_new_target_weight_proportional_to_"
                            "continuous_base_target_weight"
                        ),
                        "num_clusters": len(self._clusters),
                    }
                )
                final = replace(
                    invalid,
                    decision_id=f"{signal_date}-{DESCRIPTOR.id}",
                    target_weights=final_weights,
                    cash_weight=final_cash,
                    diagnostics=diagnostics,
                    reason_code=_append_reason(
                        invalid.reason_code, "INCUMBENT_CARRY" if incumbents else ""
                    ),
                )
                CorrelationRepresentativeSession._log_decision(self, final)
                return final
        else:
            self._maintain_locks(view, weekly_window, eligible_set, signal_date)

        try:
            adjusted_closes = view.adjusted_closes(lookback=_WINDOW_SIZE)
        except (
            AttributeError,
            KeyError,
            TypeError,
            ValueError,
            UndeclaredStrategyDataAccess,
        ):
            adjusted_closes = None
        period_values = compute_cluster_multi_horizon_returns(
            adjusted_closes,
            signal_date=signal_date,
            clusters=self._clusters,
        )
        scores = aggregate_multi_horizon_rank_scores(
            period_values,
            cluster_members=self._frozen_members,
        )
        ranked = rank_scores(scores, cluster_members=self._frozen_members)
        selected = ranked[: max(cfg.top_n, 0)]
        base_weights, filled, vacant, _ = build_slot_weights(
            selected, self._representatives, cfg.top_n
        )
        staged_weights, _, staged = apply_staged_reentry(previous_weights, base_weights)
        final_weights, final_cash, staged, incumbents = apply_incumbent_carry(
            previous_weights, staged_weights
        )
        rank_flips = sum(
            self._previous_ranked_clusters.index(cluster_id) != ranked.index(cluster_id)
            for cluster_id in set(self._previous_ranked_clusters) & set(ranked)
        )
        complete_cluster_ids = {
            cluster_id for cluster_id, score in scores.items() if score.eligible
        }
        rank_maps = {
            horizon: rank_period_scores(
                {
                    cluster_id: period_values[horizon][cluster_id]
                    for cluster_id in complete_cluster_ids
                },
                cluster_members=self._frozen_members,
            )
            for horizon in HORIZONS
        }
        coverage = {
            str(horizon): {
                "available": sum(
                    _finite(value) is not None
                    for value in period_values[horizon].values()
                ),
                "total": len(period_values[horizon]),
            }
            for horizon in HORIZONS
        }
        self._previous_ranked_clusters = list(ranked)
        reason = "CLUSTER_QUALITY_REJECTED" if self._last_gate_overall.value == "REJECT" else ""
        if staged:
            reason = _append_reason(reason, "STAGED_REENTRY")
        if incumbents:
            reason = _append_reason(reason, "INCUMBENT_CARRY")
        decision = TargetWeightDecision(
            decision_id=f"{signal_date}-{DESCRIPTOR.id}",
            signal_date=signal_date,
            action=DecisionKind.SET_TARGETS,
            target_weights=final_weights,
            cash_weight=final_cash,
            reason_code=reason,
            quality_status=(
                QualityStatus.VALID
                if self._last_gate_overall.value == "PASS"
                else QualityStatus.DEGRADED
            ),
            diagnostics={
                "filled_slots": filled,
                "vacant_slots": vacant,
                "score_model": {
                    "id": _SCORE_MODEL_ID,
                    "label": "Equal-Weight Multi-Horizon Rank",
                    "version": "1",
                    "direction": "LOWER_BETTER",
                },
                "strategy_scores": {
                    str(cluster_id): {
                        "value": score.value,
                        "eligible": score.eligible,
                        "components": dict(score.components),
                    }
                    for cluster_id, score in scores.items()
                },
                "rank_by_horizon": {
                    str(horizon): {str(key): value for key, value in ranks.items()}
                    for horizon, ranks in rank_maps.items()
                },
                "ranked_clusters": list(ranked),
                "rank_flip_count": rank_flips,
                "score_coverage": coverage,
                "staged_reentry_codes": sorted(staged),
                "incumbent_carry_codes": sorted(incumbents),
                "staged_reentry_rule": "new_representative_target_weight_halved_once",
                "staged_reentry_fraction": 0.5,
                "incumbent_carry_rule": "released_new_target_weight_proportional_to_continuous_base_target_weight",
                "num_clusters": len(self._clusters),
                "signal_information_cutoff": _SIGNAL_INFORMATION_CUTOFF,
                "reclustered": reclustering,
            },
        )
        CorrelationRepresentativeSession._log_decision(
            self, decision, scores=scores, ranked_subjects=ranked
        )
        return decision


class AiRotationR73R39MultiHorizonRankStrategy(AiRotationR39IncumbentCarryStrategy):
    descriptor = DESCRIPTOR

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = super().describe_decision_pipeline(config)
        pipeline["score_model"] = {
            "id": _SCORE_MODEL_ID,
            "label": "Equal-Weight Multi-Horizon Rank",
            "horizons": list(HORIZONS),
            "weights": {str(horizon): 1.0 for horizon in HORIZONS},
        }
        pipeline["selection_rule"] = (
            "Rank clusters by equal-weight rank(R60) + rank(R120) + rank(R240); no R20"
        )
        pipeline["rank_horizons"] = list(HORIZONS)
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        requirements = super().resolve_requirements(config)
        return replace(requirements, warmup_trade_days=max(requirements.warmup_trade_days, _WINDOW_SIZE))

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> AiRotationR73R39MultiHorizonRankSession:
        del initialization
        return AiRotationR73R39MultiHorizonRankSession(config)  # type: ignore[arg-type]
