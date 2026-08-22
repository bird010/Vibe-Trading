"""Focused behavior tests for round 25 representative momentum."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from backtest.fund_rotation.scoring.contracts import rank_scores
from backtest.fund_rotation.strategies.ai_rotation_r11_persist_geom.strategy import (
    persistent_geometric_score,
)
from backtest.fund_rotation.strategies.ai_rotation_r25_rep_persist_geom.strategy import (
    compute_rep_persist_geom_scores,
    representative_persistent_geometric_score,
)


def test_representative_score_requires_two_strictly_positive_complete_windows():
    cases = [
        (0.0, 0.1),
        (0.1, -0.01),
        (float("nan"), 0.1),
        (0.1, float("inf")),
    ]
    for current, lagged in cases:
        score = representative_persistent_geometric_score(current, lagged)
        assert score.value is None
        assert score.eligible is False


def test_representative_signal_ranks_differently_when_cluster_means_match():
    returns = pd.DataFrame(
        {
            "A": [0.10, 0.10, 0.10, 0.10, 0.10],
            "B": [0.01, 0.01, 0.01, 0.01, 0.01],
            "C": [0.08, 0.08, 0.08, 0.08, 0.08],
            "D": [0.03, 0.03, 0.03, 0.03, 0.03],
        }
    )
    scores, *_ = compute_rep_persist_geom_scores(
        returns,
        {"A": 1, "B": 1, "C": 2, "D": 2},
        {1: "B", 2: "C"},
        4,
    )
    assert scores[1].eligible and scores[2].eligible
    assert rank_scores(scores, cluster_members={1: ["A", "B"], 2: ["C", "D"]}) == [2, 1]


def test_equal_representative_signal_matches_champion_score_and_rank():
    returns = pd.DataFrame(
        {
            "A": [0.02, 0.02, 0.02, 0.02, 0.03],
            "B": [0.01, 0.01, 0.01, 0.01, 0.04],
        }
    )
    scores, current, lagged, _ = compute_rep_persist_geom_scores(
        returns, {"A": 1, "B": 2}, {1: "A", 2: "B"}, 4
    )
    for cluster_id in (1, 2):
        champion = persistent_geometric_score(current[cluster_id], lagged[cluster_id])
        assert scores[cluster_id].value == pytest.approx(champion.value)


def test_missing_representative_or_short_window_is_ineligible_and_json_safe():
    returns = pd.DataFrame({"A": [0.01, 0.01, 0.01, 0.01]})
    scores, *_ = compute_rep_persist_geom_scores(
        returns, {"A": 1, "B": 2}, {1: "A", 2: None}, 4
    )
    assert scores[1].value is None
    assert scores[2].value is None
    assert all(
        value is None or math.isfinite(float(value))
        for score in scores.values()
        for value in score.components.values()
        if isinstance(value, (int, float))
    )


def test_registered_identity_and_pipeline_are_isolated():
    from backtest.fund_rotation.strategies.ai_rotation_r25_rep_persist_geom.strategy import (
        AiRotationR25RepPersistGeomStrategy,
        DESCRIPTOR,
    )

    strategy = AiRotationR25RepPersistGeomStrategy()
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())
    assert DESCRIPTOR.id == "ai_rotation_r25_rep_persist_geom"
    assert "representative" in str(pipeline).lower()
    assert "ai_rotation_r11_persist_geom" not in str(pipeline)


def test_insufficient_clustering_data_keeps_round25_decision_identity():
    from backtest.fund_rotation.strategies.ai_rotation_r25_rep_persist_geom.strategy import (
        AiRotationR25RepPersistGeomSession,
        DESCRIPTOR,
    )
    from backtest.fund_rotation.strategies.correlation_representative.config import (
        CorrelationRepresentativeConfig,
    )

    session = AiRotationR25RepPersistGeomSession(CorrelationRepresentativeConfig())
    decision = session._recluster(
        None,
        pd.DataFrame(),
        [],
        set(),
        "2020-01-03",
    )

    assert decision is not None
    assert decision.action.value == "INVALID"
    assert decision.reason_code == "CLUSTERING_DATA_INSUFFICIENT"
    assert decision.decision_id == f"2020-01-03-{DESCRIPTOR.id}"
