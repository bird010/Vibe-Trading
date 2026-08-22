"""Round 16 challenger: persistent momentum rank consensus."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import replace

import pandas as pd
from pydantic import BaseModel

from backtest.fund_rotation.contracts import (
    DecisionKind, FundRotationStrategyDescriptor, QualityStatus,
    StrategyDataRequirements, StrategyDecisionContext,
    StrategyInitializationContext, TargetWeightDecision,
)
from backtest.fund_rotation.scoring.cluster_momentum import ClusterMomentumScoreModel
from backtest.fund_rotation.scoring.contracts import ScoreDirection, StrategyScore, rank_scores
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    CorrelationRepresentativeSession, CorrelationRepresentativeStrategy,
    _SIGNAL_INFORMATION_CUTOFF, _momentum_diagnostics, _serialize_scores,
    build_slot_weights,
)


DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r16_rank_consensus", name="名次共识持续动量相关性代表ETF",
    description=("复用相关性聚类代表ETF流程，要求当前及向后错一周的四周簇动量"
                 "均严格为正，并按两组动量在合格簇内的等权横截面名次共识排名。"),
    interface_version="1.0", supported_universe=("etf",), deterministic=True,
)
_SCORE_MODEL_ID = "persistent_rank_consensus_cluster_momentum"
_SCORE_MODEL_LABEL = "Persistent Rank Consensus Cluster Momentum"
_SCORE_MODEL_VERSION = "1"


def _finite_or_none(value: object) -> float | None:
    try:
        number = float(value) if value is not None else math.nan
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def rank_consensus_score(current_momentum, lagged_momentum, *, rank0, rank1) -> StrategyScore:
    current, lagged = _finite_or_none(current_momentum), _finite_or_none(lagged_momentum)
    first_rank, second_rank = _finite_or_none(rank0), _finite_or_none(rank1)
    eligible = (current is not None and lagged is not None and current > 0.0
                and lagged > 0.0 and first_rank is not None and second_rank is not None)
    consensus = (first_rank + second_rank) / 2.0 if eligible else None
    return StrategyScore(
        value=consensus, eligible=eligible, subject_id=None,
        display_label="持续动量名次共识", model_label=_SCORE_MODEL_LABEL,
        frequency="WEEKLY", scope="CLUSTER", direction=ScoreDirection.LOWER_BETTER,
        model_id=_SCORE_MODEL_ID, model_version=_SCORE_MODEL_VERSION,
        components={"current_momentum": current, "lagged_momentum": lagged,
                     "rank0": first_rank, "rank1": second_rank,
                     "rank_consensus": consensus},
    )


def _rank_values(values: Mapping[int, tuple[float | None, float | None]],
                 cluster_members: Mapping[int, Sequence[str]]) -> dict[int, StrategyScore]:
    def finite_positive(pair):
        current, lagged = pair
        return (_finite_or_none(current) is not None and _finite_or_none(lagged) is not None
                and float(current) > 0.0 and float(lagged) > 0.0)

    valid = {cid: pair for cid, pair in values.items() if finite_positive(pair)}

    def tie_code(cid: int) -> str:
        members = cluster_members.get(cid, ())
        return min(members) if members else str(cid)

    rank0 = {cid: float(i) for i, (cid, _) in enumerate(
        sorted(valid.items(), key=lambda item: (-float(item[1][0]), tie_code(item[0]))), 1)}
    rank1 = {cid: float(i) for i, (cid, _) in enumerate(
        sorted(valid.items(), key=lambda item: (-float(item[1][1]), tie_code(item[0]))), 1)}
    return {cid: rank_consensus_score(current, lagged, rank0=rank0.get(cid), rank1=rank1.get(cid))
            for cid, (current, lagged) in values.items()}


def compute_rank_consensus_scores(
    values_or_returns,
    members_or_clusters,
    momentum_window=None,
    *,
    cluster_members: Mapping[int, Sequence[str]] | None = None,
):
    """Compute rank scores directly, or derive causal M0/M1 from weekly returns."""
    if isinstance(values_or_returns, pd.DataFrame):
        weekly_returns = values_or_returns
        clusters = members_or_clusters
        cluster_ids = sorted(set(clusters.values()))
        if momentum_window != 4 or len(weekly_returns) < (momentum_window or 0) + 1:
            current = {cid: None for cid in cluster_ids}
            lagged = {cid: None for cid in cluster_ids}
        else:
            model = ClusterMomentumScoreModel()
            signal_window = weekly_returns.iloc[-(momentum_window + 1):]
            current_scores = model.score(signal_window.iloc[-momentum_window:], dict(clusters), momentum_window)
            lagged_scores = model.score(signal_window.iloc[:momentum_window], dict(clusters), momentum_window)
            current = {cid: score.value for cid, score in current_scores.items()}
            lagged = {cid: score.value for cid, score in lagged_scores.items()}
        values = {cid: (current.get(cid), lagged.get(cid)) for cid in cluster_ids}
        scores = _rank_values(
            values,
            cluster_members or {cid: [] for cid in cluster_ids},
        )
        return scores, current, lagged, {cid: score.value for cid, score in scores.items()}
    return _rank_values(values_or_returns, members_or_clusters)


class AiRotationR16RankConsensusSession(CorrelationRepresentativeSession):
    """Session retaining the shared representative, quality, and execution contract."""

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        signal_date, view = context.signal_date, context.data_view
        week_index = self._week_index
        self._week_index += 1
        dim_pool = self._pool_at_signal(view)
        window = view.returns("weekly", self._config.correlation_lookback_weeks)
        from backtest.fund_rotation.strategies.correlation_all_members.signals import signal_date_eligible
        from backtest.fund_rotation.universe import check_historical_eligibility
        historically_eligible, historical_excluded = check_historical_eligibility(dim_pool, signal_date)
        kept, market_excluded = signal_date_eligible(view, historically_eligible, signal_date)
        self._exclusions.extend(market_excluded)
        eligible_set = set(kept)
        reclustering = (week_index - self._last_recluster_week >= self._config.recluster_interval_weeks
                        or not self._clusters)
        if reclustering:
            self._exclusions.extend(historical_excluded)
            decision = self._recluster(view, window, kept, eligible_set, signal_date)
            if decision is not None:
                self._log_decision(decision)
                return decision
        else:
            self._maintain_locks(view, window, eligible_set, signal_date)
        momentum_window = window.iloc[-(self._config.momentum_window_weeks + 1):]
        scores, current_raw, lagged_raw, consensus_raw = compute_rank_consensus_scores(
            momentum_window,
            self._clusters,
            self._config.momentum_window_weeks,
            cluster_members=self._frozen_members,
        )
        ranked = rank_scores(scores, cluster_members=self._frozen_members)
        selected = ranked[:max(self._config.top_n, 0)]
        weights, filled, vacant, cash_weight = build_slot_weights(selected, self._representatives, self._config.top_n)
        current_values, current_unavailable = _momentum_diagnostics({cid: value if value is not None else math.nan for cid, value in current_raw.items()})
        lagged_values, lagged_unavailable = _momentum_diagnostics({cid: value if value is not None else math.nan for cid, value in lagged_raw.items()})
        consensus_values, consensus_unavailable = _momentum_diagnostics({cid: value if value is not None else math.nan for cid, value in consensus_raw.items()})
        unavailable = sorted(set(current_unavailable) | set(lagged_unavailable) | set(consensus_unavailable))
        quality = QualityStatus.VALID if self._last_gate_overall.value == "PASS" else QualityStatus.DEGRADED
        decision = TargetWeightDecision(
            decision_id=f"{signal_date}-{DESCRIPTOR.id}", signal_date=signal_date,
            action=DecisionKind.SET_TARGETS, target_weights=dict(weights), cash_weight=cash_weight,
            reason_code="CLUSTER_QUALITY_REJECTED" if self._last_gate_overall.value == "REJECT" else "",
            quality_status=quality,
            diagnostics={
                "filled_slots": filled, "vacant_slots": vacant, "momentum": current_values,
                "momentum_status": "PARTIAL" if unavailable else "COMPLETE",
                "momentum_unavailable_clusters": unavailable,
                "momentum_available_cluster_count": len(scores) - len(unavailable),
                "momentum_total_cluster_count": len(scores), "lagged_momentum": lagged_values,
                "lagged_momentum_unavailable_clusters": lagged_unavailable,
                "rank_consensus": consensus_values,
                "rank_consensus_unavailable_clusters": consensus_unavailable,
                "lagged_momentum_window_weeks": self._config.momentum_window_weeks,
                "persistence_gate": "current_and_lagged_strictly_positive",
                "score_model": {"id": _SCORE_MODEL_ID, "label": _SCORE_MODEL_LABEL,
                                "version": _SCORE_MODEL_VERSION, "direction": "LOWER_BETTER"},
                "strategy_scores": _serialize_scores(scores), "num_clusters": len(self._clusters),
                "signal_information_cutoff": _SIGNAL_INFORMATION_CUTOFF,
            },
        )
        self._log_decision(decision, scores=scores, ranked_subjects=ranked)
        return decision

    def _recluster(self, view, window, kept, eligible_set, signal_date):
        decision = super()._recluster(view, window, kept, eligible_set, signal_date)
        return replace(decision, decision_id=f"{signal_date}-{DESCRIPTOR.id}") if decision is not None else None


class AiRotationR16RankConsensusStrategy:
    descriptor = DESCRIPTOR
    config_model = CorrelationRepresentativeStrategy.config_model
    artifact_roles = ("cluster_history", "gates", "representatives", "exclusions", "decisions")

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = CorrelationRepresentativeStrategy().describe_decision_pipeline(config)
        pipeline["selection_rule"] = (
            f"Top {config.top_n} clusters with strictly positive current and one-week-lagged momentum, "
            "ranked by equal-weight cross-sectional consensus of their two descending ranks"
        )
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        return CorrelationRepresentativeStrategy().resolve_requirements(config)

    def create_session(self, initialization: StrategyInitializationContext, config: BaseModel) -> AiRotationR16RankConsensusSession:
        return AiRotationR16RankConsensusSession(config)  # type: ignore[arg-type]
