"""R90: R88 with a role-level R61 dual-horizon ranking score."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Mapping

import pandas as pd
from pydantic import BaseModel

from backtest.fund_rotation.contracts import (
    FundRotationStrategyDescriptor,
    StrategyDecisionContext,
    StrategyInitializationContext,
)
from backtest.fund_rotation.scoring.contracts import StrategyScore
from backtest.fund_rotation.strategies.ai_rotation_r86_r81_transition_cap_50.r88_r81_role_r60_gate import (
    AiRotationR88R81RoleR60GateStrategy,
    EconomicRoleR81RoleR60GateSession,
    apply_role_medium_trend_gate,
    _causal,
)
from backtest.fund_rotation.strategies.ai_rotation_r86_r81_transition_cap_50.r87_role_rank_buffer import (
    select_rank_buffer_roles,
)


DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r90_r81_role_r61_dual_horizon",
    name="R88 Role R61 双期限标准化排名",
    description="R88 门禁与生命周期之上，以 50/50 标准化短中期限 Role 得分排名。",
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)


def _z_scores(values: Mapping[str, float]) -> dict[str, float]:
    mean = sum(values.values()) / len(values)
    variance = sum((value - mean) ** 2 for value in values.values()) / len(values)
    deviation = math.sqrt(variance)
    if deviation == 0.0:
        return {role_id: 0.0 for role_id in values}
    return {role_id: (value - mean) / deviation for role_id, value in values.items()}


def fuse_dual_horizon_role_scores(
    short_scores: Mapping[str, StrategyScore],
    medium_returns: Mapping[str, object],
) -> tuple[list[str], dict[str, dict[str, object]]]:
    """Rank complete roles using equal population-standardized horizons."""
    diagnostics: dict[str, dict[str, object]] = {}
    short: dict[str, float] = {}
    medium: dict[str, float] = {}
    for role_id, score in short_scores.items():
        row: dict[str, object] = {
            "role_id": role_id,
            "scope": "ECONOMIC_ROLE",
            "short_score": score.value if score.eligible else None,
            "medium_return_126d": medium_returns.get(role_id),
        }
        if not score.eligible or score.value is None or not math.isfinite(float(score.value)):
            row["status"] = "SHORT_SCORE_UNAVAILABLE"
        else:
            short[role_id] = float(score.value)
        value = medium_returns.get(role_id)
        if isinstance(value, bool) or value is None:
            row.setdefault("status", "MEDIUM_RETURN_UNAVAILABLE")
        else:
            try:
                numeric = float(value)
            except (TypeError, ValueError, OverflowError):
                numeric = math.nan
            if math.isfinite(numeric):
                medium[role_id] = numeric
            else:
                row.setdefault("status", "MEDIUM_RETURN_UNAVAILABLE")
        diagnostics[role_id] = row

    complete = sorted(set(short) & set(medium))
    short_z = _z_scores({role_id: short[role_id] for role_id in complete}) if complete else {}
    medium_z = _z_scores({role_id: medium[role_id] for role_id in complete}) if complete else {}
    for role_id in complete:
        row = diagnostics[role_id]
        row.update(
            {
                "status": "VALID",
                "short_z": short_z[role_id],
                "medium_z": medium_z[role_id],
                "fused_score": 0.5 * (short_z[role_id] + medium_z[role_id]),
            }
        )
    ranked = sorted(
        complete,
        key=lambda role_id: (-float(diagnostics[role_id]["fused_score"]), role_id),
    )
    return ranked, diagnostics


class EconomicRoleR81RoleR61DualHorizonSession(EconomicRoleR81RoleR60GateSession):
    """R88 session with only its role ranking score replaced."""

    STRATEGY_ID = DESCRIPTOR.id

    def __init__(self, config) -> None:
        super().__init__(config)
        self._last_dual_horizon_diagnostics: dict[str, dict[str, object]] = {}

    def _rank_roles(self, scores):
        context = self._current_context
        if context is None:
            self._last_dual_horizon_diagnostics = {}
            return []
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
        _qualified, gate_diagnostics = apply_role_medium_trend_gate(
            self._representatives, bars, adjustments, signal_date,
        )
        medium_returns = {
            role_id: row.get("return_126d")
            for role_id, row in gate_diagnostics.items()
        }
        ranked, fusion_diagnostics = fuse_dual_horizon_role_scores(
            scores, medium_returns,
        )
        for role_id, row in fusion_diagnostics.items():
            row["medium_trend_positive"] = gate_diagnostics.get(role_id, {}).get(
                "medium_trend_positive", False
            )
        self._last_dual_horizon_diagnostics = fusion_diagnostics
        qualified_roles = {
            role_id for role_id, row in gate_diagnostics.items()
            if row.get("medium_trend_positive") is True
        }
        valid_roles = {
            role_id for role_id in ranked
            if role_id in qualified_roles and scores[role_id].eligible
        }
        epoch_reset = (
            not self._role_members
            or self._week_index - self._last_role_refresh_week >= self._config.refresh_interval_weeks
        )
        selected, diagnostics = select_rank_buffer_roles(
            ranked, self._previous_selected_roles, valid_roles,
            top_n=self._config.top_n, exit_rank=self._config.top_n + 1,
            epoch_reset=epoch_reset,
        )
        self._previous_selected_roles = set(selected)
        self._last_rank_buffer_diagnostics = diagnostics
        return selected + [role_id for role_id in ranked if role_id not in selected]

    def evaluate(self, context: StrategyDecisionContext):
        decision = super().evaluate(context)
        diagnostics = dict(decision.diagnostics)
        diagnostics["dual_horizon_role_score"] = {
            "rule": "50/50 standardized short/medium horizon score",
            "short_component": "existing R81 representative role score",
            "medium_component": "current representative adjusted_return_126d",
            "required_observations": 127,
            "roles": dict(self._last_dual_horizon_diagnostics),
        }
        self._decision_log[-1]["decision_id"] = f"{context.signal_date}-{DESCRIPTOR.id}"
        self._decision_log[-1]["diagnostics"] = diagnostics
        return replace(
            decision,
            decision_id=f"{context.signal_date}-{DESCRIPTOR.id}",
            diagnostics=diagnostics,
        )


class AiRotationR90R81RoleR61DualHorizonStrategy(AiRotationR88R81RoleR60GateStrategy):
    descriptor = DESCRIPTOR

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = super().describe_decision_pipeline(config)
        pipeline["role_score_rule"] = "50/50 standardized short/medium horizon score"
        return pipeline

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> EconomicRoleR81RoleR61DualHorizonSession:
        del initialization
        return EconomicRoleR81RoleR61DualHorizonSession(config)
