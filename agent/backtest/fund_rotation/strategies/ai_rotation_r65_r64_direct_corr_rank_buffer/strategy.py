"""Round 65: R64 direct-correlation selection with a Top-4 exit buffer."""

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
from backtest.fund_rotation.strategies.ai_rotation_r34_staged_reentry.strategy import (
    _append_reason,
    apply_staged_reentry,
)
from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import (
    apply_incumbent_carry,
)
from backtest.fund_rotation.strategies.ai_rotation_r58_r39_signal_r57.strategy import (
    AiRotationR58R39SignalR57Session,
)
from backtest.fund_rotation.strategies.ai_rotation_r59_r39_signal_r57_positive_slope.strategy import (
    AiRotationR59R39SignalR57PositiveSlopeSession,
)
from backtest.fund_rotation.strategies.ai_rotation_r64_direct_corr_diversification.strategy import (
    AiRotationR64DirectCorrelationSession,
    build_r64_score_evidence,
    select_direct_correlation_diversified,
)
from backtest.fund_rotation.strategies.correlation_all_members.signals import (
    ensure_instrument_pool,
    signal_date_eligible,
)
from backtest.fund_rotation.universe import check_historical_eligibility
from backtest.fund_rotation.evaluation import iso_week_endings

from .config import DirectCorrelationRankBufferConfig


TOP_N = 3
EXIT_RANK = 4
MAX_PAIRWISE_CORRELATION = 0.80

DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r65_r64_direct_corr_rank_buffer",
    name="R64 直接相关性约束加 Top4 退出缓冲",
    description=(
        "保持 R64 的 PIT、R57 正斜率、52 周 pairwise corr<0.80、equal-slot、"
        "staging 与 carry，仅优先保留上一期实际持仓中当前 rank<=4 的标的。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)


def _positive_weight(value: object) -> bool:
    try:
        weight = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(weight) and weight > 0.0


def select_r65_rank_buffered_direct_corr(
    ranked_codes: list[str],
    previous_weights: Mapping[str, float],
    correlations: Mapping[str, float],
    observations: Mapping[str, int],
    *,
    top_n: int = TOP_N,
    exit_rank: int = EXIT_RANK,
    threshold: float = MAX_PAIRWISE_CORRELATION,
    min_pairwise_weeks: int = 20,
) -> tuple[list[str], dict[str, object]]:
    """Prioritize held rank<=4 codes, then apply the unchanged R64 selector."""

    ranked = [str(code) for code in ranked_codes]
    current_rank = {code: index + 1 for index, code in enumerate(ranked)}
    previous_held = {
        str(code)
        for code, weight in previous_weights.items()
        if _positive_weight(weight)
    }
    rank_qualified = sorted(
        (
            code
            for code in previous_held
            if current_rank.get(code, exit_rank + 1) <= exit_rank
        ),
        key=lambda code: (current_rank[code], code),
    )
    priority_order = rank_qualified + [
        code for code in ranked if code not in set(rank_qualified)
    ]
    selected, correlation_diag = select_direct_correlation_diversified(
        priority_order,
        dict(correlations),
        dict(observations),
        top_n=top_n,
        threshold=threshold,
        min_pairwise_weeks=min_pairwise_weeks,
    )
    selected_set = set(selected)
    diagnostics = {
        "entry_rank": top_n,
        "exit_rank": exit_rank,
        "previous_held_codes": sorted(previous_held),
        "rank_qualified_previous_codes": rank_qualified,
        "priority_order": priority_order,
        "current_rank_by_code": {
            str(code): rank for code, rank in current_rank.items()
        },
        "retained_codes": [
            code for code in rank_qualified if code in selected_set
        ],
        "buffer_rejected_previous_codes": [
            code for code in rank_qualified if code not in selected_set
        ],
        "forced_exit_codes": sorted(previous_held - selected_set),
        "selected_codes": selected,
        "correlation": correlation_diag,
    }
    return selected, diagnostics


class AiRotationR65R64DirectCorrRankBufferSession:
    """R64 session with only candidate priority changed before selection."""

    def __init__(self, config):
        self._config = config
        self._previous_weights = {}
        self._representatives = {}
        self._factor_scores = []
        self._decisions = []
        self._correlations = []
        self._exclusions = []
        self._decision_trace = []

    def scheduled_dates(self, calendar, simulation_start_date, evaluation_end_date):
        return tuple(
            date for date in iso_week_endings(calendar)
            if simulation_start_date <= date <= evaluation_end_date
        )

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        signal_date, view = context.signal_date, context.data_view
        previous_weights = dict(self._previous_weights)
        pool = ensure_instrument_pool(
            view,
            lookback_trade_days=(self._config.correlation_lookback_weeks + 1) * 5 - 1,
        )
        historical, historical_exclusions = check_historical_eligibility(
            pool, signal_date
        )
        eligible, market_exclusions = signal_date_eligible(
            view, historical, signal_date
        )
        self._exclusions.extend(historical_exclusions)
        self._exclusions.extend(market_exclusions)
        self._representatives = {
            index + 1: code for index, code in enumerate(sorted(eligible))
        }
        rows = AiRotationR58R39SignalR57Session._factor_rows(
            self, view, signal_date
        )
        for row in rows.values():
            row["raw_slope_25d"] = row.get("slope")
        raw = {
            code: {name: row.get(name) for name in ("bias", "slope", "efficiency")}
            for code, row in rows.items()
        }
        from backtest.fund_rotation.strategies.ai_rotation_r57_three_factor_representative.factors import (
            score_complete_candidates,
        )

        composite, details = score_complete_candidates(
            raw, {"bias": 0.3, "slope": 0.3, "efficiency": 0.4}, 2
        )
        composite, details = (
            AiRotationR59R39SignalR57PositiveSlopeSession._apply_positive_slope_filter(
                rows, composite, details
            )
        )
        for row in rows.values():
            row.pop("cluster_id", None)
            row.pop("is_representative", None)
        ranked = list(composite)
        returns = view.returns(
            "weekly", self._config.correlation_lookback_weeks
        )
        correlations: dict[str, float] = {}
        observations: dict[str, int] = {}
        for left_index, left in enumerate(ranked):
            for right in ranked[left_index + 1 :]:
                pair = (
                    returns[[left, right]].dropna()
                    if left in returns and right in returns
                    else pd.DataFrame()
                )
                key = "|".join(sorted((left, right)))
                observations[key] = len(pair)
                correlations[key] = (
                    float(pair[left].corr(pair[right]))
                    if len(pair) >= self._config.min_pairwise_weeks
                    else math.nan
                )
        selected, rank_buffer = select_r65_rank_buffered_direct_corr(
            ranked,
            previous_weights,
            correlations,
            observations,
            top_n=self._config.top_n,
            exit_rank=self._config.exit_rank,
            min_pairwise_weeks=self._config.min_pairwise_weeks,
        )
        base = {code: 1.0 / self._config.top_n for code in selected}
        staged, _, staged_codes = apply_staged_reentry(
            previous_weights, base
        )
        final, cash, staged_codes, incumbents = apply_incumbent_carry(
            previous_weights, staged
        )

        complete = set(details.get("complete_candidates", []))
        rank_by_code = {code: rank for rank, code in enumerate(ranked, 1)}
        for code, row in rows.items():
            evidence = build_r64_score_evidence(code, composite, details, row)
            row.update(
                {
                    "complete_candidate": code in complete,
                    "composite_score": composite.get(code),
                    "bias_zscore": evidence["components"]["bias"],
                    "slope_zscore": evidence["components"]["slope"],
                    "efficiency_zscore": evidence["components"]["efficiency"],
                    "rank": rank_by_code.get(code),
                    "top_3": code in selected,
                    "base_slot_weight": float(base.get(code, 0.0)),
                    "staged": code in staged_codes,
                    "incumbent_carry": code in incumbents,
                    "final_weight": float(final.get(code, 0.0)),
                    "cash_weight": float(cash),
                }
            )
        reason = (
            "INSUFFICIENT_COMPLETE_CANDIDATES"
            if len(details.get("complete_candidates", [])) < 2
            else ""
        )
        reason = _append_reason(reason, "STAGED_REENTRY" if staged_codes else "")
        reason = _append_reason(reason, "INCUMBENT_CARRY" if incumbents else "")
        quality = (
            QualityStatus.DEGRADED
            if reason.startswith("INSUFFICIENT_COMPLETE_CANDIDATES")
            else QualityStatus.VALID
        )
        diagnostics = {
            "factor_scores": rows,
            "score_details": details,
            "complete_candidate_count": len(details.get("complete_candidates", [])),
            "ranked_codes": ranked,
            "selected_codes": selected,
            "correlation": rank_buffer["correlation"],
            "rank_buffer": rank_buffer,
            "staged_reentry_codes": sorted(staged_codes),
            "incumbent_carry_codes": sorted(incumbents),
            "selection_filter": "R57 positive slope then Top4 rank buffer and pairwise correlation < 0.80",
        }
        decision = TargetWeightDecision(
            f"{signal_date}-{DESCRIPTOR.id}",
            signal_date,
            DecisionKind.SET_TARGETS,
            final,
            cash,
            reason,
            quality,
            diagnostics,
        )
        self._factor_scores.append(
            {
                "signal_date": signal_date,
                "rows": rows,
                "ranked_codes": ranked,
                "selected_codes": selected,
                "correlation": rank_buffer["correlation"],
            }
        )
        self._correlations.append(
            {"signal_date": signal_date, **rank_buffer["correlation"]}
        )
        self._decisions.append(
            {
                "decision_id": decision.decision_id,
                "signal_date": signal_date,
                "target_weights": dict(final),
                "cash_weight": cash,
                "reason_code": reason,
                "diagnostics": diagnostics,
            }
        )
        self._decision_trace.append(
            {
                "decision_id": decision.decision_id,
                "signal_date": signal_date,
                "candidates": [
                    {
                        "ts_code": code,
                        "stages": {
                            "universe_eligible": True,
                            "ranking_eligible": code in complete,
                            "rank": rank_by_code.get(code),
                            "portfolio_selected": code in selected,
                        },
                        "primary_metric": {
                            "id": "r57_three_factor",
                            "label": "R57 Three-Factor Momentum",
                            "value": composite.get(code),
                        },
                        "score": build_r64_score_evidence(
                            code, composite, details, rows[code]
                        ),
                        "previous_weight": float(previous_weights.get(code, 0.0)),
                        "before_weight": float(previous_weights.get(code, 0.0)),
                        "target_weight": float(final.get(code, 0.0)),
                    }
                    for code in sorted(rows)
                ],
                "target_weights": dict(final),
                "cash_weight": cash,
            }
        )
        self._previous_weights = dict(final)
        return decision

    def finalize(self):
        return StrategyDiagnostics(
            artifacts=(
                StrategyArtifact(role="factor_scores", media_type="application/json", payload=self._factor_scores),
                StrategyArtifact(role="correlations", media_type="application/json", payload=self._correlations),
                StrategyArtifact(role="exclusions", media_type="application/json", payload=self._exclusions),
                StrategyArtifact(role="decisions", media_type="application/json", payload=self._decisions),
            ),
            decision_trace=tuple(self._decision_trace),
        )


class AiRotationR65R64DirectCorrRankBufferStrategy:
    descriptor = DESCRIPTOR
    config_model = DirectCorrelationRankBufferConfig
    artifact_roles = ("factor_scores", "correlations", "exclusions", "decisions")

    def describe_decision_pipeline(self, config: BaseModel):
        return {
            "universe": "PIT eligible ETF",
            "dedup_method": "Greedy pairwise correlation constraint",
            "selection_rule": "R64 ranking with previous-held rank<=4 priority",
            "top_n": config.top_n,
            "exit_rank": config.exit_rank,
            "weighting_rule": "Equal slots with vacant cash",
            "rebalance_frequency": "Weekly",
        }

    def resolve_requirements(self, config: BaseModel):
        del config
        return StrategyDataRequirements(
            required_datasets=("fund", "fact_fund_adj", "dim_fund"),
            required_fields=(
                "ts_code", "trade_date", "name", "list_date", "open", "close",
                "high", "low", "pre_close", "vol", "amount", "adj_factor",
            ),
            warmup_trade_days=264,
            frequency="weekly",
            needs_benchmark=False,
        )

    def create_session(self, initialization: StrategyInitializationContext, config: BaseModel):
        del initialization
        return AiRotationR65R64DirectCorrRankBufferSession(config)
