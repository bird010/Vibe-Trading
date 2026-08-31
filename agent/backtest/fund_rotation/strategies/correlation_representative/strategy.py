"""Correlation representative strategy — design §8/§9.

Eligibility is resolved point-in-time for every signal date. Representative
selection occurs on reclustering dates; between reclusters the selected ETF is
kept unless a hard tradability or liquidity failure occurs.
"""

from __future__ import annotations

import math
from typing import Mapping

import pandas as pd
from pydantic import BaseModel

from backtest.fund_rotation.contracts import (
    DecisionKind,
    FundRotationStrategyDescriptor,
    QualityStatus,
    StrategyArtifact,
    StrategyDataRequirements,
    StrategyDecisionContext,
    StrategyDiagnostics,
    StrategyInitializationContext,
    TargetWeightDecision,
)
from backtest.fund_rotation.evaluation import iso_week_endings
from backtest.fund_rotation.scoring.cluster_momentum import ClusterMomentumScoreModel
from backtest.fund_rotation.scoring.contracts import StrategyScore, rank_scores
from backtest.fund_rotation.strategies.correlation_all_members.signals import (
    ensure_instrument_pool,
    signal_date_eligible,
)
from backtest.fund_rotation.strategies.correlation_representative.clustering import (
    correlation_cluster,
    cross_sectional_valid_count_distribution,
    prepare_cluster_returns,
)
from backtest.fund_rotation.strategies.correlation_representative.config import (
    CorrelationRepresentativeConfig,
)
from backtest.fund_rotation.strategies.correlation_representative.gates import (
    GateStatus,
    evaluate_cluster_gates,
)
from backtest.fund_rotation.strategies.correlation_representative.representative import (
    maintain_representative_lock,
)
from backtest.fund_rotation.universe import (
    ExclusionReason,
    ExclusionRecord,
    check_historical_eligibility,
)

DESCRIPTOR = FundRotationStrategyDescriptor(
    id="correlation_representative",
    name="相关性聚类代表ETF",
    description=(
        "相关性聚类后每个入选簇只持有一只流动性最佳代表 ETF（medoid 邻域 + "
        "代表相关性诊断 + 因果 ADV 选择 + 锁定/硬失效回退）。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)

_ZERO_TURN_STREAK_DAYS = 5
_SIGNAL_INFORMATION_CUTOFF = "CLOSE"


def build_slot_weights(
    selected_cluster_ids: list[int],
    representatives: Mapping[int, str | None],
    top_n: int,
) -> tuple[dict[str, float], list[int], list[int], float]:
    """Use a fixed ``1/top_n`` allocation for every selected cluster slot."""
    slot_weight = 1.0 / top_n
    weights: dict[str, float] = {}
    filled: list[int] = []
    vacant: list[int] = []
    for cluster_id in selected_cluster_ids:
        representative = representatives.get(cluster_id)
        if representative:
            weights[representative] = (
                weights.get(representative, 0.0) + slot_weight
            )
            filled.append(cluster_id)
        else:
            vacant.append(cluster_id)
    cash_weight = max(0.0, 1.0 - sum(weights.values()))
    return weights, filled, vacant, cash_weight


def _momentum_diagnostics(
    momentum: Mapping[int, float],
) -> tuple[dict[str, float | None], list[int]]:
    """Translate internal non-finite momentum sentinels to strict JSON facts."""
    values: dict[str, float | None] = {}
    unavailable: list[int] = []
    for cluster_id, raw_value in sorted(momentum.items()):
        value = float(raw_value)
        if math.isfinite(value):
            values[str(cluster_id)] = value
        else:
            values[str(cluster_id)] = None
            unavailable.append(cluster_id)
    return values, unavailable


def _serialize_score(score: StrategyScore) -> dict[str, object]:
    return {
        "id": "primary_score",
        "label": score.label,
        "display_label": score.display_label,
        "model_label": score.model_label,
        "value": score.value,
        "eligible": score.eligible,
        "direction": score.direction.value,
        "frequency": score.frequency,
        "scope": score.scope,
        "subject_id": score.subject_id,
        "model_id": score.model_id,
        "model_version": score.model_version,
        "components": dict(score.components),
    }


def _serialize_scores(
    scores: Mapping[int, StrategyScore],
) -> dict[str, dict[str, object]]:
    return {str(cluster_id): _serialize_score(score) for cluster_id, score in scores.items()}


def _cluster_state_diagnostics(
    clusters: Mapping[str, int],
    representatives: Mapping[int, str | None],
) -> dict[str, int]:
    return {
        "cluster_count": len(set(clusters.values())),
        "cluster_member_count": len(clusters),
        "representative_count": sum(
            value is not None for value in representatives.values()
        ),
    }


def _candidate_threshold_diagnostics(
    candidates,
    *,
    selected: str | None,
    threshold: float,
) -> dict[str, object]:
    valid = [
        record
        for record in candidates
        if record.adv20 is not None and record.leave_one_out_corr is not None
    ]
    selected_corr = next(
        (
            record.leave_one_out_corr
            for record in candidates
            if record.code == selected
        ),
        None,
    )
    return {
        "candidate_scope": "MEDOID_NEIGHBORHOOD",
        "eligible_candidate_count": len(valid),
        "eligible_candidate_below_legacy_threshold_count": sum(
            record.leave_one_out_corr < threshold for record in valid
        ),
        "selected_leave_one_out_corr": selected_corr,
        "selected_below_legacy_threshold": (
            None
            if selected_corr is None
            else selected_corr < threshold
        ),
        "leave_one_out_corr_space": "RAW",
    }


class CorrelationRepresentativeSession:
    """Per-run session; all clustering, gate and lock state is isolated."""

    def __init__(self, config: CorrelationRepresentativeConfig) -> None:
        self._config = config
        self._week_index = 0
        self._last_recluster_week = -config.recluster_interval_weeks
        self._clusters: dict[str, int] = {}
        self._frozen_distance: pd.DataFrame | None = None
        self._frozen_members: dict[int, list[str]] = {}
        self._representatives: dict[int, str | None] = {}
        self._last_gate_overall = GateStatus.PASS
        self._cluster_history: list[dict] = []
        self._gate_history: list[dict] = []
        self._selection_history: list[dict] = []
        self._exclusions: list[ExclusionRecord] = []
        self._decision_log: list[dict] = []
        self._decision_trace: list[dict] = []
        self._previous_weights: dict[str, float] = {}

    def scheduled_dates(
        self,
        calendar: tuple[str, ...],
        simulation_start_date: str,
        evaluation_end_date: str,
    ) -> tuple[str, ...]:
        endings = iso_week_endings(calendar)
        return tuple(
            date
            for date in endings
            if simulation_start_date <= date <= evaluation_end_date
        )

    def _pool_at_signal(self, view) -> pd.DataFrame:
        cfg = self._config
        lookback_days = (
            cfg.correlation_lookback_weeks + 1
        ) * 5 - 1
        return ensure_instrument_pool(
            view,
            lookback_trade_days=lookback_days,
        )

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        cfg = self._config
        signal_date = context.signal_date
        view = context.data_view
        week_index = self._week_index
        self._week_index += 1

        dim_pool = self._pool_at_signal(view)
        window = view.returns("weekly", cfg.correlation_lookback_weeks)

        historically_eligible, historical_excluded = (
            check_historical_eligibility(dim_pool, signal_date)
        )
        kept, market_excluded = signal_date_eligible(
            view,
            historically_eligible,
            signal_date,
        )
        self._exclusions.extend(market_excluded)
        eligible_set = set(kept)

        reclustering = (
            week_index - self._last_recluster_week
            >= cfg.recluster_interval_weeks
            or not self._clusters
        )
        if reclustering:
            self._exclusions.extend(historical_excluded)
            decision = self._recluster(
                view,
                window,
                kept,
                eligible_set,
                signal_date,
            )
            if decision is not None:
                self._log_decision(decision)
                return decision
        else:
            self._maintain_locks(
                view,
                window,
                eligible_set,
                signal_date,
            )

        momentum_window = window.iloc[
            -(cfg.momentum_window_weeks + 1):
        ]
        score_model = ClusterMomentumScoreModel()
        scores = score_model.score(
            momentum_window,
            self._clusters,
            cfg.momentum_window_weeks,
        )
        ranked = rank_scores(
            scores,
            cluster_members=self._frozen_members,
        )
        selected = ranked[: max(cfg.top_n, 0)]
        weights, filled, vacant, cash_weight = build_slot_weights(
            selected,
            self._representatives,
            cfg.top_n,
        )
        momentum_values, unavailable_clusters = _momentum_diagnostics(
            {cluster_id: score.value if score.value is not None else math.nan for cluster_id, score in scores.items()}
        )
        rejected = self._last_gate_overall is GateStatus.REJECT
        quality = (
            QualityStatus.VALID
            if self._last_gate_overall is GateStatus.PASS
            else QualityStatus.DEGRADED
        )
        decision = TargetWeightDecision(
            decision_id=f"{signal_date}-{DESCRIPTOR.id}",
            signal_date=signal_date,
            action=DecisionKind.SET_TARGETS,
            target_weights=dict(weights),
            cash_weight=cash_weight,
            reason_code=("CLUSTER_QUALITY_REJECTED" if rejected else ""),
            quality_status=quality,
            diagnostics={
                "filled_slots": filled,
                "vacant_slots": vacant,
                "momentum": momentum_values,
                "momentum_status": (
                    "PARTIAL" if unavailable_clusters else "COMPLETE"
                ),
                "momentum_unavailable_clusters": unavailable_clusters,
                "momentum_available_cluster_count": (
                    len(scores) - len(unavailable_clusters)
                ),
                "momentum_total_cluster_count": len(scores),
                "score_model": {
                    "id": score_model.id,
                    "label": score_model.label,
                    "version": score_model.version,
                    "direction": "HIGHER_BETTER",
                },
                "strategy_scores": _serialize_scores(scores),
                **_cluster_state_diagnostics(
                    self._clusters, self._representatives,
                ),
                "num_clusters": len(self._clusters),
                "signal_information_cutoff": _SIGNAL_INFORMATION_CUTOFF,
            },
        )
        self._log_decision(decision, scores=scores, ranked_subjects=ranked)
        return decision

    def _recluster(
        self,
        view,
        window: pd.DataFrame,
        kept: list[str],
        eligible_set: set,
        signal_date: str,
    ) -> TargetWeightDecision | None:
        cfg = self._config
        valid_codes = [code for code in kept if code in window.columns]
        if cfg.min_valid_weeks > 0 and valid_codes:
            counts = window[valid_codes].notna().sum()
            qualified = [
                code
                for code in valid_codes
                if counts.get(code, 0) >= cfg.min_valid_weeks
            ]
            for code in sorted(set(valid_codes) - set(qualified)):
                self._exclusions.append(
                    ExclusionRecord(
                        ts_code=code,
                        reason=ExclusionReason.INSUFFICIENT_VALID_WEEKS,
                        details=(
                            f"valid_weeks={int(counts.get(code, 0))}; "
                            f"required={cfg.min_valid_weeks}"
                        ),
                        signal_date=signal_date,
                    )
                )
            valid_codes = qualified

        cluster_returns, demean_insufficient = prepare_cluster_returns(
            window,
            valid_codes,
            demean=cfg.cluster_cross_sectional_demean,
        )
        valid_count_distribution = cross_sectional_valid_count_distribution(
            cluster_returns,
        )
        try:
            outcome = correlation_cluster(
                cluster_returns,
                valid_codes,
                k=cfg.k,
                min_pairwise_weeks=cfg.min_pairwise_weeks,
            )
        except ValueError as exc:
            return TargetWeightDecision(
                decision_id=f"{signal_date}-{DESCRIPTOR.id}",
                signal_date=signal_date,
                action=DecisionKind.INVALID,
                reason_code="CLUSTERING_DATA_INSUFFICIENT",
                quality_status=QualityStatus.INVALID,
                diagnostics={"error": str(exc)},
            )

        self._exclusions.extend(outcome.pairwise_excluded)
        self._clusters = outcome.clusters
        self._frozen_distance = outcome.distance
        self._frozen_members = {}
        for code, cluster_id in outcome.clusters.items():
            self._frozen_members.setdefault(cluster_id, []).append(code)
        for cluster_id in self._frozen_members:
            self._frozen_members[cluster_id] = sorted(
                self._frozen_members[cluster_id]
            )

        gates = evaluate_cluster_gates(outcome.clusters, cfg)
        self._last_gate_overall = gates.overall
        self._cluster_history.append(
            {
                "week": signal_date,
                "clusters": dict(outcome.clusters),
                "num_etfs": len(outcome.clusters),
                "cluster_count": len(set(outcome.clusters.values())),
                "cluster_member_count": len(outcome.clusters),
                "cluster_input_space": (
                    "CROSS_SECTIONAL_DEMEANED"
                    if cfg.cluster_cross_sectional_demean
                    else "RAW"
                ),
                "demean_insufficient_cross_section_weeks": (
                    demean_insufficient
                ),
                "cross_sectional_valid_observation_count_distribution": (
                    valid_count_distribution
                ),
            }
        )
        self._gate_history.append(
            {
                "week": signal_date,
                "overall": gates.overall.value,
                "results": [
                    {
                        "code": result.code,
                        "status": result.status.value,
                        "actual": result.actual,
                        "warn_threshold": result.warn_threshold,
                        "reject_threshold": result.reject_threshold,
                        "affected_codes": list(result.affected_codes),
                    }
                    for result in gates.results
                ],
            }
        )
        self._last_recluster_week = self._week_index - 1

        self._representatives = {}
        self._lock_representatives(
            view,
            window,
            eligible_set,
            signal_date,
            current_map=False,
        )
        return None

    def _maintain_locks(
        self,
        view,
        window,
        eligible_set: set,
        signal_date: str,
    ) -> None:
        self._lock_representatives(
            view,
            window,
            eligible_set,
            signal_date,
            current_map=True,
        )

    def _lock_representatives(
        self,
        view,
        window,
        eligible_set: set,
        signal_date: str,
        current_map: bool = False,
    ) -> None:
        cfg = self._config
        adv, tie_break, hard_failed = self._liquidity_stats(
            view,
            signal_date,
        )
        tradable = eligible_set - hard_failed
        for cluster_id, members in sorted(self._frozen_members.items()):
            locked = (
                self._representatives.get(cluster_id)
                if current_map
                else None
            )
            selection = maintain_representative_lock(
                distance=self._frozen_distance,
                weekly_window=window,
                members=members,
                adv20=adv,
                candidate_count=cfg.representative_candidate_count,
                min_cluster_corr=cfg.representative_min_cluster_corr,
                eligible=tradable,
                current=locked,
                tie_break=tie_break,
                relaxed_selection=cfg.representative_relaxed_selection,
            )
            self._representatives[cluster_id] = selection.selected
            self._selection_history.append(
                {
                    "week": signal_date,
                    "cluster_id": cluster_id,
                    "medoid": selection.medoid,
                    "selected": selection.selected,
                    "previous": locked,
                    "selection_mode": (
                        "LOCK_MAINTENANCE" if current_map else "FRESH"
                    ),
                    "lock_maintained": selection.lock_maintained,
                    "exclusion_reason": selection.exclusion_reason,
                    "signal_information_cutoff": (
                        _SIGNAL_INFORMATION_CUTOFF
                    ),
                    "candidates": [
                        {
                            "code": record.code,
                            "distance_to_medoid": (
                                record.distance_to_medoid
                            ),
                            "leave_one_out_corr": (
                                record.leave_one_out_corr
                            ),
                            "adv20": record.adv20,
                            "excluded_reason": record.excluded_reason,
                        }
                        for record in selection.candidates
                    ],
                    **_candidate_threshold_diagnostics(
                        selection.candidates,
                        selected=selection.selected,
                        threshold=cfg.representative_min_cluster_corr,
                    ),
                }
            )

    def _liquidity_stats(self, view, signal_date: str):
        """Return causal liquidity using information available at market close.

        Because decisions are defined after the signal-day close, that day's
        amount is observable and is included. Execution still occurs later.
        """
        cfg = self._config
        bars = view.daily_bars(
            ["vol", "amount"],
            lookback=cfg.representative_liquidity_window_days,
        )
        bars = bars[bars["trade_date"].astype(str) <= signal_date]

        listing_days: dict[str, int] = {}
        signal_timestamp = pd.Timestamp(signal_date)
        for instrument in view.eligible_universe():
            try:
                listed = pd.Timestamp(instrument.list_date)
                listing_days[instrument.ts_code] = max(
                    (signal_timestamp - listed).days,
                    0,
                )
            except (TypeError, ValueError):
                listing_days[instrument.ts_code] = 0

        adv: dict[str, float] = {}
        tie_break: dict[str, tuple[int, int]] = {}
        hard_failed: set[str] = set()
        if bars.empty:
            return adv, tie_break, hard_failed
        for code, group in bars.sort_values("trade_date").groupby("ts_code"):
            tail = group.tail(cfg.representative_liquidity_window_days)
            amounts = pd.to_numeric(tail["amount"], errors="coerce")
            streak = amounts.tail(_ZERO_TURN_STREAK_DAYS)
            if (
                len(streak) >= _ZERO_TURN_STREAK_DAYS
                and (streak.fillna(0.0) <= 0).all()
            ):
                hard_failed.add(str(code))
                continue
            valid_days = int((amounts > 0).sum())
            if valid_days < cfg.representative_min_liquidity_observations:
                continue
            adv[str(code)] = float(amounts[amounts > 0].mean())
            tie_break[str(code)] = (
                valid_days,
                listing_days.get(str(code), 0),
            )
        return adv, tie_break, hard_failed

    def _log_decision(
        self,
        decision: TargetWeightDecision,
        *,
        scores: Mapping[int, StrategyScore] | None = None,
        ranked_subjects: list[int] | None = None,
    ) -> None:
        self._decision_log.append(
            {
                "signal_date": decision.signal_date,
                "action": decision.action.value,
                "reason_code": decision.reason_code,
                "quality_status": decision.quality_status.value,
                "target_weights": dict(decision.target_weights),
                "cash_weight": decision.cash_weight,
                "diagnostics": dict(decision.diagnostics),
            }
        )
        score_by_cluster = dict(scores or {})
        ranked_clusters = (
            list(ranked_subjects)
            if ranked_subjects is not None
            else rank_scores(score_by_cluster, cluster_members=self._frozen_members)
        )
        rank_by_cluster = {
            cluster_id: index
            for index, cluster_id in enumerate(ranked_clusters, start=1)
        }
        selected_codes = set(decision.target_weights)
        candidates: list[dict] = []
        for code, cluster_id in sorted(self._clusters.items()):
            representative = self._representatives.get(cluster_id) == code
            score = score_by_cluster.get(cluster_id) if representative else None
            score_value = score.value if score is not None else None
            score_eligible = bool(score is not None and score.eligible)
            rank = rank_by_cluster.get(cluster_id) if score_eligible else None
            row = {
                "ts_code": code,
                "stages": {
                    "universe_eligible": True,
                    "cluster_id": cluster_id,
                    "cluster_representative": representative,
                    "ranking_eligible": score_eligible,
                    "rank": rank,
                    "portfolio_selected": code in selected_codes,
                },
                "primary_metric": {
                    "id": score.model_id if score is not None else "strategy_score",
                    "label": score.label if score is not None else "Strategy Score",
                    "value": score_value,
                } if score is not None and score_value is not None else None,
                "score": _serialize_score(score) if score is not None else None,
                "previous_weight": float(self._previous_weights.get(code, 0.0)),
                "target_weight": float(decision.target_weights.get(code, 0.0)),
                "exclusion_stage": (
                    "CLUSTER" if not representative else None
                ),
                "exclusion_reason": (
                    "SAME_CLUSTER_EXCLUDED" if not representative else None
                ),
            }
            candidates.append(row)
        cluster_snapshot = None
        if self._cluster_history:
            gate = self._gate_history[-1] if self._gate_history else {}
            results = gate.get("results", [])
            max_share = next(
                (item for item in results if item.get("code") == "MAX_CLUSTER_SHARE"),
                {},
            )
            effective = next(
                (item for item in results if item.get("code") == "EFFECTIVE_CLUSTER_COUNT"),
                {},
            )
            cluster_snapshot = {
                "snapshot_date": self._cluster_history[-1]["week"],
                "overall": gate.get("overall"),
                "max_cluster_share": max_share.get("actual"),
                "max_cluster_share_warn_threshold": max_share.get("warn_threshold"),
                "max_cluster_share_reject_threshold": max_share.get("reject_threshold"),
                "effective_cluster_count": effective.get("actual"),
                "effective_cluster_count_warn_threshold": effective.get("warn_threshold"),
                "effective_cluster_count_reject_threshold": effective.get("reject_threshold"),
            }
        self._decision_trace.append(
            {
                "signal_date": decision.signal_date,
                "cluster_snapshot": cluster_snapshot,
                "candidates": candidates,
            }
        )
        self._previous_weights = dict(decision.target_weights)

    def finalize(self) -> StrategyDiagnostics:
        return StrategyDiagnostics(
            artifacts=(
                StrategyArtifact(
                    role="cluster_history",
                    media_type="application/json",
                    payload=self._cluster_history,
                ),
                StrategyArtifact(
                    role="gates",
                    media_type="application/json",
                    payload=self._gate_history,
                ),
                StrategyArtifact(
                    role="representatives",
                    media_type="application/json",
                    payload=self._selection_history,
                ),
                StrategyArtifact(
                    role="exclusions",
                    media_type="application/json",
                    payload=self._exclusions,
                ),
                StrategyArtifact(
                    role="decisions",
                    media_type="application/json",
                    payload=self._decision_log,
                ),
            ),
            decision_trace=tuple(self._decision_trace),
        )

class CorrelationRepresentativeStrategy:
    descriptor = DESCRIPTOR
    config_model = CorrelationRepresentativeConfig
    artifact_roles: tuple[str, ...] = (
        "cluster_history",
        "gates",
        "representatives",
        "exclusions",
        "decisions",
    )

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        cfg: CorrelationRepresentativeConfig = config  # type: ignore[assignment]
        return {
            "universe": "ETF",
            "dedup_method": "Correlation Clustering",
            "representative_method": "Liquidity representative with lock",
            "score_model": {
                "id": ClusterMomentumScoreModel.id,
                "label": ClusterMomentumScoreModel.label,
                "version": ClusterMomentumScoreModel.version,
                "direction": "HIGHER_BETTER",
            },
            "selection_rule": f"Top {cfg.top_n}",
            "top_n": cfg.top_n,
            "weighting_rule": "Equal Weight",
            "rebalance_frequency": "Weekly",
        }

    def resolve_requirements(
        self,
        config: BaseModel,
    ) -> StrategyDataRequirements:
        cfg: CorrelationRepresentativeConfig = config  # type: ignore[assignment]
        warmup_trade_days = (
            cfg.correlation_lookback_weeks + 1
        ) * 5 - 1
        return StrategyDataRequirements(
            required_datasets=(
                "fund",
                "fact_fund_adj",
                "dim_fund",
            ),
            required_fields=(
                "ts_code",
                "trade_date",
                "name",
                "list_date",
                "open",
                "close",
                "high",
                "low",
                "pre_close",
                "vol",
                "amount",
                "adj_factor",
            ),
            warmup_trade_days=warmup_trade_days,
            frequency="weekly",
            needs_benchmark=False,
        )

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> CorrelationRepresentativeSession:
        return CorrelationRepresentativeSession(config)  # type: ignore[arg-type]
