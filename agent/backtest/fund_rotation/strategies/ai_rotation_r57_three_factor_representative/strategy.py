"""Daily R57 strategy: article factors on locked correlation representatives."""

from __future__ import annotations

import math

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
from backtest.fund_rotation.strategies.correlation_all_members.signals import (
    signal_date_eligible,
)
from backtest.fund_rotation.strategies.correlation_representative.gates import GateStatus
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    CorrelationRepresentativeSession,
)
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    _SIGNAL_INFORMATION_CUTOFF,
)
from backtest.fund_rotation.strategies.ai_rotation_r57_three_factor_representative.config import (
    ArticleThreeFactorRepresentativeConfig,
)
from backtest.fund_rotation.strategies.ai_rotation_r57_three_factor_representative.factors import (
    adjust_ohlc,
    apply_rebalance_threshold,
    compute_bias_momentum,
    compute_efficiency_momentum,
    compute_slope_momentum,
    score_complete_candidates,
)
from backtest.fund_rotation.universe import check_historical_eligibility


DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r57_three_factor_representative",
    name="文章三因子代表基金轮动",
    description="在冻结相关性聚类代表基金集合上按日计算乖离、斜率和效率动量，使用严格1.5倍阈值选择Top-1。",
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)


def _json_number(value: object) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


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


class AiRotationR57ThreeFactorRepresentativeSession(CorrelationRepresentativeSession):
    def __init__(self, config: ArticleThreeFactorRepresentativeConfig) -> None:
        super().__init__(config)
        self._last_processed_iso_week: tuple[int, int] | None = None
        self._completed_iso_weeks = 0
        self._r57_last_recluster_week = -config.recluster_interval_weeks
        self._week_end_dates: set[str] = set()
        self._previous_target: str | None = None
        self._effective_weights: dict[str, float] = {}
        self._factor_scores: list[dict[str, object]] = []

    def scheduled_dates(self, calendar: tuple[str, ...], simulation_start_date: str, evaluation_end_date: str) -> tuple[str, ...]:
        self._week_end_dates = set()
        for index, date in enumerate(calendar):
            current = pd.Timestamp(date).isocalendar()
            next_week = None
            if index + 1 < len(calendar):
                next_stamp = pd.Timestamp(calendar[index + 1]).isocalendar()
                next_week = (int(next_stamp.year), int(next_stamp.week))
            current_week = (int(current.year), int(current.week))
            if next_week != current_week:
                self._week_end_dates.add(date)
        return tuple(
            date
            for date in calendar
            if simulation_start_date <= date <= evaluation_end_date
        )

    def _record_completed_iso_week(self, signal_date: str) -> bool:
        if signal_date not in self._week_end_dates:
            return False
        stamp = pd.Timestamp(signal_date)
        key = (int(stamp.isocalendar().year), int(stamp.isocalendar().week))
        if key != self._last_processed_iso_week:
            self._last_processed_iso_week = key
            self._completed_iso_weeks += 1
            return True
        return False

    def _factor_rows(self, view, signal_date: str) -> dict[str, dict[str, object]]:
        cfg = self._config
        bars = view.daily_bars(["open", "high", "low", "close", "vol", "amount"], lookback=49)
        adjustments = view.fund_adjustments(lookback=49)
        signal_key = pd.Timestamp(signal_date).strftime("%Y%m%d")
        rows: dict[str, dict[str, object]] = {}
        for cluster_id, code in sorted((cid, code) for cid, code in self._representatives.items() if code):
            code = str(code)
            code_bars = bars[bars["ts_code"].astype(str).eq(code)].sort_values("trade_date")
            code_adj = adjustments[adjustments["ts_code"].astype(str).eq(code)].sort_values("trade_date")
            raw: dict[str, object] = {
                "ts_code": code,
                "cluster_id": cluster_id,
                "is_representative": True,
                "observations": int(len(code_bars)),
                "bias_observations": 0,
                "slope_observations": 0,
                "efficiency_observations": 0,
                "bias_required_observations": cfg.bias_ma_days + cfg.bias_regression_days - 1,
                "slope_required_observations": cfg.slope_days,
                "efficiency_required_observations": cfg.efficiency_days,
                "bias_status": "NOT_EVALUATED",
                "slope_status": "NOT_EVALUATED",
                "efficiency_status": "NOT_EVALUATED",
            }
            try:
                adjusted = adjust_ohlc(code_bars, code_adj, signal_key)
                bias_required = int(raw["bias_required_observations"])
                slope_required = int(raw["slope_required_observations"])
                efficiency_required = int(raw["efficiency_required_observations"])
                bias_count, bias_status = _close_factor_status(
                    adjusted["close"], bias_required
                )
                slope_count, slope_status = _close_factor_status(
                    adjusted["close"].tail(slope_required), slope_required
                )
                efficiency_count, efficiency_status = _ohlc_factor_status(
                    adjusted[["open", "high", "low", "close"]].tail(efficiency_required),
                    efficiency_required,
                )
                raw.update({
                    "bias": _json_number(compute_bias_momentum(adjusted["close"], cfg.bias_ma_days, cfg.bias_regression_days)),
                    "slope": _json_number(compute_slope_momentum(adjusted["close"], cfg.slope_days)),
                    "efficiency": _json_number(compute_efficiency_momentum(adjusted, cfg.efficiency_days)),
                    "adjusted_observations": int(len(adjusted)),
                    "bias_observations": bias_count,
                    "slope_observations": slope_count,
                    "efficiency_observations": efficiency_count,
                    "bias_status": bias_status,
                    "slope_status": slope_status,
                    "efficiency_status": efficiency_status,
                })
                for factor_name in ("bias", "slope", "efficiency"):
                    if raw[f"{factor_name}_status"] == "VALID" and raw[factor_name] is None:
                        raw[f"{factor_name}_status"] = "INVALID_RESULT"
            except (KeyError, TypeError, ValueError):
                raw.update({
                    "bias": None,
                    "slope": None,
                    "efficiency": None,
                    "adjusted_observations": 0,
                    "status_code": "INVALID_OHLC_OR_ADJUSTMENT",
                    "bias_status": "ADJUSTMENT_DATA_INVALID",
                    "slope_status": "ADJUSTMENT_DATA_INVALID",
                    "efficiency_status": "ADJUSTMENT_DATA_INVALID",
                })
            rows[code] = raw
        return rows

    def _patch_effective_trace(
        self,
        effective_weights: dict[str, float],
        factor_rows: dict[str, dict[str, object]],
    ) -> None:
        if not self._decision_trace:
            return
        selected_codes = set(effective_weights)
        for candidate in self._decision_trace[-1].get("candidates", []):
            code = candidate.get("ts_code")
            stages = candidate.get("stages")
            if not isinstance(stages, dict):
                continue
            stages["portfolio_selected"] = code in selected_codes
            candidate["target_weight"] = _json_number(
                effective_weights.get(code, 0.0)
            )
            row = factor_rows.get(str(code))
            if row is None:
                continue
            eligible = bool(row.get("complete_candidate", False))
            score_value = _json_number(row.get("composite_score"))
            stages["ranking_eligible"] = eligible
            stages["rank"] = row.get("rank") if eligible else None
            candidate["primary_metric"] = (
                {
                    "id": "article_three_factor",
                    "label": "Article Three-Factor Momentum",
                    "value": score_value,
                }
                if eligible and score_value is not None
                else None
            )
            candidate["score"] = (
                {
                    "id": "article_three_factor",
                    "label": "Article Three-Factor Momentum",
                    "display_label": "文章三因子综合评分",
                    "model_label": "Article Three-Factor Momentum",
                    "value": score_value,
                    "eligible": eligible,
                    "direction": "HIGHER_BETTER",
                    "frequency": "D",
                    "scope": "representative",
                    "subject_id": str(code),
                    "model_id": "article_three_factor",
                    "model_version": "1.0",
                    "components": {
                        name: _json_number(row.get(f"{name}_zscore"))
                        for name in ("bias", "slope", "efficiency")
                    },
                }
                if score_value is not None
                else None
            )

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        cfg: ArticleThreeFactorRepresentativeConfig = self._config  # type: ignore[assignment]
        signal_date, view = context.signal_date, context.data_view
        completed_week = self._record_completed_iso_week(signal_date)
        dim_pool = self._pool_at_signal(view)
        window = view.returns("weekly", cfg.correlation_lookback_weeks)
        historically_eligible, historical_excluded = check_historical_eligibility(dim_pool, signal_date)
        kept, market_excluded = signal_date_eligible(view, historically_eligible, signal_date)
        self._exclusions.extend(market_excluded)
        eligible_set = set(kept)
        reclustering = (
            not self._clusters
            or (
                completed_week
                and self._completed_iso_weeks - self._r57_last_recluster_week
                >= cfg.recluster_interval_weeks
            )
        )
        if reclustering:
            self._exclusions.extend(historical_excluded)
            self._week_index = self._completed_iso_weeks
            invalid = self._recluster(view, window, kept, eligible_set, signal_date)
            self._r57_last_recluster_week = self._completed_iso_weeks
            if invalid is not None:
                self._log_decision(invalid)
                return invalid
        else:
            self._maintain_locks(view, window, eligible_set, signal_date)

        raw_rows = self._factor_rows(view, signal_date)
        raw_scores = {code: {name: row.get(name) for name in ("bias", "slope", "efficiency")} for code, row in raw_rows.items()}
        composite, score_details = score_complete_candidates(raw_scores, {"bias": cfg.bias_weight, "slope": cfg.slope_weight, "efficiency": cfg.efficiency_weight}, cfg.minimum_complete_candidates)
        complete = set(score_details["complete_candidates"])
        current_reps = set(raw_rows)
        previous_valid = self._previous_target in current_reps and self._previous_target in complete and self._previous_target in eligible_set
        diagnostics: dict[str, object] = {
            "factor_scores": raw_rows,
            "score_details": score_details,
            "previous_target": self._previous_target,
            "signal_information_cutoff": _SIGNAL_INFORMATION_CUTOFF,
            "reclustered": reclustering,
        }
        daily_top1 = next(iter(composite), None)
        forced_switch_reason: str | None = None
        if len(complete) < cfg.minimum_complete_candidates:
            if previous_valid:
                action, target, reason = DecisionKind.HOLD_TARGETS, self._previous_target, "INSUFFICIENT_COMPLETE_CANDIDATES"
                diagnostics.update({"threshold_passed": False, "negative_threshold_case": False})
            else:
                action, target, reason = DecisionKind.SET_TARGETS, None, "INSUFFICIENT_COMPLETE_CANDIDATES"
                diagnostics.update({"threshold_passed": False, "negative_threshold_case": False})
                if self._previous_target is not None:
                    forced_switch_reason = (
                        "PREVIOUS_TARGET_NOT_CURRENT_COMPLETE_CANDIDATE"
                    )
        else:
            target, action_name, threshold_details = apply_rebalance_threshold(composite, self._previous_target, cfg.rebalance_threshold)
            action = DecisionKind(action_name)
            forced = self._previous_target is not None and not previous_valid and target is not None
            if forced:
                if self._previous_target not in current_reps:
                    forced_switch_reason = "PREVIOUS_TARGET_NOT_CURRENT_REPRESENTATIVE"
                elif self._previous_target not in complete:
                    forced_switch_reason = "PREVIOUS_TARGET_INCOMPLETE_FACTORS"
                else:
                    forced_switch_reason = "PREVIOUS_TARGET_NOT_PIT_ELIGIBLE"
            reason = "FORCED_REP_SWITCH" if forced else ("ARTICLE_TOP1_ENTRY" if self._previous_target is None else ("ARTICLE_THRESHOLD_SWITCH" if action is DecisionKind.SET_TARGETS else "ARTICLE_TOP1_HOLD"))
            diagnostics.update(threshold_details)
        targets = {target: cfg.target_weight} if action is DecisionKind.SET_TARGETS and target else {}
        cash = 0.0 if targets else (1.0 if action is DecisionKind.SET_TARGETS else 0.0)
        effective_target = target
        if action is DecisionKind.HOLD_TARGETS:
            effective_weights = dict(self._effective_weights)
            if not effective_weights and effective_target is not None:
                effective_weights = {effective_target: cfg.target_weight}
        else:
            effective_weights = dict(targets)
        effective_cash = max(0.0, 1.0 - sum(effective_weights.values()))
        diagnostics.update(
            {
                "daily_top1": daily_top1,
                "effective_target": effective_target,
                "decision_target_weights": dict(targets),
                "decision_cash_weight": cash,
                "effective_weights": dict(effective_weights),
                "effective_cash_weight": effective_cash,
                "forced_switch_reason": forced_switch_reason,
            }
        )
        standardization = score_details.get("standardization", {})
        for code, row in raw_rows.items():
            row["complete_candidate"] = code in complete
            row["composite_score"] = _json_number(composite.get(code))
            row["rank"] = (list(composite).index(code) + 1) if code in composite else None
            for factor_name in ("bias", "slope", "efficiency"):
                factor_details = standardization.get(factor_name, {})
                z_scores = factor_details.get("z_scores", {})
                row[f"{factor_name}_mean"] = _json_number(factor_details.get("mean"))
                row[f"{factor_name}_std"] = _json_number(factor_details.get("std"))
                row[f"{factor_name}_zscore"] = _json_number(z_scores.get(code))
            row["threshold"] = _json_number(diagnostics.get("threshold", cfg.rebalance_threshold))
            row["held_score"] = _json_number(diagnostics.get("held_score"))
            row["challenger_score"] = _json_number(diagnostics.get("challenger_score"))
            row["threshold_right_side"] = _json_number(diagnostics.get("threshold_right_side"))
            row["threshold_passed"] = bool(diagnostics.get("threshold_passed", False))
            row["negative_threshold_case"] = bool(diagnostics.get("negative_threshold_case", False))
            row["top1"] = daily_top1
            row["effective_target"] = effective_target
            row["previous_target"] = self._previous_target
            row["action"] = action.value
            row["forced_switch_reason"] = forced_switch_reason
            row["target_weight"] = _json_number(effective_weights.get(code, 0.0))
            row["decision_target_weight"] = _json_number(targets.get(code, 0.0))
            row["cash_weight"] = effective_cash
            row["decision_cash_weight"] = cash
        decision = TargetWeightDecision(
            decision_id=f"{signal_date}-{DESCRIPTOR.id}", signal_date=signal_date, action=action,
            target_weights=targets, cash_weight=cash, reason_code=reason,
            quality_status=QualityStatus.VALID if self._last_gate_overall is GateStatus.PASS else QualityStatus.DEGRADED,
            diagnostics=diagnostics,
        )
        self._previous_target = effective_target if effective_target and (action is DecisionKind.SET_TARGETS or previous_valid) else None
        self._effective_weights = dict(effective_weights)
        self._factor_scores.append({"signal_date": signal_date, **diagnostics})
        self._log_decision(decision)
        self._previous_weights = dict(effective_weights)
        self._patch_effective_trace(effective_weights, raw_rows)
        return decision

    def finalize(self) -> StrategyDiagnostics:
        base = super().finalize()
        return StrategyDiagnostics(
            artifacts=base.artifacts + (StrategyArtifact(role="factor_scores", media_type="application/json", payload=self._factor_scores),),
            decision_trace=base.decision_trace,
        )


class AiRotationR57ThreeFactorRepresentativeStrategy:
    descriptor = DESCRIPTOR
    config_model = ArticleThreeFactorRepresentativeConfig
    artifact_roles: tuple[str, ...] = ("cluster_history", "gates", "representatives", "exclusions", "factor_scores", "decisions")

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        cfg: ArticleThreeFactorRepresentativeConfig = config  # type: ignore[assignment]
        return {"universe": "ETF", "dedup_method": "Correlation Clustering", "representative_method": "Locked liquidity representative", "score_model": {"id": "article_three_factor", "label": "Article Three-Factor Momentum", "version": "1.0", "direction": "HIGHER_BETTER"}, "selection_rule": "Top 1 with strict 1.5x threshold", "top_n": cfg.top_n, "weighting_rule": "100% Top-1", "rebalance_frequency": "Daily"}

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        cfg: ArticleThreeFactorRepresentativeConfig = config  # type: ignore[assignment]
        return StrategyDataRequirements(required_datasets=("fund", "fact_fund_adj", "dim_fund"), required_fields=("ts_code", "trade_date", "name", "list_date", "open", "close", "high", "low", "vol", "amount", "adj_factor"), warmup_trade_days=(cfg.correlation_lookback_weeks + 1) * 5 - 1, frequency="D", needs_benchmark=True)

    def create_session(self, initialization: StrategyInitializationContext, config: BaseModel) -> AiRotationR57ThreeFactorRepresentativeSession:
        del initialization
        return AiRotationR57ThreeFactorRepresentativeSession(config)  # type: ignore[arg-type]
