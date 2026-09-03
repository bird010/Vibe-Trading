"""R91: R88 with equal-weight causal multi-horizon role ranks."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Mapping

import pandas as pd
from pydantic import BaseModel

from backtest.fund_rotation.contracts import FundRotationStrategyDescriptor, StrategyDecisionContext, StrategyInitializationContext
from backtest.fund_rotation.strategies.ai_rotation_r86_r81_transition_cap_50.r88_r81_role_r60_gate import AiRotationR88R81RoleR60GateStrategy, EconomicRoleR81RoleR60GateSession, apply_role_medium_trend_gate, _causal
from backtest.fund_rotation.strategies.ai_rotation_r86_r81_transition_cap_50.r87_role_rank_buffer import select_rank_buffer_roles

HORIZONS = (60, 120, 240)
DESCRIPTOR = FundRotationStrategyDescriptor(id="ai_rotation_r91_r81_role_r73_multi_horizon", name="R88 Role R73 多周期排名", description="R88 全部语义之上，以当前代表的 60/120/240 日复权收益等权排名。", interface_version="1.0", supported_universe=("etf",), deterministic=True)


def _finite(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def compute_role_multi_horizon_returns(adjusted_closes: object, *, signal_date: str, representatives: Mapping[str, str | None]) -> dict[int, dict[str, float | None]]:
    result = {h: {role: None for role in representatives} for h in HORIZONS}
    if not isinstance(adjusted_closes, pd.DataFrame):
        return result
    try:
        cutoff = pd.Timestamp(signal_date)
        frame = adjusted_closes.loc[[i for i in adjusted_closes.index if pd.Timestamp(i) <= cutoff]].sort_index()
    except (TypeError, ValueError, KeyError):
        return result
    for role, code in representatives.items():
        if not code or str(code) not in frame.columns:
            continue
        values = pd.to_numeric(frame[str(code)], errors="coerce")
        for horizon in HORIZONS:
            if len(values) < horizon + 1:
                continue
            numbers = [_finite(x) for x in values.iloc[-(horizon + 1):]]
            if any(x is None or x <= 0 for x in numbers):
                continue
            start, end = numbers[0], numbers[-1]
            assert start is not None and end is not None
            change = end / start - 1.0
            result[horizon][role] = change if math.isfinite(change) else None
    return result


def aggregate_role_multi_horizon_rank_scores(period_returns: Mapping[int, Mapping[str, object]]) -> tuple[list[str], dict[str, dict[str, object]]]:
    roles = sorted({role for values in period_returns.values() for role in values})
    diagnostics = {role: {"role_id": role, "scope": "ECONOMIC_ROLE"} for role in roles}
    ranks = {}
    for horizon in HORIZONS:
        valid = {role: _finite(period_returns.get(horizon, {}).get(role)) for role in roles}
        ordered = sorted((role for role, value in valid.items() if value is not None), key=lambda role: (-valid[role], role))
        ranks[horizon] = {role: rank for rank, role in enumerate(ordered, 1)}
    complete = []
    for role in roles:
        missing = tuple(h for h in HORIZONS if role not in ranks[h])
        diagnostics[role]["status"] = "VALID" if not missing else "INCOMPLETE_HORIZON"
        diagnostics[role]["missing_horizons"] = missing
        for horizon in HORIZONS:
            diagnostics[role][f"rank_{horizon}"] = ranks[horizon].get(role)
        if not missing:
            diagnostics[role]["aggregate_rank"] = sum(ranks[h][role] for h in HORIZONS) / len(HORIZONS)
            complete.append(role)
    return sorted(complete, key=lambda role: (diagnostics[role]["aggregate_rank"], role)), diagnostics


class EconomicRoleR81RoleR73MultiHorizonSession(EconomicRoleR81RoleR60GateSession):
    STRATEGY_ID = DESCRIPTOR.id

    def __init__(self, config) -> None:
        super().__init__(config)
        self._last_multi_horizon_diagnostics = {}

    def _rank_roles(self, scores):
        context = self._current_context
        if context is None:
            return []
        date, view = context.signal_date, context.data_view
        bars = _causal(view.daily_bars(["open", "high", "low", "close", "vol", "amount"], lookback=127), date)
        adjustments = _causal(view.fund_adjustments(lookback=127), date)
        qualified, gate = apply_role_medium_trend_gate(self._representatives, bars, adjustments, date)
        try:
            closes = view.adjusted_closes(lookback=max(HORIZONS) + 1)
        except (AttributeError, KeyError, TypeError, ValueError):
            closes = None
        ranked, diagnostics = aggregate_role_multi_horizon_rank_scores(compute_role_multi_horizon_returns(closes, signal_date=date, representatives=self._representatives))
        for role, row in diagnostics.items():
            row["representative"] = self._representatives.get(role)
            row["medium_trend_positive"] = gate.get(role, {}).get("medium_trend_positive", False)
        self._last_multi_horizon_diagnostics = diagnostics
        valid = {role for role in ranked if role in qualified and scores.get(role) is not None and scores[role].eligible}
        epoch_reset = not self._role_members or self._week_index - self._last_role_refresh_week >= self._config.refresh_interval_weeks
        selected, buffer = select_rank_buffer_roles(ranked, self._previous_selected_roles, valid, top_n=self._config.top_n, exit_rank=self._config.top_n + 1, epoch_reset=epoch_reset)
        self._previous_selected_roles = set(selected)
        self._last_rank_buffer_diagnostics = buffer
        return selected + [role for role in ranked if role not in selected]

    def evaluate(self, context: StrategyDecisionContext):
        decision = super().evaluate(context)
        diagnostics = dict(decision.diagnostics)
        diagnostics["role_multi_horizon_rank"] = {"rule": "equal-weight rank of causal adjusted returns over 60/120/240 trading days", "horizons": list(HORIZONS), "required_observations": {str(h): h + 1 for h in HORIZONS}, "roles": dict(self._last_multi_horizon_diagnostics)}
        self._decision_log[-1]["decision_id"] = f"{context.signal_date}-{DESCRIPTOR.id}"
        self._decision_log[-1]["diagnostics"] = diagnostics
        return replace(decision, decision_id=f"{context.signal_date}-{DESCRIPTOR.id}", diagnostics=diagnostics)


class AiRotationR91R81RoleR73MultiHorizonStrategy(AiRotationR88R81RoleR60GateStrategy):
    descriptor = DESCRIPTOR

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = super().describe_decision_pipeline(config)
        pipeline["role_rank_horizons"] = list(HORIZONS)
        pipeline["role_score_rule"] = "equal-weight rank of causal adjusted returns over 60/120/240 trading days"
        return pipeline

    def create_session(self, initialization: StrategyInitializationContext, config: BaseModel):
        del initialization
        return EconomicRoleR81RoleR73MultiHorizonSession(config)
