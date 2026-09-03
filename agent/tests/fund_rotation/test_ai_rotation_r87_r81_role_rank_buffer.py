"""TDD contract tests for R87: R86 plus role-level Top3/Top4 hysteresis."""

from __future__ import annotations

import pytest

try:
    from backtest.fund_rotation.strategies.ai_rotation_r86_r81_transition_cap_50.r87_role_rank_buffer import (
        DESCRIPTOR,
        AiRotationR87R81RoleRankBufferStrategy,
        EconomicRoleR81RoleRankBufferSession,
        select_rank_buffer_roles,
    )
    _R87_IMPORT_ERROR = None
except ImportError as exc:  # Expected RED state before the package is added.
    DESCRIPTOR = None
    AiRotationR87R81RoleRankBufferStrategy = None
    EconomicRoleR81RoleRankBufferSession = None
    select_rank_buffer_roles = None
    _R87_IMPORT_ERROR = exc


def _require_r87() -> None:
    assert _R87_IMPORT_ERROR is None, f"R87 package missing: {_R87_IMPORT_ERROR}"


def test_role_rank_buffer_retains_rank_four_and_replaces_rank_five():
    _require_r87()
    selected, diagnostics = select_rank_buffer_roles(
        ["R1", "R2", "R3", "R4", "R5"],
        {"R4", "R5"},
        {"R1", "R2", "R3", "R4", "R5"},
        top_n=3,
        exit_rank=4,
    )
    assert selected == ["R4", "R1", "R2"]
    assert diagnostics["retained_roles"] == ["R4"]
    assert diagnostics["forced_exit_roles"] == ["R5"]


def test_role_rank_buffer_epoch_reset_clears_state_and_invalid_roles():
    _require_r87()
    selected, diagnostics = select_rank_buffer_roles(
        ["R1", "R2", "R3", "R4"],
        {"R4", "R9"},
        {"R1", "R2", "R3", "R4"},
        top_n=3,
        exit_rank=4,
        epoch_reset=True,
    )
    assert selected == ["R1", "R2", "R3"]
    assert diagnostics["epoch_reset"] is True
    assert "R9" not in diagnostics["retained_roles"]


def test_role_rank_buffer_is_deterministic_and_role_only():
    _require_r87()
    selected, diagnostics = select_rank_buffer_roles(
        ["R2", "R1", "R3", "R4"],
        {"R4"},
        {"R1", "R2", "R3", "R4"},
        top_n=3,
        exit_rank=4,
    )
    assert selected == ["R4", "R2", "R1"]
    assert diagnostics["current_rank_by_role"] == {
        "R2": 1,
        "R1": 2,
        "R3": 3,
        "R4": 4,
    }
    assert "cluster_id" not in diagnostics
    assert "cluster_state" not in diagnostics


def test_r87_preserves_r86_cap_and_r81_role_pipeline():
    _require_r87()
    assert DESCRIPTOR.id == "ai_rotation_r87_r81_role_rank_buffer"
    strategy = AiRotationR87R81RoleRankBufferStrategy()
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())
    assert pipeline["transition_cap_rule"] == (
        "one-week positive target exposure capped at 50%"
    )
    assert pipeline["selection_rule"] == "role ranking with entry Top3 and exit Top4 rank buffer"
    assert EconomicRoleR81RoleRankBufferSession.__mro__[1].__name__ == (
        "EconomicRoleR81TransitionCap50Session"
    )


def test_r87_has_unique_registered_identity():
    _require_r87()
    from backtest.fund_rotation.strategies.registry import default_fund_rotation_strategies

    ids = [item.descriptor.id for item in default_fund_rotation_strategies()]
    assert ids.count(DESCRIPTOR.id) == 1


def test_r87_role_ranking_does_not_replace_process_global_rank_scores():
    _require_r87()
    from backtest.fund_rotation.strategies.economic_role_rotation import strategy as role_module

    session = EconomicRoleR81RoleRankBufferSession(
        AiRotationR87R81RoleRankBufferStrategy.config_model()
    )
    original = role_module.rank_scores
    session._rank_roles({})
    assert role_module.rank_scores is original
    assert role_module.rank_scores is original


def test_r87_returned_and_finalized_decision_diagnostics_are_identical(monkeypatch):
    _require_r87()
    from backtest.fund_rotation.contracts import DecisionKind, TargetWeightDecision

    upstream = TargetWeightDecision(
        decision_id="20200131-r81",
        signal_date="20200131",
        action=DecisionKind.SET_TARGETS,
        target_weights={"A": 0.2},
        cash_weight=0.8,
        reason_code="FIXED_SHORT_BOND_UNAVAILABLE",
        diagnostics={"upstream": True},
    )

    def fake_evaluate(self, context):
        return upstream

    monkeypatch.setattr(
        "backtest.fund_rotation.strategies.economic_role_rotation.strategy.EconomicRoleSession.evaluate",
        fake_evaluate,
    )
    session = EconomicRoleR81RoleRankBufferSession.__new__(
        EconomicRoleR81RoleRankBufferSession
    )
    session._previous_weights = {}
    session._decision_log = [{}]
    session._decision_trace = []
    session._role_members = {"seed": []}
    session._week_index = 1
    session._last_role_refresh_week = 0
    session._config = AiRotationR87R81RoleRankBufferStrategy.config_model()
    session._last_rank_buffer_diagnostics = {
        "selected_roles": ["R1", "R2", "R3"],
        "epoch_reset": False,
    }
    session._previous_selected_roles = {"R1", "R2", "R3"}
    session._role_history = []
    session._role_representatives = []
    session._role_diagnostics = []
    session._exclusions = []
    decision = session.evaluate(object())
    finalized = session.finalize()
    artifact_row = next(item for item in finalized.artifacts if item.role == "decisions").payload[0]
    assert artifact_row["diagnostics"] == decision.diagnostics
