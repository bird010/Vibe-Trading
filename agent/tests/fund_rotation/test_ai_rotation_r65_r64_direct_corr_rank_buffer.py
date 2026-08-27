"""Focused tests for R65: R64 direct-correlation selection with rank buffer."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from backtest.fund_rotation.contracts import (
    StrategyDecisionContext,
    validate_diagnostics,
)
from stockpred.fund_rotation.api_models import CandidateDecisionRow
from backtest.fund_rotation.strategies.ai_rotation_r64_direct_corr_diversification.strategy import (
    AiRotationR64DirectCorrDiversificationStrategy,
    select_direct_correlation_diversified,
)
from backtest.fund_rotation.strategies.ai_rotation_r65_r64_direct_corr_rank_buffer.strategy import (
    AiRotationR65R64DirectCorrRankBufferStrategy,
    select_r65_rank_buffered_direct_corr,
)

import backtest.fund_rotation.strategies.ai_rotation_r65_r64_direct_corr_rank_buffer.strategy as r65_module
import backtest.fund_rotation.strategies.ai_rotation_r64_direct_corr_diversification.strategy as r64_module
import backtest.fund_rotation.strategies.ai_rotation_r64_direct_corr_diversification.strategy as r64_module


def _pair_data(codes: list[str]) -> tuple[dict[str, float], dict[str, int]]:
    correlations = {}
    observations = {}
    for left_index, left in enumerate(codes):
        for right in codes[left_index + 1 :]:
            key = "|".join(sorted((left, right)))
            correlations[key] = 0.0
            observations[key] = 52
    return correlations, observations


def test_r65_rank_buffer_prefers_held_rank_four_before_new_entries():
    ranked = ["A", "B", "C", "D", "E"]
    correlations, observations = _pair_data(ranked)

    selected, diagnostics = select_r65_rank_buffered_direct_corr(
        ranked,
        {"D": 1.0 / 3.0},
        correlations,
        observations,
    )

    assert selected == ["D", "A", "B"]
    assert diagnostics["rank_qualified_previous_codes"] == ["D"]
    assert diagnostics["retained_codes"] == ["D"]
    assert diagnostics["current_rank_by_code"]["D"] == 4


def test_r65_buffer_does_not_violate_r64_pairwise_constraint():
    ranked = ["A", "B", "C"]
    correlations = {"A|B": 0.90, "A|C": 0.10, "B|C": 0.10}
    observations = {key: 52 for key in correlations}

    selected, diagnostics = select_r65_rank_buffered_direct_corr(
        ranked,
        {"B": 1.0 / 3.0},
        correlations,
        observations,
    )

    assert selected == ["B", "C"]
    assert diagnostics["retained_codes"] == ["B"]
    assert diagnostics["correlation"]["correlation_rejected_candidates"]["A"] == (
        "PAIRWISE_CORRELATION_TOO_HIGH"
    )
    assert all(
        correlations["|".join(sorted((left, right)))] < 0.80
        for index, left in enumerate(selected)
        for right in selected[index + 1 :]
    )


@pytest.mark.parametrize(
    ("pairwise_corr", "expected"),
    [(0.80, ["A"]), (0.79, ["A", "B"])],
)
def test_r65_corr_boundary_is_strictly_less_than_080(pairwise_corr, expected):
    selected, _ = select_r65_rank_buffered_direct_corr(
        ["A", "B"],
        {"A": 1.0 / 3.0},
        {"A|B": pairwise_corr},
        {"A|B": 52},
    )
    assert selected == expected


@pytest.mark.parametrize(
    ("correlations", "observations"),
    [
        ({"A|B": float("nan")}, {"A|B": 52}),
        ({"A|B": 0.10}, {"A|B": 19}),
    ],
)
def test_r65_unusable_pairwise_data_is_conservatively_skipped(
    correlations, observations
):
    selected, diagnostics = select_r65_rank_buffered_direct_corr(
        ["A", "B"], {"A": 1.0 / 3.0}, correlations, observations
    )
    assert selected == ["A"]
    assert diagnostics["correlation"]["correlation_rejected_candidates"]["B"] == (
        "PAIRWISE_CORRELATION_UNAVAILABLE"
    )


def test_r65_rank_buffer_selection_is_deterministic_under_mapping_order():
    first = select_r65_rank_buffered_direct_corr(
        ["A", "B", "C", "D"],
        {"D": 1.0 / 3.0, "B": 1.0 / 3.0},
        {"A|B": 0.1, "A|C": 0.1, "A|D": 0.1, "B|C": 0.1, "B|D": 0.1, "C|D": 0.1},
        {key: 52 for key in ("A|B", "A|C", "A|D", "B|C", "B|D", "C|D")},
    )
    second = select_r65_rank_buffered_direct_corr(
        ["A", "B", "C", "D"],
        {"B": 1.0 / 3.0, "D": 1.0 / 3.0},
        {"C|D": 0.1, "B|D": 0.1, "B|C": 0.1, "A|D": 0.1, "A|C": 0.1, "A|B": 0.1},
        {key: 52 for key in ("C|D", "B|D", "B|C", "A|D", "A|C", "A|B")},
    )
    assert first == second


def test_r65_rank_buffer_exits_held_rank_five_and_matches_r64_without_holdings():
    ranked = ["A", "B", "C", "D", "E"]
    correlations, observations = _pair_data(ranked)

    selected, diagnostics = select_r65_rank_buffered_direct_corr(
        ranked,
        {"E": 1.0 / 3.0},
        correlations,
        observations,
    )
    r64_selected, _ = select_direct_correlation_diversified(
        ranked,
        correlations,
        observations,
    )
    no_holdings_selected, _ = select_r65_rank_buffered_direct_corr(
        ranked,
        {},
        correlations,
        observations,
    )

    assert selected == r64_selected == ["A", "B", "C"]
    assert no_holdings_selected == r64_selected
    assert diagnostics["forced_exit_codes"] == ["E"]


def _rows() -> dict[str, dict[str, object]]:
    return {
        code: {
            "ts_code": code,
            "bias": value,
            "slope": value,
            "raw_slope_25d": value,
            "efficiency": value,
        }
        for code, value in (
            ("A", 5.0),
            ("B", 4.0),
            ("C", 3.0),
            ("D", 2.0),
            ("E", 1.0),
        )
    }


def _context(view: object) -> StrategyDecisionContext:
    return StrategyDecisionContext(signal_date="20240105", data_view=view)


def _patch_r65_inputs(monkeypatch, rows):
    weekly_returns = pd.DataFrame(
        {
            code: np.random.default_rng(index).normal(size=52)
            for index, code in enumerate(rows, 1)
        }
    )
    monkeypatch.setattr(
        r65_module,
        "ensure_instrument_pool",
        lambda view, lookback_trade_days: pd.DataFrame(),
    )
    monkeypatch.setattr(
        r65_module,
        "check_historical_eligibility",
        lambda pool, date: (list(rows), []),
    )
    monkeypatch.setattr(
        r65_module,
        "signal_date_eligible",
        lambda view, eligible, date: (list(eligible), []),
    )
    monkeypatch.setattr(
        r65_module.AiRotationR58R39SignalR57Session,
        "_factor_rows",
        lambda self, view, date: rows,
    )
    return SimpleNamespace(
        returns=lambda frequency, lookback: weekly_returns,
    )


def _patch_module_inputs(monkeypatch, module, rows, weekly_returns):
    monkeypatch.setattr(module, "ensure_instrument_pool", lambda view, lookback_trade_days: pd.DataFrame())
    monkeypatch.setattr(module, "check_historical_eligibility", lambda pool, date: (list(rows), []))
    monkeypatch.setattr(module, "signal_date_eligible", lambda view, eligible, date: (list(eligible), []))
    monkeypatch.setattr(module.AiRotationR58R39SignalR57Session, "_factor_rows", lambda self, view, date: rows)
    return SimpleNamespace(returns=lambda frequency, lookback: weekly_returns)


def _strip_r65_only_fields(value):
    if isinstance(value, dict):
        return {
            key: _strip_r65_only_fields(item)
            for key, item in value.items()
            if key not in {"rank_buffer", "selection_filter", "decision_id"}
        }
    if isinstance(value, list):
        return [_strip_r65_only_fields(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_strip_r65_only_fields(item) for item in value)
    return value


def test_r65_without_buffer_is_value_equivalent_to_r64_across_lifecycle_and_artifacts(
    monkeypatch,
):
    weekly_returns = pd.DataFrame(
        {code: np.random.default_rng(index).normal(size=52) for index, code in enumerate(_rows(), 1)}
    )
    r64_rows = _rows()
    r64_view = _patch_module_inputs(monkeypatch, r64_module, r64_rows, weekly_returns)
    r64_session = AiRotationR64DirectCorrDiversificationStrategy().create_session(
        None, AiRotationR64DirectCorrDiversificationStrategy().config_model()
    )
    r64_decision = r64_session.evaluate(_context(r64_view))
    r64_finalized = r64_session.finalize()

    r65_rows = copy.deepcopy(_rows())
    r65_view = _patch_module_inputs(monkeypatch, r65_module, r65_rows, weekly_returns)
    r65_session = AiRotationR65R64DirectCorrRankBufferStrategy().create_session(
        None, AiRotationR65R64DirectCorrRankBufferStrategy().config_model()
    )
    r65_decision = r65_session.evaluate(_context(r65_view))
    r65_finalized = r65_session.finalize()

    assert r65_decision.diagnostics["rank_buffer"]["retained_codes"] == []
    assert r65_decision.diagnostics["selected_codes"] == r64_decision.diagnostics["selected_codes"]
    assert r65_decision.diagnostics["factor_scores"] == r64_decision.diagnostics["factor_scores"]
    assert dict(r65_decision.target_weights) == dict(r64_decision.target_weights)
    assert r65_decision.cash_weight == pytest.approx(r64_decision.cash_weight)
    assert _strip_r65_only_fields(r65_decision.diagnostics) == _strip_r65_only_fields(r64_decision.diagnostics)
    assert _strip_r65_only_fields(r65_finalized.decision_trace) == _strip_r65_only_fields(r64_finalized.decision_trace)
    assert [(item.role, item.media_type) for item in r65_finalized.artifacts] == [
        (item.role, item.media_type) for item in r64_finalized.artifacts
    ]
    for r65_artifact, r64_artifact in zip(r65_finalized.artifacts, r64_finalized.artifacts):
        assert _strip_r65_only_fields(r65_artifact.payload) == _strip_r65_only_fields(r64_artifact.payload)
    assert "portfolio_weighting" not in r65_decision.diagnostics
    assert "portfolio_weighting" not in json.dumps(r65_decision.diagnostics)


def test_r65_signal_date_future_append_does_not_change_causal_result(monkeypatch):
    rows = _rows()
    dates = pd.date_range("2023-01-06", periods=52, freq="W-FRI")
    base = pd.DataFrame({code: np.random.default_rng(index).normal(size=52) for index, code in enumerate(rows, 1)}, index=dates)
    appended = pd.concat([base, pd.DataFrame({code: 1000.0 for code in rows}, index=[pd.Timestamp("2024-01-12")])])
    signal_date = pd.Timestamp("2024-01-05")

    def causal_view(frame):
        _patch_module_inputs(monkeypatch, r65_module, rows, frame.loc[frame.index <= signal_date].tail(52))
        return SimpleNamespace(returns=lambda frequency, lookback: frame.loc[frame.index <= signal_date].tail(lookback))

    first = AiRotationR65R64DirectCorrRankBufferStrategy().create_session(None, AiRotationR65R64DirectCorrRankBufferStrategy().config_model()).evaluate(_context(causal_view(base)))
    second = AiRotationR65R64DirectCorrRankBufferStrategy().create_session(None, AiRotationR65R64DirectCorrRankBufferStrategy().config_model()).evaluate(_context(causal_view(appended)))
    assert first.diagnostics["selected_codes"] == second.diagnostics["selected_codes"]
    assert dict(first.target_weights) == dict(second.target_weights)
    assert first.cash_weight == pytest.approx(second.cash_weight)


def test_r65_final_weights_are_nonnegative_and_bounded_without_invvol_layer(monkeypatch):
    rows = _rows()
    view = _patch_r65_inputs(monkeypatch, rows)
    decision = AiRotationR65R64DirectCorrRankBufferStrategy().create_session(
        None, AiRotationR65R64DirectCorrRankBufferStrategy().config_model()
    ).evaluate(_context(view))
    assert all(float(weight) >= 0.0 for weight in decision.target_weights.values())
    assert decision.cash_weight >= 0.0
    assert sum(map(float, decision.target_weights.values())) + float(decision.cash_weight) <= 1.0 + 1e-12
    assert "portfolio_weighting" not in decision.diagnostics


def test_r65_session_preserves_r64_pipeline_and_publishes_invariants(monkeypatch):
    rows = _rows()
    view = _patch_r65_inputs(monkeypatch, rows)
    strategy = AiRotationR65R64DirectCorrRankBufferStrategy()
    session = strategy.create_session(None, strategy.config_model())
    session._previous_weights = {"D": 1.0 / 3.0}

    decision = session.evaluate(_context(view))
    finalized = session.finalize()

    assert decision.diagnostics["selected_codes"] == ["D", "A", "B"]
    rank_buffer = decision.diagnostics["rank_buffer"]
    assert rank_buffer["retained_codes"] == ["D"]
    assert rank_buffer["current_rank_by_code"]["D"] == 4
    assert decision.diagnostics["correlation"]["selected_codes"] == ["D", "A", "B"]
    assert decision.diagnostics["factor_scores"]["D"]["base_slot_weight"] == pytest.approx(1.0 / 3.0)
    assert decision.target_weights["D"] == pytest.approx(2.0 / 3.0)

    validate_diagnostics(decision.diagnostics)
    json.dumps(decision.diagnostics, allow_nan=False)
    trace = finalized.decision_trace[-1]
    for candidate in trace["candidates"]:
        parsed = CandidateDecisionRow.model_validate(candidate)
        assert parsed.target_weight == pytest.approx(
            decision.target_weights.get(parsed.ts_code, 0.0)
        )
    factor_artifact = next(item for item in finalized.artifacts if item.role == "factor_scores")
    for code, row in factor_artifact.payload[-1]["rows"].items():
        assert row["final_weight"] == pytest.approx(decision.target_weights.get(code, 0.0))
        assert row["cash_weight"] == pytest.approx(decision.cash_weight)
    decision_artifact = next(item for item in finalized.artifacts if item.role == "decisions")
    assert decision_artifact.payload[-1]["target_weights"] == decision.target_weights


def test_r65_strategy_is_independent_and_catalog_descriptor_is_frozen():
    strategy = AiRotationR65R64DirectCorrRankBufferStrategy()
    assert strategy.descriptor.id == "ai_rotation_r65_r64_direct_corr_rank_buffer"
    assert strategy.config_model().top_n == 3
    r65_session = strategy.create_session(None, strategy.config_model())
    r64_strategy = AiRotationR64DirectCorrDiversificationStrategy()
    r64_session = r64_strategy.create_session(None, r64_strategy.config_model())
    assert type(r65_session) is not type(r64_session)
    assert type(r65_session).__module__.endswith(
        "ai_rotation_r65_r64_direct_corr_rank_buffer.strategy"
    )


@pytest.mark.parametrize("corr, expected", [(0.80, "PAIRWISE_CORRELATION_TOO_HIGH"), (0.79, None)])
def test_r65_uses_strict_pairwise_boundary(corr, expected):
    selected, diagnostics = select_r65_rank_buffered_direct_corr(
        ["A", "B", "C"], {"A": 1 / 3}, {"A|B": corr, "A|C": 0.1, "B|C": 0.1}, {"A|B": 52, "A|C": 52, "B|C": 52}
    )
    if expected:
        assert selected == ["A", "C"]
        assert diagnostics["correlation"]["correlation_rejected_candidates"]["B"] == expected
    else:
        assert selected == ["A", "B", "C"]


def test_r65_unknown_or_short_pairwise_history_is_conservatively_skipped():
    selected, diagnostics = select_r65_rank_buffered_direct_corr(
        ["A", "B", "C"], {"A": 1 / 3}, {"A|B": float("nan"), "A|C": 0.1, "B|C": 0.1}, {"A|B": 19, "A|C": 52, "B|C": 52}
    )
    assert selected == ["A", "C"]
    assert diagnostics["correlation"]["correlation_rejected_candidates"]["B"] == "PAIRWISE_CORRELATION_UNAVAILABLE"


def test_r65_selector_is_deterministic_and_final_weights_are_safe():
    args = (["A", "B", "C", "D"], {"D": 1 / 3}, {"A|B": 0.0, "A|C": 0.0, "A|D": 0.0, "B|C": 0.0, "B|D": 0.0, "C|D": 0.0}, {key: 52 for key in ("A|B", "A|C", "A|D", "B|C", "B|D", "C|D")})
    first = select_r65_rank_buffered_direct_corr(*args)
    second = select_r65_rank_buffered_direct_corr(*args)
    assert first == second
    assert all(float(weight) >= 0 for weight in {"A": 1 / 3, "B": 1 / 3}.values())
    assert sum({"A": 1 / 3, "B": 1 / 3}.values()) <= 1


def test_r65_session_matches_r64_lifecycle_when_buffer_is_not_triggered(monkeypatch):
    rows = _rows()
    weekly_returns = pd.DataFrame({code: np.random.default_rng(index).normal(size=52) for index, code in enumerate(rows, 1)})
    for module in (r65_module, r64_module):
        monkeypatch.setattr(module, "ensure_instrument_pool", lambda view, lookback_trade_days: pd.DataFrame())
        monkeypatch.setattr(module, "check_historical_eligibility", lambda pool, date: (list(rows), []))
        monkeypatch.setattr(module, "signal_date_eligible", lambda view, eligible, date: (list(eligible), []))
        monkeypatch.setattr(module.AiRotationR58R39SignalR57Session, "_factor_rows", lambda self, view, date: copy.deepcopy(rows))
    view = SimpleNamespace(returns=lambda frequency, lookback: weekly_returns)
    r65 = AiRotationR65R64DirectCorrRankBufferStrategy().create_session(None, AiRotationR65R64DirectCorrRankBufferStrategy().config_model())
    r64 = AiRotationR64DirectCorrDiversificationStrategy().create_session(None, AiRotationR64DirectCorrDiversificationStrategy().config_model())
    d65, d64 = r65.evaluate(_context(view)), r64.evaluate(_context(view))
    f65, f64 = r65.finalize(), r64.finalize()
    assert d65.target_weights == d64.target_weights
    assert d65.cash_weight == d64.cash_weight
    assert d65.diagnostics["factor_scores"] == d64.diagnostics["factor_scores"]
    assert f65.decision_trace[-1]["target_weights"] == f64.decision_trace[-1]["target_weights"]
    assert d65.diagnostics["correlation"]["selected_codes"] == d64.diagnostics["correlation"]["selected_codes"]
    assert "portfolio_weighting" not in d65.diagnostics
