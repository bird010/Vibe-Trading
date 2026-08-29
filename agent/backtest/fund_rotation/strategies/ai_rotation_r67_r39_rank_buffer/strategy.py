"""Round 67: R39 with same-epoch Top3-entry/Top4-exit hysteresis."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import replace

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
from backtest.fund_rotation.scoring.contracts import rank_scores
from backtest.fund_rotation.strategies.ai_rotation_r11_persist_geom.strategy import (
    AiRotationR11PersistGeomStrategy,
    _SIGNAL_INFORMATION_CUTOFF,
    _momentum_diagnostics,
    _serialize_scores,
    compute_persist_geom_scores,
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
    CorrelationRepresentativeStrategy,
    build_slot_weights,
)
from backtest.fund_rotation.universe import check_historical_eligibility


_ENTRY_RANK = 3
_EXIT_RANK = 4

DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r67_r39_rank_buffer",
    name="R39 Top3入场Top4退出排名迟滞",
    description=(
        "完全沿用 R39 的持续几何动量、代表 ETF、半仓再入场和 incumbent carry，"
        "仅在同一聚类 epoch 内保留当前排名第四的上一期入选簇。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)


def select_rank_buffer_clusters(
    ranked_clusters: Sequence[int],
    previous_selected: Sequence[int],
    top_n: int = _ENTRY_RANK,
    exit_rank: int = _EXIT_RANK,
    epoch_reset: bool = False,
) -> tuple[list[int], list[int]]:
    """Select current top-N clusters while retaining eligible prior clusters."""
    ranked = list(ranked_clusters)
    if epoch_reset:
        return ranked[: max(top_n, 0)], []

    rank_by_cluster = {
        cluster_id: rank for rank, cluster_id in enumerate(ranked, start=1)
    }
    retained = sorted(
        {
            cluster_id
            for cluster_id in previous_selected
            if cluster_id in rank_by_cluster
            and rank_by_cluster[cluster_id] <= exit_rank
        },
        key=lambda cluster_id: (rank_by_cluster[cluster_id], cluster_id),
    )
    retained_set = set(retained)
    fillers = [cluster_id for cluster_id in ranked if cluster_id not in retained_set]
    return (retained + fillers)[: max(top_n, 0)], retained


class AiRotationR67R39RankBufferSession(AiRotationR39IncumbentCarrySession):
    def __init__(self, config) -> None:
        super().__init__(config)
        self._previous_selected_clusters: list[int] = []

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        cfg = self._config
        signal_date = context.signal_date
        view = context.data_view
        week_index = self._week_index
        self._week_index += 1
        previous_weights = dict(self._previous_weights)

        dim_pool = self._pool_at_signal(view)
        window = view.returns("weekly", cfg.correlation_lookback_weeks)
        historically_eligible, historical_excluded = check_historical_eligibility(
            dim_pool, signal_date
        )
        kept, market_excluded = signal_date_eligible(
            view, historically_eligible, signal_date
        )
        self._exclusions.extend(market_excluded)
        eligible_set = set(kept)
        reclustering = (
            week_index - self._last_recluster_week >= cfg.recluster_interval_weeks
            or not self._clusters
        )
        if reclustering:
            self._previous_selected_clusters = []
            self._exclusions.extend(historical_excluded)
            decision = self._recluster(view, window, kept, eligible_set, signal_date)
            if decision is not None:
                return self._apply_r39_overlays(decision, previous_weights)
        else:
            self._maintain_locks(view, window, eligible_set, signal_date)

        momentum_window = window.iloc[-(cfg.momentum_window_weeks + 1):]
        scores, current_raw, lagged_raw, geometric_raw = compute_persist_geom_scores(
            momentum_window, self._clusters, cfg.momentum_window_weeks
        )
        ranked = rank_scores(scores, cluster_members=self._frozen_members)
        previous_selected = list(self._previous_selected_clusters)
        selected, retained = select_rank_buffer_clusters(
            ranked,
            previous_selected,
            top_n=cfg.top_n,
            exit_rank=_EXIT_RANK,
            epoch_reset=reclustering,
        )
        self._previous_selected_clusters = list(selected)
        base_weights, filled, vacant, _ = build_slot_weights(
            selected, self._representatives, cfg.top_n
        )
        staged_weights, _, staged = apply_staged_reentry(
            previous_weights, base_weights
        )
        final_weights, final_cash, staged, incumbents = apply_incumbent_carry(
            previous_weights, staged_weights
        )

        current_values, current_unavailable = _momentum_diagnostics(
            {key: value if value is not None else math.nan for key, value in current_raw.items()}
        )
        lagged_values, lagged_unavailable = _momentum_diagnostics(
            {key: value if value is not None else math.nan for key, value in lagged_raw.items()}
        )
        geometric_values, geometric_unavailable = _momentum_diagnostics(
            {key: value if value is not None else math.nan for key, value in geometric_raw.items()}
        )
        unavailable = sorted(
            set(current_unavailable) | set(lagged_unavailable) | set(geometric_unavailable)
        )
        quality = (
            QualityStatus.VALID
            if self._last_gate_overall.value == "PASS"
            else QualityStatus.DEGRADED
        )
        reason = "CLUSTER_QUALITY_REJECTED" if quality is QualityStatus.DEGRADED else ""
        if staged:
            reason = _append_reason(reason, "STAGED_REENTRY")
        if incumbents:
            reason = _append_reason(reason, "INCUMBENT_CARRY")
        rank_by_cluster = {
            str(cluster_id): rank for rank, cluster_id in enumerate(ranked, start=1)
        }
        diagnostics = {
            "filled_slots": filled,
            "vacant_slots": vacant,
            "momentum": current_values,
            "momentum_status": "PARTIAL" if unavailable else "COMPLETE",
            "momentum_unavailable_clusters": unavailable,
            "momentum_available_cluster_count": len(scores) - len(unavailable),
            "momentum_total_cluster_count": len(scores),
            "lagged_momentum": lagged_values,
            "lagged_momentum_unavailable_clusters": lagged_unavailable,
            "geometric_momentum": geometric_values,
            "geometric_momentum_unavailable_clusters": geometric_unavailable,
            "lagged_momentum_window_weeks": cfg.momentum_window_weeks,
            "persistence_gate": "current_and_lagged_strictly_positive",
            "score_model": {
                "id": "persistent_geometric_cluster_momentum",
                "label": "Persistent Geometric Cluster Momentum",
                "version": "1",
                "direction": "HIGHER_BETTER",
            },
            "strategy_scores": _serialize_scores(scores),
            "num_clusters": len(self._clusters),
            "signal_information_cutoff": _SIGNAL_INFORMATION_CUTOFF,
            "rank_buffer": {
                "entry_rank": _ENTRY_RANK,
                "exit_rank": _EXIT_RANK,
                "ranked_clusters": list(ranked),
                "retained_clusters": list(retained),
                "selected_clusters": list(selected),
                "forced_exit_clusters": sorted(set(previous_selected) - set(retained)),
                "current_rank_by_cluster": rank_by_cluster,
                "epoch_reset": reclustering,
            },
            "staged_reentry_fraction": 0.5,
            "staged_reentry_codes": sorted(staged),
            "incumbent_carry_codes": sorted(incumbents),
            "staged_reentry_rule": "new_representative_target_weight_halved_once",
            "incumbent_carry_rule": "released_new_target_weight_proportional_to_continuous_base_target_weight",
            "reclustered": reclustering,
        }
        decision = TargetWeightDecision(
            decision_id=f"{signal_date}-{DESCRIPTOR.id}",
            signal_date=signal_date,
            action=DecisionKind.SET_TARGETS,
            target_weights=final_weights,
            cash_weight=final_cash,
            reason_code=reason,
            quality_status=quality,
            diagnostics=diagnostics,
        )
        self._log_decision(decision, scores=scores, ranked_subjects=ranked)
        self._patch_r39_artifacts(decision)
        return decision

    def _apply_r39_overlays(
        self,
        decision: TargetWeightDecision,
        previous_weights: dict[str, float],
    ) -> TargetWeightDecision:
        staged_weights, _, staged = apply_staged_reentry(
            previous_weights, decision.target_weights
        )
        final_weights, final_cash, staged, incumbents = apply_incumbent_carry(
            previous_weights, staged_weights
        )
        diagnostics = dict(decision.diagnostics)
        diagnostics.update(
            {
                "staged_reentry_fraction": 0.5,
                "staged_reentry_codes": sorted(staged),
                "incumbent_carry_codes": sorted(incumbents),
                "reclustered": True,
            }
        )
        patched = replace(
            decision,
            decision_id=f"{decision.signal_date}-{DESCRIPTOR.id}",
            target_weights=final_weights,
            cash_weight=final_cash,
            reason_code=_append_reason(
                decision.reason_code, "INCUMBENT_CARRY" if incumbents else ""
            ),
            diagnostics=diagnostics,
        )
        self._patch_r39_artifacts(patched)
        return patched

    def _patch_r39_artifacts(self, decision: TargetWeightDecision) -> None:
        if self._decision_log:
            self._decision_log[-1].update(
                {
                    "decision_id": decision.decision_id,
                    "target_weights": dict(decision.target_weights),
                    "cash_weight": decision.cash_weight,
                    "reason_code": decision.reason_code,
                    "diagnostics": dict(decision.diagnostics),
                }
            )
        if self._decision_trace:
            for candidate in self._decision_trace[-1].get("candidates", []):
                code = candidate.get("ts_code")
                candidate["target_weight"] = float(decision.target_weights.get(code, 0.0))
                candidate.setdefault("stages", {})["portfolio_selected"] = (
                    code in decision.target_weights
                )
        self._previous_weights = dict(decision.target_weights)


class AiRotationR67R39RankBufferStrategy(AiRotationR39IncumbentCarryStrategy):
    descriptor = DESCRIPTOR
    config_model = CorrelationRepresentativeStrategy.config_model
    artifact_roles = AiRotationR39IncumbentCarryStrategy.artifact_roles

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = AiRotationR11PersistGeomStrategy().describe_decision_pipeline(config)
        pipeline["selection_rule"] += "; same-epoch Top3 entry and Top4 exit rank buffer"
        pipeline["staging_rule"] = "50% staged re-entry once for new targets"
        pipeline["carry_rule"] = "Incumbent carry proportional to continuous base target weight"
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        return CorrelationRepresentativeStrategy().resolve_requirements(config)

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> AiRotationR67R39RankBufferSession:
        del initialization
        return AiRotationR67R39RankBufferSession(config)
