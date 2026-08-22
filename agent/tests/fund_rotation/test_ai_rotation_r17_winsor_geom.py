"""Focused behavior tests for the round 17 winsorized geometric challenger."""

from __future__ import annotations

import math

import pytest

from backtest.fund_rotation.contracts import FundRotationStrategy
from backtest.fund_rotation.scoring.contracts import rank_scores
from backtest.fund_rotation.strategies.ai_rotation_r17_winsor_geom.strategy import (
    AiRotationR17WinsorGeomStrategy,
    compute_winsor_geom_scores,
    winsor_geometric_score,
)


def test_winsorizes_each_window_independently_before_geometric_score():
    scores = compute_winsor_geom_scores(
        {1: (0.01, 0.02), 2: (0.02, 0.03), 3: (0.03, 0.04), 4: (0.04, 0.05),
         5: (0.05, 0.06), 6: (0.06, 0.07), 7: (0.07, 0.08), 8: (0.08, 0.09),
         9: (0.09, 0.10), 10: (0.10, 0.11)},
        {cid: [chr(64 + cid)] for cid in range(1, 11)},
    )
    assert scores[1].components["winsorized_current_momentum"] == pytest.approx(0.019)
    assert scores[10].components["winsorized_current_momentum"] == pytest.approx(0.091)
    assert scores[1].value == pytest.approx(
        math.sqrt((1.019) * (1.029)) - 1.0
    )
    assert rank_scores(scores)[0] == 10


@pytest.mark.parametrize(
    ("current", "lagged"),
    [(0.0, 0.1), (-0.1, 0.1), (0.1, -0.1), (math.nan, 0.1),
     (0.1, math.inf), (None, 0.1)],
)
def test_winsor_score_rejects_invalid_original_momentum(current, lagged):
    score = winsor_geometric_score(current, lagged, current_bounds=(0.0, 1.0), lagged_bounds=(0.0, 1.0))
    assert score.eligible is False
    assert score.value is None
    assert all(value is None or math.isfinite(value) for value in score.components.values())


def test_internal_values_are_unchanged_and_extreme_values_are_capped():
    scores = compute_winsor_geom_scores(
        {1: (0.10, 0.10), 2: (0.20, 0.20), 3: (0.30, 0.30), 4: (9.0, 0.40)},
        {1: ["A"], 2: ["B"], 3: ["C"], 4: ["D"]},
    )
    assert scores[2].components["winsorized_current_momentum"] == pytest.approx(0.20)
    assert scores[4].components["winsorized_current_momentum"] < 9.0
    assert {cid for cid, score in scores.items() if score.eligible} == {1, 2, 3, 4}


def test_registered_strategy_identity_and_pipeline_are_isolated():
    strategy = AiRotationR17WinsorGeomStrategy()
    assert isinstance(strategy, FundRotationStrategy)
    assert strategy.descriptor.id == "ai_rotation_r17_winsor_geom"
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())
    assert "winsor" in pipeline["selection_rule"].lower()
    assert "ai_rotation_r11_persist_geom" not in str(pipeline)
