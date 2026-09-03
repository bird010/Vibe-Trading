"""R100: R88 with inverse-volatility weighting of filled role slots only."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import replace

import pandas as pd
from pydantic import BaseModel

from backtest.fund_rotation.contracts import (
    FundRotationStrategyDescriptor,
    StrategyDecisionContext,
    StrategyInitializationContext,
    TargetWeightDecision,
)
from backtest.fund_rotation.strategies.ai_rotation_r86_r81_transition_cap_50.r88_r81_role_r60_gate import (
    AiRotationR88R81RoleR60GateStrategy,
    EconomicRoleR81RoleR60GateSession,
)


DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r100_r81_r88_invvol_slots",
    name="R100 已选角色逆波动槽位权重",
    description=(
        "完全沿用 R88 的角色选择、动态代表、趋势门禁、生命周期、防御和执行；"
        "仅对已填角色代表的最终槽位按最近八个完整周收益逆波动调整。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)
_VOLATILITY_WINDOW_WEEKS = 8


def _finite_or_none(value: object) -> float | None:
    try:
        number = float(value) if value is not None else math.nan
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def build_role_inverse_volatility_slot_weights(
    selected_roles: list[str],
    representatives: Mapping[str, str | None],
    weekly_returns: pd.DataFrame,
    top_n: int,
    window_weeks: int = _VOLATILITY_WINDOW_WEEKS,
    quality_gate: str = "PASS",
    base_weights: Mapping[str, float] | None = None,
    base_cash: float | None = None,
    protected_codes: set[str] | None = None,
) -> tuple[dict[str, float], list[str], float, dict[str, object]]:
    """Adjust only filled role slots; preserve supplied cash and non-slot state."""
    protected = {str(code) for code in (protected_codes or set())}
    all_filled = [role for role in selected_roles if representatives.get(role)]
    filled = [
        role for role in all_filled
        if str(representatives[role]) not in protected
    ]
    vacant = [role for role in selected_roles if not representatives.get(role)]
    default_slot_weight = 1.0 / top_n if top_n > 0 else 0.0
    slot_base = {
        str(representatives[role]): (
            float(base_weights[str(representatives[role])])
            if base_weights is not None and str(representatives[role]) in base_weights
            else default_slot_weight
        )
        for role in all_filled
    }
    cash = float(base_cash) if base_cash is not None else max(0.0, 1.0 - sum(slot_base.values()))
    diagnostics: dict[str, object] = {
        "window_weeks": window_weeks,
        "weight_mode": "champion_equal_slot",
        "volatility": {},
        "inverse_volatility_factor": {},
        "fallback_reason": None,
        "protected_codes": sorted(protected),
    }
    if not filled:
        diagnostics["fallback_reason"] = "no_filled_slots"
        return slot_base, vacant, cash, diagnostics
    if getattr(quality_gate, "value", quality_gate) != "PASS":
        diagnostics["fallback_reason"] = "quality_gate_rejected"
        return slot_base, vacant, cash, diagnostics
    if window_weeks <= 0 or len(weekly_returns) < window_weeks:
        diagnostics["fallback_reason"] = "insufficient_window"
        return slot_base, vacant, cash, diagnostics

    recent = weekly_returns.iloc[-window_weeks:]
    factors: dict[str, float] = {}
    volatilities: dict[str, float] = {}
    for role in filled:
        representative = str(representatives[role])
        if representative not in recent.columns:
            diagnostics["fallback_reason"] = "representative_window_unavailable"
            return slot_base, vacant, cash, diagnostics
        values = [_finite_or_none(value) for value in recent[representative].tolist()]
        if len(values) != window_weeks or any(value is None for value in values):
            diagnostics["fallback_reason"] = "representative_window_invalid"
            return slot_base, vacant, cash, diagnostics
        finite_values = [value for value in values if value is not None]
        mean = sum(finite_values) / len(finite_values)
        sigma = math.sqrt(sum((value - mean) ** 2 for value in finite_values) / len(finite_values))
        factor = 1.0 / (1.0 + sigma)
        if not math.isfinite(sigma) or not math.isfinite(factor):
            diagnostics["fallback_reason"] = "representative_volatility_invalid"
            return slot_base, vacant, cash, diagnostics
        volatilities[representative] = sigma
        factors[representative] = factor

    base_mass = sum(slot_base[code] for code in factors)
    mean_factor = (
        sum(slot_base[code] * factors[code] for code in factors) / base_mass
        if base_mass > 0.0 else math.nan
    )
    if not math.isfinite(mean_factor) or mean_factor <= 0.0:
        diagnostics["fallback_reason"] = "inverse_volatility_mean_invalid"
        return slot_base, vacant, cash, diagnostics
    weights = dict(slot_base)
    for code in factors:
        weights[code] = slot_base[code] * factors[code] / mean_factor
    diagnostics.update(
        {
            "weight_mode": "inverse_volatility_with_fixed_cash_slots",
            "volatility": dict(sorted(volatilities.items())),
            "inverse_volatility_factor": dict(sorted(factors.items())),
        }
    )
    return weights, vacant, cash, diagnostics


def apply_r100_adjustable_transition_cap(
    previous_weights: Mapping[str, float],
    target_weights: Mapping[str, float],
    adjustable_codes: set[str],
    cap: float = 0.50,
) -> tuple[dict[str, float], float, dict[str, object]]:
    """Cap adjustable increases while preserving non-adjustable targets exactly."""
    previous = {str(code): max(0.0, float(weight)) for code, weight in previous_weights.items()}
    candidate = {str(code): max(0.0, float(weight)) for code, weight in target_weights.items()}
    adjustable = {str(code) for code in adjustable_codes}
    all_codes = sorted(set(previous) | set(candidate))
    uncontrollable = math.fsum(
        max(0.0, candidate.get(code, 0.0) - previous.get(code, 0.0))
        for code in all_codes if code not in adjustable
    )
    adjustable_positive = math.fsum(
        max(0.0, candidate.get(code, 0.0) - previous.get(code, 0.0))
        for code in all_codes if code in adjustable
    )
    budget = max(0.0, float(cap) - uncontrollable)
    scale = min(1.0, budget / adjustable_positive) if adjustable_positive > 0.0 else 1.0
    adjusted = dict(candidate)
    for code in all_codes:
        if code not in adjustable:
            continue
        target_value = candidate.get(code, 0.0)
        previous_value = previous.get(code, 0.0)
        adjusted[code] = (
            previous_value + (target_value - previous_value) * scale
            if target_value > previous_value else target_value
        )
        if adjusted[code] == 0.0:
            adjusted.pop(code, None)
    total = math.fsum(adjusted.values())
    cash = max(0.0, 1.0 - total)
    diagnostics = {
        "cap": float(cap),
        "uncontrollable_positive_exposure": uncontrollable,
        "adjustable_positive_exposure": adjustable_positive,
        "adjustable_budget": budget,
        "adjustable_budget_exhausted": adjustable_positive > budget,
        "adjustable_scale": scale,
    }
    return adjusted, cash, diagnostics


def merge_adjusted_role_weights(
    base_target_weights: Mapping[str, float],
    slot_weights: Mapping[str, float],
    adjustable_codes: set[str],
) -> dict[str, float]:
    """Write back only adjustable role codes, preserving every other target."""
    merged = dict(base_target_weights)
    for code in sorted(adjustable_codes):
        if code in slot_weights:
            merged[code] = max(0.0, float(slot_weights[code]))
    return merged


class EconomicRoleR100InvvolSlotsSession(EconomicRoleR81RoleR60GateSession):
    """R88 session with a final filled-role slot-weight adjustment."""

    STRATEGY_ID = DESCRIPTOR.id

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        previous_weights = dict(self._previous_weights)
        decision = super().evaluate(context)
        selected_roles = list(decision.diagnostics.get("selected_roles", []))
        representatives = dict(self._representatives)
        selected_codes = {
            str(representatives[role])
            for role in selected_roles
            if representatives.get(role)
        }
        defense_code = decision.diagnostics.get("defense_asset")
        protected_codes = {str(defense_code)} if defense_code else set()
        adjustable_codes = selected_codes - protected_codes
        base_slot_weights = {code: decision.target_weights.get(code, 0.0) for code in selected_codes}
        weekly_returns = context.data_view.returns("weekly", _VOLATILITY_WINDOW_WEEKS)
        adjusted, vacant, cash, weighting_diagnostics = build_role_inverse_volatility_slot_weights(
            selected_roles,
            representatives,
            weekly_returns,
            top_n=int(self._config.top_n),
            quality_gate="PASS" if decision.quality_status.value == "VALID" else "DEGRADED",
            base_weights=base_slot_weights,
            base_cash=decision.cash_weight,
            protected_codes=protected_codes,
        )
        target_weights = merge_adjusted_role_weights(
            decision.target_weights, adjusted, adjustable_codes
        )
        target_weights, cash, cap_diagnostics = apply_r100_adjustable_transition_cap(
            previous_weights, target_weights, adjustable_codes
        )
        diagnostics = dict(decision.diagnostics)
        diagnostics["representative_inverse_volatility"] = weighting_diagnostics
        diagnostics["invvol_selected_roles"] = selected_roles
        diagnostics["invvol_vacant_roles"] = vacant
        diagnostics["invvol_protected_codes"] = sorted(protected_codes)
        diagnostics["transition_cap"] = cap_diagnostics
        self._previous_weights = dict(target_weights)
        decision_trace = getattr(self, "_decision_trace", None)
        if decision_trace:
            for row in decision_trace[-1].get("candidates", []):
                code = row.get("ts_code")
                if isinstance(code, str):
                    row["target_weight"] = float(target_weights.get(code, 0.0))
        self._decision_log[-1].update(
            {
                "decision_id": f"{decision.signal_date}-{DESCRIPTOR.id}",
                "target_weights": dict(target_weights),
                "cash_weight": cash,
                "diagnostics": diagnostics,
            }
        )
        return replace(
            decision,
            decision_id=f"{decision.signal_date}-{DESCRIPTOR.id}",
            target_weights=target_weights,
            cash_weight=cash,
            diagnostics=diagnostics,
        )


class AiRotationR100R81R88InvvolSlotsStrategy(AiRotationR88R81RoleR60GateStrategy):
    descriptor = DESCRIPTOR

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = super().describe_decision_pipeline(config)
        pipeline["slot_weighting_rule"] = "filled role slots use eight-week inverse volatility"
        return pipeline

    def create_session(self, initialization: StrategyInitializationContext, config: BaseModel):
        del initialization
        return EconomicRoleR100InvvolSlotsSession(config)
