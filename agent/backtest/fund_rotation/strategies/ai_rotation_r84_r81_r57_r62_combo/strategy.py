"""R84: R82 plus the R62 true inverse-volatility allocation layer."""

from __future__ import annotations

import math
from dataclasses import replace

from pydantic import BaseModel

from backtest.fund_rotation.contracts import (
    FundRotationStrategyDescriptor,
    StrategyDecisionContext,
    StrategyInitializationContext,
)
from backtest.fund_rotation.strategies.ai_rotation_r34_staged_reentry.strategy import (
    apply_staged_reentry,
)
from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import (
    apply_incumbent_carry,
)
from backtest.fund_rotation.strategies.ai_rotation_r62_r59_true_invvol.strategy import (
    build_true_inverse_volatility_weights,
)
from backtest.fund_rotation.strategies.ai_rotation_r82_economic_role_dynamic_rep_r57_signal.strategy import (
    EconomicRoleR57Session,
    AiRotationR82EconomicRoleDynamicRepR57SignalStrategy,
)
from backtest.fund_rotation.risk_layers import apply_defense_asset


DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r84_r81_r57_r62_combo",
    name="R81 动态代表 R57 信号 R62 逆波动",
    description=(
        "保留 R81 动态代表、R57 三因子排序及 R34/R39/R76 生命周期，"
        "仅将三个已选代表的等权槽位替换为 R62 60 日真实逆波动权重。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)


class EconomicRoleR57R62Session(EconomicRoleR57Session):
    def evaluate(self, context: StrategyDecisionContext):
        previous_weights = dict(self._previous_weights)
        decision = super().evaluate(context)
        selected = [
            code
            for code, row in decision.diagnostics.get("factor_scores", {}).items()
            if row.get("top_3")
        ]
        volatility: dict[str, float] = {}
        try:
            closes = context.data_view.adjusted_closes(lookback=61)
            for code in selected:
                if code not in closes:
                    continue
                values = closes[code].pct_change(fill_method=None).dropna().tail(60)
                if len(values) != 60:
                    continue
                sigma = float(values.std(ddof=0) * math.sqrt(252.0))
                if math.isfinite(sigma) and sigma > 1e-8:
                    volatility[code] = sigma
        except (AttributeError, KeyError, TypeError, ValueError):
            volatility = {}

        base_weights, _, weighting = build_true_inverse_volatility_weights(
            volatility,
            top_n=self._config.top_n,
            selected_codes=selected,
        )
        staged_weights, _, staged = apply_staged_reentry(
            previous_weights, base_weights
        )
        carried_weights, carried_cash, carried_codes, incumbents = apply_incumbent_carry(
            previous_weights, staged_weights
        )
        defense_code = decision.diagnostics.get("defense_asset")
        final_weights, final_cash, defense_diagnostics = apply_defense_asset(
            carried_weights,
            carried_cash,
            defense_code=defense_code if isinstance(defense_code, str) else None,
        )
        weighting = dict(weighting)
        weighting.update(
            {
                "layer": "R62_true_inverse_volatility",
                "selected_codes": selected,
                "base_target_weights": base_weights,
                "final_target_weights": final_weights,
            }
        )
        diagnostics = dict(decision.diagnostics)
        diagnostics.update(
            {
                "portfolio_weighting": weighting,
                "staged_reentry_codes": sorted(staged),
                "incumbent_carry_codes": sorted(incumbents),
                "carried_codes": sorted(carried_codes),
                "base_target_weights": base_weights,
                "defense_diagnostics": defense_diagnostics,
            }
        )
        decision = replace(
            decision,
            decision_id=f"{context.signal_date}-{self._descriptor_id}",
            target_weights=final_weights,
            cash_weight=final_cash,
            diagnostics=diagnostics,
        )
        self._previous_weights = dict(final_weights)
        if self._decision_log:
            self._decision_log[-1].update(
                {
                    "decision_id": decision.decision_id,
                    "target_weights": dict(final_weights),
                    "cash_weight": final_cash,
                    "diagnostics": diagnostics,
                }
            )
        if self._decision_trace:
            for candidate in self._decision_trace[-1].get("candidates", []):
                code = candidate.get("ts_code")
                candidate["target_weight"] = float(final_weights.get(code, 0.0))
        return decision


class AiRotationR84R81R57R62ComboStrategy(
    AiRotationR82EconomicRoleDynamicRepR57SignalStrategy
):
    descriptor = DESCRIPTOR

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = super().describe_decision_pipeline(config)
        pipeline["downstream"] = "R34 staged reentry -> R39 incumbent carry -> R76 fixed short bond"
        pipeline["weighting_rule"] = "R62 true inverse volatility over 60 daily returns, cap 0.50"
        return pipeline

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> EconomicRoleR57R62Session:
        del initialization
        return EconomicRoleR57R62Session(config, self.descriptor.id)
