"""Round 64: score-first greedy pairwise correlation diversification."""
from __future__ import annotations
import math
from pydantic import BaseModel
from backtest.fund_rotation.contracts import FundRotationStrategyDescriptor, StrategyDataRequirements, StrategyInitializationContext
from backtest.fund_rotation.strategies.ai_rotation_r59_r39_signal_r57_positive_slope.strategy import AiRotationR59R39SignalR57PositiveSlopeStrategy, AiRotationR59R39SignalR57PositiveSlopeSession
from .config import DirectCorrelationDiversificationConfig

MAX_PAIRWISE_CORRELATION = 0.80
DESCRIPTOR = FundRotationStrategyDescriptor(id="ai_rotation_r64_direct_corr_diversification", name="R59 信号直接相关性约束 ETF 轮动", description="去掉聚类，以严格 pairwise correlation constraint 做 Top3 diversification。", interface_version="1.0", supported_universe=("etf",), deterministic=True)

def select_direct_correlation_diversified(ranked_codes: list[str], correlations: dict[str, float], observations: dict[str, int], top_n: int = 3, threshold: float = MAX_PAIRWISE_CORRELATION, min_pairwise_weeks: int = 20) -> tuple[list[str], dict[str, object]]:
    selected: list[str] = []; rejected: dict[str, str] = {}; used: dict[str, float] = {}
    for code in ranked_codes:
        if len(selected) >= top_n: break
        checks = []
        for held in selected:
            key = "|".join(sorted((str(code), str(held))))
            corr = correlations.get(key); count = observations.get(key, 0)
            if corr is None or count < min_pairwise_weeks or not math.isfinite(float(corr)):
                rejected[code] = "PAIRWISE_CORRELATION_UNAVAILABLE"; break
            checks.append((key, float(corr)))
        else:
            if all(corr < threshold for _, corr in checks):
                selected.append(str(code)); used.update(checks)
            elif checks:
                rejected[code] = "PAIRWISE_CORRELATION_TOO_HIGH"
    return selected, {"ranked_codes": [str(code) for code in ranked_codes], "selected_codes": selected, "max_pairwise_correlation": threshold, "selection_pairwise_correlations": dict(sorted(used.items())), "correlation_rejected_candidates": dict(sorted(rejected.items()))}

class AiRotationR64DirectCorrelationSession(AiRotationR59R39SignalR57PositiveSlopeSession): pass
class AiRotationR64DirectCorrDiversificationStrategy(AiRotationR59R39SignalR57PositiveSlopeStrategy):
    descriptor = DESCRIPTOR; config_model = DirectCorrelationDiversificationConfig
    def describe_decision_pipeline(self, config: BaseModel):
        return {"universe": "PIT eligible ETF", "dedup_method": "Greedy pairwise correlation constraint", "selection_rule": "R57 score, positive slope gate, then corr < 0.80", "top_n": config.top_n, "weighting_rule": "Equal slots with vacant cash", "rebalance_frequency": "Weekly"}
    def resolve_requirements(self, config: BaseModel):
        del config
        return StrategyDataRequirements(required_datasets=("fund", "fact_fund_adj", "dim_fund"), required_fields=("ts_code", "trade_date", "name", "list_date", "open", "close", "high", "low", "pre_close", "vol", "amount", "adj_factor"), warmup_trade_days=264, frequency="weekly", needs_benchmark=False)
    def create_session(self, initialization: StrategyInitializationContext, config: BaseModel):
        del initialization; return AiRotationR64DirectCorrelationSession(config)
