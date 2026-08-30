"""Round 74: R39 with fixed 60-day volatility-adjusted momentum ranking."""

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
from backtest.fund_rotation.scoring.cluster_momentum import ClusterMomentumScoreModel
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
from backtest.fund_rotation.universe import check_historical_eligibility


VOLATILITY_WINDOW_DAYS = 60
_WINDOW_SIZE = VOLATILITY_WINDOW_DAYS + 1
_MIN_VOLATILITY = 1e-8
_SCORE_MODEL_ID = "r74_momentum_over_volatility_60"

DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r74_r39_vol_adjusted_score",
    name="R74 R39 60日波动率调整动量排名",
    description=(
        "完全沿用 R39 的聚类、代表、staging、carry、槽位和执行规则，"
        "仅将 cluster 排名分数替换为 momentum / volatility_60；不改变仓位权重。"
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


def _date_key(value: object) -> str | None:
    try:
        return pd.Timestamp(value).strftime("%Y%m%d")
    except (TypeError, ValueError, OverflowError):
        return None


def _causal_frame(adjusted_closes: object, signal_date: str) -> pd.DataFrame | None:
    if not isinstance(adjusted_closes, pd.DataFrame):
        return None
    signal_key = _date_key(signal_date)
    if signal_key is None:
        return None
    try:
        rows = [
            index
            for index in adjusted_closes.index
            if (index_key := _date_key(index)) is not None and index_key <= signal_key
        ]
        rows.sort(key=lambda index: _date_key(index) or "")
        return adjusted_closes.loc[rows]
    except (KeyError, TypeError, ValueError):
        return None


def compute_daily_volatility_60(
    adjusted_closes: object,
    *,
    signal_date: str,
    codes: Sequence[str],
) -> dict[str, float | None]:
    """Compute annualized volatility from exactly 60 causal daily returns."""
    result = {str(code): None for code in codes}
    frame = _causal_frame(adjusted_closes, signal_date)
    if frame is None:
        return result
    for code in result:
        if code not in frame.columns:
            continue
        prices = pd.to_numeric(frame[code], errors="coerce")
        if len(prices) < _WINDOW_SIZE:
            continue
        window = prices.iloc[-_WINDOW_SIZE:]
        values = [_finite(value) for value in window]
        if any(value is None or value <= 0.0 for value in values):
            continue
        returns = pd.Series(values, dtype="float64").pct_change(fill_method=None).iloc[1:]
        if len(returns) != VOLATILITY_WINDOW_DAYS or not returns.map(math.isfinite).all():
            continue
        volatility = float(returns.std(ddof=0) * math.sqrt(252.0))
        if math.isfinite(volatility) and volatility > _MIN_VOLATILITY:
            result[code] = volatility
    return result


def compute_cluster_volatility_60(
    adjusted_closes: object,
    *,
    signal_date: str,
    clusters: Mapping[str, int],
) -> dict[int, float | None]:
    per_code = compute_daily_volatility_60(
        adjusted_closes,
        signal_date=signal_date,
        codes=tuple(clusters),
    )
    result: dict[int, float | None] = {}
    for cluster_id in sorted(set(clusters.values())):
        members = [code for code, value in clusters.items() if value == cluster_id]
        values = [per_code[code] for code in members]
        if not values or any(value is None for value in values):
            result[cluster_id] = None
            continue
        volatility = sum(value for value in values if value is not None) / len(values)
        result[cluster_id] = volatility if _finite(volatility) is not None and volatility > _MIN_VOLATILITY else None
    return result


def build_volatility_adjusted_scores(
    momentum_values: Mapping[int, object],
    volatility_values: Mapping[int, object],
    *,
    cluster_members: Mapping[int, Sequence[str]],
) -> dict[int, StrategyScore]:
    """Build R39-compatible higher-is-better scores with strict eligibility."""
    cluster_ids = sorted(set(momentum_values) | set(volatility_values))
    scores: dict[int, StrategyScore] = {}
    for cluster_id in cluster_ids:
        momentum = _finite(momentum_values.get(cluster_id))
        volatility = _finite(volatility_values.get(cluster_id))
        members = cluster_members.get(cluster_id, ())
        value = None
        eligible = False
        if (
            members
            and momentum is not None
            and momentum > 0.0
            and volatility is not None
            and volatility > _MIN_VOLATILITY
        ):
            candidate = momentum / volatility
            if math.isfinite(candidate):
                value = candidate
                eligible = True
        scores[cluster_id] = StrategyScore(
            value=value,
            eligible=eligible,
            subject_id=f"cluster:{cluster_id}",
            display_label="动量/60日波动率",
            model_label="Volatility-Adjusted Momentum",
            frequency="WEEKLY",
            scope="CLUSTER",
            direction=ScoreDirection.HIGHER_BETTER,
            model_id=_SCORE_MODEL_ID,
            model_version="1",
            components={"momentum": momentum, "volatility_60": volatility},
        )
    return scores


class AiRotationR74R39VolAdjustedScoreSession(AiRotationR39IncumbentCarrySession):
    """R39 lifecycle with only the volatility-adjusted ranking score replaced."""

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
                    reason_code=_append_reason(
                        invalid.reason_code, "INCUMBENT_CARRY" if incumbents else ""
                    ),
                    diagnostics=diagnostics,
                )
                CorrelationRepresentativeSession._log_decision(self, final)
                return final
        else:
            self._maintain_locks(view, weekly_window, eligible_set, signal_date)

        momentum_window = weekly_window.iloc[-(cfg.momentum_window_weeks + 1):]
        momentum_scores = ClusterMomentumScoreModel().score(
            momentum_window,
            self._clusters,
            cfg.momentum_window_weeks,
        )
        momentum_values = {
            cluster_id: score.value for cluster_id, score in momentum_scores.items()
        }
        try:
            adjusted_closes = view.adjusted_closes(lookback=_WINDOW_SIZE)
        except (AttributeError, KeyError, TypeError, ValueError):
            adjusted_closes = None
        volatility_values = compute_cluster_volatility_60(
            adjusted_closes,
            signal_date=signal_date,
            clusters=self._clusters,
        )
        scores = build_volatility_adjusted_scores(
            momentum_values,
            volatility_values,
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
        self._previous_ranked_clusters = list(ranked)
        unavailable = [cluster_id for cluster_id, score in scores.items() if not score.eligible]
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
                "momentum": {
                    str(cluster_id): momentum_values.get(cluster_id)
                    for cluster_id in sorted(momentum_values)
                },
                "volatility_60": {
                    str(cluster_id): volatility_values.get(cluster_id)
                    for cluster_id in sorted(volatility_values)
                },
                "score_model": {
                    "id": _SCORE_MODEL_ID,
                    "label": "Volatility-Adjusted Momentum",
                    "version": "1",
                    "direction": "HIGHER_BETTER",
                },
                "strategy_scores": {
                    str(cluster_id): {
                        "value": score.value,
                        "eligible": score.eligible,
                        "components": dict(score.components),
                    }
                    for cluster_id, score in scores.items()
                },
                "momentum_unavailable_clusters": [
                    cluster_id
                    for cluster_id, score in momentum_scores.items()
                    if not score.eligible
                ],
                "volatility_unavailable_clusters": [
                    cluster_id
                    for cluster_id, value in volatility_values.items()
                    if value is None
                ],
                "score_unavailable_clusters": unavailable,
                "ranked_clusters": list(ranked),
                "rank_flip_count": rank_flips,
                "staged_reentry_codes": sorted(staged),
                "incumbent_carry_codes": sorted(incumbents),
                "staged_reentry_fraction": 0.5,
                "staged_reentry_rule": "new_representative_target_weight_halved_once",
                "incumbent_carry_rule": "released_new_target_weight_proportional_to_continuous_base_target_weight",
                "signal_information_cutoff": _SIGNAL_INFORMATION_CUTOFF,
                "reclustered": reclustering,
                "num_clusters": len(self._clusters),
            },
        )
        CorrelationRepresentativeSession._log_decision(
            self, decision, scores=scores, ranked_subjects=ranked
        )
        return decision


class AiRotationR74R39VolAdjustedScoreStrategy(AiRotationR39IncumbentCarryStrategy):
    descriptor = DESCRIPTOR

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = super().describe_decision_pipeline(config)
        pipeline["score_model"] = {
            "id": _SCORE_MODEL_ID,
            "label": "Volatility-Adjusted Momentum",
            "momentum_source": "R39 cluster momentum",
            "volatility_window_days": VOLATILITY_WINDOW_DAYS,
            "volatility_annualization": 252,
            "formula": "momentum / volatility_60",
        }
        pipeline["selection_rule"] = (
            "Rank clusters by positive R39 momentum / annualized volatility_60; invalid values are ineligible"
        )
        pipeline["weighting_rule"] = "Fixed 1/top_n slots with vacant cash"
        pipeline["volatility_window_days"] = VOLATILITY_WINDOW_DAYS
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        requirements = super().resolve_requirements(config)
        return replace(requirements, warmup_trade_days=max(requirements.warmup_trade_days, _WINDOW_SIZE))

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> AiRotationR74R39VolAdjustedScoreSession:
        del initialization
        return AiRotationR74R39VolAdjustedScoreSession(config)  # type: ignore[arg-type]
