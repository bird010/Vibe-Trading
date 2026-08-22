"""Focused behavior tests for round 22 path-consistency scoring."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from backtest.fund_rotation.scoring.contracts import rank_scores
from backtest.fund_rotation.strategies.ai_rotation_r11_persist_geom.strategy import (
    persistent_geometric_score,
)
from backtest.fund_rotation.strategies.ai_rotation_r22_path_consistency.strategy import (
    path_consistent_score,
    compute_path_consistency_scores,
)


def test_path_score_is_geometric_persistency_scaled_by_positive_week_ratio():
    score = path_consistent_score(0.20, 0.05, [0.10, -0.02, 0.03, 0.01, 0.02, 0.0, 0.04, 0.01])
    geometric = math.sqrt(1.20 * 1.05) - 1.0
    assert score.value == pytest.approx(geometric * 0.75)
    assert score.value != pytest.approx(geometric)
    assert score.value != pytest.approx((0.20 + 0.05) / 2.0)
    assert score.components["positive_week_ratio"] == pytest.approx(0.75)


def test_all_positive_weeks_reduce_exactly_to_champion_score():
    score = path_consistent_score(0.12, 0.08, [0.01] * 8)
    champion = persistent_geometric_score(0.12, 0.08)
    assert score.value == pytest.approx(champion.value)
    assert score.components["positive_week_ratio"] == pytest.approx(1.0)


@pytest.mark.parametrize("weeks", [[], [float("nan")] * 8, [float("inf")] * 8])
def test_missing_or_nonfinite_path_window_is_json_safe(weeks):
    score = path_consistent_score(0.1, 0.1, weeks)
    assert score.value is None
    assert score.eligible is False
    assert score.components["positive_week_ratio"] is None


def test_path_ratio_uses_only_finite_weekly_cluster_returns():
    score = path_consistent_score(0.1, 0.1, [0.01, -0.01, float("nan"), 0.0])
    assert score.value == pytest.approx(0.1 / 3.0)
    assert score.components["positive_week_ratio"] == pytest.approx(1.0 / 3.0)


def test_zero_week_is_not_positive_and_strict_gate_is_unchanged():
    score = path_consistent_score(0.1, 0.1, [0.0] * 8)
    assert score.value == pytest.approx(0.0)
    assert score.eligible is True
    assert score.components["positive_week_ratio"] == pytest.approx(0.0)

    ineligible = path_consistent_score(0.0, 0.1, [0.1] * 8)
    assert ineligible.value is None
    assert ineligible.eligible is False


def test_scores_use_supplied_cluster_epoch_and_keep_deterministic_tie_break():
    returns = pd.DataFrame({
        "A": (0.04,) * 8,
        "B": (0.04,) * 8,
    })
    scores, _, _, _ = compute_path_consistency_scores(returns, {"A": 1, "B": 2}, 4)
    assert rank_scores(scores, cluster_members={1: ["A"], 2: ["B"]}) == [1, 2]
