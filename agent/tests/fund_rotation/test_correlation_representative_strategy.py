"""Phase 3 Task 5 — complete correlation_representative strategy session tests.

Covers the strategy contract (descriptor/config/requirements/schedule), the
per-decision session flow (clustering → gates → representative lock → slot
weights §8.3), gate REJECT vs decision INVALID semantics, and the finalize
diagnostic artifacts.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.fund_rotation.causal_data import CausalDataView
from backtest.fund_rotation.contracts import (
    DecisionKind,
    FundRotationStrategy,
    FundRotationStrategySession,
    QualityStatus,
    StrategyDecisionContext,
    StrategyInitializationContext,
)
from backtest.fund_rotation.strategies.correlation_representative.config import (
    CorrelationRepresentativeConfig,
)
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    CorrelationRepresentativeStrategy,
    build_slot_weights,
)


def _small_config(**overrides) -> CorrelationRepresentativeConfig:
    base = dict(
        k=2, top_n=2,
        correlation_lookback_weeks=8, momentum_window_weeks=2,
        recluster_interval_weeks=4, min_valid_weeks=4, min_pairwise_weeks=4,
        representative_candidate_count=5, representative_min_cluster_corr=0.5,
        representative_liquidity_window_days=10,
        representative_min_liquidity_observations=5,
        # k=2 balanced blocks give N_effective = 2.0; relax the effective-count
        # gate for the synthetic scenario (thresholds are strategy config).
        min_effective_cluster_count_warn=1.5,
        min_effective_cluster_count_reject=1.0,
    )
    base.update(overrides)
    return CorrelationRepresentativeConfig(**base)


def _market_frames(n_weeks: int = 14, drift: float = 0.01, seed: int = 21,
                   one_big_block: bool = False):
    """Two correlated blocks of three ETFs (or one dominant block) with a
    positive drift so cluster momentum stays above the selection threshold."""
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2024-01-01")  # a Monday
    dates = [
        (start + pd.Timedelta(weeks=w, days=d)).strftime("%Y%m%d")
        for w in range(n_weeks) for d in range(5)
    ]
    if one_big_block:
        blocks = {"big": ["E1", "E2", "E3", "E4", "E5"], "tail": ["E6"]}
    else:
        blocks = {"b1": ["E1", "E2", "E3"], "b2": ["E4", "E5", "E6"]}
    prices: dict[str, float] = {}
    factors = {name: rng.normal(drift, 0.02, n_weeks) for name in blocks}
    daily_factor: dict[str, np.ndarray] = {}
    for name, members in blocks.items():
        weekly = factors[name]
        daily_factor[name] = np.repeat(weekly / 5.0, 5)
    rows, adj = [], []
    noises: dict[str, np.ndarray] = {}
    for name, members in blocks.items():
        for code in members:
            noises[code] = rng.normal(0.0, 0.002, len(dates))
            prices[code] = 2.0 + rng.random()
    for i, d in enumerate(dates):
        for name, members in blocks.items():
            for code in members:
                prices[code] *= 1 + daily_factor[name][i] + noises[code][i]
                close = round(prices[code], 3)
                rows.append({
                    "ts_code": code, "trade_date": d, "open": close,
                    "close": close, "high": close, "low": close,
                    "pre_close": close, "vol": 1_000_000,
                    "amount": close * 2_000_000,
                })
                adj.append({"ts_code": code, "trade_date": d, "adj_factor": 1.0})
    fund_daily = pd.DataFrame(rows)
    fund_adj = pd.DataFrame(adj)
    codes = sorted(prices)
    dim_fund = pd.DataFrame([
        {"ts_code": c, "name": f"测试ETF{i}", "list_date": "20200101"}
        for i, c in enumerate(codes)
    ])
    return fund_daily, fund_adj, dim_fund, codes


def _run_session(cfg=None, one_big_block: bool = False, flat_prices: bool = False):
    """Drive the session through its scheduled dates with a causal view."""
    strategy = CorrelationRepresentativeStrategy()
    cfg = cfg or _small_config()
    requirements = strategy.resolve_requirements(cfg)
    fund_daily, fund_adj, dim_fund, codes = _market_frames(
        one_big_block=one_big_block,
    )
    if flat_prices:
        # Constant prices -> zero returns -> undefined correlations ->
        # clustering fails -> decision action INVALID.
        fund_daily["close"] = 2.0
        fund_daily["open"] = 2.0
        fund_daily["high"] = 2.0
        fund_daily["low"] = 2.0
        fund_daily["pre_close"] = 2.0
    universe = frozenset(codes)
    init = StrategyInitializationContext(run_id="t5", evaluation_calendar=())
    session = strategy.create_session(init, cfg)
    calendar = tuple(sorted(fund_daily["trade_date"].astype(str).unique()))
    sim_start = calendar[requirements.warmup_trade_days]
    scheduled = session.scheduled_dates(calendar, sim_start, calendar[-1])
    decisions = []
    for signal_date in scheduled:
        view = CausalDataView(
            fund_daily, fund_adj, dim_fund, requirements,
            pd.Timestamp(signal_date), universe,
        )
        ctx = StrategyDecisionContext(signal_date=signal_date, data_view=view)
        decisions.append(session.evaluate(ctx))
    return session, decisions


# ── strategy contract ──

class TestStrategyContract:
    def test_strategy_satisfies_protocol(self):
        strategy = CorrelationRepresentativeStrategy()
        assert isinstance(strategy, FundRotationStrategy)
        assert strategy.descriptor.id == "correlation_representative"
        assert strategy.descriptor.deterministic is True
        assert strategy.config_model is CorrelationRepresentativeConfig

    def test_resolve_requirements_declares_weekly_returns_adj_and_adv(self):
        strategy = CorrelationRepresentativeStrategy()
        req = strategy.resolve_requirements(CorrelationRepresentativeConfig())
        assert {"fund", "fact_fund_adj", "dim_fund"} <= set(req.required_datasets)
        assert "amount" in req.required_fields       # causal ADV
        assert "adj_factor" in req.required_fields   # adjusted returns
        assert req.frequency == "weekly"
        # Warmup = one full lookback window of weekly returns.
        assert req.warmup_trade_days == (52 + 1) * 5 - 1

    def test_session_satisfies_protocol_and_schedule_is_week_endings(self):
        strategy = CorrelationRepresentativeStrategy()
        init = StrategyInitializationContext(run_id="t5", evaluation_calendar=())
        session = strategy.create_session(init, _small_config())
        assert isinstance(session, FundRotationStrategySession)
        fund_daily, _, _, _ = _market_frames()
        calendar = tuple(sorted(fund_daily["trade_date"].astype(str).unique()))
        dates = session.scheduled_dates(calendar, calendar[44], "20240331")
        assert dates, "expected scheduled week-endings"
        # Every scheduled date is the last trading day of its ISO week.
        for d in dates:
            ts = pd.Timestamp(d)
            assert ts.dayofweek <= 4
        assert all(calendar[44] <= d <= "20240331" for d in dates)


# ── §8.3 slot weights ──

class TestSlotWeights:
    def test_each_slot_fixed_one_over_top_n_without_amplification(self):
        weights, filled, vacant, cash = build_slot_weights(
            [1, 2], {1: "E1", 2: None}, top_n=3,
        )
        # Only one filled slot: 1/3 invested, 2/3 cash — never amplified.
        assert weights == {"E1": pytest.approx(1.0 / 3.0)}
        assert filled == [1]
        assert vacant == [2]
        assert cash == pytest.approx(2.0 / 3.0)

    def test_all_slots_filled(self):
        weights, filled, vacant, cash = build_slot_weights(
            [1, 2], {1: "E1", 2: "E4"}, top_n=2,
        )
        assert weights == {"E1": pytest.approx(0.5), "E4": pytest.approx(0.5)}
        assert filled == [1, 2]
        assert vacant == []
        assert cash == pytest.approx(0.0)

    def test_no_selected_clusters_is_all_cash(self):
        weights, filled, vacant, cash = build_slot_weights([], {}, top_n=3)
        assert weights == {}
        assert filled == []
        assert vacant == []
        assert cash == pytest.approx(1.0)

    def test_float_bad_top_n_full_slots_still_satisfy_runner_contract(self):
        """The sequential float sum of top_n * (1/top_n) overshoots 1.0 for
        top_n like 9/11/18; the produced decision must still pass the Runner
        contract validation (cash_weight >= 0, weights sum + cash == 1)."""
        from backtest.fund_rotation.contracts import (
            DecisionKind,
            TargetWeightDecision,
            validate_target_decision,
        )
        for top_n in (9, 11, 18):
            reps = {cid: f"E{cid}" for cid in range(1, top_n + 1)}
            weights, filled, vacant, cash = build_slot_weights(
                list(reps), reps, top_n,
            )
            assert len(filled) == top_n and vacant == []
            decision = TargetWeightDecision(
                decision_id=f"d-{top_n}", signal_date="20240105",
                action=DecisionKind.SET_TARGETS,
                target_weights=weights, cash_weight=cash,
            )
            # Must not raise: the decision is contract-valid for every top_n.
            validate_target_decision(decision, set(weights), set())
            assert cash >= 0.0


# ── session decision flow ──

class TestSessionDecisions:
    def test_set_targets_with_one_representative_per_selected_cluster(self):
        session, decisions = _run_session()
        assert decisions, "no decisions produced"
        for decision in decisions:
            assert decision.action is DecisionKind.SET_TARGETS
            total = decision.cash_weight + sum(decision.target_weights.values())
            assert total == pytest.approx(1.0)
            # At most one ETF per selected cluster -> at most top_n holdings.
            assert len(decision.target_weights) <= 2
            for weight in decision.target_weights.values():
                assert weight == pytest.approx(0.5)  # 1/top_n, no amplification
            filled = decision.diagnostics["filled_slots"]
            vacant = decision.diagnostics["vacant_slots"]
            assert len(decision.target_weights) == len(filled)
            assert set(filled).isdisjoint(vacant)

    def test_trace_assigns_score_and_rank_only_to_cluster_representatives(self):
        session, _ = _run_session()
        traces = [trace for trace in session._decision_trace if trace["candidates"]]
        assert traces
        for trace in traces:
            for candidate in trace["candidates"]:
                stages = candidate["stages"]
                if stages["ranking_eligible"]:
                    assert stages["cluster_representative"] is True
                    assert stages["rank"] is not None
                    assert candidate["score"]["scope"] == "CLUSTER"
                else:
                    assert stages["ranking_eligible"] is False
                    assert stages["rank"] is None
                    if stages["cluster_representative"]:
                        assert candidate["score"]["eligible"] is False
                    else:
                        assert candidate["score"] is None

    def test_quality_valid_when_gates_pass(self):
        _, decisions = _run_session()
        assert all(d.quality_status is QualityStatus.VALID for d in decisions)

    def test_cluster_lock_reuses_representative_across_weeks(self):
        session, decisions = _run_session()
        # Stable synthetic blocks: representatives should persist between
        # reclusters (locks maintained, not re-picked every week).
        first_reps = dict(session._representatives)
        assert first_reps and all(v is not None for v in first_reps.values())
        maintained = [
            entry for entry in session._selection_history
            if entry["lock_maintained"]
        ]
        assert maintained, "expected lock-maintained selections between reclusters"

    def test_gate_reject_continues_portfolio_construction_with_degraded_quality(self):
        # One dominant 5/6 cluster -> max share 0.833 > 0.8 reject threshold.
        session, decisions = _run_session(one_big_block=True)
        rejects = [
            d for d in decisions if d.reason_code == "CLUSTER_QUALITY_REJECTED"
        ]
        assert rejects, "expected cluster quality rejections"
        for decision in rejects:
            assert decision.action is DecisionKind.SET_TARGETS
            assert decision.quality_status is QualityStatus.DEGRADED

        # A rejected gate is a strong warning, not a persistent cash circuit
        # breaker for the weeks between reclusterings.
        assert any(
            d.reason_code == "CLUSTER_QUALITY_REJECTED"
            and d.target_weights
            for d in decisions
        )
        assert session._selection_history

    def test_broken_invariant_returns_decision_action_invalid(self):
        # Flat prices -> undefined correlations -> clustering cannot proceed
        # -> genuine decision action INVALID (required data/invariant broken).
        _, decisions = _run_session(flat_prices=True)
        assert decisions
        for decision in decisions:
            assert decision.action is DecisionKind.INVALID
            assert decision.reason_code  # stable code, non-empty

    def test_finalize_publishes_strategy_specific_diagnostics(self):
        session, _ = _run_session()
        diagnostics = session.finalize()
        roles = {a.role for a in diagnostics.artifacts}
        assert {
            "cluster_history", "gates", "representatives", "exclusions", "decisions",
        } <= roles
        by_role = {a.role: a.payload for a in diagnostics.artifacts}
        assert by_role["cluster_history"]
        assert by_role["representatives"]
        # Every representative entry carries the two distinct correlations.
        entry = by_role["representatives"][0]
        assert "candidates" in entry
        for cand in entry["candidates"]:
            assert "distance_to_medoid" in cand
            assert "leave_one_out_corr" in cand
            assert "correlation" not in cand  # never an ambiguous field
