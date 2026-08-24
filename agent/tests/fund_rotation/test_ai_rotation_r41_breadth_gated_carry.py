"""Focused behavior tests for the R41 breadth-gated incumbent carry overlay."""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping

import pytest

from backtest.fund_rotation.contracts import (
    DecisionKind,
    QualityStatus,
    StrategyDecisionContext,
    TargetWeightDecision,
)
from backtest.fund_rotation.strategies.ai_rotation_r11_persist_geom.strategy import (
    AiRotationR11PersistGeomSession,
)
from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import (
    apply_incumbent_carry,
)
from backtest.fund_rotation.strategies.correlation_representative.config import (
    CorrelationRepresentativeConfig,
)

try:
    from backtest.fund_rotation.strategies.ai_rotation_r41_breadth_gated_carry.strategy import (
        DESCRIPTOR,
        AiRotationR41BreadthGatedCarrySession,
        AiRotationR41BreadthGatedCarryStrategy,
        apply_breadth_gated_carry,
    )
    _R41_IMPORT_ERROR = None
except ImportError as exc:  # Red phase: the new strategy does not exist yet.
    DESCRIPTOR = None
    AiRotationR41BreadthGatedCarrySession = None
    AiRotationR41BreadthGatedCarryStrategy = None
    apply_breadth_gated_carry = None
    _R41_IMPORT_ERROR = exc


class _DuplicateItemsMapping(Mapping[str, float]):
    def __init__(self, items: list[tuple[str, float]]) -> None:
        self._items = items

    def __getitem__(self, key: str) -> float:
        for item_key, value in self._items:
            if item_key == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter({key for key, _ in self._items})

    def __len__(self) -> int:
        return len({key for key, _ in self._items})

    def items(self):
        return list(self._items)


def _require_r41() -> None:
    assert apply_breadth_gated_carry is not None, (
        f"R41 strategy is not implemented: {_R41_IMPORT_ERROR}"
    )


def test_single_incumbent_with_staged_target_keeps_r34_staged_output():
    _require_r41()

    targets, cash, staged, incumbents, carry_applied = apply_breadth_gated_carry(
        {"HELD": 1 / 3},
        {"HELD": 1 / 3, "NEW": 1 / 6},
    )

    assert targets == {
        "HELD": pytest.approx(1 / 3),
        "NEW": pytest.approx(1 / 6),
    }
    assert cash == pytest.approx(1 / 2)
    assert staged == {"NEW"}
    assert incumbents == set()
    assert carry_applied is False


def test_multiple_incumbents_are_exactly_r39():
    _require_r41()

    actual = apply_breadth_gated_carry(
        {"A": 1 / 6, "B": 1 / 6},
        {"A": 1 / 6, "B": 1 / 3, "NEW": 1 / 6},
    )
    expected = apply_incumbent_carry(
        {"A": 1 / 6, "B": 1 / 6},
        {"A": 1 / 6, "B": 1 / 3, "NEW": 1 / 6},
    )

    assert actual[:4] == expected
    assert actual[4] is True


@pytest.mark.parametrize(
    ("previous_weights", "staged_target_weights"),
    [
        ({"HELD": 1 / 3}, {"HELD": 1 / 3}),
        ({}, {"NEW": 1 / 6}),
        ({"OLD": 1 / 3}, {"NEW": 1 / 6}),
        (None, {"NEW": 1 / 6}),
        ({"HELD": math.nan}, {"HELD": 1 / 3, "NEW": 1 / 6}),
        ({"HELD": 1 / 3}, {"NEW": -1 / 6}),
        (_DuplicateItemsMapping([("HELD", 1 / 3), ("HELD", 1 / 6)]), {"NEW": 1 / 6}),
    ],
)
def test_other_and_invalid_states_are_exactly_r39(
    previous_weights, staged_target_weights
):
    _require_r41()

    actual = apply_breadth_gated_carry(previous_weights, staged_target_weights)
    expected = apply_incumbent_carry(previous_weights, staged_target_weights)

    assert actual[:4] == expected
    assert actual[4] is False


def test_mapping_order_does_not_change_breadth_gate():
    _require_r41()

    forward = apply_breadth_gated_carry(
        {"HELD": 1 / 3},
        {"HELD": 1 / 3, "NEW": 1 / 6},
    )
    reverse = apply_breadth_gated_carry(
        {"HELD": 1 / 3},
        {"NEW": 1 / 6, "HELD": 1 / 3},
    )

    assert reverse == forward


def test_extreme_integer_weight_fails_closed_without_overflow():
    _require_r41()

    result = apply_breadth_gated_carry(
        {"HELD": 10**10000},
        {"HELD": 1 / 3, "NEW": 1 / 6},
    )

    assert result == ({"HELD": pytest.approx(1 / 3), "NEW": pytest.approx(1 / 6)}, 1 / 2, {"NEW"}, set(), False)


def test_session_cancels_single_incumbent_carry_and_uses_r41_identity(monkeypatch):
    _require_r41()

    def fake_r11_evaluate(self, context):
        return TargetWeightDecision(
            decision_id=f"{context.signal_date}-ai_rotation_r11_persist_geom",
            signal_date=context.signal_date,
            action=DecisionKind.SET_TARGETS,
            target_weights={"HELD": 1 / 3, "NEW": 1 / 3},
            cash_weight=0.0,
            quality_status=QualityStatus.VALID,
        )

    monkeypatch.setattr(AiRotationR11PersistGeomSession, "evaluate", fake_r11_evaluate)
    session = AiRotationR41BreadthGatedCarrySession(CorrelationRepresentativeConfig())
    session._previous_weights = {"HELD": 1 / 3}

    decision = session.evaluate(
        StrategyDecisionContext(signal_date="2020-01-01", data_view=object())
    )

    assert decision.target_weights == {
        "HELD": pytest.approx(1 / 3),
        "NEW": pytest.approx(1 / 6),
    }
    assert decision.cash_weight == pytest.approx(1 / 2)
    assert decision.decision_id == (
        "2020-01-01-ai_rotation_r41_breadth_gated_carry"
    )
    assert not decision.reason_code.endswith("INCUMBENT_CARRY")
    assert decision.diagnostics["breadth_gate_incumbent_codes"] == ["HELD"]
    assert decision.diagnostics["breadth_gate_triggered"] is True
    assert decision.diagnostics["incumbent_carry_codes"] == []
    assert math.isfinite(decision.cash_weight)


def test_session_extreme_integer_previous_weight_fails_closed(monkeypatch):
    _require_r41()

    def fake_r11_evaluate(self, context):
        return TargetWeightDecision(
            decision_id=f"{context.signal_date}-ai_rotation_r11_persist_geom",
            signal_date=context.signal_date,
            action=DecisionKind.SET_TARGETS,
            target_weights={"HELD": 1 / 3, "NEW": 1 / 3},
            cash_weight=0.0,
            quality_status=QualityStatus.VALID,
        )

    monkeypatch.setattr(AiRotationR11PersistGeomSession, "evaluate", fake_r11_evaluate)
    session = AiRotationR41BreadthGatedCarrySession(CorrelationRepresentativeConfig())
    session._previous_weights = {"HELD": 10**10000}

    decision = session.evaluate(
        StrategyDecisionContext(signal_date="2020-01-01", data_view=object())
    )

    assert math.isfinite(decision.cash_weight)
    assert decision.target_weights == {
        "HELD": pytest.approx(1 / 3),
        "NEW": pytest.approx(1 / 6),
    }


def test_registered_identity_and_pipeline_are_r41_specific():
    _require_r41()

    strategy = AiRotationR41BreadthGatedCarryStrategy()
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())

    assert DESCRIPTOR.id == "ai_rotation_r41_breadth_gated_carry"
    assert "breadth" in str(pipeline).lower()
    assert "incumbent" in str(pipeline).lower()
