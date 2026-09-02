"""R85: R81 dynamic representatives plus the R74 score layer."""

from __future__ import annotations

import math

from pydantic import BaseModel

from backtest.fund_rotation.contracts import (
    FundRotationStrategyDescriptor,
    StrategyInitializationContext,
)
from backtest.fund_rotation.strategies.ai_rotation_r74_r39_vol_adjusted_score.strategy import (
    compute_daily_volatility_60,
)
from backtest.fund_rotation.strategies.ai_rotation_r57_three_factor_representative.factors import (
    score_complete_candidates,
)
from backtest.fund_rotation.strategies.ai_rotation_r82_economic_role_dynamic_rep_r57_signal.strategy import (
    EconomicRoleR57Session,
    AiRotationR82EconomicRoleDynamicRepR57SignalStrategy,
)


DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r85_r81_r74_combo",
    name="R81 动态代表 R74 波动率调整动量",
    description=(
        "保留 R81 动态代表与 R34/R39/R76 生命周期，仅将当前代表排序"
        "替换为 R74 的四周动量除以 60 日年化波动率。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)


class EconomicRoleR74Session(EconomicRoleR57Session):
    def _score_representatives(self, view, signal_date, rows):
        codes = sorted(rows)
        try:
            weekly = view.returns("weekly", self._config.correlation_lookback_weeks)
            closes = view.adjusted_closes(lookback=61)
        except (AttributeError, KeyError, TypeError, ValueError):
            weekly = None
            closes = None
        volatility = compute_daily_volatility_60(
            closes,
            signal_date=signal_date,
            codes=codes,
        )
        raw = {}
        momenta = {}
        for code in codes:
            momentum = None
            if hasattr(weekly, "columns") and code in weekly.columns:
                values = weekly[code].iloc[-self._config.momentum_window_weeks :]
                parsed = [float(value) for value in values.tolist()]
                if len(parsed) == self._config.momentum_window_weeks and all(
                    math.isfinite(value) for value in parsed
                ):
                    momentum = math.prod(1.0 + value for value in parsed) - 1.0
            sigma = volatility.get(code)
            score = (
                momentum / sigma
                if momentum is not None
                and momentum > 0.0
                and sigma is not None
                and sigma > 1e-8
                else None
            )
            raw[code] = {"bias": score, "slope": score, "efficiency": score}
            momenta[code] = momentum
        composite, details = score_complete_candidates(
            raw,
            {"bias": 1 / 3, "slope": 1 / 3, "efficiency": 1 / 3},
            2,
        )
        details["r74_inputs"] = {
            code: {"momentum": momenta[code], "volatility_60": volatility.get(code)}
            for code in codes
        }
        return composite, details

    def _score_model_metadata(self):
        return {
            "id": "r74_momentum_over_volatility_60",
            "label": "Volatility-Adjusted Momentum",
            "version": "1",
            "direction": "HIGHER_BETTER",
            "scope": "ECONOMIC_ROLE_REPRESENTATIVE",
            "momentum_window_weeks": self._config.momentum_window_weeks,
            "volatility_window_days": 60,
            "volatility_annualization": 252,
            "formula": "positive momentum / annualized volatility_60",
        }


class AiRotationR85R81R74ComboStrategy(
    AiRotationR82EconomicRoleDynamicRepR57SignalStrategy
):
    descriptor = DESCRIPTOR

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = super().describe_decision_pipeline(config)
        pipeline["score_model"] = {
            "id": "r74_momentum_over_volatility_60",
            "scope": "ECONOMIC_ROLE_REPRESENTATIVE",
            "formula": "positive momentum / annualized volatility_60",
            "volatility_window_days": 60,
        }
        return pipeline

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> EconomicRoleR74Session:
        del initialization
        return EconomicRoleR74Session(config, self.descriptor.id)
