"""Focused behavior tests for the R37 decelerating full-entry overlay."""

from __future__ import annotations

import math

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
    from backtest.fund_rotation.strategies.ai_rotation_r37_decelerating_full_entry.strategy import (
        DESCRIPTOR,
        AiRotationR37DeceleratingFullEntrySession,
        AiRotationR37DeceleratingFullEntryStrategy,
        apply_decelerating_full_entry,
    )
    _R37_IMPORT_ERROR = None
except ImportError as exc:  # Red phase: the new strategy does not exist yet.
    DESCRIPTOR = None
    AiRotationR37DeceleratingFullEntrySession = None
    AiRotationR37DeceleratingFullEntryStrategy = None
    apply_decelerating_full_entry = None
    _R37_IMPORT_ERROR = exc


def _require_r37():
    assert apply_decelerating_full_entry is not None, (
        f"R37 strategy is not implemented: {_R37_IMPORT_ERROR}"
    )


def test_decelerating_new_target_is_full_size_while_other_new_target_stays_staged():
    _require_r37()
    targets, cash, staged, full_size = apply_decelerating_full_entry(
        {"HELD": 1 / 3},
        {"HELD": 1 / 3, "DECEL": 1 / 6, "ACCEL": 1 / 6},
        {"DECEL": {7}, "ACCEL": {8}},
        {7: 0.04, 8: 0.08},
        {7: 0.06, 8: 0.04},
    )

    assert targets == {
        "HELD": pytest.approx(1 / 3),
        "DECEL": pytest.approx(1 / 3),
        "ACCEL": pytest.approx(1 / 6),
    }
    assert cash == pytest.approx(1 / 6)
    assert staged == {"ACCEL"}
    assert full_size == {"HELD", "DECEL"}


def test_equal_current_and_lagged_momentum_is_the_full_size_boundary():
    _require_r37()
    targets, cash, staged, full_size = apply_decelerating_full_entry(
        {},
        {"NEW": 1 / 6},
        {"NEW": {7}},
        {7: 0.05},
        {7: 0.05},
    )

    assert targets == {"NEW": pytest.approx(1 / 3)}
    assert cash == pytest.approx(2 / 3)
    assert staged == set()
    assert full_size == {"NEW"}


@pytest.mark.parametrize(
    ("cluster_ids", "current", "lagged"),
    [
        ({}, {7: 0.04}, {7: 0.06}),
        ({"NEW": {7, 8}}, {7: 0.04, 8: 0.04}, {7: 0.06, 8: 0.06}),
        ({"NEW": {7}}, {}, {7: 0.06}),
        ({"NEW": {7}}, {7: None}, {7: 0.06}),
        ({"NEW": {7}}, {7: math.nan}, {7: 0.06}),
        ({"NEW": {7}}, {7: math.inf}, {7: 0.06}),
        ({"NEW": {7}}, {7: 0.0}, {7: 0.06}),
        ({"NEW": {7}}, {7: 0.04}, {7: -0.01}),
    ],
)
def test_missing_ambiguous_or_invalid_signal_fails_closed_to_r34_half_size(
    cluster_ids, current, lagged
):
    _require_r37()
    targets, cash, staged, full_size = apply_decelerating_full_entry(
        {}, {"NEW": 1 / 6}, cluster_ids, current, lagged
    )

    assert targets == {"NEW": pytest.approx(1 / 6)}
    assert cash == pytest.approx(5 / 6)
    assert staged == {"NEW"}
    assert full_size == set()


@pytest.mark.parametrize(
    ("cluster_ids", "current", "lagged"),
    [
        (None, {7: 0.04}, {7: 0.06}),
        ({"NEW": {7}}, None, {7: 0.06}),
        ({"NEW": {7}}, {7: 0.04}, None),
    ],
)
def test_non_mapping_signal_inputs_fail_closed_without_raising(
    cluster_ids, current, lagged
):
    _require_r37()
    try:
        targets, cash, staged, full_size = apply_decelerating_full_entry(
            {}, {"NEW": 1 / 6}, cluster_ids, current, lagged
        )
    except Exception as exc:  # pragma: no cover - documents the failure mode
        pytest.fail(f"malformed signal input must fail closed: {exc}")

    assert targets == {"NEW": pytest.approx(1 / 6)}
    assert cash == pytest.approx(5 / 6)
    assert staged == {"NEW"}
    assert full_size == set()


def test_session_uses_signal_close_trace_and_diagnostics_for_r37_overlay(monkeypatch):
    _require_r37()

    def fake_r11_evaluate(self, context):
        self._decision_trace.append(
            {
                "signal_date": context.signal_date,
                "candidates": [
                    {
                        "ts_code": "HELD",
                        "stages": {
                            "cluster_id": 1,
                            "cluster_representative": True,
                        },
                    },
                    {
                        "ts_code": "NEW",
                        "stages": {
                            "cluster_id": 7,
                            "cluster_representative": True,
                        },
                    },
                ],
            }
        )
        return TargetWeightDecision(
            decision_id=f"{context.signal_date}-ai_rotation_r11_persist_geom",
            signal_date=context.signal_date,
            action=DecisionKind.SET_TARGETS,
            target_weights={"HELD": 1 / 3, "NEW": 1 / 3},
            cash_weight=0.0,
            quality_status=QualityStatus.VALID,
            diagnostics={
                "momentum": {"7": 0.04},
                "lagged_momentum": {"7": 0.06},
            },
        )

    monkeypatch.setattr(AiRotationR11PersistGeomSession, "evaluate", fake_r11_evaluate)
    session = AiRotationR37DeceleratingFullEntrySession(
        CorrelationRepresentativeConfig()
    )
    session._previous_weights = {"HELD": 1 / 3}

    decision = session.evaluate(
        StrategyDecisionContext(signal_date="2020-01-01", data_view=object())
    )

    assert decision.target_weights == {
        "HELD": pytest.approx(1 / 3),
        "NEW": pytest.approx(1 / 3),
    }
    assert decision.cash_weight == pytest.approx(1 / 3)
    assert decision.decision_id == (
        "2020-01-01-ai_rotation_r37_decelerating_full_entry"
    )
    assert decision.diagnostics["decelerating_full_entry_codes"] == ["NEW"]
    assert decision.reason_code.endswith("DECELERATING_FULL_ENTRY")
    assert math.isfinite(decision.cash_weight)


def test_registered_identity_and_pipeline_are_r37_specific():
    _require_r37()
    strategy = AiRotationR37DeceleratingFullEntryStrategy()
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())

    assert DESCRIPTOR.id == "ai_rotation_r37_decelerating_full_entry"
    assert "decelerating" in str(pipeline).lower()
    assert "ai_rotation_r34_staged_reentry" not in str(pipeline)
