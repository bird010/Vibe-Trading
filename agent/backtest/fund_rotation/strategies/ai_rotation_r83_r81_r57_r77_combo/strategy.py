"""R83: R82 plus frozen-pool defensive relative momentum."""

from __future__ import annotations

from pydantic import BaseModel

from backtest.fund_rotation.contracts import (
    FundRotationStrategyDescriptor,
    StrategyDecisionContext,
    StrategyInitializationContext,
)
from backtest.fund_rotation.strategies.ai_rotation_r77_defense_relative_momentum.strategy import (
    DEFENSE_POOL,
    DEFENSE_WINDOW_WEEKS,
    compute_defense_relative_momentum,
)
from backtest.fund_rotation.strategies.ai_rotation_r82_economic_role_dynamic_rep_r57_signal.strategy import (
    EconomicRoleR57Session,
    AiRotationR82EconomicRoleDynamicRepR57SignalStrategy,
)


DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r83_r81_r57_r77_combo",
    name="R81 动态代表 R57 信号 R77 防御",
    description=(
        "保留 R81 动态代表与 R57 三因子排序，仅将 R82 的固定短债防御层"
        "替换为 R77 防御池四周相对动量选择。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)


class EconomicRoleR57R77Session(EconomicRoleR57Session):
    def _resolve_defense(
        self,
        view,
        signal_date: str,
        signal_eligible: set[str],
    ) -> tuple[str | None, dict[str, object], str]:
        try:
            weekly_returns = view.returns(
                "weekly", self._config.correlation_lookback_weeks
            )
        except (AttributeError, KeyError, TypeError, ValueError):
            weekly_returns = None
        relative_scores = compute_defense_relative_momentum(weekly_returns)
        eligible_scores = {
            code: score
            for code, score in relative_scores.items()
            if code in signal_eligible
        }
        candidates = [
            (code, score)
            for code, score in eligible_scores.items()
            if score is not None and score > 0.0
        ]
        candidates.sort(key=lambda item: (-float(item[1]), item[0]))
        defense_code = candidates[0][0] if candidates else None
        recent = (
            weekly_returns.iloc[-DEFENSE_WINDOW_WEEKS:]
            if hasattr(weekly_returns, "iloc")
            else None
        )
        audit_scores = {
            code: {
                "score": score,
                "signal_date_eligible": code in signal_eligible,
                "score_status": (
                    "VALID_POSITIVE"
                    if code in eligible_scores and score is not None and score > 0.0
                    else "VALID_NONPOSITIVE"
                    if code in eligible_scores and score is not None
                    else "SIGNAL_DATE_INELIGIBLE"
                    if score is not None
                    else "INSUFFICIENT_OR_INVALID_RETURNS"
                ),
            }
            for code, score in relative_scores.items()
        }
        return (
            defense_code,
            {
                "risk_layer": "defense_relative_momentum",
                "arm": "relative_momentum",
                "defense_pool": list(DEFENSE_POOL),
                "window_weeks": DEFENSE_WINDOW_WEEKS,
                "fallback": "cash",
                "relative_momentum": relative_scores,
                "audit": {
                    "observations": int(len(recent)) if recent is not None else 0,
                    "window_start": str(recent.index[0]) if recent is not None and len(recent) else None,
                    "window_end": str(recent.index[-1]) if recent is not None and len(recent) else None,
                    "assets": audit_scores,
                },
                "signal_date": signal_date,
            },
            "DEFENSE_RELATIVE_MOMENTUM" if defense_code else "DEFENSE_CASH_FALLBACK",
        )


class AiRotationR83R81R57R77ComboStrategy(
    AiRotationR82EconomicRoleDynamicRepR57SignalStrategy
):
    descriptor = DESCRIPTOR

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = super().describe_decision_pipeline(config)
        pipeline["downstream"] = "R34 staged reentry -> R39 incumbent carry -> R77 relative-momentum defense"
        pipeline["defense_layer"] = {
            "arm": "relative_momentum",
            "pool": list(DEFENSE_POOL),
            "window_weeks": DEFENSE_WINDOW_WEEKS,
            "fallback": "cash",
        }
        return pipeline

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> EconomicRoleR57R77Session:
        del initialization
        return EconomicRoleR57R77Session(config, self.descriptor.id)
