"""Focused behavior tests for round 30 endpoint-breadth scoring."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from backtest.fund_rotation.scoring.contracts import rank_scores
from backtest.fund_rotation.strategies.ai_rotation_r30_endpoint_breadth_geom.strategy import (
    DESCRIPTOR,
    endpoint_breadth_geometric_score,
    compute_endpoint_breadth_geom_scores,
)


def test_endpoint_breadth_adjusts_geometric_score_using_only_endpoints():
    score = endpoint_breadth_geometric_score(
        0.10,
        0.10,
        [0.20, -0.20, 0.10],
        [0.10, 0.05, -0.05],
    )
    expected_g = math.sqrt(1.10 * 1.10) - 1.0
    expected_adjustment = math.sqrt((2 / 3) * (2 / 3))
    assert score.eligible is True
    assert score.value == pytest.approx(expected_g * expected_adjustment)
    assert score.components["current_positive_breadth"] == pytest.approx(2 / 3)
    assert score.components["lagged_positive_breadth"] == pytest.approx(2 / 3)


def test_endpoint_breadth_keeps_weekly_mean_then_compound_semantics():
    returns = pd.DataFrame(
        {
            "A": [0.50, -0.20, 0.10, 0.10, 0.10],
            "B": [-0.20, 0.50, 0.10, -0.10, 0.10],
        }
    )
    scores, current, lagged, _ = compute_endpoint_breadth_geom_scores(
        returns, {"A": 1, "B": 1}, {1: ("A", "B")}, 4
    )
    current_expected = (1.0 + 0.15) * (1.0 + 0.10) * (1.0 + 0.00) * (1.0 + 0.10) - 1.0
    lagged_expected = (
        (1.0 + 0.15) * (1.0 + 0.15) * (1.0 + 0.10) * (1.0 + 0.00) - 1.0
    )
    assert current[1] == pytest.approx(current_expected)
    assert lagged[1] == pytest.approx(lagged_expected)
    assert scores[1].components["current_positive_breadth"] == pytest.approx(1.0)
    assert scores[1].components["lagged_positive_breadth"] == pytest.approx(1 / 2)


def test_endpoint_breadth_changes_order_only_when_geometric_score_is_equal():
    first = endpoint_breadth_geometric_score(0.10, 0.10, [0.1, 0.1], [0.1, 0.1])
    second = endpoint_breadth_geometric_score(0.10, 0.10, [0.1, -0.1], [0.1, -0.1])
    ranked = rank_scores({1: first, 2: second}, cluster_members={1: ("A",), 2: ("B",)})
    assert ranked == [1, 2]


@pytest.mark.parametrize(
    "current,lagged,current_members,lagged_members",
    [
        (0.0, 0.1, [0.1], [0.1]),
        (-0.1, 0.1, [0.1], [0.1]),
        (math.nan, 0.1, [0.1], [0.1]),
        (0.1, math.inf, [0.1], [0.1]),
        (0.1, 0.1, [0.1, math.nan], [0.1, 0.1]),
        (0.1, 0.1, [0.1, 0.1], [0.1]),
    ],
)
def test_invalid_momentum_or_endpoint_data_is_ineligible(
    current, lagged, current_members, lagged_members
):
    score = endpoint_breadth_geometric_score(
        current, lagged, current_members, lagged_members
    )
    assert score.eligible is False
    assert score.value is None


def test_missing_window_does_not_shorten_or_fill():
    returns = pd.DataFrame({"A": [0.01] * 4})
    scores, current, lagged, adjustment = compute_endpoint_breadth_geom_scores(
        returns, {"A": 1}, {1: ("A",)}, 4
    )
    assert scores[1].eligible is False
    assert current[1] is None and lagged[1] is None
    assert adjustment[1] is None


def test_descriptor_identity_is_round_30_strategy():
    assert DESCRIPTOR.id == "ai_rotation_r30_endpoint_breadth_geom"
