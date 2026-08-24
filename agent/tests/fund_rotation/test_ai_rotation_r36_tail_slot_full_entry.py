"""Focused behavior tests for the R36 tail-slot full-entry overlay."""

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
from backtest.fund_rotation.strategies.ai_rotation_r36_tail_slot_full_entry.strategy import (
    DESCRIPTOR,
    AiRotationR36TailSlotFullEntrySession,
    AiRotationR36TailSlotFullEntryStrategy,
    apply_tail_slot_full_entry,
)
from backtest.fund_rotation.strategies.correlation_representative.config import (
    CorrelationRepresentativeConfig,
)


def test_new_rank_three_target_is_full_size_and_other_new_targets_remain_staged():
    targets, cash, staged, full_size = apply_tail_slot_full_entry(
        {"HELD": 1 / 3},
        {"HELD": 1 / 3, "TOP": 1 / 6, "TAIL": 1 / 6},
        {"TOP": 1, "TAIL": 3},
    )

    assert targets == {
        "HELD": pytest.approx(1 / 3),
        "TOP": pytest.approx(1 / 6),
        "TAIL": pytest.approx(1 / 3),
    }
    assert cash == pytest.approx(1 / 6)
    assert staged == {"TOP"}
    assert full_size == {"HELD", "TAIL"}


@pytest.mark.parametrize("rank", [None, float("nan"), float("inf"), -float("inf")])
def test_missing_or_nonfinite_rank_fails_closed_to_r34_half_size(rank):
    targets, cash, staged, full_size = apply_tail_slot_full_entry(
        {},
        {"TAIL": 1 / 6},
        {"TAIL": rank},
    )

    assert targets == {"TAIL": pytest.approx(1 / 6)}
    assert cash == pytest.approx(5 / 6)
    assert staged == {"TAIL"}
    assert full_size == set()


def test_session_uses_r11_trace_rank_and_preserves_r34_identity(monkeypatch):
    def fake_r11_evaluate(self, context):
        self._decision_trace.append(
            {
                "signal_date": context.signal_date,
                "candidates": [
                    {"ts_code": "HELD", "stages": {"rank": 1}},
                    {"ts_code": "TOP", "stages": {"rank": 2}},
                    {"ts_code": "TAIL", "stages": {"rank": 3}},
                ],
            }
        )
        return TargetWeightDecision(
            decision_id=f"{context.signal_date}-ai_rotation_r11_persist_geom",
            signal_date=context.signal_date,
            action=DecisionKind.SET_TARGETS,
            target_weights={"HELD": 1 / 3, "TOP": 1 / 3, "TAIL": 1 / 3},
            cash_weight=0.0,
            quality_status=QualityStatus.VALID,
        )

    monkeypatch.setattr(AiRotationR11PersistGeomSession, "evaluate", fake_r11_evaluate)
    session = AiRotationR36TailSlotFullEntrySession(CorrelationRepresentativeConfig())
    session._previous_weights = {"HELD": 1 / 3}

    decision = session.evaluate(
        StrategyDecisionContext(signal_date="2020-01-01", data_view=object())
    )

    assert decision.target_weights == {
        "HELD": pytest.approx(1 / 3),
        "TOP": pytest.approx(1 / 6),
        "TAIL": pytest.approx(1 / 3),
    }
    assert decision.cash_weight == pytest.approx(1 / 6)
    assert decision.decision_id == (
        "2020-01-01-ai_rotation_r36_tail_slot_full_entry"
    )
    assert decision.diagnostics["tail_slot_full_entry_codes"] == ["TAIL"]
    assert math.isfinite(decision.cash_weight)


def test_registered_identity_and_pipeline_are_r36_specific():
    strategy = AiRotationR36TailSlotFullEntryStrategy()
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())

    assert DESCRIPTOR.id == "ai_rotation_r36_tail_slot_full_entry"
    assert "rank-three" in str(pipeline).lower()
    assert "ai_rotation_r34_staged_reentry" not in str(pipeline)
