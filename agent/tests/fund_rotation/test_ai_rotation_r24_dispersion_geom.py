"""Focused behavior tests for the round 24 dispersion-penalized challenger."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from backtest.fund_rotation.scoring.contracts import rank_scores
from backtest.fund_rotation.strategies.ai_rotation_r11_persist_geom.strategy import (
    persistent_geometric_score,
)
from backtest.fund_rotation.strategies.ai_rotation_r24_dispersion_geom.strategy import (
    dispersion_geometric_score,
    compute_dispersion_geom_scores,
)


def test_dispersion_penalty_uses_population_std_of_member_compound_returns():
    score = dispersion_geometric_score(
        0.20, 0.05,
        [[0.10, 0.10, 0.10, 0.10], [0.02, 0.02, 0.02, 0.02]],
    )
    q1 = 1.1**4 - 1.0
    q2 = 1.02**4 - 1.0
    dispersion = math.sqrt(((q1 - (q1 + q2) / 2) ** 2 + (q2 - (q1 + q2) / 2) ** 2) / 2)
    geometric = math.sqrt(1.20 * 1.05) - 1.0
    expected = geometric / (1.0 + dispersion)
    assert score.value == pytest.approx(expected)
    assert score.value != pytest.approx(geometric)
    assert score.components["member_dispersion"] == pytest.approx(dispersion)


def test_zero_dispersion_is_exactly_champion_score():
    score = dispersion_geometric_score(0.12, 0.08, [[0.01] * 4, [0.01] * 4])
    champion = persistent_geometric_score(0.12, 0.08)
    assert score.value == pytest.approx(champion.value)
    assert score.components["member_dispersion"] == pytest.approx(0.0)


def test_high_dispersion_cluster_ranks_below_low_dispersion_cluster():
    returns = pd.DataFrame(
        {
            "LOW1": [0.02, 0.02, 0.02, 0.02, 0.02],
            "LOW2": [0.02, 0.02, 0.02, 0.02, 0.02],
            "HIGH1": [0.08, 0.08, 0.08, 0.08, 0.08],
            "HIGH2": [-0.04, -0.04, -0.04, -0.04, -0.04],
        }
    )
    scores, *_ = compute_dispersion_geom_scores(
        returns,
        {"LOW1": 1, "LOW2": 1, "HIGH1": 2, "HIGH2": 2},
        4,
    )
    assert scores[1].eligible and scores[2].eligible
    assert rank_scores(scores, cluster_members={1: ["LOW1"], 2: ["HIGH1"]}) == [1, 2]


@pytest.mark.parametrize(
    "current,lagged,members",
    [
        (0.0, 0.1, [[0.01] * 4, [0.01] * 4]),
        (0.1, -0.01, [[0.01] * 4, [0.01] * 4]),
        (0.1, 0.1, [[0.01, 0.01, 0.01], [0.01] * 4]),
        (0.1, 0.1, [[0.01, float("nan"), 0.01, 0.01], [0.01] * 4]),
    ],
)
def test_strict_gate_and_complete_member_windows_are_json_safe(current, lagged, members):
    score = dispersion_geometric_score(current, lagged, members)
    assert score.value is None
    assert score.eligible is False
    assert score.components["member_dispersion"] is None


def test_fewer_than_two_finite_member_windows_is_ineligible():
    score = dispersion_geometric_score(0.1, 0.1, [[0.01] * 4])
    assert score.value is None
    assert score.eligible is False


def test_missing_member_is_excluded_from_finite_q_set_when_two_members_remain():
    score = dispersion_geometric_score(
        0.1,
        0.1,
        [[0.01] * 4, [0.02, float("nan"), 0.02, 0.02], [0.03] * 4],
    )
    q1 = 1.01**4 - 1.0
    q3 = 1.03**4 - 1.0
    expected_dispersion = abs(q1 - q3) / 2.0
    assert score.eligible is True
    assert score.components["member_dispersion"] == pytest.approx(expected_dispersion)
    assert score.value == pytest.approx(
        persistent_geometric_score(0.1, 0.1).value / (1.0 + expected_dispersion)
    )


def test_registered_identity_and_pipeline_are_isolated():
    from backtest.fund_rotation.strategies.ai_rotation_r24_dispersion_geom.strategy import (
        AiRotationR24DispersionGeomStrategy,
        DESCRIPTOR,
    )

    strategy = AiRotationR24DispersionGeomStrategy()
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())
    assert DESCRIPTOR.id == "ai_rotation_r24_dispersion_geom"
    assert "dispersion" in str(pipeline).lower()
    assert "ai_rotation_r11_persist_geom" not in str(pipeline)
