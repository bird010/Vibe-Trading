"""TDD contract tests for R100: R88 with final filled-role inverse-vol slots."""

from __future__ import annotations

import pandas as pd
import pytest

try:
    from backtest.fund_rotation.strategies.ai_rotation_r86_r81_transition_cap_50.r100_r81_r88_invvol_slots import (
        DESCRIPTOR,
        AiRotationR100R81R88InvvolSlotsStrategy,
        apply_r100_adjustable_transition_cap,
        build_role_inverse_volatility_slot_weights,
        merge_adjusted_role_weights,
    )
    _IMPORT_ERROR = None
except ImportError as exc:
    DESCRIPTOR = None
    AiRotationR100R81R88InvvolSlotsStrategy = None
    apply_r100_adjustable_transition_cap = None
    merge_adjusted_role_weights = None
    build_role_inverse_volatility_slot_weights = None
    _IMPORT_ERROR = exc


def _require_r100() -> None:
    assert _IMPORT_ERROR is None, f"R100 package missing: {_IMPORT_ERROR}"


def test_r100_adjusts_only_filled_roles_and_preserves_vacant_cash():
    _require_r100()
    weekly = pd.DataFrame(
        {
            "A": [0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01],
            "B": [0.02, -0.02, 0.02, -0.02, 0.02, -0.02, 0.02, -0.02],
        }
    )

    weights, vacant, cash, diagnostics = build_role_inverse_volatility_slot_weights(
        ["R1", "R2", "R3"],
        {"R1": "A", "R2": "B", "R3": None},
        weekly,
        top_n=3,
    )

    assert vacant == ["R3"]
    assert weights["A"] > 1 / 3
    assert weights["B"] < 1 / 3
    assert cash == pytest.approx(1 / 3)
    assert sum(weights.values()) + cash == pytest.approx(1.0)
    assert diagnostics["weight_mode"] == "inverse_volatility_with_fixed_cash_slots"


def test_r100_preserves_non_equal_base_weight_mass_and_non_negative_weights():
    _require_r100()
    weekly = pd.DataFrame(
        {
            "A": [0.01] * 8,
            "B": [0.02, -0.02, 0.02, -0.02, 0.02, -0.02, 0.02, -0.02],
        }
    )
    weights, vacant, cash, _ = build_role_inverse_volatility_slot_weights(
        ["R1", "R2"], {"R1": "A", "R2": "B"}, weekly, top_n=2,
        base_weights={"A": 0.7, "B": 0.2}, base_cash=0.1,
    )

    assert vacant == []
    assert all(value >= 0.0 for value in weights.values())
    assert sum(weights.values()) + cash == pytest.approx(1.0)
    assert weights["A"] > weights["B"]


def test_r100_does_not_adjust_role_representative_when_it_is_protected_defense_code():
    _require_r100()
    weekly = pd.DataFrame({"DEF": [0.01] * 8, "A": [0.02, -0.02] * 4})
    weights, _, cash, diagnostics = build_role_inverse_volatility_slot_weights(
        ["R1", "R2"], {"R1": "DEF", "R2": "A"}, weekly, top_n=2,
        protected_codes={"DEF"},
    )

    assert weights["DEF"] == pytest.approx(0.5)
    assert weights["A"] == pytest.approx(0.5)
    assert cash == pytest.approx(0.0)
    assert diagnostics["protected_codes"] == ["DEF"]


def test_r100_mixed_protected_and_adjustable_merge_preserves_defense_base_weight():
    _require_r100()
    weekly = pd.DataFrame({"DEF": [0.01] * 8, "A": [0.02, -0.02] * 4})
    base_targets = {"DEF": 0.4, "A": 0.5, "OTHER": 0.0}
    adjusted, _, cash, _ = build_role_inverse_volatility_slot_weights(
        ["R1", "R2"], {"R1": "DEF", "R2": "A"}, weekly, top_n=2,
        base_weights={"DEF": 0.4, "A": 0.5}, base_cash=0.1,
        protected_codes={"DEF"},
    )

    merged = merge_adjusted_role_weights(base_targets, adjusted, {"A"})

    assert adjusted["DEF"] == pytest.approx(0.4)
    assert merged["DEF"] == pytest.approx(0.4)
    assert merged["OTHER"] == pytest.approx(0.0)
    assert sum(merged.values()) + cash == pytest.approx(1.0)


def test_r100_limits_adjustable_increases_after_uncontrollable_non_slot_increase():
    _require_r100()
    adjusted, cash, diagnostics = apply_r100_adjustable_transition_cap(
        {"A": 0.2, "B": 0.2, "DEF": 0.1},
        {"A": 0.9333333333, "B": 0.1333333333, "DEF": 0.4},
        {"A", "B"},
    )

    assert adjusted["DEF"] == pytest.approx(0.4)
    assert diagnostics["uncontrollable_positive_exposure"] == pytest.approx(0.3)
    assert diagnostics["adjustable_budget_exhausted"] is True
    assert adjusted["A"] == pytest.approx(0.4)
    assert adjusted["B"] == pytest.approx(0.1333333333)
    assert sum(max(0.0, adjusted.get(code, 0.0) - {"A": 0.2, "B": 0.2}.get(code, 0.0)) for code in {"A", "B"}) <= 0.5 + 1e-12
    assert sum(adjusted.values()) + cash == pytest.approx(1.0)


def test_r100_adjustable_cap_preserves_non_adjustable_target_exactly_and_cash_non_negative():
    _require_r100()
    previous = {"A": 0.2, "DEF": 0.1, "OTHER": 0.1}
    candidate = {"A": 0.8, "DEF": 0.1, "OTHER": 0.2}
    adjusted, cash, diagnostics = apply_r100_adjustable_transition_cap(
        previous, candidate, {"A"}
    )

    assert adjusted["DEF"] == pytest.approx(0.1)
    assert adjusted["OTHER"] == pytest.approx(0.2)
    assert diagnostics["uncontrollable_positive_exposure"] == pytest.approx(0.1)
    assert adjusted["A"] == pytest.approx(0.6)
    assert cash == pytest.approx(0.1)
    assert all(value >= 0.0 for value in adjusted.values())
    assert sum(adjusted.values()) + cash == pytest.approx(1.0)


def test_r100_falls_back_to_equal_slots_for_invalid_quality_or_incomplete_window():
    _require_r100()
    weekly = pd.DataFrame({"A": [0.01] * 8, "B": [0.02] * 8})
    expected = {"A": pytest.approx(0.5), "B": pytest.approx(0.5)}

    for frame, gate in (
        (weekly.iloc[:7], "PASS"),
        (weekly, "DEGRADED"),
        (weekly.assign(B=[0.02, 0.02, float("nan"), 0.02, 0.02, 0.02, 0.02, 0.02]), "PASS"),
    ):
        weights, vacant, cash, diagnostics = build_role_inverse_volatility_slot_weights(
            ["R1", "R2"], {"R1": "A", "R2": "B"}, frame, top_n=2, quality_gate=gate
        )
        assert weights == expected
        assert vacant == []
        assert cash == pytest.approx(0.0)
        assert diagnostics["fallback_reason"] is not None


def test_r100_is_a_distinct_r88_derived_strategy_with_pipeline_boundary():
    _require_r100()
    assert DESCRIPTOR.id == "ai_rotation_r100_r81_r88_invvol_slots"
    strategy = AiRotationR100R81R88InvvolSlotsStrategy()
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())
    assert pipeline["transition_cap_rule"] == "one-week positive target exposure capped at 50%"
    assert pipeline["selection_rule"] == "role ranking with entry Top3 and exit Top4 rank buffer"
    assert pipeline["medium_trend_gate"] == "adjusted_return_126d > 0 on current representatives"
    assert pipeline["slot_weighting_rule"] == "filled role slots use eight-week inverse volatility"
