"""Focused behavior tests for the round 14 median-persistent challenger."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from backtest.fund_rotation.contracts import FundRotationStrategy
from backtest.fund_rotation.scoring.contracts import rank_scores
from backtest.fund_rotation.strategies.ai_rotation_r14_median_persist.strategy import (
    AiRotationR14MedianPersistStrategy, compute_median_persist_scores,
    median_persistent_score,
)


def test_median_uses_four_week_cluster_means_and_geometric_persistent_score():
    frame = pd.DataFrame({"A": [0.01, 0.03, 0.20, 0.05, 0.07], "B": [0.01] * 5})
    scores, current, lagged, _, _ = compute_median_persist_scores(frame, {"A": 1, "B": 1}, 4)
    assert current[1] == pytest.approx(0.035)
    assert lagged[1] == pytest.approx(0.025)
    assert scores[1].value == pytest.approx((1.035 * 1.025) ** 0.5 - 1)


@pytest.mark.parametrize("current,lagged", [(0.0, 0.1), (-0.1, 0.1), (0.1, -0.1), (float("nan"), 0.1), (0.1, float("inf")), (None, 0.1)])
def test_median_score_invalid_inputs_are_ineligible_and_json_safe(current, lagged):
    score = median_persistent_score(current, lagged)
    assert score.eligible is False and score.value is None
    assert all(value is None or math.isfinite(value) for value in score.components.values())


def test_median_score_keeps_geometric_ranking_and_tie_break():
    scores = {1: median_persistent_score(0.30, 0.01), 2: median_persistent_score(0.16, 0.16)}
    assert rank_scores(scores, cluster_members={1: ["A"], 2: ["B"]}) == [2, 1]


def test_registered_strategy_has_round14_identity_and_isolated_pipeline():
    strategy = AiRotationR14MedianPersistStrategy()
    assert isinstance(strategy, FundRotationStrategy)
    assert strategy.descriptor.id == "ai_rotation_r14_median_persist"
    assert strategy.artifact_roles == ("cluster_history", "gates", "representatives", "exclusions", "decisions")
    assert "median" in strategy.describe_decision_pipeline(strategy.config_model())["selection_rule"]
