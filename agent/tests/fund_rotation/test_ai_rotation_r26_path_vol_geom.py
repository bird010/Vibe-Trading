"""Focused behavior tests for round 26 path-volatility momentum."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from backtest.fund_rotation.scoring.contracts import rank_scores
from backtest.fund_rotation.strategies.ai_rotation_r26_path_vol_geom.strategy import (
    compute_path_vol_geom_scores,
    path_volatility_geometric_score,
)


def test_path_score_requires_two_positive_windows_and_complete_eight_week_path():
    for current, lagged, path in [
        (0.0, 0.1, [0.01] * 8),
        (0.1, -0.01, [0.01] * 8),
        (0.1, 0.1, [0.01] * 7),
        (0.1, 0.1, [0.01, math.nan] + [0.01] * 6),
    ]:
        score = path_volatility_geometric_score(current, lagged, path)
        assert score.value is None
        assert score.eligible is False


def test_lower_path_volatility_wins_when_geometric_score_matches():
    low = path_volatility_geometric_score(0.1, 0.1, [0.01] * 8)
    high = path_volatility_geometric_score(0.1, 0.1, [0.0, 0.02] * 4)
    assert low.value > high.value


def test_geometric_score_still_drives_order_when_path_volatility_matches():
    low = path_volatility_geometric_score(0.05, 0.05, [0.01] * 8)
    high = path_volatility_geometric_score(0.10, 0.10, [0.01] * 8)
    assert rank_scores({1: low, 2: high}, cluster_members={1: ["A"], 2: ["B"]}) == [2, 1]


def test_compute_uses_eight_complete_cluster_mean_returns_and_is_json_safe():
    returns = pd.DataFrame({"A": [0.01] * 8, "B": [0.01] * 8, "C": [0.02] * 8})
    scores, current, lagged, volatility = compute_path_vol_geom_scores(
        returns, {"A": 1, "B": 1, "C": 2}, 4
    )
    assert scores[1].eligible and scores[2].eligible
    assert volatility[1] == pytest.approx(0.0)
    assert all(value is None or math.isfinite(float(value)) for score in scores.values() for value in score.components.values() if isinstance(value, (int, float)))


def test_missing_member_or_short_path_is_ineligible():
    returns = pd.DataFrame({"A": [0.01] * 7})
    scores, *_ = compute_path_vol_geom_scores(returns, {"A": 1, "B": 1}, 4)
    assert scores[1].eligible is False


def test_registered_identity_and_pipeline_are_isolated():
    from backtest.fund_rotation.strategies.ai_rotation_r26_path_vol_geom.strategy import (
        AiRotationR26PathVolGeomStrategy, DESCRIPTOR,
    )

    strategy = AiRotationR26PathVolGeomStrategy()
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())
    assert DESCRIPTOR.id == "ai_rotation_r26_path_vol_geom"
    assert "volatility" in str(pipeline).lower()
    assert "ai_rotation_r11_path_vol_geom" not in str(pipeline)
