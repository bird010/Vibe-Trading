"""Round 63: deterministic Top3 entry / Top4 exit hysteresis."""
from __future__ import annotations
from pydantic import BaseModel
from backtest.fund_rotation.contracts import FundRotationStrategyDescriptor, StrategyInitializationContext
from backtest.fund_rotation.strategies.ai_rotation_r59_r39_signal_r57_positive_slope.strategy import AiRotationR59R39SignalR57PositiveSlopeStrategy, AiRotationR59R39SignalR57PositiveSlopeSession

DESCRIPTOR = FundRotationStrategyDescriptor(id="ai_rotation_r63_r59_rank_buffer", name="R59 Top3 入场 Top4 退出排名缓冲", description="R59 的唯一新增机制是 Top4 rank hysteresis。", interface_version="1.0", supported_universe=("etf",), deterministic=True)

def select_rank_buffer_clusters(ranked_clusters: list[int], previous_selected: set[int], valid_clusters: set[int], top_n: int = 3, exit_rank: int = 4) -> tuple[list[int], dict[str, object]]:
    current_rank = {cluster: index + 1 for index, cluster in enumerate(ranked_clusters)}
    retained = sorted((cluster for cluster in previous_selected if cluster in valid_clusters and current_rank.get(cluster, exit_rank + 1) <= exit_rank), key=lambda cluster: (current_rank[cluster], cluster))
    fillers = [cluster for cluster in ranked_clusters if cluster not in retained]
    selected = (retained + fillers)[:top_n]
    return selected, {"entry_rank": top_n, "exit_rank": exit_rank, "previous_selected_clusters": sorted(previous_selected), "retained_clusters": retained, "current_rank_by_cluster": current_rank, "forced_exit_clusters": sorted(previous_selected - set(retained))}

class AiRotationR63R59RankBufferSession(AiRotationR59R39SignalR57PositiveSlopeSession):
    def __init__(self, config):
        super().__init__(config); self._previous_selected_clusters: set[int] = set()
class AiRotationR63R59RankBufferStrategy(AiRotationR59R39SignalR57PositiveSlopeStrategy):
    descriptor = DESCRIPTOR
    def describe_decision_pipeline(self, config: BaseModel):
        result = super().describe_decision_pipeline(config); result["selection_rule"] = "R59 ranking with entry Top3 and exit Top4 rank buffer"; return result
    def create_session(self, initialization: StrategyInitializationContext, config: BaseModel):
        del initialization; return AiRotationR63R59RankBufferSession(config)
