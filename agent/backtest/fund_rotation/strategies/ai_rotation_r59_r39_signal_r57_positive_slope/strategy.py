"""Round 59: R58's weekly lifecycle with a causal positive raw-slope gate."""

from __future__ import annotations

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
from backtest.fund_rotation.strategies.ai_rotation_r34_staged_reentry.strategy import (
    _append_reason,
    apply_staged_reentry,
)
from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import (
    apply_incumbent_carry,
)
from backtest.fund_rotation.strategies.ai_rotation_r57_three_factor_representative.factors import (
    score_complete_candidates,
)
from backtest.fund_rotation.strategies.ai_rotation_r58_r39_signal_r57.strategy import (
    AiRotationR58R39SignalR57Session,
    _json_number,
)
from backtest.fund_rotation.strategies.correlation_all_members.signals import (
    signal_date_eligible,
)
from backtest.fund_rotation.strategies.correlation_representative.gates import (
    GateStatus,
)
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    CorrelationRepresentativeStrategy,
    _SIGNAL_INFORMATION_CUTOFF,
    build_slot_weights,
)
from backtest.fund_rotation.universe import check_historical_eligibility


DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r59_r39_signal_r57_positive_slope",
    name="周频R39生命周期三因子正斜率代表ETF轮动",
    description=(
        "完全沿用R58的R39周末调度、相关性聚类、锁定代表、三槽位、现金、"
        "半仓再入场与持续目标承接，仅在R57三因子评分后要求同一因果49行"
        "adjusted OHLC口径的raw_slope_25d > 0。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)

_R59_TOP_N = 3


def _validate_r59_config(config) -> None:
    if getattr(config, "top_n", None) != _R59_TOP_N:
        raise ValueError("ai_rotation_r59_r39_signal_r57_positive_slope requires top_n=3")


class AiRotationR59R39SignalR57PositiveSlopeSession(AiRotationR58R39SignalR57Session):
    """R58's R39 state machine with a post-score positive-slope filter."""

    def __init__(self, config) -> None:
        _validate_r59_config(config)
        super().__init__(config)

    def _factor_rows(self, view, signal_date: str) -> dict[str, dict[str, object]]:
        rows = super()._factor_rows(view, signal_date)
        for row in rows.values():
            # R58's slope is computed from the causal 49-row adjusted OHLC window.
            row["raw_slope_25d"] = _json_number(row.get("slope"))
        return rows

    @staticmethod
    def _apply_positive_slope_filter(
        factor_rows: dict[str, dict[str, object]],
        composite: dict[str, float],
        score_details: dict[str, object],
    ) -> tuple[dict[str, float], dict[str, object]]:
        before = list(score_details.get("complete_candidates", []))
        positive_codes = {
            code
            for code, row in factor_rows.items()
            if (slope := _json_number(row.get("raw_slope_25d"))) is not None
            and slope > 0.0
        }
        filtered_composite = {
            code: value for code, value in composite.items() if code in positive_codes
        }
        filtered_details = dict(score_details)
        filtered_details["complete_candidates"] = [
            code for code in before if code in filtered_composite
        ]
        filtered_details["r57_complete_candidates_before_positive_slope"] = before
        filtered_details["raw_slope_25d_qualified_candidates"] = sorted(
            code for code in before if code in positive_codes
        )
        filtered_details["raw_slope_25d_rule"] = "raw_slope_25d > 0"
        return filtered_composite, filtered_details

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        cfg = self._config
        signal_date = context.signal_date
        view = context.data_view
        week_index = self._week_index
        self._week_index += 1

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
            week_index - self._last_recluster_week
            >= cfg.recluster_interval_weeks
            or not self._clusters
        )
        if reclustering:
            self._exclusions.extend(historical_excluded)
            invalid = self._recluster(
                view, window, kept, eligible_set, signal_date
            )
            if invalid is not None:
                previous_weights = dict(self._previous_weights)
                staged_weights, _, _ = apply_staged_reentry(
                    previous_weights,
                    invalid.target_weights,
                )
                final_weights, final_cash, staged, incumbents = apply_incumbent_carry(
                    previous_weights,
                    staged_weights,
                )
                invalid_diagnostics = dict(invalid.diagnostics)
                invalid_diagnostics.update(
                    {
                        "staged_reentry_fraction": 0.5,
                        "staged_reentry_codes": sorted(staged),
                        "incumbent_carry_codes": sorted(incumbents),
                        "staged_reentry_rule": (
                            "new_representative_target_weight_halved_once"
                        ),
                        "incumbent_carry_rule": (
                            "released_new_target_weight_proportional_to_"
                            "continuous_base_target_weight"
                        ),
                    }
                )
                invalid = replace(
                    invalid,
                    decision_id=f"{signal_date}-{DESCRIPTOR.id}",
                    target_weights=final_weights,
                    cash_weight=final_cash,
                    reason_code=_append_reason(
                        invalid.reason_code,
                        "INCUMBENT_CARRY" if incumbents else "",
                    ),
                    diagnostics=invalid_diagnostics,
                )
                self._log_decision(invalid)
                return invalid
        else:
            self._maintain_locks(view, window, eligible_set, signal_date)

        factor_rows = self._factor_rows(view, signal_date)
        raw_scores = {
            code: {
                name: row.get(name)
                for name in ("bias", "slope", "efficiency")
            }
            for code, row in factor_rows.items()
        }
        composite, score_details = score_complete_candidates(
            raw_scores,
            {"bias": 0.3, "slope": 0.3, "efficiency": 0.4},
            2,
        )
        composite, score_details = self._apply_positive_slope_filter(
            factor_rows, composite, score_details
        )
        complete = set(score_details["complete_candidates"])
        cluster_by_code = {
            code: int(row["cluster_id"])
            for code, row in factor_rows.items()
            if code in complete
        }
        ranked_codes = list(composite)
        selected_codes = set(ranked_codes[:_R59_TOP_N])
        selected_clusters = [
            cluster_by_code[code]
            for code in ranked_codes
            if code in selected_codes
        ]
        base_weights, filled, vacant, _ = build_slot_weights(
            selected_clusters,
            self._representatives,
            _R59_TOP_N,
        )
        previous_weights = dict(self._previous_weights)
        staged_weights, _, _ = apply_staged_reentry(
            previous_weights,
            base_weights,
        )
        final_weights, final_cash, staged, incumbents = apply_incumbent_carry(
            previous_weights,
            staged_weights,
        )
        self._enrich_factor_rows(
            factor_rows,
            score_details,
            composite,
            selected_codes,
            base_weights,
            final_weights,
            final_cash,
            staged,
            incumbents,
        )

        rejected = self._last_gate_overall is GateStatus.REJECT
        quality = (
            QualityStatus.VALID
            if self._last_gate_overall is GateStatus.PASS
            else QualityStatus.DEGRADED
        )
        reason = (
            "INSUFFICIENT_COMPLETE_CANDIDATES"
            if len(complete) < 2
            else ("CLUSTER_QUALITY_REJECTED" if rejected else "")
        )
        if staged:
            reason = _append_reason(reason, "STAGED_REENTRY")
        if incumbents:
            reason = _append_reason(reason, "INCUMBENT_CARRY")
        diagnostics = {
            "filled_slots": filled,
            "vacant_slots": vacant,
            "factor_scores": factor_rows,
            "score_details": score_details,
            "complete_candidate_count": len(complete),
            "ranked_codes": ranked_codes,
            "staged_reentry_fraction": 0.5,
            "staged_reentry_codes": sorted(staged),
            "incumbent_carry_codes": sorted(incumbents),
            "staged_reentry_rule": "new_representative_target_weight_halved_once",
            "incumbent_carry_rule": (
                "released_new_target_weight_proportional_to_"
                "continuous_base_target_weight"
            ),
            "signal_information_cutoff": _SIGNAL_INFORMATION_CUTOFF,
            "reclustered": reclustering,
            "score_model": {
                "id": "r57_three_factor",
                "label": "R57 Three-Factor Momentum",
                "version": "1",
                "direction": "HIGHER_BETTER",
            },
            "selection_filter": "raw_slope_25d > 0 after R57 composite scoring",
            "num_clusters": len(self._clusters),
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
        scores = self._scores_by_cluster(factor_rows, composite)
        ranked_clusters = [cluster_by_code[code] for code in ranked_codes]
        self._log_decision(
            decision,
            scores=scores,
            ranked_subjects=ranked_clusters,
        )
        self._factor_scores.append(
            {
                "signal_date": signal_date,
                "rows": [factor_rows[code] for code in sorted(factor_rows)],
                "complete_candidates": sorted(complete),
                "ranked_codes": ranked_codes,
            }
        )
        return decision


class AiRotationR59R39SignalR57PositiveSlopeStrategy:
    """Complete round 59 strategy plug-in."""

    descriptor = DESCRIPTOR
    config_model = CorrelationRepresentativeStrategy.config_model
    artifact_roles: tuple[str, ...] = (
        "cluster_history",
        "gates",
        "representatives",
        "exclusions",
        "factor_scores",
        "decisions",
    )

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        _validate_r59_config(config)
        return {
            "universe": "ETF",
            "dedup_method": "Correlation Clustering",
            "representative_method": "R39 locked liquidity representative",
            "score_model": {
                "id": "r57_three_factor",
                "label": "R57 Three-Factor Momentum",
                "version": "1",
                "direction": "HIGHER_BETTER",
            },
            "selection_rule": (
                "R57 three-factor score, then raw_slope_25d > 0, then Top 3 "
                "representative clusters"
            ),
            "top_n": _R59_TOP_N,
            "weighting_rule": "Fixed 1/top_n slots with vacant cash",
            "rebalance_frequency": "Weekly",
            "staging_rule": "50% staged re-entry once for new targets",
            "carry_rule": "Incumbent carry proportional to continuous base target weight",
        }

    def resolve_requirements(
        self,
        config: BaseModel,
    ) -> StrategyDataRequirements:
        _validate_r59_config(config)
        return CorrelationRepresentativeStrategy().resolve_requirements(config)

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> AiRotationR59R39SignalR57PositiveSlopeSession:
        del initialization
        return AiRotationR59R39SignalR57PositiveSlopeSession(config)


AiRotationR59R39SignalR57Session = AiRotationR59R39SignalR57PositiveSlopeSession
AiRotationR59R39SignalR57Strategy = AiRotationR59R39SignalR57PositiveSlopeStrategy

