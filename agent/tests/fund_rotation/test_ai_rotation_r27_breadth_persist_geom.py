"""Focused behavior tests for round 27 breadth-adjusted persistent momentum."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from backtest.fund_rotation.scoring.contracts import rank_scores
from backtest.fund_rotation.strategies.ai_rotation_r27_breadth_persist_geom.strategy import (
    breadth_persistent_geometric_score,
    compute_breadth_persist_geom_scores,
)


def test_score_requires_two_positive_complete_member_windows():
    for current, lagged, current_members, lagged_members in [
        (0.0, 0.1, [0.1, 0.1], [0.1, 0.1]),
        (0.1, -0.1, [0.1, 0.1], [0.1, 0.1]),
        (0.1, 0.1, [0.1, math.nan], [0.1, 0.1]),
        (0.1, 0.1, [0.1], [0.1, 0.1]),
    ]:
        score = breadth_persistent_geometric_score(
            current, lagged, current_members, lagged_members,
        )
        assert score.value is None
        assert score.eligible is False


def test_breadth_breaks_equal_geometric_score_tie():
    broad = breadth_persistent_geometric_score(0.1, 0.1, [0.1, 0.1], [0.1, 0.1])
    narrow = breadth_persistent_geometric_score(0.1, 0.1, [0.2, -0.0], [0.2, -0.0])
    assert broad.value > narrow.value
    assert rank_scores({1: broad, 2: narrow}, cluster_members={1: ["A"], 2: ["B"]}) == [1, 2]


def test_geometric_score_still_drives_order_when_breadth_matches():
    low = breadth_persistent_geometric_score(0.05, 0.05, [0.1, 0.1], [0.1, 0.1])
    high = breadth_persistent_geometric_score(0.10, 0.10, [0.1, 0.1], [0.1, 0.1])
    assert rank_scores({1: low, 2: high}, cluster_members={1: ["A"], 2: ["B"]}) == [2, 1]


def test_compute_uses_current_and_lagged_member_breadth_and_is_json_safe():
    returns = pd.DataFrame({"A": [0.01] * 5, "B": [0.01] * 5, "C": [0.02, 0.02, 0.02, -0.01, -0.01]})
    scores, current, lagged, breadth = compute_breadth_persist_geom_scores(
        returns, {"A": 1, "B": 1, "C": 2}, 4,
    )
    assert scores[1].eligible and scores[2].eligible
    assert breadth[1] == pytest.approx(1.0)
    assert all(
        value is None or math.isfinite(float(value))
        for score in scores.values()
        for value in score.components.values()
        if isinstance(value, (int, float))
    )
    assert current[1] == pytest.approx(lagged[1])


def test_compute_averages_member_compounded_returns_before_scoring():
    returns = pd.DataFrame({
        "A": [0.10, 0.10, 0.10, 0.10, 0.20],
        "B": [0.10, 0.10, 0.10, 0.10, -0.10],
    })
    _, current, lagged, _ = compute_breadth_persist_geom_scores(
        returns, {"A": 1, "B": 1}, 4,
    )
    expected_lagged = (1.10**4 - 1.0)
    expected_current = ((1.10**3 * 1.20 - 1.0) + (1.10**3 * 0.90 - 1.0)) / 2.0
    assert lagged[1] == pytest.approx(expected_lagged)
    assert current[1] == pytest.approx(expected_current)


def test_missing_member_or_short_window_is_ineligible():
    returns = pd.DataFrame({"A": [0.01] * 4})
    scores, *_ = compute_breadth_persist_geom_scores(returns, {"A": 1, "B": 1}, 4)
    assert scores[1].eligible is False


def test_registered_identity_and_pipeline_are_isolated():
    from backtest.fund_rotation.strategies.ai_rotation_r27_breadth_persist_geom.strategy import (
        AiRotationR27BreadthPersistGeomStrategy,
        DESCRIPTOR,
    )

    strategy = AiRotationR27BreadthPersistGeomStrategy()
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())
    assert DESCRIPTOR.id == "ai_rotation_r27_breadth_persist_geom"
    assert "breadth" in str(pipeline).lower()
    assert "ai_rotation_r11_breadth" not in str(pipeline)
