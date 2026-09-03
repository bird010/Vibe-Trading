"""TDD contract tests for R86: repaired R81 plus a 50% transition cap."""

from __future__ import annotations

import pytest

from backtest.fund_rotation.contracts import (
    DecisionKind,
    TargetWeightDecision,
)

try:
    from backtest.fund_rotation.strategies.ai_rotation_r86_r81_transition_cap_50.strategy import (
        DESCRIPTOR,
        AiRotationR86R81TransitionCap50Strategy,
        EconomicRoleR81TransitionCap50Session,
        apply_transition_cap,
    )
    _R86_IMPORT_ERROR = None
except ImportError as exc:  # Expected RED state before the package is added.
    DESCRIPTOR = None
    AiRotationR86R81TransitionCap50Strategy = None
    EconomicRoleR81TransitionCap50Session = None
    apply_transition_cap = None
    _R86_IMPORT_ERROR = exc


def _require_r86() -> None:
    assert _R86_IMPORT_ERROR is None, f"R86 package missing: {_R86_IMPORT_ERROR}"


def test_r86_caps_positive_additions_and_releases_excess_to_cash():
    _require_r86()
    adjusted, cash, exposure, capped = apply_transition_cap(
        {"A": 0.5, "B": 0.2}, {"A": 0.0, "B": 0.8, "C": 0.4}, 0.50
    )
    assert exposure == pytest.approx(1.0)
    assert adjusted == {"B": pytest.approx(0.5), "C": pytest.approx(0.2)}
    assert cash == pytest.approx(0.30)
    assert capped is True


def test_r86_does_not_scale_reductions_or_unchanged_positions():
    _require_r86()
    adjusted, cash, exposure, capped = apply_transition_cap(
        {"A": 0.6, "B": 0.2}, {"A": 0.1, "B": 0.2, "C": 0.8}, 0.50
    )
    assert exposure == pytest.approx(0.8)
    assert adjusted["A"] == pytest.approx(0.1)
    assert adjusted["B"] == pytest.approx(0.2)
    assert adjusted["C"] == pytest.approx(0.5)
    assert cash == pytest.approx(0.2)
    assert capped is True


def test_r86_session_preserves_upstream_decision_except_weights_and_diagnostics(
    monkeypatch,
):
    _require_r86()
    upstream = TargetWeightDecision(
        decision_id="20200131-ai_rotation_r81_economic_role_dynamic_rep",
        signal_date="20200131",
        action=DecisionKind.SET_TARGETS,
        target_weights={"A": 0.8, "B": 0.2},
        cash_weight=0.0,
        reason_code="STAGED_REENTRY",
        diagnostics={"upstream": {"selected_roles": ["X"]}},
    )

    def fake_evaluate(self, context):
        return upstream

    monkeypatch.setattr(
        "backtest.fund_rotation.strategies.economic_role_rotation.strategy.EconomicRoleSession.evaluate",
        fake_evaluate,
    )
    session = EconomicRoleR81TransitionCap50Session.__new__(
        EconomicRoleR81TransitionCap50Session
    )
    session._previous_weights = {"A": 0.0, "B": 0.0}
    decision = session.evaluate(object())

    assert decision.signal_date == upstream.signal_date
    assert decision.action is upstream.action
    assert decision.reason_code == upstream.reason_code
    assert decision.diagnostics["upstream"] == upstream.diagnostics["upstream"]
    assert decision.target_weights == {"A": pytest.approx(0.4), "B": pytest.approx(0.1)}
    assert decision.cash_weight == pytest.approx(0.5)
    assert decision.decision_id.endswith("ai_rotation_r86_r81_transition_cap_50")
    assert decision.diagnostics["transition_cap"]["cap"] == pytest.approx(0.5)


def test_r86_has_unique_registered_identity_and_advertises_cap():
    _require_r86()
    assert DESCRIPTOR.id == "ai_rotation_r86_r81_transition_cap_50"
    strategy = AiRotationR86R81TransitionCap50Strategy()
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())
    assert pipeline["transition_cap_rule"] == "one-week positive target exposure capped at 50%"
    from backtest.fund_rotation.strategies.registry import default_fund_rotation_strategies

    ids = [item.descriptor.id for item in default_fund_rotation_strategies()]
    assert ids.count(DESCRIPTOR.id) == 1
