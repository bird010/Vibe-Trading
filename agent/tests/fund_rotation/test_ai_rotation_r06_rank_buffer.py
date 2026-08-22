"""Focused behavior tests for the round 06 rank-buffer challenger."""

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
from backtest.fund_rotation.scoring.cluster_momentum import ClusterMomentumScoreModel
from backtest.fund_rotation.scoring.contracts import rank_scores
from backtest.fund_rotation.strategies.ai_rotation_r06_rank_buffer.strategy import (
    AiRotationR06RankBufferStrategy,
    select_rank_buffered_clusters,
)
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    build_slot_weights,
)
from tests.fund_rotation.test_correlation_representative_strategy import (
    _market_frames,
    _small_config,
)


def test_rank_four_previous_selection_is_retained_over_new_top_three():
    selected, retained = select_rank_buffered_clusters(
        [1, 2, 4, 3],
        previous_selected=[1, 2, 3],
        top_n=3,
        epoch_reset=False,
    )

    assert retained == [1, 2, 3]
    assert selected == [1, 2, 3]


def test_rank_five_negative_and_missing_scores_cannot_be_retained():
    assert select_rank_buffered_clusters(
        [1, 2, 4],
        previous_selected=[1, 2, 3],
        top_n=3,
        epoch_reset=False,
    ) == ([1, 2, 4], [1, 2])


def test_new_epoch_resets_buffer_state():
    selected, retained = select_rank_buffered_clusters(
        [4, 1, 2, 3],
        previous_selected=[1, 2, 3],
        top_n=3,
        epoch_reset=True,
    )

    assert retained == []
    assert selected == [4, 1, 2]


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


def test_rank_ties_are_deterministic_before_buffer_selection():
    first = pd.DataFrame({"B": (0.01,) * 5, "A": (0.01,) * 5})
    second = first[["A", "B"]]
    first_scores = ClusterMomentumScoreModel().score(
        first, {"B": 2, "A": 1}, 4,
    )
    second_scores = ClusterMomentumScoreModel().score(
        second, {"A": 1, "B": 2}, 4,
    )
    members = {1: ["A"], 2: ["B"]}

    assert rank_scores(first_scores, cluster_members=members) == [1, 2]
    assert rank_scores(second_scores, cluster_members=members) == [1, 2]


def test_registered_strategy_preserves_identity_cutoff_and_contract():
    strategy = AiRotationR06RankBufferStrategy()
    assert isinstance(strategy, FundRotationStrategy)
    config = _small_config()
    fund_daily, fund_adj, dim_fund, codes = _market_frames()
    requirements = strategy.resolve_requirements(config)
    calendar = tuple(sorted(fund_daily["trade_date"].astype(str).unique()))
    start = calendar[requirements.warmup_trade_days]
    session = strategy.create_session(
        StrategyInitializationContext(run_id="r06", evaluation_calendar=calendar),
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

    assert decision.decision_id.endswith("-ai_rotation_r06_rank_buffer")
    assert decision.diagnostics["signal_information_cutoff"] == "CLOSE"
    assert decision.diagnostics["rank_buffer_clusters"] == []
    assert decision.diagnostics["rank_buffer_size"] == 1
    assert decision.cash_weight + sum(decision.target_weights.values()) == pytest.approx(1.0)


def test_invalid_clustering_path_keeps_new_strategy_identity():
    strategy = AiRotationR06RankBufferStrategy()
    config = _small_config()
    fund_daily, fund_adj, dim_fund, codes = _market_frames()
    fund_daily.loc[:, ["open", "close", "high", "low", "pre_close"]] = 2.0
    requirements = strategy.resolve_requirements(config)
    calendar = tuple(sorted(fund_daily["trade_date"].astype(str).unique()))
    start = calendar[requirements.warmup_trade_days]
    session = strategy.create_session(
        StrategyInitializationContext(run_id="r06-invalid", evaluation_calendar=calendar),
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
    assert decision.decision_id.endswith("-ai_rotation_r06_rank_buffer")


def test_future_rows_after_signal_date_do_not_change_decision():
    config = _small_config()
    strategy = AiRotationR06RankBufferStrategy()
    fund_daily, fund_adj, dim_fund, codes = _market_frames()
    requirements = strategy.resolve_requirements(config)
    calendar = tuple(sorted(fund_daily["trade_date"].astype(str).unique()))
    signal_date = calendar[requirements.warmup_trade_days]

    def evaluate_with_frames(daily, adjusted):
        session = strategy.create_session(
            StrategyInitializationContext(run_id="r06-causal", evaluation_calendar=calendar),
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
