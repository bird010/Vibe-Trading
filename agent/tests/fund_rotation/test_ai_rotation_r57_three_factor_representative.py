from __future__ import annotations

import math
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from backtest.fund_rotation.contracts import (
    validate_diagnostics,
    validate_target_decision,
)
from backtest.fund_rotation.strategies.ai_rotation_r57_three_factor_representative.config import (
    ArticleThreeFactorRepresentativeConfig,
)
from backtest.fund_rotation.strategies.ai_rotation_r57_three_factor_representative.factors import (
    adjust_ohlc,
    apply_rebalance_threshold,
    compute_bias_momentum,
    compute_efficiency_momentum,
    compute_slope_momentum,
    score_complete_candidates,
)
from backtest.fund_rotation.strategies.ai_rotation_r57_three_factor_representative.strategy import (
    AiRotationR57ThreeFactorRepresentativeStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r57_three_factor_representative.strategy import (
    AiRotationR57ThreeFactorRepresentativeSession,
)
from backtest.fund_rotation.strategies.ai_rotation_r57_three_factor_representative import (
    strategy as r57_strategy_module,
)


def test_adjust_ohlc_uses_signal_day_factor_as_reference():
    bars = pd.DataFrame(
        {
            "ts_code": ["X", "X"],
            "trade_date": ["20240101", "20240102"],
            "open": [10.0, 5.0], "high": [11.0, 5.5],
            "low": [9.0, 4.5], "close": [10.0, 5.0],
        }
    )
    adj = pd.DataFrame(
        {"ts_code": ["X", "X"], "trade_date": ["20240101", "20240102"], "adj_factor": [2.0, 1.0]}
    )
    result = adjust_ohlc(bars, adj, "20240102")
    assert result.loc[0, "close"] == 20.0
    assert result.loc[1, "open"] == 5.0


def test_adjust_ohlc_normalizes_timestamp_trade_dates():
    bars = pd.DataFrame(
        {
            "ts_code": ["X"],
            "trade_date": [pd.Timestamp("2024-01-02")],
            "open": [5.0], "high": [5.5], "low": [4.5], "close": [5.0],
        }
    )
    adj = pd.DataFrame(
        {
            "ts_code": ["X"],
            "trade_date": [pd.Timestamp("2024-01-02")],
            "adj_factor": [1.0],
        }
    )

    result = adjust_ohlc(bars, adj, "2024-01-02")

    assert result.loc[0, "close"] == 5.0


def test_adjust_ohlc_excludes_rows_after_signal_date():
    bars = pd.DataFrame(
        {
            "ts_code": ["X", "X", "X"],
            "trade_date": ["20240101", "20240102", "20240103"],
            "open": [10.0, 5.0, 100.0], "high": [10.0, 5.0, 100.0],
            "low": [10.0, 5.0, 100.0], "close": [10.0, 5.0, 100.0],
        }
    )
    adj = pd.DataFrame(
        {
            "ts_code": ["X", "X", "X"],
            "trade_date": ["20240101", "20240102", "20240103"],
            "adj_factor": [1.0, 2.0, 2.0],
        }
    )

    result = adjust_ohlc(bars, adj, "20240102")

    assert result["trade_date"].tolist() == ["20240101", "20240102"]


def test_factor_formulas_and_invalid_inputs():
    close = pd.Series(np.arange(1.0, 50.0))
    assert compute_bias_momentum(close) is not None
    assert compute_slope_momentum(close) == pytest.approx(400.0, rel=1e-6)
    ohlc = pd.DataFrame({c: close for c in ("open", "high", "low", "close")})
    assert compute_efficiency_momentum(ohlc) > 0
    assert compute_slope_momentum(pd.Series([2.0] * 25)) == 0.0
    assert compute_slope_momentum(pd.Series([1.0, math.nan] * 13)) is None


def test_bias_momentum_matches_independent_hand_calculation():
    close = pd.Series(np.arange(1.0, 50.0))

    assert compute_bias_momentum(close) == pytest.approx(
        -118.05065473318565, abs=1e-10
    )


def test_split_adjustment_does_not_create_false_momentum():
    raw_close = np.array([10.0] * 12 + [5.0] * 13)
    bars = pd.DataFrame(
        {
            "ts_code": ["X"] * 25,
            "trade_date": [f"202401{day:02d}" for day in range(1, 26)],
            "open": raw_close,
            "high": raw_close,
            "low": raw_close,
            "close": raw_close,
        }
    )
    adjustments = pd.DataFrame(
        {
            "ts_code": ["X"] * 25,
            "trade_date": [f"202401{day:02d}" for day in range(1, 26)],
            "adj_factor": [1.0] * 12 + [2.0] * 13,
        }
    )

    adjusted = adjust_ohlc(bars, adjustments, "20240125")

    assert np.allclose(adjusted["close"], 5.0)
    assert compute_slope_momentum(adjusted["close"]) == 0.0
    assert compute_efficiency_momentum(adjusted) == 0.0


def test_cross_sectional_scores_use_one_complete_set_and_ddof_zero():
    scores, details = score_complete_candidates(
        {
            "A": {"bias": 1.0, "slope": 2.0, "efficiency": 3.0},
            "B": {"bias": 2.0, "slope": 2.0, "efficiency": 1.0},
            "C": {"bias": None, "slope": 100.0, "efficiency": 100.0},
        },
        {"bias": 0.3, "slope": 0.3, "efficiency": 0.4},
    )
    assert list(scores) == ["A", "B"]
    assert scores["A"] == pytest.approx(-scores["B"])
    assert details["complete_candidates"] == ["A", "B"]
    assert details["standardization"]["slope"]["std"] == 0.0


def test_cross_sectional_ties_are_sorted_by_code():
    scores, _ = score_complete_candidates(
        {
            "B": {"bias": 1.0, "slope": 1.0, "efficiency": 1.0},
            "A": {"bias": 1.0, "slope": 1.0, "efficiency": 1.0},
        },
        {"bias": 0.3, "slope": 0.3, "efficiency": 0.4},
    )

    assert list(scores) == ["A", "B"]


@pytest.mark.parametrize(
    ("held", "challenger", "expected"),
    [(1.0, 1.5, "HOLD_TARGETS"), (1.0, 1.500001, "SET_TARGETS"), (-1.4, -1.0, "SET_TARGETS")],
)
def test_threshold_is_strict_and_keeps_negative_score_literal(held, challenger, expected):
    selected, action, diagnostics = apply_rebalance_threshold(
        {"A": held, "B": challenger}, "A"
    )
    assert selected == ("B" if expected == "SET_TARGETS" else "A")
    assert action == expected
    assert diagnostics["negative_threshold_case"] is (held < 0 or challenger < 0)


def test_config_is_frozen_and_article_parameters_are_not_searchable():
    config = ArticleThreeFactorRepresentativeConfig()
    assert config.top_n == 1
    assert config.rebalance_freq == "D"
    with pytest.raises(ValueError):
        ArticleThreeFactorRepresentativeConfig(rebalance_threshold=1.4)
    with pytest.raises(ValueError):
        ArticleThreeFactorRepresentativeConfig(top_n=2)


def test_strategy_descriptor_and_daily_requirements():
    strategy = AiRotationR57ThreeFactorRepresentativeStrategy()
    config = ArticleThreeFactorRepresentativeConfig()
    requirements = strategy.resolve_requirements(config)
    assert strategy.descriptor.id == "ai_rotation_r57_three_factor_representative"
    assert requirements.frequency == "D"
    assert requirements.warmup_trade_days == 264
    assert "factor_scores" in strategy.artifact_roles


def test_session_evaluate_builds_factor_diagnostics_before_returning_decision(monkeypatch):
    session = AiRotationR57ThreeFactorRepresentativeSession(
        ArticleThreeFactorRepresentativeConfig()
    )
    session._clusters = {"A": 1, "B": 2}
    session._representatives = {1: "A", 2: "B"}
    session._frozen_members = {1: ["A"], 2: ["B"]}
    session._r57_last_recluster_week = 0
    session._pool_at_signal = lambda view: pd.DataFrame()
    session._maintain_locks = lambda *args, **kwargs: None
    session._factor_rows = lambda view, signal_date: {
        "A": {"ts_code": "A", "cluster_id": 1, "is_representative": True, "bias": 1.0, "slope": 1.0, "efficiency": 1.0},
        "B": {"ts_code": "B", "cluster_id": 2, "is_representative": True, "bias": 2.0, "slope": 2.0, "efficiency": 2.0},
    }
    monkeypatch.setattr(
        r57_strategy_module,
        "check_historical_eligibility",
        lambda dim_pool, signal_date: (["A", "B"], []),
    )
    monkeypatch.setattr(
        r57_strategy_module,
        "signal_date_eligible",
        lambda view, eligible, signal_date: (list(eligible), []),
    )

    context = SimpleNamespace(
        signal_date="20240102",
        data_view=SimpleNamespace(
            returns=lambda frequency, lookback: pd.DataFrame()
        ),
    )
    decision = session.evaluate(context)

    assert decision.action.value == "SET_TARGETS"
    assert decision.target_weights == {"B": 1.0}
    assert decision.cash_weight == 0.0
    assert decision.diagnostics["factor_scores"]["B"]["target_weight"] == 1.0


def _representative_rows(*codes):
    return {
        code: {
            "ts_code": code,
            "cluster_id": index + 1,
            "is_representative": True,
            "bias": float(index + 1),
            "slope": float(index + 1),
            "efficiency": float(index + 1),
        }
        for index, code in enumerate(codes)
    }


def _stub_session_inputs(monkeypatch, session, rows_sequence):
    session._pool_at_signal = lambda view: pd.DataFrame()
    session._maintain_locks = lambda *args, **kwargs: None
    rows = iter(rows_sequence)
    session._factor_rows = lambda view, signal_date: next(rows)
    monkeypatch.setattr(
        r57_strategy_module,
        "check_historical_eligibility",
        lambda dim_pool, signal_date: (["A", "B", "C"], []),
    )
    monkeypatch.setattr(
        r57_strategy_module,
        "signal_date_eligible",
        lambda view, eligible, signal_date: (list(eligible), []),
    )


def _session_context(signal_date="20240102"):
    return SimpleNamespace(
        signal_date=signal_date,
        data_view=SimpleNamespace(
            returns=lambda frequency, lookback: pd.DataFrame()
        ),
    )


def test_scheduled_dates_only_recluster_on_completed_iso_week_end(monkeypatch):
    calendar = tuple(
        pd.bdate_range("2024-01-01", periods=27 * 5).strftime("%Y%m%d")
    )
    session = AiRotationR57ThreeFactorRepresentativeSession(
        ArticleThreeFactorRepresentativeConfig()
    )
    scheduled = session.scheduled_dates(calendar, calendar[0], calendar[-1])
    assert scheduled == calendar

    session._clusters = {"A": 1, "B": 2}
    session._representatives = {1: "A", 2: "B"}
    session._frozen_members = {1: ["A"], 2: ["B"]}
    session._r57_last_recluster_week = 0
    _stub_session_inputs(monkeypatch, session, [_representative_rows("A", "B")] * 27)
    recluster_dates = []
    maintain_dates = []
    monkeypatch.setattr(
        session,
        "_maintain_locks",
        lambda *args: maintain_dates.append(args[-1]),
    )
    monkeypatch.setattr(
        session,
        "_recluster",
        lambda *args: recluster_dates.append(args[-1]) or None,
    )

    week_ends = sorted(session._week_end_dates)
    for date in week_ends[:25]:
        session.evaluate(_session_context(date))
    week_26 = week_ends[25]
    week_26_monday = next(
        date
        for date in calendar
        if pd.Timestamp(date).isocalendar().week
        == pd.Timestamp(week_26).isocalendar().week
        and date != week_26
    )
    session.evaluate(_session_context(week_26_monday))
    assert recluster_dates == []
    assert maintain_dates[-1] == week_26_monday
    session.evaluate(_session_context(week_26))
    assert recluster_dates == [week_26]


def test_hold_diagnostics_separate_daily_top1_from_effective_target(monkeypatch):
    session = AiRotationR57ThreeFactorRepresentativeSession(
        ArticleThreeFactorRepresentativeConfig()
    )
    session._clusters = {"A": 1, "B": 2}
    session._representatives = {1: "A", 2: "B"}
    session._frozen_members = {1: ["A"], 2: ["B"]}
    session._r57_last_recluster_week = 0
    _stub_session_inputs(
        monkeypatch,
        session,
        [_representative_rows("A", "B"), _representative_rows("A", "B")],
    )
    score_sequence = iter(({"B": 2.0, "A": 1.0}, {"A": 1.0, "B": 1.0}))

    def fake_score(*args, **kwargs):
        scores = next(score_sequence)
        complete = sorted(scores)
        details = {
            "complete_candidates": complete,
            "standardization": {
                name: {
                    "mean": 0.0,
                    "std": 1.0,
                    "z_scores": {code: 0.0 for code in complete},
                }
                for name in ("bias", "slope", "efficiency")
            },
        }
        return dict(sorted(scores.items(), key=lambda item: (-item[1], item[0]))), details

    monkeypatch.setattr(r57_strategy_module, "score_complete_candidates", fake_score)
    decision_first = session.evaluate(_session_context())
    decision_hold = session.evaluate(_session_context())

    assert decision_first.target_weights == {"B": 1.0}
    assert decision_hold.action.value == "HOLD_TARGETS"
    assert decision_hold.target_weights == {}
    assert decision_hold.diagnostics["daily_top1"] == "A"
    assert decision_hold.diagnostics["effective_target"] == "B"
    assert decision_hold.diagnostics["decision_target_weights"] == {}
    assert decision_hold.diagnostics["effective_weights"] == {"B": 1.0}
    trace = session._decision_trace[-1]
    selected = {row["ts_code"]: row for row in trace["candidates"]}
    assert selected["B"]["stages"]["portfolio_selected"] is True
    assert selected["B"]["target_weight"] == 1.0
    assert selected["B"]["stages"]["ranking_eligible"] is True
    assert selected["B"]["stages"]["rank"] == 2
    assert selected["B"]["primary_metric"]["id"] == "article_three_factor"
    assert selected["B"]["primary_metric"]["value"] == pytest.approx(1.0)
    assert selected["A"]["stages"]["rank"] == 1


def test_invalid_previous_target_forces_switch_and_records_reason(monkeypatch):
    session = AiRotationR57ThreeFactorRepresentativeSession(
        ArticleThreeFactorRepresentativeConfig()
    )
    session._clusters = {"A": 1, "B": 2, "C": 3}
    session._representatives = {1: "A", 2: "B", 3: "C"}
    session._frozen_members = {1: ["A"], 2: ["B"], 3: ["C"]}
    session._r57_last_recluster_week = 0
    _stub_session_inputs(
        monkeypatch,
        session,
        [_representative_rows("A", "B"), _representative_rows("A", "C")],
    )
    score_sequence = iter(({"B": 2.0, "A": 1.0}, {"A": 2.0, "C": 1.0}))

    def fake_score(*args, **kwargs):
        scores = next(score_sequence)
        complete = sorted(scores)
        return dict(sorted(scores.items(), key=lambda item: (-item[1], item[0]))), {
            "complete_candidates": complete,
            "standardization": {},
        }

    monkeypatch.setattr(r57_strategy_module, "score_complete_candidates", fake_score)
    session.evaluate(_session_context())
    decision = session.evaluate(_session_context())

    assert decision.action.value == "SET_TARGETS"
    assert decision.target_weights == {"A": 1.0}
    assert decision.reason_code == "FORCED_REP_SWITCH"
    assert decision.diagnostics["forced_switch_reason"] == (
        "PREVIOUS_TARGET_NOT_CURRENT_REPRESENTATIVE"
    )


def test_insufficient_candidates_cash_when_previous_target_is_invalid(monkeypatch):
    session = AiRotationR57ThreeFactorRepresentativeSession(
        ArticleThreeFactorRepresentativeConfig()
    )
    session._clusters = {"A": 1, "B": 2}
    session._representatives = {1: "A", 2: "B"}
    session._frozen_members = {1: ["A"], 2: ["B"]}
    session._r57_last_recluster_week = 0
    _stub_session_inputs(
        monkeypatch,
        session,
        [_representative_rows("A", "B"), _representative_rows("A")],
    )
    session.evaluate(_session_context())
    decision = session.evaluate(_session_context())

    assert decision.action.value == "SET_TARGETS"
    assert decision.target_weights == {}
    assert decision.cash_weight == 1.0
    assert decision.reason_code == "INSUFFICIENT_COMPLETE_CANDIDATES"
    assert decision.diagnostics["effective_weights"] == {}


def test_insufficient_candidates_holds_valid_previous_target(monkeypatch):
    session = AiRotationR57ThreeFactorRepresentativeSession(
        ArticleThreeFactorRepresentativeConfig()
    )
    session._clusters = {"A": 1, "B": 2}
    session._representatives = {1: "A", 2: "B"}
    session._frozen_members = {1: ["A"], 2: ["B"]}
    session._r57_last_recluster_week = 0
    _stub_session_inputs(
        monkeypatch,
        session,
        [_representative_rows("A", "B"), _representative_rows("B")],
    )
    session.evaluate(_session_context())
    decision = session.evaluate(_session_context())

    assert decision.action.value == "HOLD_TARGETS"
    assert decision.target_weights == {}
    assert decision.diagnostics["effective_target"] == "B"
    assert decision.diagnostics["effective_weights"] == {"B": 1.0}


def test_factor_input_requests_are_declared_and_bounded():
    session = AiRotationR57ThreeFactorRepresentativeSession(
        ArticleThreeFactorRepresentativeConfig()
    )
    session._representatives = {1: "A"}
    calls = []

    class View:
        def daily_bars(self, fields, *, lookback):
            calls.append(("daily_bars", tuple(fields), lookback))
            return pd.DataFrame(
                columns=["ts_code", "trade_date", *fields]
            )

        def fund_adjustments(self, *, lookback):
            calls.append(("fund_adjustments", lookback))
            return pd.DataFrame(columns=["ts_code", "trade_date", "adj_factor"])

    session._factor_rows(View(), "20240125")

    assert calls == [
        (
            "daily_bars",
            ("open", "high", "low", "close", "vol", "amount"),
            49,
        ),
        ("fund_adjustments", 49),
    ]


def test_factor_rows_report_factor_specific_observations_and_statuses():
    session = AiRotationR57ThreeFactorRepresentativeSession(
        ArticleThreeFactorRepresentativeConfig()
    )
    session._representatives = {1: "A"}
    bars = pd.DataFrame(
        {
            "ts_code": ["A"] * 10,
            "trade_date": [f"202401{day:02d}" for day in range(1, 11)],
            "open": [1.0] * 10,
            "high": [1.0] * 10,
            "low": [1.0] * 10,
            "close": [1.0] * 10,
            "vol": [1.0] * 10,
            "amount": [1.0] * 10,
        }
    )
    adjustments = pd.DataFrame(
        {
            "ts_code": ["A"] * 10,
            "trade_date": [f"202401{day:02d}" for day in range(1, 11)],
            "adj_factor": [1.0] * 10,
        }
    )

    class View:
        def daily_bars(self, fields, *, lookback):
            return bars

        def fund_adjustments(self, *, lookback):
            return adjustments

    row = session._factor_rows(View(), "20240110")["A"]

    assert row["bias_observations"] == 10
    assert row["slope_observations"] == 10
    assert row["efficiency_observations"] == 10
    assert row["bias_required_observations"] == 49
    assert row["slope_required_observations"] == 25
    assert row["efficiency_required_observations"] == 25
    assert row["bias_status"] == "INSUFFICIENT_OBSERVATIONS"
    assert row["slope_status"] == "INSUFFICIENT_OBSERVATIONS"
    assert row["efficiency_status"] == "INSUFFICIENT_OBSERVATIONS"


def test_factor_rows_distinguish_nonpositive_prices_from_short_history():
    session = AiRotationR57ThreeFactorRepresentativeSession(
        ArticleThreeFactorRepresentativeConfig()
    )
    session._representatives = {1: "A"}
    dates = list(pd.date_range("2024-01-01", periods=49, freq="D").strftime("%Y%m%d"))
    close = [1.0] * 48 + [0.0]
    bars = pd.DataFrame(
        {
            "ts_code": ["A"] * 49,
            "trade_date": dates,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "vol": [1.0] * 49,
            "amount": [1.0] * 49,
        }
    )
    adjustments = pd.DataFrame(
        {"ts_code": ["A"] * 49, "trade_date": dates, "adj_factor": [1.0] * 49}
    )

    class View:
        def daily_bars(self, fields, *, lookback):
            return bars

        def fund_adjustments(self, *, lookback):
            return adjustments

    row = session._factor_rows(View(), dates[-1])["A"]

    assert row["bias_status"] == "NON_POSITIVE_PRICE"
    assert row["slope_status"] == "NON_POSITIVE_PRICE"
    assert row["efficiency_status"] == "NON_POSITIVE_OHLC"


def test_factor_observation_windows_match_factor_inputs():
    session = AiRotationR57ThreeFactorRepresentativeSession(
        ArticleThreeFactorRepresentativeConfig()
    )
    session._representatives = {1: "A"}
    dates = list(pd.date_range("2024-01-01", periods=49, freq="D").strftime("%Y%m%d"))
    close = [float("nan")] * 24 + [1.0] * 25
    bars = pd.DataFrame(
        {
            "ts_code": ["A"] * 49,
            "trade_date": dates,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "vol": [1.0] * 49,
            "amount": [1.0] * 49,
        }
    )
    adjustments = pd.DataFrame(
        {"ts_code": ["A"] * 49, "trade_date": dates, "adj_factor": [1.0] * 49}
    )

    class View:
        def daily_bars(self, fields, *, lookback):
            return bars

        def fund_adjustments(self, *, lookback):
            return adjustments

    row = session._factor_rows(View(), dates[-1])["A"]

    assert row["bias_observations"] == 25
    assert row["slope_observations"] == 25
    assert row["efficiency_observations"] == 25
    assert row["bias_status"] == "NONFINITE_PRICE"
    assert row["slope_status"] == "VALID"
    assert row["efficiency_status"] == "VALID"


def test_decisions_and_final_artifacts_are_contract_valid(monkeypatch):
    session = AiRotationR57ThreeFactorRepresentativeSession(
        ArticleThreeFactorRepresentativeConfig()
    )
    session._clusters = {"A": 1, "B": 2}
    session._representatives = {1: "A", 2: "B"}
    session._frozen_members = {1: ["A"], 2: ["B"]}
    session._r57_last_recluster_week = 0
    _stub_session_inputs(
        monkeypatch,
        session,
        [_representative_rows("A", "B"), _representative_rows("A", "B")],
    )
    decisions = iter(({"B": 2.0, "A": 1.0}, {"A": 1.0, "B": 1.0}))

    def fake_score(*args, **kwargs):
        scores = next(decisions)
        complete = sorted(scores)
        return dict(sorted(scores.items(), key=lambda item: (-item[1], item[0]))), {
            "complete_candidates": complete,
            "standardization": {},
        }

    monkeypatch.setattr(r57_strategy_module, "score_complete_candidates", fake_score)
    first = session.evaluate(_session_context())
    hold = session.evaluate(_session_context())
    validate_target_decision(first, {"A", "B"}, set())
    validate_target_decision(hold, {"A", "B"}, set())
    validate_diagnostics(first.diagnostics)
    validate_diagnostics(hold.diagnostics)

    diagnostics = session.finalize()
    assert {artifact.role for artifact in diagnostics.artifacts} == {
        "cluster_history", "gates", "representatives", "exclusions",
        "factor_scores", "decisions",
    }
    factor_artifact = next(
        artifact for artifact in diagnostics.artifacts if artifact.role == "factor_scores"
    )
    json.dumps(factor_artifact.payload, allow_nan=False)
    decision_artifact = next(
        artifact for artifact in diagnostics.artifacts if artifact.role == "decisions"
    )
    hold_rows = [
        row for row in decision_artifact.payload if row["action"] == "HOLD_TARGETS"
    ]
    assert hold_rows
    assert all(row["target_weights"] == {} for row in hold_rows)


def test_factor_scores_include_hand_calculated_slope_and_efficiency_values():
    close = pd.Series(1.0 + 0.01 * np.arange(25))
    assert compute_slope_momentum(close) == pytest.approx(100.0, abs=1e-10)
    path = np.linspace(1.0, 2.0, 25)
    ohlc = pd.DataFrame({field: path for field in ("open", "high", "low", "close")})
    assert compute_efficiency_momentum(ohlc) == pytest.approx(100.0 * np.log(2.0), rel=1e-12)
