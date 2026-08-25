"""Round 61: independent short/medium cross-sectional ranking."""
from __future__ import annotations
import math
from dataclasses import replace
from pydantic import BaseModel
from backtest.fund_rotation.contracts import FundRotationStrategyDescriptor, StrategyInitializationContext
from backtest.fund_rotation.scoring.contracts import StrategyScore
from backtest.fund_rotation.strategies.ai_rotation_r59_r39_signal_r57_positive_slope.strategy import AiRotationR59R39SignalR57PositiveSlopeStrategy, AiRotationR59R39SignalR57PositiveSlopeSession
from backtest.fund_rotation.strategies.ai_rotation_r60_r59_medium_trend_gate.strategy import compute_adjusted_return_126d, _causal

DESCRIPTOR = FundRotationStrategyDescriptor(id="ai_rotation_r61_r59_dual_horizon_score", name="R59 短中期双尺度趋势评分", description="R59 正斜率门禁下，以 50/50 短中期标准化分数排名。", interface_version="1.0", supported_universe=("etf",), deterministic=True)

def _z(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    mean = sum(values.values()) / len(values)
    std = math.sqrt(sum((value - mean) ** 2 for value in values.values()) / len(values))
    return {code: 0.0 if std <= 1e-12 else (value - mean) / std for code, value in values.items()}

def dual_horizon_scores(short_scores: dict[str, float], medium_returns: dict[str, float]) -> tuple[dict[str, float], dict[str, object]]:
    complete = sorted(set(short_scores) & set(medium_returns))
    short = {code: float(short_scores[code]) for code in complete if math.isfinite(float(short_scores[code])) and math.isfinite(float(medium_returns[code]))}
    medium = {code: float(medium_returns[code]) for code in short}
    short_z, medium_z = _z(short), _z(medium)
    scores = {code: 0.5 * short_z[code] + 0.5 * medium_z[code] for code in short}
    ranked = dict(sorted(scores.items(), key=lambda item: (-item[1], item[0])))
    return ranked, {"short_z": short_z, "medium_z": medium_z, "complete_candidates": sorted(ranked)}

class AiRotationR61R59DualHorizonScoreSession(AiRotationR59R39SignalR57PositiveSlopeSession):
    @staticmethod
    def _scores_by_cluster(factor_rows, composite):
        scores = {}
        for code, row in factor_rows.items():
            value = composite.get(code)
            if value is None:
                continue
            scores[int(row["cluster_id"])] = StrategyScore(value=float(value), eligible=True, subject_id=code, display_label="R61短中期双尺度评分", model_label="R61 Dual-Horizon Trend", frequency="WEEKLY", scope="CLUSTER", model_id="r61_dual_horizon_trend", model_version="1", components={"short_r57_composite_z": row.get("r57_composite_zscore"), "medium_return_126d_z": row.get("medium_return_zscore")})
        return scores

    def evaluate(self, context):
        decision = super().evaluate(context)
        diagnostics = dict(decision.diagnostics)
        diagnostics["score_model"] = {"id": "r61_dual_horizon_trend", "version": "1", "direction": "HIGHER_BETTER", "components": {"short_r57_composite_z": 0.5, "medium_return_126d_z": 0.5}}
        patched = replace(decision, decision_id=f"{context.signal_date}-{DESCRIPTOR.id}", diagnostics=diagnostics)
        if self._decision_log:
            self._decision_log[-1]["decision_id"] = patched.decision_id
            self._decision_log[-1]["diagnostics"] = diagnostics
        if self._decision_trace:
            self._decision_trace[-1]["score_model"] = diagnostics["score_model"]
        return patched

    def _factor_rows(self, view, signal_date: str):
        rows = super()._factor_rows(view, signal_date)
        bars = _causal(view.daily_bars(["open", "high", "low", "close", "vol", "amount"], lookback=127), signal_date)
        adjustments = _causal(view.fund_adjustments(lookback=127), signal_date)
        for code, row in rows.items():
            result = compute_adjusted_return_126d(bars[bars["ts_code"].eq(str(code))], adjustments[adjustments["ts_code"].eq(str(code))], signal_date)
            row.update({"medium_return_126d": result["return_126d"], "medium_return_status": result["status"], "medium_return_observations": result["observations"]})
        return rows

    @staticmethod
    def _apply_positive_slope_filter(factor_rows, composite, score_details):
        filtered, details = AiRotationR59R39SignalR57PositiveSlopeSession._apply_positive_slope_filter(factor_rows, composite, score_details)
        medium = {code: float(factor_rows[code]["medium_return_126d"]) for code in filtered if factor_rows[code].get("medium_return_126d") is not None and math.isfinite(float(factor_rows[code]["medium_return_126d"]))}
        fused, components = dual_horizon_scores(filtered, medium)
        details = dict(details)
        details.update({"dual_horizon_score": components, "score_model": {"id": "r61_dual_horizon_trend", "version": "1", "components": {"short_r57_composite_z": 0.5, "medium_return_126d_z": 0.5}}})
        details["complete_candidates"] = list(fused)
        for code, value in fused.items():
            factor_rows[code]["dual_horizon_score"] = value
            factor_rows[code]["r57_composite_score"] = filtered[code]
            factor_rows[code]["medium_return_zscore"] = components["medium_z"].get(code)
            factor_rows[code]["r57_composite_zscore"] = components["short_z"].get(code)
        return fused, details

class AiRotationR61R59DualHorizonScoreStrategy(AiRotationR59R39SignalR57PositiveSlopeStrategy):
    descriptor = DESCRIPTOR
    def describe_decision_pipeline(self, config: BaseModel):
        result = super().describe_decision_pipeline(config)
        result["selection_rule"] = "R59 positive slope gate, then 0.5*z(R57) + 0.5*z(adjusted_return_126d)"
        return result
    def create_session(self, initialization: StrategyInitializationContext, config: BaseModel):
        del initialization
        return AiRotationR61R59DualHorizonScoreSession(config)
