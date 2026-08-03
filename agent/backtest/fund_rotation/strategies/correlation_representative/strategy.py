"""Correlation representative strategy — design §8/§9.

Eligibility is resolved point-in-time for every signal date. Representative
selection occurs on reclustering dates; between reclusters the selected ETF is
kept unless a hard tradability or liquidity failure occurs.
"""

from __future__ import annotations

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
from backtest.fund_rotation.momentum import (
    compute_cluster_momentum,
    select_top_clusters,
)
from backtest.fund_rotation.strategies.correlation_all_members.signals import (
    ensure_instrument_pool,
    signal_date_eligible,
)
from backtest.fund_rotation.strategies.correlation_representative.clustering import (
    correlation_cluster,
)
from backtest.fund_rotation.strategies.correlation_representative.config import (
    CorrelationRepresentativeConfig,
)
from backtest.fund_rotation.strategies.correlation_representative.gates import (
    GateStatus,
    cluster_quality_rejection_decision,
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
        "留一簇指数相关性门禁 + 因果 ADV 选择 + 锁定/硬失效回退）。"
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
        self._cycle_rejected = False
        self._last_gate_overall = GateStatus.PASS
        self._cluster_history: list[dict] = []
        self._gate_history: list[dict] = []
        self._selection_history: list[dict] = []
        self._exclusions: list[ExclusionRecord] = []
        self._decision_log: list[dict] = []

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
        elif self._cycle_rejected:
            gates = self._gate_history[-1] if self._gate_history else None
            decision = cluster_quality_rejection_decision(
                signal_date=signal_date,
                decision_id=f"{signal_date}-{DESCRIPTOR.id}",
                gates=_rebuild_gate_evaluation(gates),
            )
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
        momentum = compute_cluster_momentum(
            momentum_window,
            self._clusters,
            cfg.momentum_window_weeks,
        )
        selected = select_top_clusters(
            momentum,
            top_n=cfg.top_n,
            threshold=0.0,
            cluster_members=self._frozen_members,
        )
        weights, filled, vacant, cash_weight = build_slot_weights(
            selected,
            self._representatives,
            cfg.top_n,
        )
        quality = (
            QualityStatus.DEGRADED
            if self._last_gate_overall is GateStatus.WARN
            else QualityStatus.VALID
        )
        decision = TargetWeightDecision(
            decision_id=f"{signal_date}-{DESCRIPTOR.id}",
            signal_date=signal_date,
            action=DecisionKind.SET_TARGETS,
            target_weights=dict(weights),
            cash_weight=cash_weight,
            quality_status=quality,
            diagnostics={
                "filled_slots": filled,
                "vacant_slots": vacant,
                "momentum": {
                    str(cluster_id): value
                    for cluster_id, value in momentum.items()
                },
                "num_clusters": len(self._clusters),
                "signal_information_cutoff": _SIGNAL_INFORMATION_CUTOFF,
            },
        )
        self._log_decision(decision)
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

        try:
            outcome = correlation_cluster(
                window,
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

        if gates.rejected:
            self._cycle_rejected = True
            self._representatives = {}
            return cluster_quality_rejection_decision(
                signal_date=signal_date,
                decision_id=f"{signal_date}-{DESCRIPTOR.id}",
                gates=gates,
            )

        self._cycle_rejected = False
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
            )
            self._representatives[cluster_id] = selection.selected
            self._selection_history.append(
                {
                    "week": signal_date,
                    "cluster_id": cluster_id,
                    "medoid": selection.medoid,
                    "selected": selection.selected,
                    "previous": locked,
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

    def _log_decision(self, decision: TargetWeightDecision) -> None:
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
            )
        )


def _rebuild_gate_evaluation(entry: dict | None):
    from backtest.fund_rotation.strategies.correlation_representative.gates import (
        GateEvaluation,
        GateResult,
    )

    if not entry:
        return GateEvaluation(overall=GateStatus.REJECT, results=())
    return GateEvaluation(
        overall=GateStatus(entry["overall"]),
        results=tuple(
            GateResult(
                code=result["code"],
                status=GateStatus(result["status"]),
                actual=result["actual"],
                warn_threshold=result["warn_threshold"],
                reject_threshold=result["reject_threshold"],
                affected_codes=tuple(result["affected_codes"]),
            )
            for result in entry["results"]
        ),
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
