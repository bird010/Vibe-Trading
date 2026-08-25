"""Round 62: true inverse-volatility allocation helper."""
from __future__ import annotations
import math
from dataclasses import replace
from pydantic import BaseModel
from backtest.fund_rotation.contracts import FundRotationStrategyDescriptor, StrategyInitializationContext
from backtest.fund_rotation.strategies.ai_rotation_r59_r39_signal_r57_positive_slope.strategy import AiRotationR59R39SignalR57PositiveSlopeStrategy, AiRotationR59R39SignalR57PositiveSlopeSession
from backtest.fund_rotation.strategies.ai_rotation_r34_staged_reentry.strategy import apply_staged_reentry
from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import apply_incumbent_carry

DESCRIPTOR = FundRotationStrategyDescriptor(id="ai_rotation_r62_r59_true_invvol", name="R59 Top3 真逆波动风险权重", description="保持 R59 选择不变，仅对 filled slots 使用 60 日真 inverse-volatility。", interface_version="1.0", supported_universe=("etf",), deterministic=True)

def build_true_inverse_volatility_weights(volatility: dict[str, float], top_n: int = 3, cap: float = 0.50, selected_codes: list[str] | None = None) -> tuple[dict[str, float], float, dict[str, object]]:
    selected = list(selected_codes) if selected_codes is not None else list(volatility)
    complete = set(volatility) == set(selected)
    if not selected or not complete or any(not math.isfinite(float(volatility.get(code, math.nan))) or float(volatility[code]) <= 1e-8 for code in selected):
        equal = {code: 1.0 / top_n for code in selected}
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
    def evaluate(self, context):
        previous = dict(self._previous_weights)
        decision = super().evaluate(context)
        factor_rows = decision.diagnostics.get("factor_scores", {})
        selected = [code for code, row in factor_rows.items() if row.get("top_3")]
        if not selected:
            return decision
        closes = context.data_view.adjusted_closes(lookback=61)
        volatility = {}
        for code in selected:
            if code not in closes:
                continue
            values = closes[code].pct_change(fill_method=None).dropna().tail(60)
            if len(values) == 60:
                sigma = float(values.std(ddof=0) * math.sqrt(252))
                volatility[code] = sigma
        base, _, weighting = build_true_inverse_volatility_weights(volatility, top_n=self._config.top_n, selected_codes=selected)
        staged, _, staged_codes = apply_staged_reentry(previous, base)
        final, cash, staged_codes, incumbents = apply_incumbent_carry(previous, staged)
        diagnostics = dict(decision.diagnostics)
        diagnostics["portfolio_weighting"] = weighting
        diagnostics["portfolio_weighting"]["selected_codes"] = selected
        diagnostics["portfolio_weighting"]["pre_cap_weights"] = base
        diagnostics["portfolio_weighting"]["post_cap_weights"] = final
        patched = replace(decision, decision_id=f"{context.signal_date}-{DESCRIPTOR.id}", target_weights=final, cash_weight=cash, diagnostics=diagnostics)
        self._patch_artifacts(patched)
        self._previous_weights = dict(final)
        return patched

    def _patch_artifacts(self, decision):
        rows = decision.diagnostics.get("factor_scores", {})
        selected = set(decision.diagnostics.get("portfolio_weighting", {}).get("selected_codes", []))
        base = decision.diagnostics.get("portfolio_weighting", {}).get("pre_cap_weights", {})
        for code, row in rows.items():
            row["base_slot_weight"] = float(base.get(code, 0.0))
            row["staged"] = code in set(decision.diagnostics.get("staged_reentry_codes", []))
            row["incumbent_carry"] = code in set(decision.diagnostics.get("incumbent_carry_codes", []))
            row["final_weight"] = float(decision.target_weights.get(code, 0.0))
            row["cash_weight"] = float(decision.cash_weight)
        if self._decision_log:
            self._decision_log[-1].update({"decision_id": decision.decision_id, "target_weights": dict(decision.target_weights), "cash_weight": decision.cash_weight, "diagnostics": dict(decision.diagnostics)})
        if self._decision_trace:
            for candidate in self._decision_trace[-1].get("candidates", []):
                code = candidate.get("ts_code")
                candidate["target_weight"] = float(decision.target_weights.get(code, 0.0))
                candidate.setdefault("stages", {})["portfolio_selected"] = code in selected
class AiRotationR62R59TrueInvvolStrategy(AiRotationR59R39SignalR57PositiveSlopeStrategy):
    descriptor = DESCRIPTOR
    def describe_decision_pipeline(self, config: BaseModel):
        result = super().describe_decision_pipeline(config); result["weighting_rule"] = "true inverse volatility over 60 daily returns, cap 0.50"; return result
    def create_session(self, initialization: StrategyInitializationContext, config: BaseModel):
        del initialization; return AiRotationR62R59TrueInvvolSession(config)
