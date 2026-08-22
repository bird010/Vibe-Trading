"""Focused behavior tests for the round 19 top-two-with-cash challenger."""

from __future__ import annotations

import math

import pytest

from backtest.fund_rotation.contracts import FundRotationStrategy
from backtest.fund_rotation.scoring.contracts import rank_scores
from backtest.fund_rotation.strategies.ai_rotation_r19_top2_cash.strategy import (
    ACTIVE_SLOT_COUNT,
    AiRotationR19Top2CashStrategy,
    build_top2_cash_slot_weights,
    persistent_geometric_score,
)


def test_top_two_ranking_uses_only_two_fixed_thirds_and_leaves_cash():
    scores = {
        1: persistent_geometric_score(0.80, 0.60),
        2: persistent_geometric_score(0.70, 0.50),
        3: persistent_geometric_score(0.60, 0.40),
        4: persistent_geometric_score(0.50, 0.30),
    }
    ranked = rank_scores(
        scores,
        cluster_members={1: ["c1"], 2: ["c2"], 3: ["c3"], 4: ["c4"]},
    )
    weights, filled, vacant, cash = build_top2_cash_slot_weights(
        ranked, {1: "E1", 2: "E2", 3: "E3", 4: "E4"},
    )
    assert ranked[:2] == [1, 2]
    assert weights == {"E1": pytest.approx(1 / 3), "E2": pytest.approx(1 / 3)}
    assert filled == [1, 2]
    assert vacant == []
    assert cash == pytest.approx(1 / 3)
    assert "E3" not in weights and "E4" not in weights


@pytest.mark.parametrize(
    ("ranked", "representatives", "expected_filled", "expected_vacant", "expected_cash"),
    [
        ([1, 2, 3], {1: "E1", 2: "E2", 3: "E3"}, [1, 2], [], 1 / 3),
        ([1, 2, 3], {1: None, 2: "E2", 3: "E3"}, [2], [1], 2 / 3),
        ([1, 2, 3], {1: "E1", 2: None, 3: "E3"}, [1], [2], 2 / 3),
        ([1, 2, 3], {1: None, 2: None, 3: "E3"}, [], [1, 2], 1.0),
        ([1], {1: "E1"}, [1], [], 2 / 3),
        ([], {}, [], [], 1.0),
    ],
)
def test_top_two_slots_never_backfill_or_reweight(
    ranked, representatives, expected_filled, expected_vacant, expected_cash
):
    weights, filled, vacant, cash = build_top2_cash_slot_weights(
        ranked, representatives,
    )
    assert filled == expected_filled
    assert vacant == expected_vacant
    assert cash == pytest.approx(expected_cash)
    assert all(weight <= 1 / 3 for weight in weights.values())


def test_r11_score_and_strict_positive_gate_are_unchanged():
    score = persistent_geometric_score(0.80, 0.60)
    assert score.eligible is True
    assert score.value == pytest.approx(math.sqrt(1.8 * 1.6) - 1.0)
    assert persistent_geometric_score(0.0, 0.6).eligible is False
    assert persistent_geometric_score(0.8, -0.1).eligible is False


def test_registered_strategy_identity_and_pipeline_are_isolated():
    strategy = AiRotationR19Top2CashStrategy()
    assert isinstance(strategy, FundRotationStrategy)
    assert strategy.descriptor.id == "ai_rotation_r19_top2_cash"
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())
    assert "top 2" in pipeline["selection_rule"].lower()
    assert "ai_rotation_r11_persist_geom" not in str(pipeline)
    assert ACTIVE_SLOT_COUNT == 2
