"""Focused behavior tests for the round 07 tail-persistence challenger."""

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
from backtest.fund_rotation.scoring.cluster_momentum import (
    ClusterMomentumScoreModel,
)
from backtest.fund_rotation.scoring.contracts import rank_scores
from backtest.fund_rotation.strategies.ai_rotation_r07_tail_persist.strategy import (
    AiRotationR07TailPersistStrategy,
    compute_tail_persist_scores,
    select_tail_persist_clusters,
)
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    build_slot_weights,
)
from tests.fund_rotation.test_correlation_representative_strategy import (
    _market_frames,
    _small_config,
)


def _ranked_scores(weekly_returns: pd.DataFrame, clusters: dict[str, int]):
    current, lagged = compute_tail_persist_scores(
        weekly_returns,
        clusters,
        momentum_window=4,
    )
    members: dict[int, list[str]] = {}
    for code, cluster_id in clusters.items():
        members.setdefault(cluster_id, []).append(code)
    ranked = rank_scores(current, cluster_members=members)
    return ranked, current, lagged


def test_first_two_slots_ignore_lagged_gate():
    returns = pd.DataFrame(
        {
            "A": (0.01, 0.01, 0.01, 0.01, 0.10),
            "B": (0.02, 0.02, 0.02, 0.02, 0.08),
            "C": (0.015, 0.015, 0.015, 0.015, 0.06),
        }
    )
    ranked, _, lagged = _ranked_scores(
        returns,
        {"A": 1, "B": 2, "C": 3},
    )

    selected = select_tail_persist_clusters(
        ranked,
        lagged_values={1: -0.1, 2: 0.0, 3: 0.1},
        top_n=3,
    )

    assert selected == ranked[:2] + [3]
    assert lagged[1] > 0 and lagged[2] > 0


def test_rank_three_positive_lagged_matches_champion():
    returns = pd.DataFrame({code: (value,) * 5 for code, value in {
        "A": 0.04, "B": 0.03, "C": 0.02, "D": 0.01,
    }.items()})
    ranked, _, lagged = _ranked_scores(
        returns,
        {"A": 1, "B": 2, "C": 3, "D": 4},
    )

    assert all(value is not None and value > 0 for value in lagged.values())
    assert select_tail_persist_clusters(ranked, lagged_values=lagged, top_n=3) == ranked[:3]


def test_tail_falls_back_from_rank_three_to_rank_four_and_rank_five():
    ranked = [1, 2, 3, 4, 5]

    assert select_tail_persist_clusters(
        ranked,
        lagged_values={3: 0.0, 4: 0.2, 5: 0.3},
        top_n=3,
    ) == [1, 2, 4]
    assert select_tail_persist_clusters(
        ranked,
        lagged_values={3: -0.1, 4: None, 5: 0.3},
        top_n=3,
    ) == [1, 2, 5]


def test_no_qualified_tail_keeps_fixed_slot_weight_and_cash():
    selected = select_tail_persist_clusters(
        [1, 2, 3],
        lagged_values={3: None},
        top_n=3,
    )
    weights, filled, vacant, cash = build_slot_weights(
        selected,
        {1: "A", 2: "B"},
        top_n=3,
    )

    assert selected == [1, 2]
    assert weights == {"A": pytest.approx(1 / 3), "B": pytest.approx(1 / 3)}
    assert filled == [1, 2]
    assert vacant == []
    assert cash == pytest.approx(1 / 3)


def test_current_qualification_precedes_lagged_qualification():
    returns = pd.DataFrame(
        {
            "A": (0.01, 0.01, 0.01, 0.01, -0.5),
            "B": (0.01, 0.01, 0.01, 0.01, 0.02),
        }
    )
    _, current, lagged = _ranked_scores(returns, {"A": 1, "B": 2})

    assert current[1].value is None or current[1].value <= 0
    assert lagged[1] is not None and lagged[1] > 0


def test_nonfinite_or_nonpositive_lagged_values_are_not_tail_eligible():
    assert select_tail_persist_clusters(
        [1, 2, 3, 4],
        lagged_values={3: float("nan"), 4: float("inf")},
        top_n=3,
    ) == [1, 2]


def test_epoch_recomputes_scores_from_current_members_only():
    returns = pd.DataFrame(
        {
            "OLD": (0.01,) * 4 + (0.01,),
            "NEW": (0.01,) * 4 + (0.08,),
            "STABLE": (0.01,) * 4 + (0.04,),
        }
    )
    baseline, _ = compute_tail_persist_scores(
        returns,
        {"NEW": 1, "STABLE": 2},
        4,
    )
    old_changed, _ = compute_tail_persist_scores(
        returns.assign(OLD=(-0.9, 0.8, -0.8, 0.7, -0.6)),
        {"NEW": 1, "STABLE": 2},
        4,
    )

    assert old_changed[1] == baseline[1]
    assert old_changed[2] == baseline[2]


def test_selection_is_deterministic_across_insertion_order():
    first = pd.DataFrame({"B": (0.01,) * 5, "A": (0.01,) * 5, "C": (0.01,) * 5})
    second = first[["C", "A", "B"]]
    first_ranked, _, first_lagged = _ranked_scores(
        first,
        {"B": 2, "A": 1, "C": 3},
    )
    second_ranked, _, second_lagged = _ranked_scores(
        second,
        {"C": 3, "A": 1, "B": 2},
    )

    assert first_ranked == second_ranked == [1, 2, 3]
    assert first_lagged == second_lagged
    assert select_tail_persist_clusters(first_ranked, first_lagged, 3) == select_tail_persist_clusters(
        second_ranked,
        second_lagged,
        3,
    )


def test_vacant_tail_does_not_refill_from_lower_rank():
    selected = select_tail_persist_clusters(
        [1, 2, 3, 4],
        lagged_values={3: 0.2, 4: 0.3},
        top_n=3,
    )
    weights, filled, vacant, cash = build_slot_weights(
        selected,
        {1: "A", 2: "B", 3: None, 4: "D"},
        top_n=3,
    )

    assert selected == [1, 2, 3]
    assert weights == {"A": pytest.approx(1 / 3), "B": pytest.approx(1 / 3)}
    assert filled == [1, 2]
    assert vacant == [3]
    assert cash == pytest.approx(1 / 3)


def test_registered_session_is_causal_and_identity_preserving():
    strategy = AiRotationR07TailPersistStrategy()
    assert isinstance(strategy, FundRotationStrategy)
    config = _small_config()
    fund_daily, fund_adj, dim_fund, codes = _market_frames()
    requirements = strategy.resolve_requirements(config)
    calendar = tuple(sorted(fund_daily["trade_date"].astype(str).unique()))
    signal_date = calendar[requirements.warmup_trade_days]

    def evaluate(daily, adjusted):
        session = strategy.create_session(
            StrategyInitializationContext(run_id="r07-causal", evaluation_calendar=calendar),
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

    assert before.decision_id.endswith("-ai_rotation_r07_tail_persist")
    assert after.action is before.action
    assert after.target_weights == before.target_weights
    assert after.cash_weight == before.cash_weight
    assert after.diagnostics == before.diagnostics
    assert before.diagnostics["signal_information_cutoff"] == "CLOSE"
    assert before.cash_weight + sum(before.target_weights.values()) == pytest.approx(1.0)


def test_invalid_clustering_path_keeps_challenger_identity():
    strategy = AiRotationR07TailPersistStrategy()
    config = _small_config()
    fund_daily, fund_adj, dim_fund, codes = _market_frames()
    fund_daily.loc[:, ["open", "close", "high", "low", "pre_close"]] = 2.0
    requirements = strategy.resolve_requirements(config)
    calendar = tuple(sorted(fund_daily["trade_date"].astype(str).unique()))
    signal_date = calendar[requirements.warmup_trade_days]
    session = strategy.create_session(
        StrategyInitializationContext(run_id="r07-invalid", evaluation_calendar=calendar),
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
    assert decision.decision_id.endswith("-ai_rotation_r07_tail_persist")


def test_score_model_uses_current_and_one_week_lagged_four_week_windows():
    returns = pd.DataFrame({"A": (0.01, 0.01, 0.01, 0.01, 0.09)})
    current, lagged = compute_tail_persist_scores(returns, {"A": 1}, 4)

    assert current[1].value == pytest.approx((1.01**3 * 1.09) - 1.0)
    assert lagged[1] == pytest.approx(1.01**4 - 1.0)
