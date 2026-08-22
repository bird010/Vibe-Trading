"""Focused behavior tests for the round 11 persistent-geometry challenger."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from backtest.fund_rotation.causal_data import CausalDataView
from backtest.fund_rotation.contracts import (
    DecisionKind,
    FundRotationStrategy,
    StrategyDecisionContext,
    StrategyInitializationContext,
)
from backtest.fund_rotation.scoring.cluster_momentum import ClusterMomentumScoreModel
from backtest.fund_rotation.scoring.contracts import rank_scores
from backtest.fund_rotation.strategies.ai_rotation_r11_persist_geom.strategy import (
    AiRotationR11PersistGeomStrategy,
    compute_persist_geom_scores,
    persistent_geometric_score,
)
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    build_slot_weights,
)
from tests.fund_rotation.test_correlation_representative_strategy import (
    _market_frames,
    _small_config,
)


def _ranked(returns: pd.DataFrame, clusters: dict[str, int]):
    scores, current, lagged, geometric = compute_persist_geom_scores(
        returns,
        clusters,
        momentum_window=4,
    )
    members: dict[int, list[str]] = {}
    for code, cluster_id in clusters.items():
        members.setdefault(cluster_id, []).append(code)
    return rank_scores(scores, cluster_members=members), scores, current, lagged, geometric


def test_geometric_score_uses_two_growth_factors_exactly():
    score = persistent_geometric_score(0.20, 0.05)

    assert score.value == pytest.approx(math.sqrt(1.20 * 1.05) - 1.0)
    assert score.eligible is True
    assert score.components == {
        "current_momentum": pytest.approx(0.20),
        "lagged_momentum": pytest.approx(0.05),
        "persistent_geometric_momentum": pytest.approx(score.value),
    }


def test_geometric_score_ranks_persistent_strength_over_current_spike():
    returns = pd.DataFrame(
        {
            "SPIKE": (0.01, 0.01, 0.01, 0.01, 0.50),
            "PERSIST": (0.10, 0.10, 0.10, 0.10, 0.05),
        }
    )

    ranked, scores, current, _, _ = _ranked(
        returns,
        {"SPIKE": 1, "PERSIST": 2},
    )

    assert current[1] > current[2]
    assert scores[2].value > scores[1].value
    assert ranked == [2, 1]


@pytest.mark.parametrize("current, lagged", [(0.0, 0.1), (-0.01, 0.1), (0.1, 0.0), (0.1, -0.01), (float("nan"), 0.1), (0.1, float("inf"))])
def test_both_momentums_must_be_finite_and_strictly_positive(current, lagged):
    score = persistent_geometric_score(current, lagged)

    assert score.value is None
    assert score.eligible is False


def test_equal_current_and_lagged_scores_match_champion_ranking():
    returns = pd.DataFrame(
        {code: (value,) * 5 for code, value in {"A": 0.04, "B": 0.03, "C": 0.02, "D": 0.01}.items()}
    )
    clusters = {"A": 1, "B": 2, "C": 3, "D": 4}
    ranked, scores, _, _, _ = _ranked(returns, clusters)
    champion_scores = ClusterMomentumScoreModel().score(returns.iloc[-4:], clusters, 4)
    members = {1: ["A"], 2: ["B"], 3: ["C"], 4: ["D"]}

    assert ranked == rank_scores(champion_scores, cluster_members=members)
    assert [cluster_id for cluster_id in ranked[:3]] == [1, 2, 3]
    assert all(score.eligible for score in scores.values())


def test_round_05_current_momentum_ranking_differs_from_round_11_geometry():
    returns = pd.DataFrame(
        {
            "SPIKE": (0.01, 0.01, 0.01, 0.01, 0.50),
            "PERSIST": (0.10, 0.10, 0.10, 0.10, 0.05),
        }
    )
    clusters = {"SPIKE": 1, "PERSIST": 2}
    ranked, _, current, _, _ = _ranked(returns, clusters)
    champion_ranked = rank_scores(
        ClusterMomentumScoreModel().score(returns.iloc[-4:], clusters, 4),
        cluster_members={1: ["SPIKE"], 2: ["PERSIST"]},
    )

    assert champion_ranked == [1, 2]
    assert ranked == [2, 1]
    assert current[1] > current[2]


def test_missing_window_is_ineligible_and_serializable_as_null():
    returns = pd.DataFrame({"A": (0.01, 0.02, 0.03, 0.04)})
    _, scores, current, lagged, geometric = _ranked(returns, {"A": 1})

    assert scores[1].value is None
    assert scores[1].eligible is False
    assert current[1] is None
    assert lagged[1] is None
    assert geometric[1] is None


def test_fixed_slots_and_vacant_representative_do_not_reweight():
    weights, filled, vacant, cash = build_slot_weights(
        [1, 2, 3],
        {1: "A", 2: None, 3: "C"},
        top_n=3,
    )

    assert weights == {"A": pytest.approx(1 / 3), "C": pytest.approx(1 / 3)}
    assert filled == [1, 3]
    assert vacant == [2]
    assert cash == pytest.approx(1 / 3)


def test_epoch_uses_only_current_frozen_members():
    returns = pd.DataFrame(
        {
            "OLD": (0.01, 0.01, 0.01, 0.01, 0.01),
            "NEW": (0.01, 0.01, 0.01, 0.01, 0.08),
            "STABLE": (0.01, 0.01, 0.01, 0.01, 0.04),
        }
    )
    baseline = compute_persist_geom_scores(
        returns,
        {"NEW": 1, "STABLE": 2},
        4,
    )
    old_changed = compute_persist_geom_scores(
        returns.assign(OLD=(-0.9, 0.8, -0.8, 0.7, -0.6)),
        {"NEW": 1, "STABLE": 2},
        4,
    )

    assert old_changed[0] == baseline[0]
    assert old_changed[1:] == baseline[1:]


def test_ties_are_deterministic_across_dataframe_and_mapping_order():
    first = pd.DataFrame({"B": (0.01,) * 5, "A": (0.01,) * 5})
    second = first[["A", "B"]]
    first_ranked, _, _, _, _ = _ranked(first, {"B": 2, "A": 1})
    second_ranked, _, _, _, _ = _ranked(second, {"A": 1, "B": 2})

    assert first_ranked == second_ranked == [1, 2]


def test_registered_session_is_causal_and_identity_preserving():
    strategy = AiRotationR11PersistGeomStrategy()
    assert isinstance(strategy, FundRotationStrategy)
    config = _small_config()
    fund_daily, fund_adj, dim_fund, codes = _market_frames()
    requirements = strategy.resolve_requirements(config)
    calendar = tuple(sorted(fund_daily["trade_date"].astype(str).unique()))
    signal_date = calendar[requirements.warmup_trade_days]

    def evaluate(daily, adjusted):
        session = strategy.create_session(
            StrategyInitializationContext(run_id="r11-causal", evaluation_calendar=calendar),
            config,
        )
        view = CausalDataView(
            daily,
            adjusted,
            dim_fund,
            requirements,
            pd.Timestamp(signal_date),
            frozenset(codes),
        )
        return session.evaluate(StrategyDecisionContext(signal_date=signal_date, data_view=view))

    future_daily = fund_daily.copy()
    future_daily.loc[
        future_daily["trade_date"].astype(str) > signal_date,
        ["open", "close", "high", "low", "pre_close"],
    ] *= 100.0
    future_adj = fund_adj.copy()
    future_adj.loc[future_adj["trade_date"].astype(str) > signal_date, "adj_factor"] *= 100.0

    before = evaluate(fund_daily, fund_adj)
    after = evaluate(future_daily, future_adj)

    assert before.decision_id.endswith("-ai_rotation_r11_persist_geom")
    assert after.action is before.action
    assert after.target_weights == before.target_weights
    assert after.cash_weight == before.cash_weight
    assert after.diagnostics == before.diagnostics
    assert before.diagnostics["signal_information_cutoff"] == "CLOSE"
    assert before.cash_weight + sum(before.target_weights.values()) == pytest.approx(1.0)


def test_invalid_clustering_path_keeps_new_strategy_identity():
    strategy = AiRotationR11PersistGeomStrategy()
    config = _small_config()
    fund_daily, fund_adj, dim_fund, codes = _market_frames()
    fund_daily.loc[:, ["open", "close", "high", "low", "pre_close"]] = 2.0
    requirements = strategy.resolve_requirements(config)
    calendar = tuple(sorted(fund_daily["trade_date"].astype(str).unique()))
    signal_date = calendar[requirements.warmup_trade_days]
    session = strategy.create_session(
        StrategyInitializationContext(run_id="r11-invalid", evaluation_calendar=calendar),
        config,
    )
    view = CausalDataView(
        fund_daily,
        fund_adj,
        dim_fund,
        requirements,
        pd.Timestamp(signal_date),
        frozenset(codes),
    )

    decision = session.evaluate(
        StrategyDecisionContext(signal_date=signal_date, data_view=view),
    )

    assert decision.action is DecisionKind.INVALID
    assert decision.decision_id.endswith("-ai_rotation_r11_persist_geom")
