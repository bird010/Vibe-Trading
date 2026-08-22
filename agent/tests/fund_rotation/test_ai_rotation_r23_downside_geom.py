"""Focused behavior tests for the round 23 downside-penalized challenger."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from backtest.fund_rotation.scoring.contracts import rank_scores
from backtest.fund_rotation.strategies.ai_rotation_r11_persist_geom.strategy import (
    persistent_geometric_score,
)
from backtest.fund_rotation.strategies.ai_rotation_r23_downside_geom.strategy import (
    downside_geometric_score,
    compute_downside_geom_scores,
)


def test_downside_score_uses_lower_partial_deviation_penalty():
    score = downside_geometric_score(
        0.20, 0.05, [0.10, -0.02, 0.03, -0.04]
    )
    geometric = math.sqrt(1.20 * 1.05) - 1.0
    downside = math.sqrt((0.02**2 + 0.04**2) / 4.0)
    expected = geometric / (1.0 + downside)
    assert score.value == pytest.approx(expected)
    assert score.value != pytest.approx(geometric)
    assert score.components["downside_semideviation"] == pytest.approx(downside)
    assert score.components["downside_penalized_persistent_momentum"] == pytest.approx(expected)


def test_zero_downside_reduces_exactly_to_champion_geometric_score():
    score = downside_geometric_score(0.12, 0.08, [0.01, 0.0, 0.03, 0.02])
    champion = persistent_geometric_score(0.12, 0.08)
    assert score.value == pytest.approx(champion.value)
    assert score.components["downside_semideviation"] == pytest.approx(0.0)


def test_zero_week_does_not_contribute_to_downside_penalty():
    score = downside_geometric_score(0.1, 0.1, [0.0, 0.02, 0.0, 0.01])
    assert score.components["downside_semideviation"] == pytest.approx(0.0)


@pytest.mark.parametrize(
    "current,lagged,window",
    [
        (0.0, 0.1, [0.01] * 4),
        (-0.01, 0.1, [0.01] * 4),
        (0.1, 0.0, [0.01] * 4),
        (0.1, -0.01, [0.01] * 4),
        (float("nan"), 0.1, [0.01] * 4),
        (0.1, float("inf"), [0.01] * 4),
        (0.1, 0.1, [0.01, float("nan"), 0.01, 0.01]),
    ],
)
def test_ineligible_or_nonfinite_windows_are_json_safe(current, lagged, window):
    score = downside_geometric_score(current, lagged, window)
    assert score.value is None
    assert score.eligible is False
    assert score.components["downside_semideviation"] is None
    assert score.components["downside_penalized_persistent_momentum"] is None


def test_missing_or_short_window_returns_null_score():
    for window in ([], [0.01, 0.02, 0.03]):
        score = downside_geometric_score(0.1, 0.1, window)
        assert score.value is None
        assert score.eligible is False


def test_only_eligible_ranking_score_changes_and_tie_break_remains_deterministic():
    returns = pd.DataFrame(
        {
            "A": (0.04, 0.04, 0.04, 0.04, 0.04),
            "B": (0.04, 0.04, 0.04, 0.04, 0.04),
        }
    )
    scores, _, _, _ = compute_downside_geom_scores(
        returns, {"A": 1, "B": 2}, 4
    )
    assert rank_scores(scores, cluster_members={1: ["A"], 2: ["B"]}) == [1, 2]
    assert all(score.eligible for score in scores.values())


def test_supplied_epoch_mapping_does_not_consume_other_members():
    returns = pd.DataFrame(
        {
            "NEW": (0.01, 0.01, 0.01, 0.01, 0.08),
            "STABLE": (0.01, 0.01, 0.01, 0.01, 0.04),
        }
    )
    baseline = compute_downside_geom_scores(
        returns, {"NEW": 1, "STABLE": 2}, 4
    )
    changed_other_epoch = compute_downside_geom_scores(
        returns, {"NEW": 1, "STABLE": 2, "OLD": 99}, 4
    )
    assert baseline[0][1].value == changed_other_epoch[0][1].value
    assert baseline[0][2].value == changed_other_epoch[0][2].value


def test_signal_window_uses_only_the_supplied_causal_tail():
    returns = pd.DataFrame(
        {
            "A": (0.99, 0.99, 0.99, 0.99, 0.01, -0.02, 0.03, 0.04, 0.05),
        }
    )
    baseline, _, _, _ = compute_downside_geom_scores(returns, {"A": 1}, 4)
    altered_prefix = returns.copy()
    altered_prefix.iloc[:4, 0] = 0.12
    changed, _, _, _ = compute_downside_geom_scores(
        altered_prefix, {"A": 1}, 4
    )
    assert changed[1].value == pytest.approx(baseline[1].value)


def test_registered_identity_and_pipeline_are_isolated():
    from backtest.fund_rotation.strategies.ai_rotation_r23_downside_geom.strategy import (
        AiRotationR23DownsideGeomStrategy,
        DESCRIPTOR,
    )

    strategy = AiRotationR23DownsideGeomStrategy()
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())
    assert DESCRIPTOR.id == "ai_rotation_r23_downside_geom"
    assert pipeline["selection_rule"]
    assert "downside" in str(pipeline).lower()
    assert "ai_rotation_r11_persist_geom" not in str(pipeline)
