"""TDD contract tests for R90 role-level R61 dual-horizon ranking."""

from __future__ import annotations

import pytest

from backtest.fund_rotation.scoring.contracts import StrategyScore

try:
    from backtest.fund_rotation.strategies.ai_rotation_r90_r81_role_r61_dual_horizon.strategy import (
        DESCRIPTOR,
        AiRotationR90R81RoleR61DualHorizonStrategy,
        EconomicRoleR81RoleR61DualHorizonSession,
        fuse_dual_horizon_role_scores,
    )
    _IMPORT_ERROR = None
except ImportError as exc:
    DESCRIPTOR = None
    AiRotationR90R81RoleR61DualHorizonStrategy = None
    EconomicRoleR81RoleR61DualHorizonSession = None
    fuse_dual_horizon_role_scores = None
    _IMPORT_ERROR = exc


def _require_r90() -> None:
    assert _IMPORT_ERROR is None, f"R90 package missing: {_IMPORT_ERROR}"


def _score(value: float | None, *, eligible: bool = True) -> StrategyScore:
    return StrategyScore(
        value=value,
        eligible=eligible and value is not None,
        subject_id="role",
        scope="ECONOMIC_ROLE",
    )


def test_r90_fuses_population_standardized_short_and_medium_scores_equally():
    _require_r90()

    ranked, diagnostics = fuse_dual_horizon_role_scores(
        {
            "R1": _score(1.0),
            "R2": _score(2.0),
            "R3": _score(3.0),
        },
        {"R1": 0.0, "R2": 0.0, "R3": 0.2},
    )

    assert ranked == ["R3", "R2", "R1"]
    assert diagnostics["R1"]["short_z"] == pytest.approx(-1.224744871)
    assert diagnostics["R3"]["medium_z"] == pytest.approx(1.414213562)
    assert diagnostics["R2"]["fused_score"] == pytest.approx(-0.353553391)
    assert diagnostics["R1"]["scope"] == "ECONOMIC_ROLE"


def test_r90_excludes_missing_or_ineligible_components_fail_closed():
    _require_r90()

    ranked, diagnostics = fuse_dual_horizon_role_scores(
        {
            "R1": _score(1.0),
            "R2": _score(None, eligible=False),
            "R3": _score(3.0),
        },
        {"R1": 0.1, "R2": 0.2, "R3": None},
    )

    assert ranked == ["R1"]
    assert diagnostics["R2"]["status"] == "SHORT_SCORE_UNAVAILABLE"
    assert diagnostics["R3"]["status"] == "MEDIUM_RETURN_UNAVAILABLE"


def test_r90_uses_deterministic_role_id_ties_without_cluster_state():
    _require_r90()

    ranked, diagnostics = fuse_dual_horizon_role_scores(
        {"R2": _score(1.0), "R1": _score(1.0)},
        {"R2": 0.1, "R1": 0.1},
    )

    assert ranked == ["R1", "R2"]
    assert all("cluster" not in key for key in diagnostics["R1"])


def test_r90_preserves_r88_gate_buffer_and_transition_cap_pipeline():
    _require_r90()

    assert DESCRIPTOR.id == "ai_rotation_r90_r81_role_r61_dual_horizon"
    strategy = AiRotationR90R81RoleR61DualHorizonStrategy()
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())
    assert pipeline["transition_cap_rule"] == (
        "one-week positive target exposure capped at 50%"
    )
    assert pipeline["selection_rule"] == (
        "role ranking with entry Top3 and exit Top4 rank buffer"
    )
    assert pipeline["medium_trend_gate"] == (
        "adjusted_return_126d > 0 on current representatives"
    )
    assert pipeline["role_score_rule"] == "50/50 standardized short/medium horizon score"
    assert EconomicRoleR81RoleR61DualHorizonSession.__mro__[1].__name__ == (
        "EconomicRoleR81RoleR60GateSession"
    )
