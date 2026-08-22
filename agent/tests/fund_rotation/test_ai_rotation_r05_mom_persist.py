"""Focused behavior tests for the round 05 momentum-persistence challenger."""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.fund_rotation.causal_data import CausalDataView
from backtest.fund_rotation.contracts import (
    DecisionKind,
    FundRotationStrategy,
    StrategyDecisionContext,
    StrategyInitializationContext,
)
from backtest.fund_rotation.scoring.contracts import rank_scores
from backtest.fund_rotation.scoring.cluster_momentum import (
    ClusterMomentumScoreModel,
)
from backtest.fund_rotation.strategies.ai_rotation_r05_mom_persist.strategy import (
    compute_persistent_scores,
    AiRotationR05MomPersistStrategy,
)
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    build_slot_weights,
)
from tests.fund_rotation.test_correlation_representative_strategy import (
    _market_frames,
    _small_config,
)


def test_lagged_positive_gate_removes_transient_current_leader():
    weekly_returns = pd.DataFrame(
        {
            "A": (-0.05, -0.05, -0.05, 0.10, 0.20),
            "B": (0.02, 0.02, 0.02, 0.02, 0.08),
            "C": (0.015, 0.015, 0.015, 0.015, 0.06),
            "D": (0.01, 0.01, 0.01, 0.01, 0.04),
        },
        index=pd.date_range("2024-01-05", periods=5, freq="W-FRI"),
    )
    clusters = {"A": 1, "B": 2, "C": 3, "D": 4}

    scores, lagged = compute_persistent_scores(
        weekly_returns,
        clusters,
        momentum_window=4,
    )

    assert rank_scores(scores, cluster_members={1: ["A"], 2: ["B"], 3: ["C"], 4: ["D"]}) == [2, 3, 4]
    assert scores[1].value is not None and scores[1].value > 0
    assert not scores[1].eligible
    assert lagged[1] is not None and lagged[1] < 0
    assert all(scores[cid].eligible for cid in (2, 3, 4))


def test_persistent_scores_use_exact_current_and_lagged_four_week_windows():
    weekly_returns = pd.DataFrame(
        {"A": (0.01, 0.01, 0.01, 0.01, 0.09)},
    )

    scores, lagged = compute_persistent_scores(
        weekly_returns,
        {"A": 1},
        momentum_window=4,
    )

    assert scores[1].value == pytest.approx((1.01**3 * 1.09) - 1.0)
    assert lagged[1] == pytest.approx((1.01**4) - 1.0)


def test_short_or_nonfinite_windows_are_unavailable():
    short_scores, short_lagged = compute_persistent_scores(
        pd.DataFrame({"A": (0.01, 0.01, 0.01, 0.01)}),
        {"A": 1},
        momentum_window=4,
    )
    assert short_scores[1].value is None
    assert short_lagged[1] is None

    missing_scores, missing_lagged = compute_persistent_scores(
        pd.DataFrame({"A": (0.01, 0.01, float("nan"), 0.01, 0.01)}),
        {"A": 1},
        momentum_window=4,
    )
    assert missing_scores[1].value is None
    assert missing_lagged[1] is None


def test_registered_strategy_session_keeps_contract_and_new_decision_identity():
    strategy = AiRotationR05MomPersistStrategy()
    assert isinstance(strategy, FundRotationStrategy)
    config = _small_config()
    fund_daily, fund_adj, dim_fund, codes = _market_frames()
    requirements = strategy.resolve_requirements(config)
    calendar = tuple(sorted(fund_daily["trade_date"].astype(str).unique()))
    start = calendar[requirements.warmup_trade_days]
    session = strategy.create_session(
        StrategyInitializationContext(run_id="r05", evaluation_calendar=calendar),
        config,
    )

    decisions = []
    for signal_date in session.scheduled_dates(calendar, start, calendar[-1]):
        view = CausalDataView(
            fund_daily,
            fund_adj,
            dim_fund,
            requirements,
            pd.Timestamp(signal_date),
            frozenset(codes),
        )
        decisions.append(
            session.evaluate(
                StrategyDecisionContext(signal_date=signal_date, data_view=view),
            )
        )

    assert decisions
    assert all(
        decision.decision_id.endswith("-ai_rotation_r05_mom_persist")
        for decision in decisions
    )
    assert all(
        decision.diagnostics["signal_information_cutoff"] == "CLOSE"
        for decision in decisions
    )
    assert all(
        decision.action is DecisionKind.SET_TARGETS
        and decision.cash_weight + sum(decision.target_weights.values()) == pytest.approx(1.0)
        for decision in decisions
    )


def test_invalid_clustering_path_uses_challenger_identity():
    strategy = AiRotationR05MomPersistStrategy()
    config = _small_config()
    fund_daily, fund_adj, dim_fund, codes = _market_frames()
    fund_daily.loc[:, ["open", "close", "high", "low", "pre_close"]] = 2.0
    requirements = strategy.resolve_requirements(config)
    calendar = tuple(sorted(fund_daily["trade_date"].astype(str).unique()))
    start = calendar[requirements.warmup_trade_days]
    session = strategy.create_session(
        StrategyInitializationContext(run_id="r05-invalid", evaluation_calendar=calendar),
        config,
    )
    signal_date = session.scheduled_dates(calendar, start, calendar[-1])[0]
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
    assert decision.decision_id.endswith("-ai_rotation_r05_mom_persist")


def test_new_epoch_uses_new_members_and_ignores_old_epoch_same_id_data():
    base = pd.DataFrame(
        {
            "OLD": (0.01, 0.01, 0.01, 0.01, 0.01),
            "NEW": (0.02, 0.02, 0.02, 0.02, 0.08),
            "STABLE": (0.01, 0.01, 0.01, 0.01, 0.04),
        }
    )
    new_epoch = {"NEW": 1, "STABLE": 2}

    baseline, _ = compute_persistent_scores(base, new_epoch, 4)
    old_epoch_changed = base.assign(OLD=(-0.9, 0.8, -0.8, 0.7, -0.6))
    unchanged, _ = compute_persistent_scores(old_epoch_changed, new_epoch, 4)
    new_member_changed = base.assign(NEW=(-0.5, 0.02, 0.02, 0.02, 0.08))
    changed, lagged = compute_persistent_scores(new_member_changed, new_epoch, 4)

    assert unchanged[1] == baseline[1]
    assert unchanged[2] == baseline[2]
    assert baseline[1].eligible
    assert not changed[1].eligible
    assert lagged[1] is not None and lagged[1] < 0


def test_future_rows_after_signal_date_do_not_change_causal_decision():
    config = _small_config()
    strategy = AiRotationR05MomPersistStrategy()
    fund_daily, fund_adj, dim_fund, codes = _market_frames()
    requirements = strategy.resolve_requirements(config)
    calendar = tuple(sorted(fund_daily["trade_date"].astype(str).unique()))
    signal_date = calendar[requirements.warmup_trade_days]

    def evaluate_with_frames(daily, adjusted):
        session = strategy.create_session(
            StrategyInitializationContext(run_id="r05-causal", evaluation_calendar=calendar),
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
    future_mask = future_daily["trade_date"].astype(str) > signal_date
    future_daily.loc[future_mask, ["open", "close", "high", "low", "pre_close"]] *= 100.0
    future_adjusted = fund_adj.copy()
    future_mask_adj = future_adjusted["trade_date"].astype(str) > signal_date
    future_adjusted.loc[future_mask_adj, "adj_factor"] *= 100.0

    before = evaluate_with_frames(fund_daily, fund_adj)
    after = evaluate_with_frames(future_daily, future_adjusted)

    assert after.action is before.action
    assert after.target_weights == before.target_weights
    assert after.cash_weight == before.cash_weight
    assert after.diagnostics == before.diagnostics


def test_fewer_than_top_n_qualified_slots_keep_fixed_weight_and_cash():
    weights, filled, vacant, cash = build_slot_weights(
        [1, 2],
        {1: "A", 2: "B"},
        top_n=3,
    )

    assert weights == {"A": pytest.approx(1 / 3), "B": pytest.approx(1 / 3)}
    assert filled == [1, 2]
    assert vacant == []
    assert cash == pytest.approx(1 / 3)


def test_all_positive_qualification_matches_champion_targets_cash_rank_and_representatives():
    weekly_returns = pd.DataFrame(
        {
            "A": (0.01, 0.01, 0.01, 0.02, 0.02),
            "B": (0.02, 0.02, 0.02, 0.01, 0.01),
            "C": (0.015, 0.015, 0.015, 0.015, 0.015),
            "D": (0.005, 0.005, 0.005, 0.005, 0.005),
        }
    )
    clusters = {"A": 1, "B": 2, "C": 3, "D": 4}
    members = {1: ["A"], 2: ["B"], 3: ["C"], 4: ["D"]}
    champion_scores = ClusterMomentumScoreModel().score(
        weekly_returns,
        clusters,
        4,
    )
    challenger_scores, lagged = compute_persistent_scores(
        weekly_returns,
        clusters,
        4,
    )
    champion_rank = rank_scores(champion_scores, cluster_members=members)
    challenger_rank = rank_scores(challenger_scores, cluster_members=members)
    representatives = {1: "A", 2: "B", 3: "C", 4: "D"}
    champion_targets = build_slot_weights(champion_rank[:3], representatives, 3)
    challenger_targets = build_slot_weights(challenger_rank[:3], representatives, 3)

    assert all(value is not None and value > 0 for value in lagged.values())
    assert challenger_rank == champion_rank
    assert challenger_targets == champion_targets


def test_ties_are_deterministic_across_cluster_and_column_insertion_order():
    first_returns = pd.DataFrame(
        {"B": (0.01,) * 5, "A": (0.01,) * 5, "D": (0.01,) * 5, "C": (0.01,) * 5},
    )
    second_returns = first_returns[["C", "D", "A", "B"]]
    first_clusters = {"B": 2, "A": 1, "D": 4, "C": 3}
    second_clusters = {"C": 3, "D": 4, "A": 1, "B": 2}
    members = {1: ["A"], 2: ["B"], 3: ["C"], 4: ["D"]}

    first_scores, _ = compute_persistent_scores(first_returns, first_clusters, 4)
    second_scores, _ = compute_persistent_scores(second_returns, second_clusters, 4)

    assert rank_scores(first_scores, cluster_members=members) == [1, 2, 3, 4]
    assert rank_scores(second_scores, cluster_members=members) == [1, 2, 3, 4]
    assert {
        cluster_id: score.value for cluster_id, score in first_scores.items()
    } == {
        cluster_id: score.value for cluster_id, score in second_scores.items()
    }
