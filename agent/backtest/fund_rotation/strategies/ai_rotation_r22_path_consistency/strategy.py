"""Round 22 challenger: persistent momentum scaled by path consistency."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import replace

import pandas as pd
from pydantic import BaseModel

from backtest.fund_rotation.contracts import (
    DecisionKind, FundRotationStrategyDescriptor, QualityStatus,
    StrategyDataRequirements, StrategyDecisionContext,
    StrategyInitializationContext, TargetWeightDecision,
)
from backtest.fund_rotation.momentum import compute_cluster_momentum
from backtest.fund_rotation.scoring.contracts import StrategyScore, rank_scores
from backtest.fund_rotation.strategies.ai_rotation_r11_persist_geom.strategy import (
    persistent_geometric_score,
)
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    CorrelationRepresentativeSession, CorrelationRepresentativeStrategy,
    _SIGNAL_INFORMATION_CUTOFF, _momentum_diagnostics, _serialize_scores,
    build_slot_weights,
)


DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r22_path_consistency",
    name="路径一致性持续动量相关性代表ETF",
    description=(
        "沿用双正四周复合动量与几何增长排名，并用最近八个有限周度簇收益的"
        "正周比例缩减得分。"
    ),
    interface_version="1.0", supported_universe=("etf",), deterministic=True,
)
_SCORE_MODEL_ID = "path_consistent_persistent_cluster_momentum"
_SCORE_MODEL_LABEL = "Path-Consistent Persistent Cluster Momentum"
_SCORE_MODEL_VERSION = "1"


def _finite_or_none(value: object) -> float | None:
    try:
        number = float(value) if value is not None else math.nan
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def path_consistent_score(
    current_momentum: object,
    lagged_momentum: object,
    path_returns: list[object] | tuple[object, ...],
) -> StrategyScore:
    """Apply the Champion gate, then scale its geometric score by q."""
    current = _finite_or_none(current_momentum)
    lagged = _finite_or_none(lagged_momentum)
    finite_path = [_finite_or_none(value) for value in path_returns]
    eligible = current is not None and lagged is not None and current > 0 and lagged > 0
    ratio = None
    score = None
    available_path = [value for value in finite_path if value is not None]
    if eligible and available_path:
        ratio = sum(value > 0 for value in available_path) / len(available_path)
        geometric = persistent_geometric_score(current, lagged).value
        score = geometric * ratio if geometric is not None else None
        eligible = score is not None and math.isfinite(score)
    else:
        eligible = False
    return StrategyScore(
        value=score if eligible else None, eligible=eligible, subject_id=None,
        display_label="路径一致性持续簇动量", model_label=_SCORE_MODEL_LABEL,
        frequency="WEEKLY", scope="CLUSTER", model_id=_SCORE_MODEL_ID,
        model_version=_SCORE_MODEL_VERSION,
        components={
            "current_momentum": current, "lagged_momentum": lagged,
            "positive_week_ratio": ratio,
            "path_consistent_persistent_momentum": score if eligible else None,
        },
    )


def _cluster_path_returns(
    weekly_returns: pd.DataFrame, clusters: Mapping[str, int],
) -> dict[int, list[float | None]]:
    recent = weekly_returns.iloc[-8:]
    result: dict[int, list[float | None]] = {}
    for cluster_id in sorted(set(clusters.values())):
        members = [code for code, cid in clusters.items() if cid == cluster_id and code in recent.columns]
        if not members or len(recent) < 8:
            result[cluster_id] = []
            continue
        means = recent[members].mean(axis=1, skipna=True)
        result[cluster_id] = [_finite_or_none(value) for value in means.tolist()]
    return result


def compute_path_consistency_scores(
    weekly_returns: pd.DataFrame, clusters: Mapping[str, int], momentum_window: int,
) -> tuple[dict[int, StrategyScore], dict[int, float | None],
           dict[int, float | None], dict[int, float | None]]:
    """Compute causal M0, M1, q and path-consistent scores from one epoch."""
    cluster_ids = sorted(set(clusters.values()))
    if len(weekly_returns) < momentum_window + 1:
        current = {cid: None for cid in cluster_ids}
        lagged = {cid: None for cid in cluster_ids}
    else:
        signal_window = weekly_returns.iloc[-(momentum_window + 1):]
        current = compute_cluster_momentum(signal_window.iloc[-momentum_window:], dict(clusters), momentum_window)
        lagged = compute_cluster_momentum(signal_window.iloc[:momentum_window], dict(clusters), momentum_window)
        current = {cid: _finite_or_none(current.get(cid)) for cid in cluster_ids}
        lagged = {cid: _finite_or_none(lagged.get(cid)) for cid in cluster_ids}
    paths = _cluster_path_returns(weekly_returns, clusters)
    scores = {cid: path_consistent_score(current.get(cid), lagged.get(cid), paths.get(cid, [])) for cid in cluster_ids}
    return scores, current, lagged, {cid: score.value for cid, score in scores.items()}


class AiRotationR22PathConsistencySession(CorrelationRepresentativeSession):
    """Isolated session preserving the Champion path and execution semantics."""

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        cfg, signal_date, view = self._config, context.signal_date, context.data_view
        week_index = self._week_index
        self._week_index += 1
        dim_pool = self._pool_at_signal(view)
        window = view.returns("weekly", cfg.correlation_lookback_weeks)
        from backtest.fund_rotation.strategies.correlation_all_members.signals import signal_date_eligible
        from backtest.fund_rotation.universe import check_historical_eligibility
        historically_eligible, historical_excluded = check_historical_eligibility(dim_pool, signal_date)
        kept, market_excluded = signal_date_eligible(view, historically_eligible, signal_date)
        self._exclusions.extend(market_excluded)
        eligible_set = set(kept)
        reclustering = (week_index - self._last_recluster_week >= cfg.recluster_interval_weeks or not self._clusters)
        if reclustering:
            self._exclusions.extend(historical_excluded)
            decision = self._recluster(view, window, kept, eligible_set, signal_date)
            if decision is not None:
                self._log_decision(decision)
                return decision
        else:
            self._maintain_locks(view, window, eligible_set, signal_date)
        scores, current_raw, lagged_raw, path_raw = compute_path_consistency_scores(
            window, self._clusters, cfg.momentum_window_weeks)
        ranked = rank_scores(scores, cluster_members=self._frozen_members)
        selected = ranked[:max(cfg.top_n, 0)]
        weights, filled, vacant, cash_weight = build_slot_weights(selected, self._representatives, cfg.top_n)
        current_values, current_unavailable = _momentum_diagnostics({cid: v if v is not None else math.nan for cid, v in current_raw.items()})
        lagged_values, lagged_unavailable = _momentum_diagnostics({cid: v if v is not None else math.nan for cid, v in lagged_raw.items()})
        path_values, path_unavailable = _momentum_diagnostics({cid: v if v is not None else math.nan for cid, v in path_raw.items()})
        unavailable = sorted(set(current_unavailable) | set(lagged_unavailable) | set(path_unavailable))
        rejected = self._last_gate_overall.value == "REJECT"
        quality = QualityStatus.VALID if self._last_gate_overall.value == "PASS" else QualityStatus.DEGRADED
        decision = TargetWeightDecision(
            decision_id=f"{signal_date}-{DESCRIPTOR.id}", signal_date=signal_date,
            action=DecisionKind.SET_TARGETS, target_weights=dict(weights), cash_weight=cash_weight,
            reason_code="CLUSTER_QUALITY_REJECTED" if rejected else "", quality_status=quality,
            diagnostics={
                "filled_slots": filled, "vacant_slots": vacant, "momentum": current_values,
                "momentum_status": "PARTIAL" if unavailable else "COMPLETE",
                "momentum_unavailable_clusters": unavailable,
                "momentum_available_cluster_count": len(scores) - len(unavailable),
                "momentum_total_cluster_count": len(scores), "lagged_momentum": lagged_values,
                "lagged_momentum_unavailable_clusters": lagged_unavailable,
                "path_consistency_momentum": path_values,
                "path_consistency_momentum_unavailable_clusters": path_unavailable,
                "lagged_momentum_window_weeks": cfg.momentum_window_weeks,
                "path_consistency_window_weeks": 8,
                "persistence_gate": "current_and_lagged_strictly_positive",
                "score_model": {"id": _SCORE_MODEL_ID, "label": _SCORE_MODEL_LABEL, "version": _SCORE_MODEL_VERSION, "direction": "HIGHER_BETTER"},
                "strategy_scores": _serialize_scores(scores), "num_clusters": len(self._clusters),
                "signal_information_cutoff": _SIGNAL_INFORMATION_CUTOFF,
            },
        )
        self._log_decision(decision, scores=scores, ranked_subjects=ranked)
        return decision

    def _recluster(self, view, window, kept, eligible_set, signal_date):
        decision = super()._recluster(view, window, kept, eligible_set, signal_date)
        return None if decision is None else replace(decision, decision_id=f"{signal_date}-{DESCRIPTOR.id}")


class AiRotationR22PathConsistencyStrategy:
    descriptor = DESCRIPTOR
    config_model = CorrelationRepresentativeStrategy.config_model
    artifact_roles = ("cluster_history", "gates", "representatives", "exclusions", "decisions")

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = CorrelationRepresentativeStrategy().describe_decision_pipeline(config)
        pipeline["selection_rule"] = (f"Top {config.top_n} clusters with strictly positive current and one-week-lagged momentum, ranked by geometric growth mean scaled by eight-week positive-return ratio")
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        return CorrelationRepresentativeStrategy().resolve_requirements(config)

    def create_session(self, initialization: StrategyInitializationContext, config: BaseModel):
        return AiRotationR22PathConsistencySession(config)  # type: ignore[arg-type]
