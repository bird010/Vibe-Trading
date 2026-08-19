"""Strategy Score contract and cluster-momentum parity tests."""

import math

import numpy as np
import pandas as pd

from backtest.fund_rotation.momentum import compute_cluster_momentum, select_top_clusters
from backtest.fund_rotation.scoring.cluster_momentum import ClusterMomentumScoreModel
from backtest.fund_rotation.scoring.contracts import (
    ScoreDirection,
    rank_scores,
    select_top_scores,
)


def test_cluster_momentum_score_model_preserves_existing_values_and_selection():
    returns = pd.DataFrame(
        {
            "A": [0.10, 0.00, 0.10],
            "B": [0.00, 0.05, 0.05],
            "C": [-0.01, -0.01, -0.01],
        }
    )
    clusters = {"A": 1, "B": 2, "C": 3}

    legacy_values = compute_cluster_momentum(returns, clusters, 3)
    legacy_selected = select_top_clusters(
        legacy_values, top_n=2, threshold=0.0, cluster_members={1: ["A"], 2: ["B"], 3: ["C"]}
    )
    scored = ClusterMomentumScoreModel().score(returns, clusters, 3)
    selected = select_top_scores(scored, top_n=2, cluster_members={1: ["A"], 2: ["B"], 3: ["C"]})

    assert [scored[cid].value for cid in sorted(scored)] == [legacy_values[cid] for cid in sorted(legacy_values)]
    assert selected == legacy_selected
    assert scored[1].label == "策略得分（周频）"
    assert ClusterMomentumScoreModel.label == "Cluster Momentum"


def test_strategy_score_eligibility_rejects_zero_negative_nan_and_bool():
    model = ClusterMomentumScoreModel()
    scores = model.from_values({1: 0.1, 2: 0.0, 3: -0.1, 4: math.nan})

    assert scores[1].eligible is True
    assert scores[2].eligible is False
    assert scores[3].eligible is False
    assert scores[4].value is None
    assert scores[4].eligible is False
    try:
        model.from_values({5: True})
    except TypeError:
        pass
    else:
        raise AssertionError("bool score must be rejected")


def test_select_top_scores_uses_deterministic_code_tie_break():
    model = ClusterMomentumScoreModel()
    scores = model.from_values({1: 0.5, 2: 0.5, 3: 0.4})

    assert select_top_scores(scores, top_n=2, cluster_members={1: ["B"], 2: ["A"], 3: ["C"]}) == [2, 1]
    assert ScoreDirection.HIGHER_BETTER.value == "HIGHER_BETTER"


def test_ranking_order_is_shared_with_selection_and_uses_member_tie_break():
    model = ClusterMomentumScoreModel()
    scores = model.from_values({10: 0.5, 2: 0.5, 3: 0.4})
    members = {10: ["510050.SH"], 2: ["159915.SZ"], 3: ["510300.SH"]}

    ranked = rank_scores(scores, cluster_members=members)

    assert ranked == [2, 10, 3]
    assert select_top_scores(scores, top_n=2, cluster_members=members) == ranked[:2]


def test_generic_selector_accepts_non_cluster_score_subjects():
    from backtest.fund_rotation.scoring.contracts import StrategyScore

    scores = {
        "etf:A": StrategyScore(value=0.2, eligible=True, subject_id="etf:A"),
        "etf:B": StrategyScore(value=0.4, eligible=True, subject_id="etf:B"),
    }

    assert select_top_scores(scores, top_n=1) == ["etf:B"]
