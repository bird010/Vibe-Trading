"""Focused behavior tests for the R39 incumbent-carry overlay."""

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
from backtest.fund_rotation.strategies.correlation_representative.config import (
    CorrelationRepresentativeConfig,
)

try:
    from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import (
        DESCRIPTOR,
        AiRotationR39IncumbentCarrySession,
        AiRotationR39IncumbentCarryStrategy,
        apply_incumbent_carry,
    )
    _R39_IMPORT_ERROR = None
except ImportError as exc:  # Red phase: the new strategy does not exist yet.
    DESCRIPTOR = None
    AiRotationR39IncumbentCarrySession = None
    AiRotationR39IncumbentCarryStrategy = None
    apply_incumbent_carry = None
    _R39_IMPORT_ERROR = exc


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


def _require_r39() -> None:
    assert apply_incumbent_carry is not None, (
        f"R39 strategy is not implemented: {_R39_IMPORT_ERROR}"
    )


def test_released_new_target_weight_is_carried_to_one_incumbent():
    _require_r39()

    targets, cash, staged, incumbents = apply_incumbent_carry(
        {"HELD": 1 / 3},
        {"HELD": 1 / 3, "NEW": 1 / 6},
    )

    assert targets == {
        "HELD": pytest.approx(1 / 2),
        "NEW": pytest.approx(1 / 6),
    }
    assert cash == pytest.approx(1 / 3)
    assert staged == {"NEW"}
    assert incumbents == {"HELD"}


def test_released_weight_is_allocated_to_multiple_incumbents_by_base_weight():
    _require_r39()

    targets, cash, staged, incumbents = apply_incumbent_carry(
        {"A": 1 / 6, "B": 1 / 6},
        {"A": 1 / 6, "B": 1 / 3, "NEW_A": 1 / 6, "NEW_B": 1 / 12},
    )

    assert targets == {
        "A": pytest.approx(1 / 4),
        "B": pytest.approx(1 / 2),
        "NEW_A": pytest.approx(1 / 6),
        "NEW_B": pytest.approx(1 / 12),
    }
    assert cash == pytest.approx(0.0)
    assert staged == {"NEW_A", "NEW_B"}
    assert incumbents == {"A", "B"}
    assert sum(targets.values()) + cash == pytest.approx(1.0)


def test_no_incumbent_or_first_decision_is_exact_r34_baseline():
    _require_r39()

    expected = ({"NEW": pytest.approx(1 / 6)}, pytest.approx(5 / 6), {"NEW"}, set())
    assert apply_incumbent_carry({}, {"NEW": 1 / 6}) == expected
    assert apply_incumbent_carry(
        {"OLD": 1 / 3}, {"NEW": 1 / 6}
    ) == expected


def test_no_new_target_or_zero_denominator_is_exact_r34_baseline():
    _require_r39()

    assert apply_incumbent_carry(
        {"HELD": 1 / 3}, {"HELD": 1 / 3}
    ) == ({"HELD": pytest.approx(1 / 3)}, pytest.approx(2 / 3), set(), set())


@pytest.mark.parametrize(
    ("previous_weights", "staged_target_weights"),
    [
        (None, {"NEW": 1 / 6}),
        ({"HELD": math.nan}, {"HELD": 1 / 3, "NEW": 1 / 6}),
        ({"HELD": math.inf}, {"HELD": 1 / 3, "NEW": 1 / 6}),
        ({"HELD": -1 / 3}, {"HELD": 1 / 3, "NEW": 1 / 6}),
        ({"HELD": 1 / 3}, {"NEW": -1 / 6}),
        (_DuplicateItemsMapping([("HELD", 1 / 3), ("HELD", 1 / 6)]), {"NEW": 1 / 6}),
        ({"HELD": 1 / 3}, _DuplicateItemsMapping([("NEW", 1 / 6), ("NEW", 1 / 12)])),
    ],
)
def test_invalid_or_nonfinite_state_fails_closed_to_r34(
    previous_weights, staged_target_weights
):
    _require_r39()

    expected_targets = dict(staged_target_weights.items())
    previous_for_baseline = (
        previous_weights if isinstance(previous_weights, Mapping) else {}
    )
    expected_cash = max(0.0, 1.0 - sum(expected_targets.values()))
    expected_staged = {
        code
        for code in expected_targets
        if previous_for_baseline.get(code, 0.0) <= 0.0
    }
    expected = (expected_targets, expected_cash, expected_staged, set())
    actual = apply_incumbent_carry(previous_weights, staged_target_weights)

    assert actual[0] == expected[0]
    assert actual[1] == pytest.approx(expected[1])
    assert actual[2:] == expected[2:]


def test_mapping_order_does_not_change_allocation():
    _require_r39()

    forward = apply_incumbent_carry(
        {"A": 1 / 6, "B": 1 / 6},
        {"A": 1 / 6, "B": 1 / 3, "NEW": 1 / 6},
    )
    reverse = apply_incumbent_carry(
        {"B": 1 / 6, "A": 1 / 6},
        {"NEW": 1 / 6, "B": 1 / 3, "A": 1 / 6},
    )

    assert reverse == forward


def test_session_applies_carry_after_r34_and_preserves_causal_identity(monkeypatch):
    _require_r39()

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
    session = AiRotationR39IncumbentCarrySession(CorrelationRepresentativeConfig())
    session._previous_weights = {"HELD": 1 / 3}

    decision = session.evaluate(
        StrategyDecisionContext(signal_date="2020-01-01", data_view=object())
    )

    assert decision.target_weights == {
        "HELD": pytest.approx(1 / 2),
        "NEW": pytest.approx(1 / 6),
    }
    assert decision.cash_weight == pytest.approx(1 / 3)
    assert decision.decision_id == (
        "2020-01-01-ai_rotation_r39_incumbent_carry"
    )
    assert decision.reason_code.endswith("INCUMBENT_CARRY")
    assert decision.diagnostics["incumbent_carry_codes"] == ["HELD"]
    assert math.isfinite(decision.cash_weight)


def test_registered_identity_and_pipeline_are_r39_specific():
    _require_r39()

    strategy = AiRotationR39IncumbentCarryStrategy()
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())

    assert DESCRIPTOR.id == "ai_rotation_r39_incumbent_carry"
    assert "incumbent" in str(pipeline).lower()
    assert "ai_rotation_r34_staged_reentry" not in str(pipeline)
