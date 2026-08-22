"""Focused behavior tests for the round 12 non-decay challenger."""

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
    compute_persist_geom_scores,
)
from backtest.fund_rotation.strategies.ai_rotation_r12_nondecay_geom.strategy import (
    AiRotationR12NondecayGeomStrategy,
    compute_nondecay_geom_scores,
    nondecay_geometric_score,
)
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    build_slot_weights,
)
from tests.fund_rotation.test_correlation_representative_strategy import (
    _market_frames,
    _small_config,
)


def _ranked(returns: pd.DataFrame, clusters: dict[str, int]):
    scores, current, lagged, delta, geometric = compute_nondecay_geom_scores(
        returns,
        clusters,
        momentum_window=4,
    )
    members: dict[int, list[str]] = {}
    for code, cluster_id in clusters.items():
        members.setdefault(cluster_id, []).append(code)
    return (
        rank_scores(scores, cluster_members=members),
        scores,
        current,
        lagged,
        delta,
        geometric,
    )


def test_nondecay_score_uses_round11_geometric_formula_and_delta_gate():
    score = nondecay_geometric_score(0.20, 0.05)

    assert score.value == pytest.approx(math.sqrt(1.20 * 1.05) - 1.0)
    assert score.eligible is True
    assert score.components["nondecay_delta"] == pytest.approx(0.15)


@pytest.mark.parametrize(
    ("current", "lagged", "eligible"),
    [
        (0.20, 0.05, True),
        (0.05, 0.05, True),
        (0.05, 0.20, False),
        (0.0, 0.05, False),
        (0.05, 0.0, False),
        (-0.01, 0.05, False),
        (0.05, -0.01, False),
        (float("nan"), 0.05, False),
        (0.05, float("inf"), False),
    ],
)
def test_nondecay_gate_handles_strict_positive_and_equality_boundaries(
    current, lagged, eligible
):
    score = nondecay_geometric_score(current, lagged)

    assert score.eligible is eligible
    assert (score.value is not None) is eligible


def test_nondecay_ranking_uses_geometric_score_not_delta():
    scores = {
        1: nondecay_geometric_score(0.20, 0.01),
        2: nondecay_geometric_score(0.12, 0.10),
    }
    ranked = rank_scores(
        scores,
        cluster_members={1: ["A"], 2: ["B"]},
    )

    assert all(score.eligible for score in scores.values())
    assert scores[1].components["nondecay_delta"] > scores[2].components["nondecay_delta"]
    assert scores[2].value > scores[1].value
    assert ranked == [2, 1]


def test_round11_equivalence_when_every_persistent_cluster_is_nondecaying():
    returns = pd.DataFrame(
        {
            "A": (0.01, 0.01, 0.01, 0.01, 0.01),
            "B": (0.02, 0.02, 0.02, 0.02, 0.02),
            "C": (0.03, 0.03, 0.03, 0.03, 0.03),
        }
    )
    clusters = {"A": 1, "B": 2, "C": 3}
    r11 = compute_persist_geom_scores(returns, clusters, 4)
    r12 = compute_nondecay_geom_scores(returns, clusters, 4)

    for cluster_id in clusters.values():
        assert r12[0][cluster_id].value == pytest.approx(r11[0][cluster_id].value)
        assert r12[0][cluster_id].eligible is r11[0][cluster_id].eligible
    assert r12[1] == r11[1]
    assert r12[2] == r11[2]
    assert r12[4] == r11[3]
    assert rank_scores(r12[0], cluster_members={1: ["A"], 2: ["B"], 3: ["C"]}) == rank_scores(
        r11[0], cluster_members={1: ["A"], 2: ["B"], 3: ["C"]}
    )


def test_missing_or_short_window_is_ineligible_and_json_safe():
    returns = pd.DataFrame({"A": (0.01, 0.02, 0.03, 0.04)})
    _, scores, current, lagged, delta, geometric = _ranked(returns, {"A": 1})

    assert scores[1].value is None
    assert scores[1].eligible is False
    assert current[1] is None
    assert lagged[1] is None
    assert delta[1] is None
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
    baseline = compute_nondecay_geom_scores(
        returns,
        {"NEW": 1, "STABLE": 2},
        4,
    )
    old_changed = compute_nondecay_geom_scores(
        returns.assign(OLD=(-0.9, 0.8, -0.8, 0.7, -0.6)),
        {"NEW": 1, "STABLE": 2},
        4,
    )

    assert old_changed == baseline


def test_ties_are_deterministic_across_dataframe_and_mapping_order():
    first = pd.DataFrame({"B": (0.01,) * 5, "A": (0.01,) * 5})
    second = first[["A", "B"]]
    first_ranked, *_ = _ranked(first, {"B": 2, "A": 1})
    second_ranked, *_ = _ranked(second, {"A": 1, "B": 2})

    assert first_ranked == second_ranked == [1, 2]


def test_registered_session_is_causal_and_round12_identity_preserving():
    strategy = AiRotationR12NondecayGeomStrategy()
    assert isinstance(strategy, FundRotationStrategy)
    assert strategy.descriptor.id == "ai_rotation_r12_nondecay_geom"
    assert "r11" not in strategy.describe_decision_pipeline(_small_config())["selection_rule"]
    config = _small_config()
    fund_daily, fund_adj, dim_fund, codes = _market_frames()
    requirements = strategy.resolve_requirements(config)
    calendar = tuple(sorted(fund_daily["trade_date"].astype(str).unique()))
    signal_date = calendar[requirements.warmup_trade_days]

    def evaluate(daily, adjusted):
        session = strategy.create_session(
            StrategyInitializationContext(
                run_id="r12-causal",
                evaluation_calendar=calendar,
            ),
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
        return session.evaluate(
            StrategyDecisionContext(signal_date=signal_date, data_view=view),
        )

    future_daily = fund_daily.copy()
    future_daily.loc[
        future_daily["trade_date"].astype(str) > signal_date,
        ["open", "close", "high", "low", "pre_close"],
    ] *= 100.0
    future_adj = fund_adj.copy()
    future_adj.loc[future_adj["trade_date"].astype(str) > signal_date, "adj_factor"] *= 100.0

    before = evaluate(fund_daily, fund_adj)
    after = evaluate(future_daily, future_adj)

    assert before.decision_id.endswith("-ai_rotation_r12_nondecay_geom")
    assert after.action is before.action
    assert after.target_weights == before.target_weights
    assert after.cash_weight == before.cash_weight
    assert after.diagnostics == before.diagnostics
    assert before.diagnostics["signal_information_cutoff"] == "CLOSE"
    assert before.cash_weight + sum(before.target_weights.values()) == pytest.approx(1.0)


def test_invalid_clustering_path_keeps_round12_identity():
    strategy = AiRotationR12NondecayGeomStrategy()
    config = _small_config()
    fund_daily, fund_adj, dim_fund, codes = _market_frames()
    fund_daily.loc[:, ["open", "close", "high", "low", "pre_close"]] = 2.0
    requirements = strategy.resolve_requirements(config)
    calendar = tuple(sorted(fund_daily["trade_date"].astype(str).unique()))
    signal_date = calendar[requirements.warmup_trade_days]
    session = strategy.create_session(
        StrategyInitializationContext(run_id="r12-invalid", evaluation_calendar=calendar),
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
    assert decision.decision_id.endswith("-ai_rotation_r12_nondecay_geom")
