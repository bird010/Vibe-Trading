import pytest
from backtest.fund_rotation.strategies.ai_rotation_r62_r59_true_invvol.strategy import build_true_inverse_volatility_weights

def test_r62_uses_true_inverse_volatility_and_preserves_exposure():
    weights, cash, diagnostics = build_true_inverse_volatility_weights({"A": 0.10, "B": 0.20})
    assert weights["A"] == pytest.approx(2 * weights["B"])
    assert sum(weights.values()) + cash == pytest.approx(1.0)
    assert diagnostics["mode"] == "true_inverse_volatility"

def test_r62_invalid_volatility_falls_back_as_a_whole_layer():
    weights, cash, diagnostics = build_true_inverse_volatility_weights({"A": 0.1, "B": float("nan")})
    assert weights == {"A": pytest.approx(1 / 3), "B": pytest.approx(1 / 3)}
    assert cash == pytest.approx(1 / 3)
    assert diagnostics["mode"] == "equal_slot_fallback"
