"""Round 63: deterministic Top3 entry / Top4 exit hysteresis."""
from __future__ import annotations
from dataclasses import replace
from pydantic import BaseModel
from backtest.fund_rotation.contracts import FundRotationStrategyDescriptor, StrategyInitializationContext
from backtest.fund_rotation.strategies.ai_rotation_r59_r39_signal_r57_positive_slope.strategy import AiRotationR59R39SignalR57PositiveSlopeStrategy, AiRotationR59R39SignalR57PositiveSlopeSession
from backtest.fund_rotation.strategies.correlation_representative.strategy import build_slot_weights
from backtest.fund_rotation.strategies.ai_rotation_r34_staged_reentry.strategy import _append_reason, apply_staged_reentry
from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import apply_incumbent_carry

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
    def evaluate(self, context):
        previous_weights = dict(self._previous_weights)
        decision = super().evaluate(context)
        diagnostics = dict(decision.diagnostics)
        rows = diagnostics.get("factor_scores", {})
        ranked_codes = diagnostics.get("ranked_codes", [])
        current_rank = {code: index + 1 for index, code in enumerate(ranked_codes)}
        if diagnostics.get("reclustered"):
            self._previous_selected_clusters.clear()
        valid_clusters = {int(row["cluster_id"]) for code, row in rows.items() if row.get("complete_candidate") and row.get("raw_slope_25d") is not None and float(row["raw_slope_25d"]) > 0}
        ranked_clusters = [int(rows[code]["cluster_id"]) for code in ranked_codes if code in rows]
        selected_clusters, buffer_diag = select_rank_buffer_clusters(ranked_clusters, self._previous_selected_clusters, valid_clusters, 3, 4)
        base, _, _, _ = build_slot_weights(selected_clusters, self._representatives, self._config.top_n)
        staged, _, staged_codes = apply_staged_reentry(previous_weights, base)
        final, cash, staged_codes, incumbents = apply_incumbent_carry(previous_weights, staged)
        self._previous_selected_clusters = set(selected_clusters)
        buffer_diag["epoch_reset"] = bool(diagnostics.get("reclustered"))
        buffer_diag["selected_clusters"] = selected_clusters
        diagnostics["staged_reentry_codes"] = sorted(staged_codes)
        diagnostics["incumbent_carry_codes"] = sorted(incumbents)
        base_reasons = [part for part in decision.reason_code.split("|") if part not in {"STAGED_REENTRY", "INCUMBENT_CARRY"}]
        reason = "|".join(part for part in base_reasons if part)
        reason = _append_reason(reason, "STAGED_REENTRY" if staged_codes else "")
        reason = _append_reason(reason, "INCUMBENT_CARRY" if incumbents else "")
        diagnostics["rank_buffer"] = buffer_diag
        patched = replace(decision, decision_id=f"{context.signal_date}-{DESCRIPTOR.id}", target_weights=final, cash_weight=cash, reason_code=reason, diagnostics=diagnostics)
        self._patch_artifacts(patched, base, staged_codes, incumbents)
        self._previous_weights = dict(final)
        return patched

    def _patch_artifacts(self, decision, base, staged_codes, incumbents):
        rows = decision.diagnostics.get("factor_scores", {})
        selected = set(decision.diagnostics.get("rank_buffer", {}).get("selected_clusters", []))
        for row in rows.values():
            code = row.get("ts_code")
            row["top_3"] = int(row.get("cluster_id", -1)) in selected
            row["base_slot_weight"] = float(base.get(code, 0.0))
            row["staged"] = code in staged_codes
            row["incumbent_carry"] = code in incumbents
            row["final_weight"] = float(decision.target_weights.get(code, 0.0))
            row["cash_weight"] = float(decision.cash_weight)
        if self._decision_log:
            self._decision_log[-1].update({"decision_id": decision.decision_id, "reason_code": decision.reason_code, "target_weights": dict(decision.target_weights), "cash_weight": decision.cash_weight, "diagnostics": dict(decision.diagnostics)})
        if self._decision_trace:
            for candidate in self._decision_trace[-1].get("candidates", []):
                code = candidate.get("ts_code")
                candidate["target_weight"] = float(decision.target_weights.get(code, 0.0))
                candidate.setdefault("stages", {})["portfolio_selected"] = code in decision.target_weights
class AiRotationR63R59RankBufferStrategy(AiRotationR59R39SignalR57PositiveSlopeStrategy):
    descriptor = DESCRIPTOR
    def describe_decision_pipeline(self, config: BaseModel):
        result = super().describe_decision_pipeline(config); result["selection_rule"] = "R59 ranking with entry Top3 and exit Top4 rank buffer"; return result
    def create_session(self, initialization: StrategyInitializationContext, config: BaseModel):
        del initialization; return AiRotationR63R59RankBufferSession(config)
