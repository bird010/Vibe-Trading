from __future__ import annotations

import math

import pytest
import pandas as pd

from backtest.fund_rotation.strategies.ai_rotation_r73_r39_multi_horizon_rank.strategy import (
    AiRotationR73R39MultiHorizonRankSession,
    AiRotationR73R39MultiHorizonRankStrategy,
    aggregate_multi_horizon_rank_scores,
    compute_cluster_multi_horizon_returns,
    compute_multi_horizon_returns,
    rank_period_scores,
)
from backtest.fund_rotation.contracts import StrategyDecisionContext
from backtest.fund_rotation.strategies.correlation_representative.config import (
    CorrelationRepresentativeConfig,
)


def test_each_horizon_is_ranked_and_aggregated_with_equal_weights():
    period_scores = {
        60: {1: 0.30, 2: 0.20, 3: 0.10},
        120: {1: 0.10, 2: 0.30, 3: 0.20},
        240: {1: 0.20, 2: 0.10, 3: 0.30},
    }
    scores = aggregate_multi_horizon_rank_scores(
        period_scores,
        cluster_members={1: ("A",), 2: ("B",), 3: ("C",)},
    )
    assert {cluster_id: score.value for cluster_id, score in scores.items()} == {
        1: pytest.approx(6.0),
        2: pytest.approx(6.0),
        3: pytest.approx(6.0),
    }
    assert all(score.eligible for score in scores.values())


def test_missing_horizon_is_ineligible_and_score_coverage_is_explicit():
    scores = aggregate_multi_horizon_rank_scores(
        {
            60: {1: 0.30, 2: 0.20},
            120: {1: 0.10, 2: None},
            240: {1: 0.20, 2: 0.10},
        },
        cluster_members={1: ("A",), 2: ("B",)},
    )
    assert scores[1].eligible is True
    assert scores[2].eligible is False
    assert scores[2].value is None
    assert scores[2].components["missing_horizons"] == 120


def test_ties_use_minimum_member_code_for_deterministic_order():
    values = {2: 0.1, 1: 0.1, 3: 0.2}
    assert rank_period_scores(
        values,
        cluster_members={1: ("Z", "A"), 2: ("B",), 3: ("C",)},
    ) == {3: 1, 1: 2, 2: 3}


def test_nonfinite_period_value_is_missing_not_ranked():
    assert rank_period_scores(
        {1: 0.1, 2: math.nan}, cluster_members={1: ("A",), 2: ("B",)}
    ) == {1: 1}


def test_incomplete_cluster_is_not_averaged_from_remaining_members():
    dates = pd.date_range("2020-01-01", periods=241, freq="D").strftime("%Y%m%d")
    closes = pd.DataFrame(
        {
            "A": [float(index + 1) for index in range(241)],
            "B": [math.nan] * 241,
            "C": [float(index + 2) for index in range(241)],
        },
        index=dates,
    )
    values = compute_cluster_multi_horizon_returns(
        closes,
        signal_date=dates[-1],
        clusters={"A": 1, "B": 1, "C": 2},
    )
    assert all(values[horizon][1] is None for horizon in (60, 120, 240))
    assert all(values[horizon][2] is not None for horizon in (60, 120, 240))


def test_incomplete_cluster_does_not_change_complete_cluster_rank_population():
    complete = {
        horizon: {1: 0.30, 2: 0.20}
        for horizon in (60, 120, 240)
    }
    with_incomplete = {
        60: {1: 0.30, 2: 0.20, 3: 0.40},
        120: {1: 0.30, 2: 0.20},
        240: {1: 0.30, 2: 0.20, 3: 0.10},
    }
    members = {1: ("A",), 2: ("B",), 3: ("C",)}
    baseline = aggregate_multi_horizon_rank_scores(complete, cluster_members=members)
    candidate = aggregate_multi_horizon_rank_scores(
        with_incomplete, cluster_members=members
    )
    assert candidate[1].value == baseline[1].value == pytest.approx(3.0)
    assert candidate[2].value == baseline[2].value == pytest.approx(6.0)
    assert candidate[3].eligible is False


def test_future_adjusted_close_is_excluded_from_all_horizon_windows():
    dates = pd.date_range("2020-01-01", periods=241, freq="D")
    closes = pd.DataFrame(
        {"A": [float(index + 1) for index in range(241)] + [1_000_000.0]},
        index=dates.strftime("%Y%m%d").tolist() + ["20200829"],
    )
    result = compute_multi_horizon_returns(
        closes, signal_date="20200828", codes=("A",)
    )
    assert result[60]["A"] == pytest.approx(241.0 / 181.0 - 1.0)


def test_r73_pipeline_has_no_short_horizon_r20():
    strategy = AiRotationR73R39MultiHorizonRankStrategy()
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())
    rendered = str(pipeline)
    assert "R60" in rendered and "R120" in rendered and "R240" in rendered
    assert pipeline["rank_horizons"] == [60, 120, 240]
    assert 20 not in pipeline["rank_horizons"]


def test_r73_session_replaces_only_ranking_and_keeps_r39_lifecycle(monkeypatch):
    dates = pd.date_range("2020-01-01", periods=241, freq="D").strftime("%Y%m%d")
    closes = pd.DataFrame(
        {"A": [float(index + 1) for index in range(241)],
         "B": [float(242 - index) for index in range(241)]},
        index=dates,
    )

    class _View:
        def returns(self, frequency, lookback):
            assert frequency == "weekly"
            return pd.DataFrame({"A": [0.01] * lookback, "B": [0.0] * lookback})

        def adjusted_closes(self, lookback=None):
            assert lookback == 241
            return closes

    monkeypatch.setattr(
        "backtest.fund_rotation.strategies.ai_rotation_r73_r39_multi_horizon_rank.strategy.check_historical_eligibility",
        lambda pool, signal_date: (["A", "B"], set()),
    )
    monkeypatch.setattr(
        "backtest.fund_rotation.strategies.ai_rotation_r73_r39_multi_horizon_rank.strategy.signal_date_eligible",
        lambda view, eligible, signal_date: (["A", "B"], set()),
    )
    session = AiRotationR73R39MultiHorizonRankSession(CorrelationRepresentativeConfig())
    session._week_index = 1
    session._last_recluster_week = 0
    session._clusters = {"A": 1, "B": 2}
    session._frozen_members = {1: ["A"], 2: ["B"]}
    session._representatives = {1: "A", 2: "B"}
    monkeypatch.setattr(session, "_pool_at_signal", lambda view: pd.DataFrame())
    monkeypatch.setattr(session, "_maintain_locks", lambda *args: None)

    decision = session.evaluate(
        StrategyDecisionContext(signal_date="20200828", data_view=_View())
    )

    assert decision.target_weights == {"A": pytest.approx(1 / 6), "B": pytest.approx(1 / 6)}
    assert decision.cash_weight == pytest.approx(2 / 3)
    assert decision.diagnostics["score_model"]["id"] == "equal_weight_rank_r60_r120_r240"
    assert decision.diagnostics["rank_flip_count"] == 0
