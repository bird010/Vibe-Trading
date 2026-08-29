import pytest

from backtest.fund_rotation.strategies.ai_rotation_r69_r39_transition_cap.strategy import (
    AiRotationR69R39TransitionCap50Strategy,
    AiRotationR70R39TransitionCap25Strategy,
    apply_transition_cap,
)


def test_transition_cap_scales_only_positive_increases():
    adjusted, cash, exposure, capped = apply_transition_cap(
        {"A": 0.5, "B": 0.2}, {"A": 0.0, "B": 0.8, "C": 0.4}, 0.5
    )
    assert exposure == pytest.approx(1.0)
    assert "A" not in adjusted
    assert adjusted["B"] == pytest.approx(0.5)
    assert adjusted["C"] == pytest.approx(0.2)
    assert cash == pytest.approx(0.3)
    assert capped is True


def test_transition_cap_leaves_targets_unchanged_under_cap():
    adjusted, cash, exposure, capped = apply_transition_cap(
        {"A": 0.3}, {"A": 0.2, "B": 0.4}, 0.5
    )
    assert adjusted == {"A": 0.2, "B": 0.4}
    assert cash == pytest.approx(0.4)
    assert exposure == pytest.approx(0.4)
    assert capped is False


def test_transition_strategies_have_distinct_registered_identities():
    assert AiRotationR69R39TransitionCap50Strategy.descriptor.id == "ai_rotation_r69_r39_transition_cap_50"
    assert AiRotationR70R39TransitionCap25Strategy.descriptor.id == "ai_rotation_r70_r39_transition_cap_25"
