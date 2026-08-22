"""Focused behavior tests for the round 21 harmonic-persistence challenger."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from backtest.fund_rotation.scoring.cluster_momentum import ClusterMomentumScoreModel
from backtest.fund_rotation.scoring.contracts import rank_scores
from backtest.fund_rotation.strategies.ai_rotation_r11_persist_geom.strategy import (
    persistent_geometric_score,
)
from backtest.fund_rotation.strategies.ai_rotation_r21_harmonic_persist.strategy import (
    harmonic_persistent_score,
    compute_harmonic_persist_scores,
)


def test_harmonic_score_uses_two_growth_factors_not_geometric_or_arithmetic():
    score = harmonic_persistent_score(0.20, 0.05)
    expected = 2.0 / (1.0 / 1.20 + 1.0 / 1.05) - 1.0

    assert score.value == pytest.approx(expected)
    assert score.value != pytest.approx(math.sqrt(1.20 * 1.05) - 1.0)
    assert score.value != pytest.approx((0.20 + 0.05) / 2.0)
    assert score.eligible is True
    assert score.components["harmonic_persistent_momentum"] == pytest.approx(expected)


def test_equal_windows_reduce_to_the_common_momentum():
    score = harmonic_persistent_score(0.12, 0.12)
    assert score.value == pytest.approx(0.12)


@pytest.mark.parametrize(
    "current,lagged",
    [
        (0.0, 0.1),
        (-0.01, 0.1),
        (0.1, 0.0),
        (0.1, -0.01),
        (float("nan"), 0.1),
        (0.1, float("inf")),
        (None, 0.1),
    ],
)
def test_nonpositive_or_nonfinite_windows_are_ineligible_and_json_safe(
    current, lagged
):
    score = harmonic_persistent_score(current, lagged)
    assert score.value is None
    assert score.eligible is False
    assert score.components["harmonic_persistent_momentum"] is None


def test_missing_window_returns_null_score_for_each_cluster():
    returns = pd.DataFrame({"A": (0.01, 0.02, 0.03, 0.04)})
    scores, current, lagged, harmonic = compute_harmonic_persist_scores(
        returns, {"A": 1}, momentum_window=4
    )
    assert scores[1].value is None
    assert current[1] is None
    assert lagged[1] is None
    assert harmonic[1] is None


def test_only_ranking_score_changes_while_persistent_gate_and_tie_break_remain():
    returns = pd.DataFrame(
        {code: (value,) * 5 for code, value in {"A": 0.04, "B": 0.03}.items()}
    )
    clusters = {"A": 1, "B": 2}
    scores, _, _, _ = compute_harmonic_persist_scores(returns, clusters, 4)
    members = {1: ["A"], 2: ["B"]}
    champion = {
        cluster_id: persistent_geometric_score(0.04, 0.04)
        if cluster_id == 1
        else persistent_geometric_score(0.03, 0.03)
        for cluster_id in (1, 2)
    }
    assert rank_scores(scores, cluster_members=members) == [1, 2]
    assert rank_scores(champion, cluster_members=members) == [1, 2]
    assert all(score.eligible for score in scores.values())


def test_epoch_members_are_consumed_only_from_the_supplied_mapping():
    returns = pd.DataFrame(
        {
            "NEW": (0.01, 0.01, 0.01, 0.01, 0.08),
            "STABLE": (0.01, 0.01, 0.01, 0.01, 0.04),
        }
    )
    baseline = compute_harmonic_persist_scores(
        returns, {"NEW": 1, "STABLE": 2}, 4
    )
    changed_other_epoch = compute_harmonic_persist_scores(
        returns, {"NEW": 1, "STABLE": 2, "OLD": 99}, 4
    )
    assert baseline[0][1].value == changed_other_epoch[0][1].value
    assert baseline[0][2].value == changed_other_epoch[0][2].value
