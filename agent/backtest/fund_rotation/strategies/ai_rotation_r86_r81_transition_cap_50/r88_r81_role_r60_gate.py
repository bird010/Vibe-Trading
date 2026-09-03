"""R88: R87 plus a causal 126-trading-day role trend gate."""

from __future__ import annotations

from dataclasses import replace
from typing import Mapping

import pandas as pd
from pydantic import BaseModel

from backtest.fund_rotation.contracts import (
    FundRotationStrategyDescriptor,
    StrategyDecisionContext,
    StrategyInitializationContext,
)
from backtest.fund_rotation.strategies.ai_rotation_r60_r59_medium_trend_gate.strategy import (
    _causal,
    compute_adjusted_return_126d,
)
from backtest.fund_rotation.strategies.ai_rotation_r86_r81_transition_cap_50.r87_role_rank_buffer import (
    AiRotationR87R81RoleRankBufferStrategy,
    EconomicRoleR81RoleRankBufferSession,
    select_rank_buffer_roles,
)
from backtest.fund_rotation.strategies.ai_rotation_r86_r81_transition_cap_50.strategy import (
    EconomicRoleR81TransitionCap50Session,
)


DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r88_r81_role_r60_gate",
    name="R87 Role 126日正趋势门禁",
    description="R87 上游加当前动态代表的因果 126 日复权收益正趋势 gate。",
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)


def apply_role_medium_trend_gate(
    representatives: Mapping[str, str | None],
    bars: pd.DataFrame,
    adjustments: pd.DataFrame,
    signal_date: str,
) -> tuple[set[str], dict[str, dict[str, object]]]:
    """Return only roles whose current representative passes the R60 gate."""
    qualified: set[str] = set()
    diagnostics: dict[str, dict[str, object]] = {}
    for role_id, representative in representatives.items():
        if not representative:
            diagnostics[role_id] = {
                "representative": None,
                "status": "MISSING_REPRESENTATIVE",
                "return_126d": None,
                "observations": 0,
                "medium_trend_positive": False,
            }
            continue
        result = compute_adjusted_return_126d(
            bars[bars["ts_code"].eq(str(representative))]
            if "ts_code" in bars.columns
            else bars.iloc[0:0],
            adjustments[adjustments["ts_code"].eq(str(representative))]
            if "ts_code" in adjustments.columns
            else adjustments.iloc[0:0],
            signal_date,
        )
        positive = bool(
            result["status"] == "VALID"
            and result["return_126d"] is not None
            and float(result["return_126d"]) > 0.0
        )
        diagnostics[role_id] = {
            "representative": str(representative),
            "return_126d": result["return_126d"],
            "observations": result["observations"],
            "required_observations": 127,
            "status": result["status"],
            "medium_trend_positive": positive,
        }
        if positive:
            qualified.add(role_id)
    return qualified, diagnostics


class EconomicRoleR81RoleR60GateSession(EconomicRoleR81RoleRankBufferSession):
    """R87 role buffer restricted to representatives passing the R60 gate."""

    STRATEGY_ID = DESCRIPTOR.id

    def __init__(self, config) -> None:
        super().__init__(config)
        self._current_context: StrategyDecisionContext | None = None
        self._last_medium_trend_diagnostics: dict[str, dict[str, object]] = {}

    def _rank_roles(self, scores):
        ranked = EconomicRoleR81TransitionCap50Session._rank_roles(self, scores)
        context = self._current_context
        if context is None:
            qualified_roles = set()
            gate_diagnostics = {}
        else:
            signal_date = context.signal_date
            view = context.data_view
            bars = _causal(
                view.daily_bars(
                    ["open", "high", "low", "close", "vol", "amount"],
                    lookback=127,
                ),
                signal_date,
            )
            adjustments = _causal(view.fund_adjustments(lookback=127), signal_date)
            qualified_roles, gate_diagnostics = apply_role_medium_trend_gate(
                self._representatives, bars, adjustments, signal_date,
            )
        self._last_medium_trend_diagnostics = gate_diagnostics
        valid_roles = {
            role for role, score in scores.items()
            if score.eligible and role in qualified_roles
        }
        epoch_reset = (
            not self._role_members
            or self._week_index - self._last_role_refresh_week >= self._config.refresh_interval_weeks
        )
        selected, diagnostics = select_rank_buffer_roles(
            list(ranked), self._previous_selected_roles, valid_roles,
            top_n=self._config.top_n, exit_rank=self._config.top_n + 1,
            epoch_reset=epoch_reset,
        )
        self._previous_selected_roles = set(selected)
        self._last_rank_buffer_diagnostics = diagnostics
        return selected + [role for role in ranked if role not in selected]

    def evaluate(self, context: StrategyDecisionContext):
        self._current_context = context
        try:
            decision = super().evaluate(context)
        finally:
            self._current_context = None
        diagnostics = dict(decision.diagnostics)
        diagnostics["medium_trend_gate"] = {
            "rule": "adjusted_return_126d > 0 on current representatives",
            "required_observations": 127,
            "roles": dict(self._last_medium_trend_diagnostics),
        }
        self._decision_log[-1]["decision_id"] = f"{context.signal_date}-{DESCRIPTOR.id}"
        self._decision_log[-1]["diagnostics"] = diagnostics
        return replace(
            decision,
            decision_id=f"{context.signal_date}-{DESCRIPTOR.id}",
            diagnostics=diagnostics,
        )


class AiRotationR88R81RoleR60GateStrategy(AiRotationR87R81RoleRankBufferStrategy):
    descriptor = DESCRIPTOR

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = super().describe_decision_pipeline(config)
        pipeline["medium_trend_gate"] = "adjusted_return_126d > 0 on current representatives"
        return pipeline

    def create_session(self, initialization: StrategyInitializationContext, config: BaseModel):
        del initialization
        return EconomicRoleR81RoleR60GateSession(config)
