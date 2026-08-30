from __future__ import annotations

import math

import pandas as pd
import pytest

from backtest.fund_rotation.contracts import StrategyDecisionContext
from backtest.fund_rotation.strategies.ai_rotation_r74_r39_vol_adjusted_score.strategy import (
    AiRotationR74R39VolAdjustedScoreSession,
    AiRotationR74R39VolAdjustedScoreStrategy,
    build_volatility_adjusted_scores,
    compute_cluster_volatility_60,
)
from backtest.fund_rotation.strategies.correlation_representative.config import (
    CorrelationRepresentativeConfig,
)


def test_momentum_is_divided_by_annualized_volatility_60_for_ranking_only():
    scores = build_volatility_adjusted_scores(
        {1: 0.20, 2: 0.15},
        {1: 0.10, 2: 0.30},
        cluster_members={1: ("A",), 2: ("B",)},
    )
    assert scores[1].value == pytest.approx(2.0)
    assert scores[2].value == pytest.approx(0.5)
    assert scores[1].direction.value == "HIGHER_BETTER"
    assert scores[1].components == {"momentum": 0.20, "volatility_60": 0.10}


@pytest.mark.parametrize("volatility", [None, 0.0, 1e-12, math.nan, math.inf, -0.1])
def test_missing_or_invalid_volatility_is_ineligible(volatility):
    scores = build_volatility_adjusted_scores(
        {1: 0.20}, {1: volatility}, cluster_members={1: ("A",)}
    )
    assert scores[1].eligible is False
    assert scores[1].value is None


def test_missing_or_nonfinite_momentum_is_ineligible():
    scores = build_volatility_adjusted_scores(
        {1: math.nan, 2: None},
        {1: 0.10, 2: 0.10},
        cluster_members={1: ("A",), 2: ("B",)},
    )
    assert all(not score.eligible and score.value is None for score in scores.values())


def test_score_builder_rejects_cluster_without_membership_evidence():
    scores = build_volatility_adjusted_scores({1: 0.20}, {1: 0.10}, cluster_members={})
    assert scores[1].eligible is False
    assert scores[1].value is None


def test_cluster_volatility_requires_all_members_and_excludes_future_rows():
    dates = pd.date_range("2020-01-01", periods=61, freq="D")
    closes = pd.DataFrame(
        {
            "A": [100.0 + index for index in range(61)] + [10_000.0],
            "B": [math.nan] * 62,
        },
        index=dates.strftime("%Y%m%d").tolist() + ["20200302"],
    )
    values = compute_cluster_volatility_60(
        closes,
        signal_date="20200301",
        clusters={"A": 1, "B": 1},
    )
    assert values[1] is None


def test_r74_pipeline_has_fixed_volatility_score_and_no_inverse_vol_weighting():
    strategy = AiRotationR74R39VolAdjustedScoreStrategy()
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())
    assert pipeline["score_model"]["id"] == "r74_momentum_over_volatility_60"
    assert pipeline["volatility_window_days"] == 60
    assert "inverse" not in str(pipeline["weighting_rule"]).lower()


def test_r74_session_keeps_r39_fixed_slots_and_lifecycle(monkeypatch):
    dates = pd.date_range("2020-06-29", periods=61, freq="D").strftime("%Y%m%d")
    closes = pd.DataFrame(
        {
            "A": [100.0 + index + (index % 3) * 0.1 for index in range(61)],
            "B": [100.0 + index * 0.8 + (index % 4) * 0.2 for index in range(61)],
        },
        index=dates,
    )

    class _View:
        def returns(self, frequency, lookback):
            assert frequency == "weekly"
            return pd.DataFrame({"A": [0.02] * lookback, "B": [0.01] * lookback})

        def adjusted_closes(self, lookback=None):
            assert lookback == 61
            return closes

    monkeypatch.setattr(
        "backtest.fund_rotation.strategies.ai_rotation_r74_r39_vol_adjusted_score.strategy.check_historical_eligibility",
        lambda pool, signal_date: (["A", "B"], set()),
    )
    monkeypatch.setattr(
        "backtest.fund_rotation.strategies.ai_rotation_r74_r39_vol_adjusted_score.strategy.signal_date_eligible",
        lambda view, eligible, signal_date: (["A", "B"], set()),
    )
    session = AiRotationR74R39VolAdjustedScoreSession(
        CorrelationRepresentativeConfig(top_n=3, momentum_window_weeks=2)
    )
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

    assert set(decision.target_weights) == {"A", "B"}
    assert all(weight == pytest.approx(1 / 6) for weight in decision.target_weights.values())
    assert decision.cash_weight == pytest.approx(2 / 3)
    assert decision.diagnostics["score_model"]["id"] == "r74_momentum_over_volatility_60"
    assert decision.diagnostics["staged_reentry_fraction"] == pytest.approx(0.5)
    assert "portfolio_weighting" not in decision.diagnostics
