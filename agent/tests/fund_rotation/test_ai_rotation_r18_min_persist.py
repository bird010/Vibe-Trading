"""Focused behavior tests for the round 18 weakest-window challenger."""

from __future__ import annotations

import math

import pytest

from backtest.fund_rotation.contracts import FundRotationStrategy
from backtest.fund_rotation.scoring.contracts import rank_scores
from backtest.fund_rotation.strategies.ai_rotation_r18_min_persist.strategy import (
    AiRotationR18MinPersistStrategy,
    compute_min_persist_scores,
    min_persistent_score,
)


def test_minimum_window_is_the_rank_score_not_geometric_or_average():
    scores = compute_min_persist_scores(
        {1: (0.80, 0.10), 2: (0.35, 0.35), 3: (0.30, 0.31)},
        {1: ["A"], 2: ["B"], 3: ["C"]},
    )
    assert scores[1].value == pytest.approx(0.10)
    assert scores[2].value == pytest.approx(0.35)
    assert rank_scores(scores)[0] == 2
    assert scores[1].components["min_persistent_momentum"] == pytest.approx(0.10)


@pytest.mark.parametrize(
    ("current", "lagged"),
    [(0.0, 0.1), (-0.1, 0.1), (0.1, -0.1), (math.nan, 0.1),
     (0.1, math.inf), (None, 0.1)],
)
def test_min_score_rejects_non_finite_missing_or_non_positive_windows(current, lagged):
    score = min_persistent_score(current, lagged)
    assert score.eligible is False
    assert score.value is None
    assert all(value is None or math.isfinite(value) for value in score.components.values())


def test_equal_windows_match_common_score_order_and_tie_break():
    scores = compute_min_persist_scores(
        {1: (0.20, 0.20), 2: (0.20, 0.20), 3: (0.10, 0.10)},
        {1: ["Z"], 2: ["A"], 3: ["C"]},
    )
    assert rank_scores(scores, cluster_members={1: ["Z"], 2: ["A"], 3: ["C"]}) == [2, 1, 3]


def test_registered_strategy_identity_and_pipeline_are_isolated():
    strategy = AiRotationR18MinPersistStrategy()
    assert isinstance(strategy, FundRotationStrategy)
    assert strategy.descriptor.id == "ai_rotation_r18_min_persist"
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())
    assert "min" in pipeline["selection_rule"].lower()
    assert "ai_rotation_r11_persist_geom" not in str(pipeline)
