"""Focused behavior tests for the R38 replacement full-entry overlay."""

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
    from backtest.fund_rotation.strategies.ai_rotation_r38_replacement_full_entry.strategy import (
        DESCRIPTOR,
        AiRotationR38ReplacementFullEntrySession,
        AiRotationR38ReplacementFullEntryStrategy,
        apply_replacement_full_entry,
    )
    _R38_IMPORT_ERROR = None
except ImportError as exc:  # Red phase: the new strategy does not exist yet.
    DESCRIPTOR = None
    AiRotationR38ReplacementFullEntrySession = None
    AiRotationR38ReplacementFullEntryStrategy = None
    apply_replacement_full_entry = None
    _R38_IMPORT_ERROR = exc


def _require_r38():
    assert apply_replacement_full_entry is not None, (
        f"R38 strategy is not implemented: {_R38_IMPORT_ERROR}"
    )


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


def test_replacement_entry_is_full_size_when_a_previous_positive_target_exits():
    _require_r38()

    targets, cash, staged, full_size = apply_replacement_full_entry(
        {"OLD": 1 / 3},
        {"NEW_A": 1 / 6, "NEW_B": 1 / 6},
    )

    assert targets == {
        "NEW_A": pytest.approx(1 / 3),
        "NEW_B": pytest.approx(1 / 3),
    }
    assert cash == pytest.approx(1 / 3)
    assert staged == set()
    assert full_size == {"NEW_A", "NEW_B"}


def test_pure_expansion_and_continuous_holding_keep_r34_sizing():
    _require_r38()

    targets, cash, staged, full_size = apply_replacement_full_entry(
        {"HELD": 1 / 3},
        {"HELD": 1 / 3, "NEW": 1 / 6},
    )

    assert targets == {
        "HELD": pytest.approx(1 / 3),
        "NEW": pytest.approx(1 / 6),
    }
    assert cash == pytest.approx(1 / 2)
    assert staged == {"NEW"}
    assert full_size == {"HELD"}


def test_first_decision_without_previous_state_stays_half_sized():
    _require_r38()

    targets, cash, staged, full_size = apply_replacement_full_entry(
        {},
        {"NEW": 1 / 6},
    )

    assert targets == {"NEW": pytest.approx(1 / 6)}
    assert cash == pytest.approx(5 / 6)
    assert staged == {"NEW"}
    assert full_size == set()


@pytest.mark.parametrize(
    ("previous_weights", "target_weights"),
    [
        (None, {"NEW": 1 / 6}),
        ({"OLD": math.nan}, {"NEW": 1 / 6}),
        ({"OLD": math.inf}, {"NEW": 1 / 6}),
        (_DuplicateItemsMapping([("OLD", 1 / 3), ("OLD", 1 / 6)]), {"NEW": 1 / 6}),
        ({"OLD": 1 / 3}, _DuplicateItemsMapping([("NEW", 1 / 6), ("NEW", 1 / 3)])),
    ],
)
def test_invalid_or_ambiguous_state_fails_closed_to_r34(
    previous_weights, target_weights
):
    _require_r38()

    expected_targets = (
        dict(target_weights.items())
        if isinstance(target_weights, Mapping)
        else {}
    )
    expected_staged = set(expected_targets)
    expected = (
        expected_targets,
        max(0.0, 1.0 - sum(expected_targets.values())),
        expected_staged,
        set(),
    )
    actual = apply_replacement_full_entry(previous_weights, target_weights)

    assert actual[0] == expected[0]
    assert actual[1] == expected[1]
    assert actual[2] == expected[2]
    assert actual[3] == expected[3]


def test_session_applies_replacement_rule_after_r34_and_keeps_causal_identity(monkeypatch):
    _require_r38()

    def fake_r11_evaluate(self, context):
        return TargetWeightDecision(
            decision_id=f"{context.signal_date}-ai_rotation_r11_persist_geom",
            signal_date=context.signal_date,
            action=DecisionKind.SET_TARGETS,
            target_weights={"NEW": 1 / 3},
            cash_weight=0.0,
            quality_status=QualityStatus.VALID,
        )

    monkeypatch.setattr(AiRotationR11PersistGeomSession, "evaluate", fake_r11_evaluate)
    session = AiRotationR38ReplacementFullEntrySession(
        CorrelationRepresentativeConfig()
    )
    session._previous_weights = {"OLD": 1 / 3}

    decision = session.evaluate(
        StrategyDecisionContext(signal_date="2020-01-01", data_view=object())
    )

    assert decision.target_weights == {"NEW": pytest.approx(1 / 3)}
    assert decision.cash_weight == pytest.approx(2 / 3)
    assert decision.decision_id == (
        "2020-01-01-ai_rotation_r38_replacement_full_entry"
    )
    assert decision.diagnostics["replacement_full_entry_codes"] == ["NEW"]
    assert math.isfinite(decision.cash_weight)


def test_registered_identity_and_pipeline_are_r38_specific():
    _require_r38()

    strategy = AiRotationR38ReplacementFullEntryStrategy()
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())

    assert DESCRIPTOR.id == "ai_rotation_r38_replacement_full_entry"
    assert "replacement" in str(pipeline).lower()
    assert "ai_rotation_r34_staged_reentry" not in str(pipeline)
