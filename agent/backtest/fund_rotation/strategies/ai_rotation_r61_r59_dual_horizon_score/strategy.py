"""Round 61: independent short/medium cross-sectional ranking."""
from __future__ import annotations
import math
from pydantic import BaseModel
from backtest.fund_rotation.contracts import FundRotationStrategyDescriptor, StrategyInitializationContext
from backtest.fund_rotation.strategies.ai_rotation_r59_r39_signal_r57_positive_slope.strategy import AiRotationR59R39SignalR57PositiveSlopeStrategy, AiRotationR59R39SignalR57PositiveSlopeSession

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
    pass

class AiRotationR61R59DualHorizonScoreStrategy(AiRotationR59R39SignalR57PositiveSlopeStrategy):
    descriptor = DESCRIPTOR
    def describe_decision_pipeline(self, config: BaseModel):
        result = super().describe_decision_pipeline(config)
        result["selection_rule"] = "R59 positive slope gate, then 0.5*z(R57) + 0.5*z(adjusted_return_126d)"
        return result
    def create_session(self, initialization: StrategyInitializationContext, config: BaseModel):
        del initialization
        return AiRotationR61R59DualHorizonScoreSession(config)
