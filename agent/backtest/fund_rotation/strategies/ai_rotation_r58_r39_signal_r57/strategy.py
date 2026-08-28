"""Round 58: weekly R39 lifecycle with the R57 three-factor signal only."""

from __future__ import annotations

import math
import re
from dataclasses import replace

import numpy as np
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
from backtest.fund_rotation.scoring.contracts import StrategyScore
from backtest.fund_rotation.strategies.ai_rotation_r34_staged_reentry.strategy import (
    _append_reason,
    apply_staged_reentry,
)
from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import (
    apply_incumbent_carry,
)
from backtest.fund_rotation.strategies.ai_rotation_r57_three_factor_representative.factors import (
    adjust_ohlc,
    compute_bias_momentum,
    compute_efficiency_momentum,
    compute_slope_momentum,
    score_complete_candidates,
)
from backtest.fund_rotation.strategies.correlation_all_members.signals import (
    signal_date_eligible,
)
from backtest.fund_rotation.strategies.correlation_representative.gates import (
    GateStatus,
)
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    CorrelationRepresentativeSession,
    CorrelationRepresentativeStrategy,
    _SIGNAL_INFORMATION_CUTOFF,
    build_slot_weights,
)
from backtest.fund_rotation.universe import check_historical_eligibility


DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r58_r39_signal_r57",
    name="周频R39生命周期三因子代表ETF轮动",
    description=(
        "完全沿用R39的周末调度、相关性聚类、锁定代表、三槽位、现金、"
        "半仓再入场与持续目标承接，仅将代表排序替换为R57三因子综合分。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)

_BIAS_MA_DAYS = 25
_BIAS_REGRESSION_DAYS = 25
_SLOPE_DAYS = 25
_EFFICIENCY_DAYS = 25
_FACTOR_LOOKBACK_DAYS = 49
_MINIMUM_COMPLETE_CANDIDATES = 2
_FACTOR_WEIGHTS = {"bias": 0.3, "slope": 0.3, "efficiency": 0.4}
_R58_TOP_N = 3


def _validate_r58_config(config) -> None:
    if getattr(config, "top_n", None) != _R58_TOP_N:
        raise ValueError(
            "ai_rotation_r58_r39_signal_r57 requires top_n=3"
        )


def _json_number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _close_factor_status(values: pd.Series, required: int) -> tuple[int, str]:
    numeric = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    finite = np.isfinite(numeric)
    valid = finite & (numeric > 0.0)
    valid_count = int(valid.sum())
    if len(numeric) < required:
        return valid_count, "INSUFFICIENT_OBSERVATIONS"
    if not finite.all():
        return valid_count, "NONFINITE_PRICE"
    if not (numeric > 0.0).all():
        return valid_count, "NON_POSITIVE_PRICE"
    return valid_count, "VALID"


def _ohlc_factor_status(values: pd.DataFrame, required: int) -> tuple[int, str]:
    numeric = values.apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    finite_rows = np.isfinite(numeric).all(axis=1)
    valid_rows = finite_rows & (numeric > 0.0).all(axis=1)
    valid_count = int(valid_rows.sum())
    if len(numeric) < required:
        return valid_count, "INSUFFICIENT_OBSERVATIONS"
    if not finite_rows.all():
        return valid_count, "NONFINITE_OHLC"
    if not (numeric > 0.0).all():
        return valid_count, "NON_POSITIVE_OHLC"
    return valid_count, "VALID"


def _normalized_timestamp(value: object) -> pd.Timestamp:
    raw = str(value).strip()
    if re.fullmatch(r"\d{8}", raw):
        return pd.Timestamp(raw)
    try:
        return pd.Timestamp(value)
    except (TypeError, ValueError):
        return pd.NaT


def _causal_frame(frame: pd.DataFrame, signal_date: str) -> pd.DataFrame:
    """Normalize and cutoff rows before any diagnostic counts are recorded."""
    if not {"ts_code", "trade_date"} <= set(frame.columns):
        return frame.copy()
    result = frame.copy()
    result["ts_code"] = result["ts_code"].astype(str)
    timestamps = result["trade_date"].map(_normalized_timestamp)
    signal_timestamp = _normalized_timestamp(signal_date)
    keep = timestamps.notna() & timestamps.le(signal_timestamp)
    result = result.loc[keep].copy()
    result["trade_date"] = timestamps.loc[result.index].dt.strftime("%Y%m%d")
    return result.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)


class AiRotationR58R39SignalR57Session(CorrelationRepresentativeSession):
    """R39's weekly state machine with an isolated R57 factor ranking."""

    def __init__(self, config) -> None:
        _validate_r58_config(config)
        super().__init__(config)
        self._factor_scores: list[dict[str, object]] = []

    def _factor_rows(self, view, signal_date: str) -> dict[str, dict[str, object]]:
        bars = _causal_frame(
            view.daily_bars(
            ["open", "high", "low", "close", "vol", "amount"],
            lookback=_FACTOR_LOOKBACK_DAYS,
            ),
            signal_date,
        )
        adjustments = _causal_frame(
            view.fund_adjustments(lookback=_FACTOR_LOOKBACK_DAYS),
            signal_date,
        )
        rows: dict[str, dict[str, object]] = {}
        for cluster_id, representative in sorted(
            (cluster_id, code)
            for cluster_id, code in self._representatives.items()
            if code
        ):
            code = str(representative)
            code_bars = bars[bars["ts_code"].eq(code)].sort_values(
                "trade_date"
            ).tail(_FACTOR_LOOKBACK_DAYS)
            code_adjustments = adjustments[
                adjustments["ts_code"].eq(code)
            ].sort_values("trade_date")
            row: dict[str, object] = {
                "ts_code": code,
                "cluster_id": int(cluster_id),
                "is_representative": True,
                "observations": int(len(code_bars)),
                "adjusted_observations": 0,
                "bias_observations": 0,
                "slope_observations": 0,
                "efficiency_observations": 0,
                "bias_required_observations": _BIAS_MA_DAYS + _BIAS_REGRESSION_DAYS - 1,
                "slope_required_observations": _SLOPE_DAYS,
                "efficiency_required_observations": _EFFICIENCY_DAYS,
                "bias": None,
                "slope": None,
                "efficiency": None,
                "bias_status": "NOT_EVALUATED",
                "slope_status": "NOT_EVALUATED",
                "efficiency_status": "NOT_EVALUATED",
            }
            try:
                adjusted = adjust_ohlc(
                    code_bars,
                    code_adjustments,
                    pd.Timestamp(signal_date).strftime("%Y%m%d"),
                )
                adjusted = (
                    adjusted.sort_values("trade_date")
                    .tail(_FACTOR_LOOKBACK_DAYS)
                    .reset_index(drop=True)
                )
                row["adjusted_observations"] = int(len(adjusted))
                full_ohlc_count, full_ohlc_status = _ohlc_factor_status(
                    adjusted[["open", "high", "low", "close"]],
                    _FACTOR_LOOKBACK_DAYS,
                )
                if full_ohlc_status != "VALID":
                    row.update(
                        {
                            "bias_observations": full_ohlc_count,
                            "slope_observations": full_ohlc_count,
                            "efficiency_observations": full_ohlc_count,
                            "bias_status": full_ohlc_status,
                            "slope_status": full_ohlc_status,
                            "efficiency_status": full_ohlc_status,
                            "status_code": "INVALID_FULL_OHLC_WINDOW",
                        }
                    )
                    rows[code] = row
                    continue
                bias_count, bias_status = _close_factor_status(
                    adjusted["close"], _BIAS_MA_DAYS + _BIAS_REGRESSION_DAYS - 1
                )
                slope_count, slope_status = _close_factor_status(
                    adjusted["close"].tail(_SLOPE_DAYS), _SLOPE_DAYS
                )
                efficiency_count, efficiency_status = _ohlc_factor_status(
                    adjusted[["open", "high", "low", "close"]].tail(
                        _EFFICIENCY_DAYS
                    ),
                    _EFFICIENCY_DAYS,
                )
                row.update(
                    {
                        "bias": _json_number(
                            compute_bias_momentum(
                                adjusted["close"],
                                _BIAS_MA_DAYS,
                                _BIAS_REGRESSION_DAYS,
                            )
                        ),
                        "slope": _json_number(
                            compute_slope_momentum(adjusted["close"], _SLOPE_DAYS)
                        ),
                        "efficiency": _json_number(
                            compute_efficiency_momentum(
                                adjusted, _EFFICIENCY_DAYS
                            )
                        ),
                        "bias_observations": bias_count,
                        "slope_observations": slope_count,
                        "efficiency_observations": efficiency_count,
                        "bias_status": bias_status,
                        "slope_status": slope_status,
                        "efficiency_status": efficiency_status,
                    }
                )
                for factor_name in ("bias", "slope", "efficiency"):
                    if (
                        row[f"{factor_name}_status"] == "VALID"
                        and row[factor_name] is None
                    ):
                        row[f"{factor_name}_status"] = "INVALID_RESULT"
            except (KeyError, TypeError, ValueError):
                row.update(
                    {
                        "status_code": "INVALID_OHLC_OR_ADJUSTMENT",
                        "bias_status": "ADJUSTMENT_DATA_INVALID",
                        "slope_status": "ADJUSTMENT_DATA_INVALID",
                        "efficiency_status": "ADJUSTMENT_DATA_INVALID",
                    }
                )
            rows[code] = row
        return rows

    @staticmethod
    def _scores_by_cluster(
        factor_rows: dict[str, dict[str, object]],
        composite: dict[str, float],
    ) -> dict[int, StrategyScore]:
        scores: dict[int, StrategyScore] = {}
        for code, row in factor_rows.items():
            value = _json_number(composite.get(code))
            cluster_id = int(row["cluster_id"])
            scores[cluster_id] = StrategyScore(
                value=value,
                eligible=value is not None,
                subject_id=code,
                display_label="R57三因子综合评分",
                model_label="R57 Three-Factor Momentum",
                frequency="WEEKLY",
                scope="CLUSTER",
                model_id="r57_three_factor",
                model_version="1",
                components={
                    name: _json_number(row.get(f"{name}_zscore"))
                    for name in ("bias", "slope", "efficiency")
                },
            )
        return scores

    def _enrich_factor_rows(
        self,
        factor_rows: dict[str, dict[str, object]],
        score_details: dict[str, object],
        composite: dict[str, float],
        selected_codes: set[str],
        base_weights: dict[str, float],
        final_weights: dict[str, float],
        final_cash: float,
        staged: set[str],
        incumbents: set[str],
    ) -> None:
        complete = set(score_details.get("complete_candidates", []))
        standardization = score_details.get("standardization", {})
        rank_by_code = {
            code: rank for rank, code in enumerate(composite, start=1)
        }
        for code, row in factor_rows.items():
            row["complete_candidate"] = code in complete
            row["composite_score"] = _json_number(composite.get(code))
            row["rank"] = rank_by_code.get(code)
            for factor_name in ("bias", "slope", "efficiency"):
                detail = standardization.get(factor_name, {})
                z_scores = detail.get("z_scores", {})
                row[f"{factor_name}_mean"] = _json_number(detail.get("mean"))
                row[f"{factor_name}_std"] = _json_number(detail.get("std"))
                row[f"{factor_name}_zscore"] = _json_number(z_scores.get(code))
            row["top_3"] = code in selected_codes
            row["base_slot_weight"] = _json_number(base_weights.get(code, 0.0))
            row["staged"] = code in staged
            row["incumbent_carry"] = code in incumbents
            row["final_weight"] = _json_number(final_weights.get(code, 0.0))
            row["cash_weight"] = _json_number(final_cash)

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
            _FACTOR_WEIGHTS,
            _MINIMUM_COMPLETE_CANDIDATES,
        )
        complete = set(score_details["complete_candidates"])
        cluster_by_code = {
            code: int(row["cluster_id"])
            for code, row in factor_rows.items()
            if code in complete
        }
        ranked_codes = list(composite)
        selected_codes = set(ranked_codes[:_R58_TOP_N])
        selected_clusters = [
            cluster_by_code[code]
            for code in ranked_codes
            if code in selected_codes
        ]
        base_weights, filled, vacant, _ = build_slot_weights(
            selected_clusters,
            self._representatives,
            _R58_TOP_N,
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
            if len(complete) < _MINIMUM_COMPLETE_CANDIDATES
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
            "staged_reentry_rule": (
                "new_representative_target_weight_halved_once"
            ),
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

    def finalize(self) -> StrategyDiagnostics:
        base = super().finalize()
        return StrategyDiagnostics(
            artifacts=base.artifacts
            + (
                StrategyArtifact(
                    role="factor_scores",
                    media_type="application/json",
                    payload=self._factor_scores,
                ),
            ),
            decision_trace=base.decision_trace,
        )


class AiRotationR58R39SignalR57Strategy:
    """Complete round 58 strategy plug-in."""

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
        _validate_r58_config(config)
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
            "selection_rule": "Top 3 representative clusters by R57 three-factor score",
            "top_n": _R58_TOP_N,
            "weighting_rule": "Fixed 1/top_n slots with vacant cash",
            "rebalance_frequency": "Weekly",
            "staging_rule": "50% staged re-entry once for new targets",
            "carry_rule": "Incumbent carry proportional to continuous base target weight",
        }

    def resolve_requirements(
        self,
        config: BaseModel,
    ) -> StrategyDataRequirements:
        _validate_r58_config(config)
        return CorrelationRepresentativeStrategy().resolve_requirements(config)

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> AiRotationR58R39SignalR57Session:
        del initialization
        return AiRotationR58R39SignalR57Session(config)
