"""Focused behavior tests for the R40 single-name ceiling overlay."""

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
    from backtest.fund_rotation.strategies.ai_rotation_r40_single_name_ceiling.strategy import (
        DESCRIPTOR,
        AiRotationR40SingleNameCeilingSession,
        AiRotationR40SingleNameCeilingStrategy,
        apply_single_name_ceiling,
    )
    _R40_IMPORT_ERROR = None
except ImportError as exc:  # Red phase: the new strategy does not exist yet.
    DESCRIPTOR = None
    AiRotationR40SingleNameCeilingSession = None
    AiRotationR40SingleNameCeilingStrategy = None
    apply_single_name_ceiling = None
    _R40_IMPORT_ERROR = exc


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


def _require_r40() -> None:
    assert apply_single_name_ceiling is not None, (
        f"R40 strategy is not implemented: {_R40_IMPORT_ERROR}"
    )


def test_caps_r39_single_name_and_returns_excess_as_cash():
    _require_r40()

    targets, cash = apply_single_name_ceiling(
        {"HELD": 2 / 3, "NEW_A": 1 / 6, "NEW_B": 1 / 6},
        0.0,
    )

    assert targets == {
        "HELD": pytest.approx(1 / 2),
        "NEW_A": pytest.approx(1 / 6),
        "NEW_B": pytest.approx(1 / 6),
    }
    assert cash == pytest.approx(1 / 6)
    assert sum(targets.values()) + cash == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("targets", "cash"),
    [
        ({"A": 5 / 12, "B": 5 / 12, "NEW": 1 / 6}, 0.0),
        ({"A": 1 / 2, "B": 1 / 3}, 1 / 6),
        ({"A": 1 / 3, "B": 1 / 6}, 1 / 2),
        ({}, 1.0),
    ],
)
def test_non_triggering_r39_output_is_preserved(targets, cash):
    _require_r40()

    actual_targets, actual_cash = apply_single_name_ceiling(targets, cash)

    assert actual_targets == targets
    assert actual_cash == pytest.approx(cash)


def test_ceiling_does_not_normalize_or_reallocate_to_other_codes():
    _require_r40()

    targets, cash = apply_single_name_ceiling(
        {"A": 0.6, "B": 0.3, "NEW": 0.1},
        0.0,
    )

    assert targets == {
        "A": pytest.approx(0.5),
        "B": pytest.approx(0.3),
        "NEW": pytest.approx(0.1),
    }
    assert cash == pytest.approx(0.1)


def test_mapping_order_does_not_change_ceiling():
    _require_r40()

    forward = apply_single_name_ceiling(
        {"HELD": 2 / 3, "NEW_A": 1 / 6, "NEW_B": 1 / 6},
        0.0,
    )
    reverse = apply_single_name_ceiling(
        {"NEW_B": 1 / 6, "HELD": 2 / 3, "NEW_A": 1 / 6},
        0.0,
    )

    assert reverse == forward


@pytest.mark.parametrize(
    ("targets", "cash"),
    [
        (None, 1.0),
        ({"A": True}, 0.0),
        ({"A": math.nan}, 0.0),
        ({"A": math.inf}, 0.0),
        ({"A": -1 / 3}, 4 / 3),
        ({"A": 1 / 3}, math.nan),
        ({"A": 1 / 3}, -1 / 3),
        ({"A": 1 / 3}, 1 / 3),
        ({"A": 1e308}, 0.0),
        (_DuplicateItemsMapping([("A", 1 / 3), ("A", 1 / 6)]), 1 / 2),
    ],
)
def test_invalid_or_nonconserving_r39_output_fails_closed(targets, cash):
    _require_r40()

    original = targets
    actual_targets, actual_cash = apply_single_name_ceiling(targets, cash)

    if isinstance(original, Mapping):
        assert actual_targets == dict(original.items())
    else:
        assert actual_targets == {}
    if isinstance(cash, float) and math.isnan(cash):
        assert isinstance(actual_cash, float) and math.isnan(actual_cash)
    else:
        assert actual_cash == cash


def test_session_caps_r39_after_signal_close_and_uses_r40_identity(monkeypatch):
    _require_r40()

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
    session = AiRotationR40SingleNameCeilingSession(CorrelationRepresentativeConfig())
    session._previous_weights = {"HELD": 1 / 3}

    decision = session.evaluate(
        StrategyDecisionContext(signal_date="2020-01-01", data_view=object())
    )

    assert decision.target_weights == {
        "HELD": pytest.approx(1 / 2),
        "NEW": pytest.approx(1 / 6),
    }
    assert decision.cash_weight == pytest.approx(1 / 3)
    assert decision.decision_id == "2020-01-01-ai_rotation_r40_single_name_ceiling"
    assert decision.reason_code.endswith("INCUMBENT_CARRY")
    assert decision.diagnostics["single_name_ceiling"] == pytest.approx(1 / 2)
    assert math.isfinite(decision.cash_weight)


def test_registered_identity_and_pipeline_are_r40_specific():
    _require_r40()

    strategy = AiRotationR40SingleNameCeilingStrategy()
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())

    assert DESCRIPTOR.id == "ai_rotation_r40_single_name_ceiling"
    assert "single-name" in str(pipeline).lower()
    assert "incumbent" in str(pipeline).lower()
