"""Focused behavior tests for the round 20 rank-frontloaded challenger."""

from __future__ import annotations

import math

import pytest

from backtest.fund_rotation.contracts import FundRotationStrategy
from backtest.fund_rotation.scoring.contracts import rank_scores
from backtest.fund_rotation.strategies.ai_rotation_r11_persist_geom.strategy import (
    persistent_geometric_score,
)
from backtest.fund_rotation.strategies.ai_rotation_r20_rank_frontload.strategy import (
    SLOT_WEIGHTS,
    AiRotationR20RankFrontloadStrategy,
    build_rank_frontload_slot_weights,
)


def test_only_top_three_get_fixed_frontloaded_weights():
    scores = {
        i: persistent_geometric_score(0.90 - i * 0.05, 0.70 - i * 0.04)
        for i in range(1, 5)
    }
    ranked = rank_scores(
        scores,
        cluster_members={i: [f"c{i}"] for i in range(1, 5)},
    )
    weights, filled, vacant, cash = build_rank_frontload_slot_weights(
        ranked, {i: f"E{i}" for i in range(1, 5)},
    )
    assert ranked[:3] == [1, 2, 3]
    assert weights == {
        "E1": pytest.approx(1 / 2),
        "E2": pytest.approx(1 / 3),
        "E3": pytest.approx(1 / 6),
    }
    assert filled == [1, 2, 3]
    assert vacant == []
    assert cash == pytest.approx(0.0)
    assert "E4" not in weights


@pytest.mark.parametrize(
    ("ranked", "representatives", "expected", "filled", "vacant", "cash"),
    [
        ([1, 2, 3], {1: "E1", 2: "E2", 3: "E3"}, {"E1": .5, "E2": 1/3, "E3": 1/6}, [1, 2, 3], [], 0),
        ([1, 2, 3], {1: None, 2: "E2", 3: "E3"}, {"E2": 1/3, "E3": 1/6}, [2, 3], [1], .5),
        ([1, 2, 3], {1: "E1", 2: None, 3: "E3"}, {"E1": .5, "E3": 1/6}, [1, 3], [2], 1/3),
        ([1, 2, 3], {1: None, 2: None, 3: None}, {}, [], [1, 2, 3], 1.0),
        ([1], {1: "E1"}, {"E1": .5}, [1], [], .5),
        ([], {}, {}, [], [], 1.0),
    ],
)
def test_fixed_slots_never_backfill_or_reweight(
    ranked, representatives, expected, filled, vacant, cash
):
    weights, actual_filled, actual_vacant, actual_cash = (
        build_rank_frontload_slot_weights(ranked, representatives)
    )
    assert weights == {key: pytest.approx(value) for key, value in expected.items()}
    assert actual_filled == filled
    assert actual_vacant == vacant
    assert actual_cash == pytest.approx(cash)
    assert all(weight <= max(SLOT_WEIGHTS) for weight in weights.values())


def test_r11_score_and_identity_are_unchanged_and_isolated():
    score = persistent_geometric_score(0.80, 0.60)
    assert score.eligible is True
    assert score.value == pytest.approx(math.sqrt(1.8 * 1.6) - 1.0)
    assert persistent_geometric_score(0.0, 0.6).eligible is False
    strategy = AiRotationR20RankFrontloadStrategy()
    assert isinstance(strategy, FundRotationStrategy)
    assert strategy.descriptor.id == "ai_rotation_r20_rank_frontload"
    assert strategy.config_model().top_n == 3
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())
    assert "1/2" in pipeline["selection_rule"]
    assert "1/3" in pipeline["selection_rule"]
    assert "1/6" in pipeline["selection_rule"]
