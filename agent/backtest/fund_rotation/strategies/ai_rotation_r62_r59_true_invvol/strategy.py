"""Round 62: true inverse-volatility allocation helper."""
from __future__ import annotations
import math
from pydantic import BaseModel
from backtest.fund_rotation.contracts import FundRotationStrategyDescriptor, StrategyInitializationContext
from backtest.fund_rotation.strategies.ai_rotation_r59_r39_signal_r57_positive_slope.strategy import AiRotationR59R39SignalR57PositiveSlopeStrategy, AiRotationR59R39SignalR57PositiveSlopeSession

DESCRIPTOR = FundRotationStrategyDescriptor(id="ai_rotation_r62_r59_true_invvol", name="R59 Top3 真逆波动风险权重", description="保持 R59 选择不变，仅对 filled slots 使用 60 日真 inverse-volatility。", interface_version="1.0", supported_universe=("etf",), deterministic=True)

def build_true_inverse_volatility_weights(volatility: dict[str, float], top_n: int = 3, cap: float = 0.50) -> tuple[dict[str, float], float, dict[str, object]]:
    if not volatility or any(not math.isfinite(float(v)) or float(v) <= 1e-8 for v in volatility.values()):
        equal = {code: 1.0 / top_n for code in volatility}
        return equal, max(0.0, 1.0 - sum(equal.values())), {"mode": "equal_slot_fallback", "fallback_reason": "invalid_volatility"}
    exposure = len(volatility) / top_n
    remaining = set(volatility)
    weights: dict[str, float] = {}
    left = exposure
    while remaining and left > 1e-12:
        inv = {code: 1.0 / float(volatility[code]) for code in remaining}
        total = sum(inv.values())
        proposed = {code: left * value / total for code, value in inv.items()}
        capped = [code for code in sorted(remaining) if proposed[code] > cap + 1e-12]
        if not capped:
            weights.update(proposed)
            break
        for code in capped:
            weights[code] = cap
            left -= cap
            remaining.remove(code)
    return weights, max(0.0, 1.0 - sum(weights.values())), {"mode": "true_inverse_volatility", "annualized_volatility": dict(sorted(volatility.items())), "base_exposure": exposure, "max_etf_weight": cap}

class AiRotationR62R59TrueInvvolSession(AiRotationR59R39SignalR57PositiveSlopeSession):
    pass
class AiRotationR62R59TrueInvvolStrategy(AiRotationR59R39SignalR57PositiveSlopeStrategy):
    descriptor = DESCRIPTOR
    def describe_decision_pipeline(self, config: BaseModel):
        result = super().describe_decision_pipeline(config); result["weighting_rule"] = "true inverse volatility over 60 daily returns, cap 0.50"; return result
    def create_session(self, initialization: StrategyInitializationContext, config: BaseModel):
        del initialization; return AiRotationR62R59TrueInvvolSession(config)
