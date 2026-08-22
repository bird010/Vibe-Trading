"""Focused behavior tests for round 29 inverse-volatility slot weights."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from backtest.fund_rotation.strategies.ai_rotation_r29_invvol_slots.strategy import (
    DESCRIPTOR,
    AiRotationR29InvvolSlotsStrategy,
    build_inverse_volatility_slot_weights,
)
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    build_slot_weights,
)


def test_inverse_volatility_formula_and_fixed_slot_sum():
    returns = pd.DataFrame(
        {
            "A": [0.00] * 8,
            "B": [-0.10, 0.10] * 4,
            "C": [-0.20, 0.20] * 4,
        }
    )
    weights, filled, vacant, cash, diagnostics = build_inverse_volatility_slot_weights(
        [1, 2, 3], {1: "A", 2: "B", 3: "C"}, returns, 3
    )
    sigma_b = 0.1
    sigma_c = 0.2
    factors = {"A": 1.0, "B": 1 / (1 + sigma_b), "C": 1 / (1 + sigma_c)}
    mean_factor = sum(factors.values()) / 3
    assert filled == [1, 2, 3] and vacant == []
    assert weights["A"] == pytest.approx((1 / 3) * factors["A"] / mean_factor)
    assert weights["B"] == pytest.approx((1 / 3) * factors["B"] / mean_factor)
    assert weights["C"] == pytest.approx((1 / 3) * factors["C"] / mean_factor)
    assert weights["A"] > weights["B"] > weights["C"]
    assert sum(weights.values()) + cash == pytest.approx(1.0)
    assert diagnostics["weight_mode"] == "inverse_volatility_with_fixed_cash_slots"


def test_equal_volatility_is_exact_champion_allocation():
    returns = pd.DataFrame({name: [0.01] * 8 for name in ("A", "B", "C")})
    actual = build_inverse_volatility_slot_weights(
        [1, 2, 3], {1: "A", 2: "B", 3: "C"}, returns, 3
    )
    expected = build_slot_weights([1, 2, 3], {1: "A", 2: "B", 3: "C"}, 3)
    assert actual[:4] == pytest.approx(expected[:4])


def test_vacant_slot_remains_cash_and_is_not_reallocated():
    returns = pd.DataFrame({"A": [0.00] * 8, "B": [-0.1, 0.1] * 4})
    weights, filled, vacant, cash, _ = build_inverse_volatility_slot_weights(
        [1, 2, 3], {1: "A", 2: "B", 3: None}, returns, 3
    )
    assert filled == [1, 2]
    assert vacant == [3]
    assert cash == pytest.approx(1 / 3)
    assert sum(weights.values()) == pytest.approx(2 / 3)


@pytest.mark.parametrize(
    "returns,reason",
    [
        (pd.DataFrame({"A": [0.01] * 7, "B": [0.01] * 7}), "insufficient_window"),
        (pd.DataFrame({"A": [0.01] * 7 + [math.nan], "B": [0.01] * 8}), "representative_window_invalid"),
        (pd.DataFrame({"B": [0.01] * 8}), "representative_window_unavailable"),
    ],
)
def test_invalid_window_falls_back_to_champion_weights(returns, reason):
    result = build_inverse_volatility_slot_weights(
        [1, 2], {1: "A", 2: "B"}, returns, 2
    )
    expected = build_slot_weights([1, 2], {1: "A", 2: "B"}, 2)
    assert result[:4] == pytest.approx(expected[:4])
    assert result[4]["fallback_reason"] == reason
    assert result[4]["weight_mode"] == "champion_equal_slot"


@pytest.mark.parametrize("quality_gate", ["REJECT", "INVALID"])
def test_rejected_or_invalid_quality_gate_falls_back_to_champion_weights(quality_gate):
    returns = pd.DataFrame(
        {"A": [0.00] * 8, "B": [-0.10, 0.10] * 4}
    )
    result = build_inverse_volatility_slot_weights(
        [1, 2],
        {1: "A", 2: "B"},
        returns,
        2,
        quality_gate=quality_gate,
    )
    expected = build_slot_weights([1, 2], {1: "A", 2: "B"}, 2)
    assert result[:4] == pytest.approx(expected[:4])
    assert result[4]["fallback_reason"] == "quality_gate_rejected"
    assert result[4]["weight_mode"] == "champion_equal_slot"


def test_future_rows_do_not_change_causal_eight_week_weights():
    base = pd.DataFrame({"A": [0.01] * 8, "B": [-0.1, 0.1] * 4})
    # The helper receives an as-of window. A row before the causal window is
    # future to the selected signal and must not affect its last eight rows.
    future = pd.concat(
        [pd.DataFrame({"A": [0.9], "B": [-0.9]}), base], ignore_index=True
    )
    args = ([1, 2], {1: "A", 2: "B"}, 2)
    first = build_inverse_volatility_slot_weights(*args[:2], base, args[2])
    second = build_inverse_volatility_slot_weights(*args[:2], future, args[2])
    assert first[0] == pytest.approx(second[0])
    assert first[1:4] == second[1:4]


def test_order_and_zero_volatility_are_deterministic_and_json_safe():
    returns = pd.DataFrame({"B": [0.02] * 8, "A": [0.01] * 8})
    result = build_inverse_volatility_slot_weights(
        [2, 1], {1: "A", 2: "B"}, returns, 2
    )
    assert result[0] == {"A": pytest.approx(0.5), "B": pytest.approx(0.5)}
    assert all(math.isfinite(value) for value in result[0].values())
    assert result[4]["volatility"] == {"A": 0.0, "B": 0.0}


def test_registered_identity_and_pipeline_are_isolated():
    strategy = AiRotationR29InvvolSlotsStrategy()
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())
    assert DESCRIPTOR.id == "ai_rotation_r29_invvol_slots"
    assert "inverse volatility" in str(pipeline).lower()
    assert "ai_rotation_r11_invvol" not in str(pipeline)
