"""Focused behavior tests for the round 15 weighted-persistence challenger."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from backtest.fund_rotation.strategies.ai_rotation_r15_weighted_persist.strategy import (
    AiRotationR15WeightedPersistStrategy,
    compute_weighted_persist_scores,
    weighted_persistent_score,
)


def test_weighted_windows_use_recent_to_oldest_weights_and_causal_slice():
    returns = pd.DataFrame({"A": [0.01, 0.02, 0.03, 0.04, 0.05]})
    scores, current, lagged, _ = compute_weighted_persist_scores(
        returns, {"A": 1}, 4
    )
    expected_current = math.exp(
        sum(w * math.log1p(r) for w, r in zip((0.4, 0.3, 0.2, 0.1), returns["A"].iloc[1:][::-1]))
    ) - 1.0
    expected_lagged = math.exp(
        sum(w * math.log1p(r) for w, r in zip((0.4, 0.3, 0.2, 0.1), returns["A"].iloc[:4][::-1]))
    ) - 1.0
    assert current[1] == pytest.approx(expected_current)
    assert lagged[1] == pytest.approx(expected_lagged)
    assert scores[1].value == pytest.approx(
        math.sqrt((1 + expected_current) * (1 + expected_lagged)) - 1
    )


@pytest.mark.parametrize("current,lagged", [(0, 0.1), (-0.1, 0.1), (math.nan, 0.1), (math.inf, 0.1), (0.1, None)])
def test_weighted_score_rejects_invalid_or_nonpositive_windows(current, lagged):
    score = weighted_persistent_score(current, lagged)
    assert score.eligible is False
    assert score.value is None
    assert score.components["weighted_persistent_momentum"] is None


def test_strategy_identity_and_pipeline_are_isolated():
    strategy = AiRotationR15WeightedPersistStrategy()
    assert strategy.descriptor.id == "ai_rotation_r15_weighted_persist"
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())
    assert "weighted" in pipeline["selection_rule"].lower()
    assert "0.4" in pipeline["selection_rule"]
