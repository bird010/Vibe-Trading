"""Focused behavior tests for the weekly R58 signal-only challenger."""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from backtest.fund_rotation.contracts import (
    DecisionKind,
    QualityStatus,
    StrategyDecisionContext,
    StrategyInitializationContext,
    TargetWeightDecision,
    validate_diagnostics,
)
from backtest.fund_rotation.causal_data import CausalDataView
from backtest.fund_rotation.strategies.ai_rotation_r57_three_factor_representative.factors import (
    score_complete_candidates,
)
from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import (
    apply_incumbent_carry,
)
from backtest.fund_rotation.strategies.ai_rotation_r34_staged_reentry.strategy import (
    apply_staged_reentry,
)
from backtest.fund_rotation.strategies.correlation_representative.config import (
    CorrelationRepresentativeConfig,
)
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    CorrelationRepresentativeSession,
)
from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import (
    AiRotationR39IncumbentCarryStrategy,
)
from tests.fund_rotation.test_correlation_representative_strategy import (
    _market_frames,
    _small_config,
)

try:
    from backtest.fund_rotation.strategies.ai_rotation_r58_r39_signal_r57.strategy import (
        DESCRIPTOR,
        AiRotationR58R39SignalR57Session,
        AiRotationR58R39SignalR57Strategy,
    )
    from backtest.fund_rotation.strategies.ai_rotation_r58_r39_signal_r57 import (
        AiRotationR58R39SignalR57Strategy as ExportedStrategy,
    )
    _R58_IMPORT_ERROR = None
except ImportError as exc:  # Red phase: R58 is not implemented yet.
    DESCRIPTOR = None
    AiRotationR58R39SignalR57Session = None
    AiRotationR58R39SignalR57Strategy = None
    ExportedStrategy = None
    _R58_IMPORT_ERROR = exc


def _require_r58() -> None:
    assert AiRotationR58R39SignalR57Strategy is not None, (
        f"R58 strategy is not implemented: {_R58_IMPORT_ERROR}"
    )


def _rows(values: dict[str, tuple[float | None, float | None, float | None]]):
    return {
        code: {
            "ts_code": code,
            "cluster_id": index + 1,
            "is_representative": True,
            "bias": factors[0],
            "slope": factors[1],
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


def _prepare_session(monkeypatch, rows):
    _require_r58()
    session = AiRotationR58R39SignalR57Session(CorrelationRepresentativeConfig())
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
        "backtest.fund_rotation.strategies.ai_rotation_r58_r39_signal_r57.strategy.check_historical_eligibility",
        lambda dim_pool, signal_date: (list(rows), []),
    )
    monkeypatch.setattr(
        "backtest.fund_rotation.strategies.ai_rotation_r58_r39_signal_r57.strategy.signal_date_eligible",
        lambda view, eligible, signal_date: (list(eligible), []),
    )
    session._factor_rows = lambda view, signal_date: rows
    return session


def test_r58_exports_weekly_r39_signal_only_strategy():
    _require_r58()

    strategy = AiRotationR58R39SignalR57Strategy()
    config = strategy.config_model()
    requirements = strategy.resolve_requirements(config)
    pipeline = strategy.describe_decision_pipeline(config)

    assert ExportedStrategy is AiRotationR58R39SignalR57Strategy
    assert DESCRIPTOR.id == "ai_rotation_r58_r39_signal_r57"
    assert DESCRIPTOR.interface_version == "1.0"
    assert requirements.frequency == "weekly"
    assert requirements.warmup_trade_days == 264
    assert config.top_n == 3
    assert config.recluster_interval_weeks == 26
    assert "weekly" in str(pipeline).lower()
    assert "top 3" in str(pipeline).lower()
    assert "threshold" not in str(pipeline).lower()
    assert "daily" not in str(pipeline).lower()
    assert "top-1" not in str(pipeline).lower()
    assert "factor_scores" in strategy.artifact_roles


def test_r58_schedule_matches_r39_week_endings():
    _require_r58()
    calendar = tuple(
        pd.bdate_range("2024-01-01", periods=30 * 5).strftime("%Y%m%d")
    )
    config = CorrelationRepresentativeConfig()
    r39 = CorrelationRepresentativeSession(config)
    r58 = AiRotationR58R39SignalR57Session(config)

    assert r58.scheduled_dates(calendar, calendar[0], calendar[-1]) == r39.scheduled_dates(
        calendar, calendar[0], calendar[-1]
    )


def test_r58_uses_one_complete_cross_section_and_ddof_zero():
    scores, details = score_complete_candidates(
        {
            "A": {"bias": 1.0, "slope": 2.0, "efficiency": 3.0},
            "B": {"bias": 2.0, "slope": 2.0, "efficiency": 1.0},
            "C": {"bias": None, "slope": 100.0, "efficiency": 100.0},
        },
        {"bias": 0.3, "slope": 0.3, "efficiency": 0.4},
        minimum_candidates=2,
    )

    assert list(scores) == ["A", "B"]
    assert details["complete_candidates"] == ["A", "B"]
    assert details["standardization"]["slope"]["std"] == 0.0


def test_r58_evaluate_selects_top_three_fixed_one_third_slots(monkeypatch):
    values = {
        "A": (4.0, 4.0, 4.0),
        "B": (3.0, 3.0, 3.0),
        "C": (2.0, 2.0, 2.0),
        "D": (1.0, 1.0, 1.0),
    }
    session = _prepare_session(monkeypatch, _rows(values))
    decision = session.evaluate(_context())

    assert decision.action is DecisionKind.SET_TARGETS
    assert decision.target_weights == {
        "A": pytest.approx(1 / 6),
        "B": pytest.approx(1 / 6),
        "C": pytest.approx(1 / 6),
    }
    assert decision.cash_weight == pytest.approx(0.5)
    assert "threshold" not in json.dumps(decision.diagnostics).lower()
    assert decision.diagnostics["factor_scores"]["A"]["rank"] == 1
    assert decision.diagnostics["factor_scores"]["A"]["base_slot_weight"] == pytest.approx(1 / 3)
    assert decision.diagnostics["factor_scores"]["A"]["staged"] is True
    assert decision.diagnostics["factor_scores"]["D"]["top_3"] is False


def test_r58_reuses_r39_staged_reentry_and_incumbent_carry_values():
    staged_targets, _, staged_codes = apply_staged_reentry(
        {"A": 1 / 3, "B": 1 / 3, "C": 1 / 3},
        {"A": 1 / 3, "B": 1 / 3, "D": 1 / 3},
    )
    targets, cash, staged, incumbents = apply_incumbent_carry(
        {"A": 1 / 3, "B": 1 / 3, "C": 1 / 3},
        staged_targets,
    )

    assert targets == {
        "A": pytest.approx(5 / 12),
        "B": pytest.approx(5 / 12),
        "D": pytest.approx(1 / 6),
    }
    assert cash == pytest.approx(0.0)
    assert staged == {"D"}
    assert staged == staged_codes
    assert incumbents == {"A", "B"}


def test_r58_candidate_shortage_sets_all_cash_instead_of_holding(monkeypatch):
    values = {
        "A": (1.0, 1.0, 1.0),
        "B": (None, 2.0, 2.0),
    }
    session = _prepare_session(monkeypatch, _rows(values))
    session._previous_weights = {"A": 1 / 3}
    decision = session.evaluate(_context())

    assert decision.action is DecisionKind.SET_TARGETS
    assert decision.target_weights == {}
    assert decision.cash_weight == pytest.approx(1.0)
    assert decision.reason_code == "INSUFFICIENT_COMPLETE_CANDIDATES"
    assert "HOLD_TARGETS" not in json.dumps(decision.diagnostics)


def test_r58_diagnostics_and_factor_artifact_are_strict_json(monkeypatch):
    values = {
        "B": (1.0, 1.0, 1.0),
        "A": (1.0, 1.0, 1.0),
    }
    session = _prepare_session(monkeypatch, _rows(values))
    decision = session.evaluate(_context())
    validate_diagnostics(decision.diagnostics)

    diagnostics = session.finalize()
    factor_artifact = next(
        artifact for artifact in diagnostics.artifacts if artifact.role == "factor_scores"
    )
    json.dumps(factor_artifact.payload, allow_nan=False)
    assert [row["ts_code"] for row in factor_artifact.payload[0]["rows"]] == ["A", "B"]
    assert decision.quality_status in {
        QualityStatus.VALID,
        QualityStatus.DEGRADED,
    }


def test_r58_invalid_cluster_path_keeps_r39_overlay_identity(monkeypatch):
    session = _prepare_session(monkeypatch, _rows({"A": (1.0, 1.0, 1.0)}))
    session._last_recluster_week = -26
    session._recluster = lambda *args, **kwargs: TargetWeightDecision(
        decision_id="old-id",
        signal_date="20240105",
        action=DecisionKind.INVALID,
        reason_code="CLUSTERING_DATA_INSUFFICIENT",
        quality_status=QualityStatus.INVALID,
        diagnostics={"error": "insufficient"},
    )

    decision = session.evaluate(_context())

    assert decision.action is DecisionKind.INVALID
    assert decision.decision_id == "20240105-ai_rotation_r58_r39_signal_r57"
    assert decision.target_weights == {}
    assert decision.cash_weight == pytest.approx(1.0)
    assert decision.diagnostics["staged_reentry_codes"] == []
    assert decision.diagnostics["incumbent_carry_codes"] == []


def _real_factor_view(signal_date: str, *, include_future: bool = False):
    dates = list(
        pd.bdate_range("2024-01-01", periods=49).strftime("%Y%m%d")
    )
    values = np.ones(49, dtype=float)
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
            "adj_factor": values,
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
        future_adj["adj_factor"] = 999.0
        adjustments = pd.concat([adjustments, future_adj], ignore_index=True)

    class View:
        def daily_bars(self, fields, *, lookback):
            return bars[["ts_code", "trade_date", *fields]].copy()

        def fund_adjustments(self, *, lookback):
            return adjustments.copy()

    return dates, bars, adjustments, View()


def test_r58_early_invalid_ohlc_invalidates_all_three_factors():
    _require_r58()
    dates, bars, adjustments, view = _real_factor_view("2024-03-07")
    bars.loc[0, "high"] = 0.0
    session = AiRotationR58R39SignalR57Session(CorrelationRepresentativeConfig())
    session._representatives = {1: "A"}

    row = session._factor_rows(view, dates[-1])["A"]

    assert row["bias"] is None
    assert row["slope"] is None
    assert row["efficiency"] is None
    assert row["bias_status"] == "NON_POSITIVE_OHLC"
    assert row["slope_status"] == "NON_POSITIVE_OHLC"
    assert row["efficiency_status"] == "NON_POSITIVE_OHLC"


def test_r58_future_rows_do_not_change_factor_artifact_fields():
    _require_r58()
    dates, _, _, before_view = _real_factor_view("2024-03-07")
    _, _, _, after_view = _real_factor_view("2024-03-07", include_future=True)
    before_session = AiRotationR58R39SignalR57Session(CorrelationRepresentativeConfig())
    after_session = AiRotationR58R39SignalR57Session(CorrelationRepresentativeConfig())
    before_session._representatives = {1: "A"}
    after_session._representatives = {1: "A"}

    before = before_session._factor_rows(before_view, dates[-1])
    after = after_session._factor_rows(after_view, dates[-1])

    assert after == before


def _parity_projection(diagnostics):
    return {
        "signal_date": diagnostics["signal_date"],
        "action": diagnostics["action"],
        "quality_status": diagnostics["quality_status"],
        "staged_reentry_fraction": diagnostics["diagnostics"].get(
            "staged_reentry_fraction"
        ),
        "incumbent_carry_rule": diagnostics["diagnostics"].get(
            "incumbent_carry_rule"
        ),
        "signal_information_cutoff": diagnostics["diagnostics"].get(
            "signal_information_cutoff"
        ),
    }


def _trace_lifecycle_projection(trace):
    return {
        "signal_date": trace["signal_date"],
        "cluster_snapshot": trace["cluster_snapshot"],
        "candidates": [
            {
                "ts_code": candidate["ts_code"],
                "universe_eligible": candidate["stages"]["universe_eligible"],
                "cluster_id": candidate["stages"]["cluster_id"],
                "cluster_representative": candidate["stages"][
                    "cluster_representative"
                ],
                "exclusion_stage": candidate["exclusion_stage"],
                "exclusion_reason": candidate["exclusion_reason"],
            }
            for candidate in trace["candidates"]
        ],
    }


def test_r58_r39_same_input_lifecycle_parity_through_26_week_recluster():
    _require_r58()
    config = _small_config(
        k=3,
        top_n=3,
        correlation_lookback_weeks=10,
        recluster_interval_weeks=26,
    )
    fund_daily, fund_adj, dim_fund, codes = _market_frames(n_weeks=40)
    calendar = tuple(sorted(fund_daily["trade_date"].astype(str).unique()))
    r39_strategy = AiRotationR39IncumbentCarryStrategy()
    r58_strategy = AiRotationR58R39SignalR57Strategy()
    requirements = r39_strategy.resolve_requirements(config)
    r39_session = r39_strategy.create_session(
        StrategyInitializationContext(run_id="r39-parity", evaluation_calendar=calendar),
        config,
    )
    r58_session = r58_strategy.create_session(
        StrategyInitializationContext(run_id="r58-parity", evaluation_calendar=calendar),
        config,
    )
    start = calendar[requirements.warmup_trade_days]
    r39_dates = r39_session.scheduled_dates(calendar, start, calendar[-1])
    r58_dates = r58_session.scheduled_dates(calendar, start, calendar[-1])
    assert r58_dates == r39_dates
    assert len(r58_dates) >= 27

    for signal_date in r58_dates:
        r39_view = CausalDataView(
            fund_daily,
            fund_adj,
            dim_fund,
            requirements,
            pd.Timestamp(signal_date),
            frozenset(codes),
        )
        r58_view = CausalDataView(
            fund_daily,
            fund_adj,
            dim_fund,
            requirements,
            pd.Timestamp(signal_date),
            frozenset(codes),
        )
        r39_session.evaluate(
            StrategyDecisionContext(signal_date=signal_date, data_view=r39_view)
        )
        r58_session.evaluate(
            StrategyDecisionContext(signal_date=signal_date, data_view=r58_view)
        )

    r39_diagnostics = r39_session.finalize()
    r58_diagnostics = r58_session.finalize()
    r39_artifacts = {artifact.role: artifact.payload for artifact in r39_diagnostics.artifacts}
    r58_artifacts = {artifact.role: artifact.payload for artifact in r58_diagnostics.artifacts}
    for role in ("cluster_history", "gates", "representatives", "exclusions"):
        assert r58_artifacts[role] == r39_artifacts[role]
    assert len(r58_artifacts["cluster_history"]) >= 2
    assert r58_session._clusters == r39_session._clusters
    assert r58_session._frozen_members == r39_session._frozen_members
    assert r58_session._representatives == r39_session._representatives
    assert r58_session._last_gate_overall == r39_session._last_gate_overall
    assert [
        _parity_projection(row) for row in r58_artifacts["decisions"]
    ] == [
        _parity_projection(row) for row in r39_artifacts["decisions"]
    ]
    assert [
        _trace_lifecycle_projection(trace) for trace in r58_diagnostics.decision_trace
    ] == [
        _trace_lifecycle_projection(trace) for trace in r39_diagnostics.decision_trace
    ]


def test_r58_rejects_non_three_top_n_and_keeps_exact_three_slots(monkeypatch):
    _require_r58()
    strategy = AiRotationR58R39SignalR57Strategy()
    invalid_config = CorrelationRepresentativeConfig(top_n=1)
    with pytest.raises(ValueError, match="top_n.*3"):
        strategy.resolve_requirements(invalid_config)
    with pytest.raises(ValueError, match="top_n.*3"):
        strategy.create_session(
            StrategyInitializationContext(run_id="bad-top-n", evaluation_calendar=()),
            invalid_config,
        )

    session = _prepare_session(
        monkeypatch,
        _rows(
            {
                "A": (4.0, 4.0, 4.0),
                "B": (3.0, 3.0, 3.0),
                "C": (2.0, 2.0, 2.0),
                "D": (1.0, 1.0, 1.0),
            }
        ),
    )
    session._previous_weights = {"A": 1 / 3, "B": 1 / 3, "C": 1 / 3}
    decision = session.evaluate(_context("20240112"))
    assert decision.target_weights == {
        "A": pytest.approx(1 / 3),
        "B": pytest.approx(1 / 3),
        "C": pytest.approx(1 / 3),
    }
    assert decision.cash_weight == pytest.approx(0.0)
