"""Cross-strategy lifecycle invariants for the R60-R64 sessions."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from backtest.fund_rotation.contracts import StrategyDecisionContext, validate_diagnostics
from stockpred.fund_rotation.api_models import CandidateDecisionRow
from backtest.fund_rotation.strategies.correlation_representative.config import (
    CorrelationRepresentativeConfig,
)
from backtest.fund_rotation.strategies.ai_rotation_r60_r59_medium_trend_gate.strategy import (
    AiRotationR60R59MediumTrendGateStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r61_r59_dual_horizon_score.strategy import (
    AiRotationR61R59DualHorizonScoreStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r62_r59_true_invvol.strategy import (
    AiRotationR62R59TrueInvvolStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r63_r59_rank_buffer.strategy import (
    AiRotationR63R59RankBufferStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r64_direct_corr_diversification.strategy import (
    AiRotationR64DirectCorrDiversificationStrategy,
)

import backtest.fund_rotation.strategies.ai_rotation_r59_r39_signal_r57_positive_slope.strategy as r59_module
import backtest.fund_rotation.strategies.ai_rotation_r64_direct_corr_diversification.strategy as r64_module


def _rows() -> dict[str, dict[str, object]]:
    return {
        code: {
            "ts_code": code,
            "cluster_id": index + 1,
            "is_representative": True,
            "bias": bias,
            "slope": slope,
            "raw_slope_25d": slope,
            "efficiency": efficiency,
            "medium_return_126d": medium,
            "medium_trend_positive": True,
        }
        for index, (code, bias, slope, efficiency, medium) in enumerate(
            (
                ("A", 3.0, 1.0, 3.0, 0.10),
                ("B", 2.0, 2.0, 2.0, 0.08),
                ("C", 1.0, 3.0, 1.0, 0.06),
            )
        )
    }


def _context(view: object) -> StrategyDecisionContext:
    return StrategyDecisionContext(signal_date="20240105", data_view=view)


def _prepare_overlay_session(monkeypatch, strategy):
    rows = _rows()
    session = strategy.create_session(None, strategy.config_model())
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
    monkeypatch.setattr(r59_module, "check_historical_eligibility", lambda pool, date: (list(rows), []))
    monkeypatch.setattr(r59_module, "signal_date_eligible", lambda view, eligible, date: (list(eligible), []))
    session._factor_rows = lambda view, date: rows
    return session, rows


def _assert_lifecycle_invariants(session, decision, finalized):
    validate_diagnostics(decision.diagnostics)
    assert finalized.decision_trace
    trace = finalized.decision_trace[-1]
    assert trace["signal_date"] == decision.signal_date

    candidates = {row["ts_code"]: row for row in trace["candidates"]}
    for candidate in candidates.values():
        parsed = CandidateDecisionRow.model_validate(candidate)
        assert parsed.target_weight == pytest.approx(
            decision.target_weights.get(parsed.ts_code, 0.0)
        )

    if hasattr(session, "_decision_log"):
        logged = session._decision_log[-1]
    else:
        decisions = next(item for item in finalized.artifacts if item.role == "decisions")
        logged = decisions.payload[-1]
    assert logged["target_weights"] == decision.target_weights
    assert logged["cash_weight"] == pytest.approx(decision.cash_weight)

    rows = decision.diagnostics.get("factor_scores", {})
    for code, row in rows.items():
        assert row["final_weight"] == pytest.approx(
            decision.target_weights.get(code, 0.0)
        )
        assert row["cash_weight"] == pytest.approx(decision.cash_weight)


@pytest.mark.parametrize(
    "strategy_cls",
    [
        AiRotationR60R59MediumTrendGateStrategy,
        AiRotationR61R59DualHorizonScoreStrategy,
        AiRotationR62R59TrueInvvolStrategy,
        AiRotationR63R59RankBufferStrategy,
    ],
)
def test_r60_r63_evaluate_and_finalize_preserve_cross_strategy_evidence(
    monkeypatch, strategy_cls
):
    session, _ = _prepare_overlay_session(monkeypatch, strategy_cls())
    closes = pd.DataFrame(
        {code: np.linspace(100.0 + index, 110.0 + index, 61) for index, code in enumerate(("A", "B", "C"))}
    )
    view = SimpleNamespace(
        returns=lambda frequency, lookback: pd.DataFrame(),
        adjusted_closes=lambda lookback: closes,
    )

    decision = session.evaluate(_context(view))
    finalized = session.finalize()

    _assert_lifecycle_invariants(session, decision, finalized)


def test_r64_evaluate_and_finalize_publish_the_same_score_evidence(monkeypatch):
    rows = _rows()
    for row in rows.values():
        row.pop("raw_slope_25d")
    weekly_returns = pd.DataFrame(
        {
            "A": np.random.default_rng(1).normal(size=52),
            "B": np.random.default_rng(2).normal(size=52),
            "C": np.random.default_rng(3).normal(size=52),
        }
    )
    view = SimpleNamespace(
        returns=lambda frequency, lookback: weekly_returns,
    )
    monkeypatch.setattr(r64_module, "ensure_instrument_pool", lambda view, lookback_trade_days: pd.DataFrame())
    monkeypatch.setattr(r64_module, "check_historical_eligibility", lambda pool, date: (list(rows), []))
    monkeypatch.setattr(r64_module, "signal_date_eligible", lambda view, eligible, date: (list(eligible), []))
    monkeypatch.setattr(r64_module.AiRotationR58R39SignalR57Session, "_factor_rows", lambda self, view, date: rows)

    strategy = AiRotationR64DirectCorrDiversificationStrategy()
    session = strategy.create_session(None, strategy.config_model())
    decision = session.evaluate(_context(view))
    finalized = session.finalize()

    assert decision.diagnostics["complete_candidate_count"] >= 2
    _assert_lifecycle_invariants(session, decision, finalized)
    factor_artifact = next(item for item in finalized.artifacts if item.role == "factor_scores")
    factor_rows = factor_artifact.payload[-1]["rows"]
    for code, row in factor_rows.items():
        assert row["composite_score"] == pytest.approx(
            decision.diagnostics["factor_scores"][code]["composite_score"]
        )
        assert row["final_weight"] == pytest.approx(decision.target_weights.get(code, 0.0))

    decision_artifact = next(item for item in finalized.artifacts if item.role == "decisions")
    assert decision_artifact.payload[-1]["decision_id"] == decision.decision_id
    assert decision_artifact.payload[-1]["target_weights"] == decision.target_weights
