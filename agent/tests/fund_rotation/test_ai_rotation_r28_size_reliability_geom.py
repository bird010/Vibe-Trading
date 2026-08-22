"""Focused behavior tests for round 28 size-reliability momentum."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from backtest.fund_rotation.scoring.contracts import rank_scores
from backtest.fund_rotation.strategies.ai_rotation_r28_size_reliability_geom.strategy import (
    compute_size_reliability_geom_scores,
    size_reliability_geometric_score,
)


def test_size_weight_preserves_strict_positive_and_finite_gates():
    for current, lagged, count in [
        (0.0, 0.1, 4), (-0.1, 0.1, 4), (0.1, math.nan, 4),
        (0.1, 0.1, 0), (0.1, 0.1, 2.5), (0.1, 0.1, math.inf),
    ]:
        score = size_reliability_geometric_score(current, lagged, count)
        assert score.value is None
        assert score.eligible is False


def test_larger_frozen_member_count_breaks_equal_geometric_score_tie():
    small = size_reliability_geometric_score(0.1, 0.1, 1)
    large = size_reliability_geometric_score(0.1, 0.1, 4)
    assert large.value > small.value
    assert rank_scores({1: small, 2: large}, cluster_members={1: ["A"], 2: ["B"]}) == [2, 1]


def test_geometric_growth_still_drives_order_when_count_matches():
    low = size_reliability_geometric_score(0.05, 0.05, 4)
    high = size_reliability_geometric_score(0.10, 0.10, 4)
    assert rank_scores({1: low, 2: high}, cluster_members={1: ["A"], 2: ["B"]}) == [2, 1]


def test_compute_uses_frozen_count_and_is_causal_and_json_safe():
    returns = pd.DataFrame({
        "A": [0.01] * 5, "B": [0.01] * 5, "C": [0.02] * 5,
    })
    clusters = {"A": 1, "B": 1, "C": 2}
    frozen = {1: ["A", "B"], 2: ["C"]}
    scores, current, lagged, reliability = compute_size_reliability_geom_scores(
        returns, clusters, frozen, 4,
    )
    assert scores[1].eligible and scores[2].eligible
    assert reliability[1] == pytest.approx(2**0.5)
    assert current[1] == pytest.approx(lagged[1])
    for score in scores.values():
        assert all(
            value is None or math.isfinite(float(value))
            for value in score.components.values()
            if isinstance(value, (int, float))
        )


def test_missing_member_data_cannot_be_repaired_by_frozen_count():
    returns = pd.DataFrame({"A": [0.01] * 5})
    scores, current, lagged, reliability = compute_size_reliability_geom_scores(
        returns, {"A": 1, "B": 1}, {1: ["A", "B"]}, 4,
    )
    assert scores[1].eligible is False
    assert current[1] is None and lagged[1] is None
    assert reliability[1] is None


def test_signal_after_cutoff_data_does_not_change_causal_score():
    base = pd.DataFrame({"A": [0.01] * 5, "B": [0.02] * 5})
    changed = base.copy()
    changed.iloc[-1] = [0.50, -0.50]
    args = ({"A": 1, "B": 1}, {1: ["A", "B"]}, 4)
    first = compute_size_reliability_geom_scores(base, *args)
    second = compute_size_reliability_geom_scores(changed, *args)
    # The final row is part of the current signal window, so changing it is
    # intentionally allowed to change the score; an extra future row is not.
    future = pd.concat([base, pd.DataFrame({"A": [0.50], "B": [-0.50]})], ignore_index=True)
    third = compute_size_reliability_geom_scores(future, *args)
    assert first[0][1].value != second[0][1].value
    assert second[0][1].value == third[0][1].value


def test_same_count_matches_persistent_geometric_score():
    from backtest.fund_rotation.strategies.ai_rotation_r11_persist_geom.strategy import persistent_geometric_score

    candidate = size_reliability_geometric_score(0.1, 0.2, 1)
    champion = persistent_geometric_score(0.1, 0.2)
    assert candidate.value == pytest.approx(champion.value)
    assert candidate.eligible == champion.eligible


def test_momentum_matches_champion_weekly_member_mean_then_compound():
    from backtest.fund_rotation.strategies.ai_rotation_r11_persist_geom.strategy import (
        compute_persist_geom_scores,
    )

    returns = pd.DataFrame({
        "A": [0.20, -0.10, 0.03, 0.04, 0.05],
        "B": [-0.10, 0.20, 0.01, 0.02, 0.06],
    })
    clusters = {"A": 1, "B": 1}
    frozen = {1: ["A", "B"]}

    candidate = compute_size_reliability_geom_scores(
        returns, clusters, frozen, 4,
    )
    champion = compute_persist_geom_scores(returns, clusters, 4)

    assert candidate[1][1] == pytest.approx(champion[1][1])
    assert candidate[2][1] == pytest.approx(champion[2][1])
    assert candidate[0][1].value == pytest.approx(champion[0][1].value * math.sqrt(2))


def test_registered_identity_and_pipeline_are_isolated():
    from backtest.fund_rotation.strategies.ai_rotation_r28_size_reliability_geom.strategy import (
        AiRotationR28SizeReliabilityGeomStrategy,
        DESCRIPTOR,
    )

    strategy = AiRotationR28SizeReliabilityGeomStrategy()
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())
    assert DESCRIPTOR.id == "ai_rotation_r28_size_reliability_geom"
    assert "square-root" in str(pipeline).lower()
    assert "ai_rotation_r11_size" not in str(pipeline)
