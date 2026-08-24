"""Focused behavior tests for the R35 short-gap re-entry overlay."""

from __future__ import annotations

import json
import math

import pytest

from backtest.fund_rotation.strategies.ai_rotation_r35_short_gap_reentry.strategy import (
    DESCRIPTOR,
    AiRotationR35ShortGapReentrySession,
    AiRotationR35ShortGapReentryStrategy,
    apply_short_gap_reentry,
)
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


def _apply(history, targets):
    return apply_short_gap_reentry(history, targets)


def test_first_entry_is_staged_at_half_size():
    targets, cash, staged, full_size, reentries, gaps = _apply(
        [], {"A": 1 / 3}
    )

    assert targets == {"A": pytest.approx(1 / 6)}
    assert cash == pytest.approx(5 / 6)
    assert staged == {"A"}
    assert full_size == set()
    assert reentries == set()
    assert gaps == {}


def test_continuous_target_remains_full_size():
    targets, cash, staged, full_size, reentries, gaps = _apply(
        [{"A": 1 / 3}], {"A": 1 / 3}
    )

    assert targets == {"A": pytest.approx(1 / 3)}
    assert cash == pytest.approx(2 / 3)
    assert staged == set()
    assert full_size == {"A"}
    assert reentries == set()
    assert gaps == {}


@pytest.mark.parametrize(
    ("missing_decisions", "expected_gap"), [(2, 2), (3, 3)]
)
def test_short_gap_reentry_restores_full_size(missing_decisions, expected_gap):
    history = [{"A": 1 / 3}] + [{}] * missing_decisions
    targets, cash, staged, full_size, reentries, gaps = _apply(
        history, {"A": 1 / 3}
    )

    assert targets == {"A": pytest.approx(1 / 3)}
    assert cash == pytest.approx(2 / 3)
    assert staged == set()
    assert full_size == {"A"}
    assert reentries == {"A"}
    assert gaps == {"A": expected_gap}


@pytest.mark.parametrize("missing_decisions", [1, 4, 5])
def test_non_short_gap_reentry_remains_staged(missing_decisions):
    history = [{"A": 1 / 3}] + [{}] * missing_decisions
    targets, cash, staged, full_size, reentries, gaps = _apply(
        history, {"A": 1 / 3}
    )

    assert targets == {"A": pytest.approx(1 / 6)}
    assert cash == pytest.approx(5 / 6)
    assert staged == {"A"}
    assert full_size == set()
    assert reentries == set()
    assert gaps == {}


def test_session_start_does_not_read_pre_session_history():
    targets, cash, staged, full_size, reentries, gaps = _apply(
        [], {"A": 1 / 3}
    )

    assert targets["A"] == pytest.approx(1 / 6)
    assert cash == pytest.approx(5 / 6)
    assert staged == {"A"}
    assert full_size == set()
    assert reentries == set()
    assert gaps == {}


def test_codes_track_independently_and_accumulated_weight_is_scaled_once():
    history = [{"A": 1 / 3}, {"B": 1 / 3}, {}]
    targets, cash, staged, full_size, reentries, gaps = _apply(
        history, {"A": 2 / 3, "B": 1 / 3}
    )

    assert targets == {
        "A": pytest.approx(2 / 3),
        "B": pytest.approx(1 / 6),
    }
    assert cash == pytest.approx(1 / 6)
    assert staged == {"B"}
    assert full_size == {"A"}
    assert reentries == {"A"}
    assert gaps == {"A": 2}
    assert sum(targets.values()) + cash == pytest.approx(1.0)


def test_empty_decisions_advance_gap_and_invalid_weights_do_not_count():
    history = [{}, {}, {}, {"A": float("nan")}]
    targets, cash, staged, full_size, reentries, gaps = _apply(
        history, {"A": 1 / 3, "BAD": float("inf"), "ZERO": 0.0}
    )

    assert targets == {"A": pytest.approx(1 / 6)}
    assert cash == pytest.approx(5 / 6)
    assert staged == {"A"}
    assert full_size == set()
    assert reentries == set()
    assert gaps == {}
    assert all(math.isfinite(value) for value in targets.values())
    json.dumps(
        {
            "target_weights": targets,
            "cash_weight": cash,
            "staged": sorted(staged),
            "full_size": sorted(full_size),
            "reentries": sorted(reentries),
            "gaps": gaps,
        },
        allow_nan=False,
    )


def test_ordering_and_empty_target_are_deterministic():
    first = _apply(
        [{"B": 1 / 3}, {}, {}], {"Z": 1 / 3, "A": 1 / 3}
    )
    second = _apply(
        [{"B": 1 / 3}, {}, {}], {"A": 1 / 3, "Z": 1 / 3}
    )

    assert first == second
    empty = _apply([{"A": 1 / 3}], {})
    assert empty[0] == {}
    assert empty[1] == pytest.approx(1.0)
    assert empty[2:] == (set(), set(), set(), {})


def test_session_uses_base_target_history_and_publishes_r35_artifacts(monkeypatch):
    base_targets = iter(
        [
            {"A": 1 / 3},
            {},
            {},
            {"A": 1 / 3},
        ]
    )

    def fake_r11_evaluate(self, context):
        return TargetWeightDecision(
            decision_id=f"{context.signal_date}-ai_rotation_r11_persist_geom",
            signal_date=context.signal_date,
            action=DecisionKind.SET_TARGETS,
            target_weights=next(base_targets),
            cash_weight=1.0,
            quality_status=QualityStatus.VALID,
        )

    monkeypatch.setattr(AiRotationR11PersistGeomSession, "evaluate", fake_r11_evaluate)
    session = AiRotationR35ShortGapReentrySession(CorrelationRepresentativeConfig())
    decisions = [
        session.evaluate(
            StrategyDecisionContext(signal_date=f"2020-01-0{index}", data_view=object())
        )
        for index in range(1, 5)
    ]

    assert decisions[0].target_weights == {"A": pytest.approx(1 / 6)}
    assert decisions[3].target_weights == {"A": pytest.approx(1 / 3)}
    assert decisions[3].decision_id == "2020-01-04-ai_rotation_r35_short_gap_reentry"
    assert decisions[3].diagnostics["short_gap_reentry_codes"] == ["A"]
    assert decisions[3].diagnostics["short_gap_reentry_gaps"] == {"A": 2}
    assert decisions[3].diagnostics["short_gap_reentry_gap_decisions"] == [2, 3]


def test_registered_identity_and_pipeline_are_r35_specific():
    strategy = AiRotationR35ShortGapReentryStrategy()
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())

    assert DESCRIPTOR.id == "ai_rotation_r35_short_gap_reentry"
    assert "short gap" in str(pipeline).lower()
    assert "ai_rotation_r34_staged_reentry" not in str(pipeline)
