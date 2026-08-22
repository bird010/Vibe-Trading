"""Focused behavior tests for the round 16 rank-consensus challenger."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from backtest.fund_rotation.contracts import FundRotationStrategy
from backtest.fund_rotation.scoring.contracts import rank_scores
from backtest.fund_rotation.strategies.ai_rotation_r16_rank_consensus.strategy import (
    AiRotationR16RankConsensusStrategy,
    compute_rank_consensus_scores,
    rank_consensus_score,
)


def test_rank_consensus_uses_two_independent_ranks_not_numeric_magnitude():
    scores = compute_rank_consensus_scores(
        {1: (0.40, 0.10), 2: (0.20, 0.30), 3: (0.10, 0.20)},
        {1: ["A"], 2: ["B"], 3: ["C"]},
    )
    assert scores[1].components["rank0"] == 1.0
    assert scores[1].components["rank1"] == 3.0
    assert scores[1].value == pytest.approx(2.0)
    assert rank_scores(scores) == [2, 1, 3]


@pytest.mark.parametrize(
    ("current", "lagged"),
    [(0.0, 0.1), (-0.1, 0.1), (0.1, -0.1), (math.nan, 0.1), (0.1, math.inf), (None, 0.1)],
)
def test_rank_consensus_rejects_nonfinite_nonpositive_or_missing_inputs(current, lagged):
    score = rank_consensus_score(current, lagged, rank0=None, rank1=None)
    assert score.eligible is False
    assert score.value is None
    assert all(value is None or math.isfinite(value) for value in score.components.values())


def test_rank_consensus_tie_break_is_minimum_frozen_member_and_input_order_invariant():
    values = {1: (0.20, 0.20), 2: (0.20, 0.20)}
    members = {1: ["B", "Z"], 2: ["A", "Y"]}
    forward = compute_rank_consensus_scores(values, members)
    reverse = compute_rank_consensus_scores(dict(reversed(list(values.items()))), members)
    assert rank_scores(forward, cluster_members=members) == [2, 1]
    assert rank_scores(reverse, cluster_members=members) == [2, 1]


def test_rank_consensus_prefers_consistent_cluster_over_one_window_spike():
    scores = compute_rank_consensus_scores(
        {1: (0.90, 0.01), 2: (0.30, 0.25), 3: (0.20, 0.20)},
        {1: ["A"], 2: ["B"], 3: ["C"]},
    )
    assert scores[2].value < scores[1].value
    assert rank_scores(scores)[0] == 2


def test_equal_window_order_produces_champion_equivalent_selection():
    values = {1: (0.40, 0.30), 2: (0.20, 0.10), 3: (0.05, 0.01)}
    members = {1: ["A"], 2: ["B"], 3: ["C"]}
    assert rank_scores(compute_rank_consensus_scores(values, members)) == [1, 2, 3]


def test_causal_four_week_windows_are_reused_for_rank_consensus():
    returns = pd.DataFrame({"A": [0.01, 0.02, 0.03, 0.04, 0.05]})
    scores, current, lagged, _ = compute_rank_consensus_scores(
        returns, {"A": 1}, 4
    )
    assert current[1] is not None and lagged[1] is not None
    assert scores[1].eligible is True


def test_registered_strategy_identity_and_pipeline_are_isolated():
    strategy = AiRotationR16RankConsensusStrategy()
    assert isinstance(strategy, FundRotationStrategy)
    assert strategy.descriptor.id == "ai_rotation_r16_rank_consensus"
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())
    assert "rank" in pipeline["selection_rule"].lower()
    assert "ai_rotation_r11_persist_geom" not in str(pipeline)
