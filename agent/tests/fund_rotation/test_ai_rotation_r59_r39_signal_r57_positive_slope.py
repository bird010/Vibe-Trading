"""Focused behavior tests for the R59 positive-slope challenger."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from backtest.fund_rotation.contracts import (
    DecisionKind,
    StrategyDecisionContext,
    StrategyInitializationContext,
)
from backtest.fund_rotation.strategies.correlation_representative.config import (
    CorrelationRepresentativeConfig,
)
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    CorrelationRepresentativeSession,
)

try:
    import backtest.fund_rotation.strategies.ai_rotation_r59_r39_signal_r57_positive_slope.strategy as r59_module
    from backtest.fund_rotation.strategies.ai_rotation_r59_r39_signal_r57_positive_slope import (
        AiRotationR59R39SignalR57Strategy as ExportedStrategy,
    )
    from backtest.fund_rotation.strategies.ai_rotation_r59_r39_signal_r57_positive_slope.strategy import (
        DESCRIPTOR,
        AiRotationR59R39SignalR57Session,
        AiRotationR59R39SignalR57Strategy,
    )
    _R59_IMPORT_ERROR = None
except ImportError as exc:  # Red phase: R59 is not implemented yet.
    r59_module = None
    DESCRIPTOR = None
    AiRotationR59R39SignalR57Session = None
    AiRotationR59R39SignalR57Strategy = None
    ExportedStrategy = None
    _R59_IMPORT_ERROR = exc


def _require_r59() -> None:
    assert AiRotationR59R39SignalR57Strategy is not None, (
        f"R59 strategy is not implemented: {_R59_IMPORT_ERROR}"
    )


def _rows(values: dict[str, tuple[float, float, float]]):
    return {
        code: {
            "ts_code": code,
            "cluster_id": index + 1,
            "is_representative": True,
            "bias": factors[0],
            "slope": factors[1],
            "raw_slope_25d": factors[1],
            "efficiency": factors[2],
        }
        for index, (code, factors) in enumerate(values.items())
    }


def _context(signal_date: str = "20240105"):
    return StrategyDecisionContext(
        signal_date=signal_date,
        data_view=SimpleNamespace(
            returns=lambda frequency, lookback: pd.DataFrame(),
        ),
    )


def _prepare_session(monkeypatch, session_cls, module_name, rows):
    _require_r59()
    session = session_cls(CorrelationRepresentativeConfig())
    session._clusters = {code: index + 1 for index, code in enumerate(rows)}
    session._representatives = {
        index + 1: code for index, code in enumerate(rows)
    }
    session._frozen_members = {
        index + 1: [code] for index, code in enumerate(rows)
    }
    session._last_recluster_week = 0
    session._pool_at_signal = lambda view: pd.DataFrame()
    session._maintain_locks = lambda *args, **kwargs: None
    monkeypatch.setattr(
        f"{module_name}.check_historical_eligibility",
        lambda dim_pool, signal_date: (list(rows), []),
    )
    monkeypatch.setattr(
        f"{module_name}.signal_date_eligible",
        lambda view, eligible, signal_date: (list(eligible), []),
    )
    session._factor_rows = lambda view, signal_date: rows
    return session


def test_r59_exports_fixed_top_three_weekly_positive_slope_strategy():
    _require_r59()

    strategy = AiRotationR59R39SignalR57Strategy()
    config = strategy.config_model()
    requirements = strategy.resolve_requirements(config)
    pipeline = strategy.describe_decision_pipeline(config)

    assert ExportedStrategy is AiRotationR59R39SignalR57Strategy
    assert DESCRIPTOR.id == "ai_rotation_r59_r39_signal_r57_positive_slope"
    assert requirements.frequency == "weekly"
    assert requirements.warmup_trade_days == 264
    assert config.top_n == 3
    assert "raw_slope_25d > 0" in pipeline["selection_rule"]
    assert pipeline["top_n"] == 3


def test_r59_filters_non_positive_slope_after_r57_composite_scoring(monkeypatch):
    values = {
        "A": (3.0, 1.0, 3.0),
        "B": (100.0, -1.0, 100.0),
        "C": (2.0, 2.0, 2.0),
        "D": (1.0, 3.0, 1.0),
    }
    rows = _rows(values)
    session = _prepare_session(
        monkeypatch,
        AiRotationR59R39SignalR57Session,
        "backtest.fund_rotation.strategies.ai_rotation_r59_r39_signal_r57_positive_slope.strategy",
        rows,
    )

    def fake_score(raw_scores, factor_weights, minimum_candidates):
        del raw_scores, factor_weights, minimum_candidates
        return (
            {"B": 4.0, "A": 3.0, "C": 2.0, "D": 1.0},
            {"complete_candidates": ["A", "B", "C", "D"], "standardization": {}},
        )

    monkeypatch.setattr(r59_module, "score_complete_candidates", fake_score)
    decision = session.evaluate(_context())

    assert decision.action is DecisionKind.SET_TARGETS
    assert set(decision.target_weights) == {"A", "C", "D"}
    assert "B" not in decision.target_weights
    assert decision.diagnostics["ranked_codes"] == ["A", "C", "D"]
    assert decision.diagnostics["complete_candidate_count"] == 3
    assert decision.diagnostics["factor_scores"]["B"]["raw_slope_25d"] == -1.0


def test_r59_matches_r58_lifecycle_and_weights_when_all_raw_slopes_are_positive(
    monkeypatch,
):
    import backtest.fund_rotation.strategies.ai_rotation_r58_r39_signal_r57.strategy as r58_module
    from backtest.fund_rotation.strategies.ai_rotation_r58_r39_signal_r57.strategy import (
        AiRotationR58R39SignalR57Session,
    )

    rows = _rows(
        {
            "A": (4.0, 4.0, 4.0),
            "B": (3.0, 3.0, 3.0),
            "C": (2.0, 2.0, 2.0),
            "D": (1.0, 1.0, 1.0),
        }
    )
    r58 = _prepare_session(
        monkeypatch,
        AiRotationR58R39SignalR57Session,
        "backtest.fund_rotation.strategies.ai_rotation_r58_r39_signal_r57.strategy",
        rows,
    )
    r59 = _prepare_session(
        monkeypatch,
        AiRotationR59R39SignalR57Session,
        "backtest.fund_rotation.strategies.ai_rotation_r59_r39_signal_r57_positive_slope.strategy",
        rows,
    )

    r58_decision = r58.evaluate(_context())
    r59_decision = r59.evaluate(_context())

    assert r59_decision.target_weights == r58_decision.target_weights
    assert r59_decision.cash_weight == pytest.approx(r58_decision.cash_weight)
    assert r59_decision.reason_code == r58_decision.reason_code
    assert r59_decision.diagnostics["staged_reentry_fraction"] == r58_decision.diagnostics[
        "staged_reentry_fraction"
    ]
    assert r59_decision.diagnostics["incumbent_carry_rule"] == r58_decision.diagnostics[
        "incumbent_carry_rule"
    ]
    assert r58_module.DESCRIPTOR.id != DESCRIPTOR.id


def test_r59_schedule_matches_r58_weekly_schedule():
    _require_r59()
    calendar = tuple(
        pd.bdate_range("2024-01-01", periods=30 * 5).strftime("%Y%m%d")
    )
    config = CorrelationRepresentativeConfig()
    r58 = CorrelationRepresentativeSession(config)
    r59 = AiRotationR59R39SignalR57Session(config)

    assert r59.scheduled_dates(calendar, calendar[0], calendar[-1]) == r58.scheduled_dates(
        calendar, calendar[0], calendar[-1]
    )


def _real_factor_view(signal_date: str, *, include_future: bool = False):
    dates = list(pd.bdate_range("2024-01-01", periods=49).strftime("%Y%m%d"))
    values = np.linspace(1.0, 2.0, 49)
    bars = pd.DataFrame(
        {
            "ts_code": ["A"] * len(dates),
            "trade_date": dates,
            "open": values,
            "high": values,
            "low": values,
            "close": values,
            "vol": values,
            "amount": values,
        }
    )
    adjustments = pd.DataFrame(
        {
            "ts_code": ["A"] * len(dates),
            "trade_date": dates,
            "adj_factor": np.ones(49),
        }
    )
    if include_future:
        future_date = pd.bdate_range(
            pd.Timestamp(signal_date) + pd.offsets.BDay(1), periods=1
        )[0].strftime("%Y%m%d")
        future_bar = bars.iloc[[-1]].copy()
        future_bar["trade_date"] = future_date
        future_bar.loc[:, ["open", "high", "low", "close"]] = 999.0
        bars = pd.concat([bars, future_bar], ignore_index=True)
        future_adj = adjustments.iloc[[-1]].copy()
        future_adj["trade_date"] = future_date
        adjustments = pd.concat([adjustments, future_adj], ignore_index=True)

    class View:
        def daily_bars(self, fields, *, lookback):
            return bars[["ts_code", "trade_date", *fields]].copy()

        def fund_adjustments(self, *, lookback):
            return adjustments.copy()

    return dates, View()


def test_r59_raw_slope_uses_causal_49_row_adjusted_window():
    _require_r59()
    dates, before_view = _real_factor_view("2024-03-07")
    _, after_view = _real_factor_view("2024-03-07", include_future=True)
    before_session = AiRotationR59R39SignalR57Session(CorrelationRepresentativeConfig())
    after_session = AiRotationR59R39SignalR57Session(CorrelationRepresentativeConfig())
    before_session._representatives = {1: "A"}
    after_session._representatives = {1: "A"}

    before = before_session._factor_rows(before_view, dates[-1])["A"]
    after = after_session._factor_rows(after_view, dates[-1])["A"]

    assert before["adjusted_observations"] == 49
    assert before["raw_slope_25d"] == before["slope"]
    assert after == before


def test_r59_rejects_non_three_top_n_and_keeps_three_slots(monkeypatch):
    _require_r59()
    strategy = AiRotationR59R39SignalR57Strategy()
    invalid_config = CorrelationRepresentativeConfig(top_n=1)
    with pytest.raises(ValueError, match="top_n.*3"):
        strategy.resolve_requirements(invalid_config)
    with pytest.raises(ValueError, match="top_n.*3"):
        strategy.create_session(
            StrategyInitializationContext(run_id="bad-r59-top-n", evaluation_calendar=()),
            invalid_config,
        )

    session = _prepare_session(
        monkeypatch,
        AiRotationR59R39SignalR57Session,
        "backtest.fund_rotation.strategies.ai_rotation_r59_r39_signal_r57_positive_slope.strategy",
        _rows(
            {
                "A": (4.0, 4.0, 4.0),
                "B": (3.0, 3.0, 3.0),
                "C": (2.0, 2.0, 2.0),
                "D": (1.0, 1.0, 1.0),
            }
        ),
    )
    decision = session.evaluate(_context())
    assert len(decision.target_weights) == 3


def test_r59_is_registered_without_removing_existing_strategies():
    _require_r59()
    from backtest.fund_rotation.strategies.registry import default_fund_rotation_strategies

    ids = [strategy().descriptor.id for strategy in default_fund_rotation_strategies()]
    assert "ai_rotation_r58_r39_signal_r57" in ids
    assert "ai_rotation_r59_r39_signal_r57_positive_slope" in ids
    assert len(ids) == len(set(ids))
