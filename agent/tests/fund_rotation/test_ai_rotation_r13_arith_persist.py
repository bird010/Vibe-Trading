"""Focused behavior tests for the round 13 arithmetic-persistent challenger."""

from __future__ import annotations

import math

import pytest

from backtest.fund_rotation.contracts import FundRotationStrategy
from backtest.fund_rotation.scoring.contracts import rank_scores
from backtest.fund_rotation.strategies.ai_rotation_r13_arith_persist.strategy import (
    AiRotationR13ArithPersistStrategy,
    arithmetic_persistent_score,
)


def test_arithmetic_score_uses_equal_weight_mean_and_shared_positive_gate():
    score = arithmetic_persistent_score(0.20, 0.05)

    assert score.value == pytest.approx(0.125)
    assert score.eligible is True
    assert score.components["arithmetic_persistent_momentum"] == pytest.approx(0.125)


@pytest.mark.parametrize("current,lagged", [
    (0.0, 0.1), (-0.1, 0.1), (0.1, -0.1), (float("nan"), 0.1),
    (0.1, float("inf")), (None, 0.1),
])
def test_arithmetic_score_invalid_inputs_are_ineligible_and_json_safe(current, lagged):
    score = arithmetic_persistent_score(current, lagged)

    assert score.eligible is False
    assert score.value is None
    assert score.components["arithmetic_persistent_momentum"] is None
    assert all(value is None or math.isfinite(value) for value in score.components.values())


def test_arithmetic_ranking_uses_mean_not_m0_or_geometric_score():
    scores = {
        1: arithmetic_persistent_score(0.30, 0.01),
        2: arithmetic_persistent_score(0.16, 0.16),
    }

    assert scores[1].components["current_momentum"] > scores[2].components["current_momentum"]
    assert scores[2].value > scores[1].value
    assert rank_scores(scores, cluster_members={1: ["A"], 2: ["B"]}) == [2, 1]


def test_registered_strategy_has_round13_identity_and_isolated_pipeline():
    strategy = AiRotationR13ArithPersistStrategy()

    assert isinstance(strategy, FundRotationStrategy)
    assert strategy.descriptor.id == "ai_rotation_r13_arith_persist"
    assert strategy.artifact_roles == (
        "cluster_history", "gates", "representatives", "exclusions", "decisions"
    )
    assert "arithmetic" in strategy.describe_decision_pipeline(
        strategy.config_model()
    )["selection_rule"]
