"""Phase 2 Task 4 — baseline strategy through the common Runner (parity).

Since Phase 2 Task 6 the legacy ``run_signal_pipeline`` is itself a Runner
adapter, so this module verifies ADAPTER vs DIRECT-RUNNER consistency (same
targets/execution evidence through both entry points). The independent
behavioral baseline is the frozen Phase 0 golden (test_phase0_golden.py),
which pins the pre-migration pipeline output field-by-field under §35.1
tolerances (with the documented Phase 0 approved_delta exemptions for the
initial_nav anchor and full-interval positions).

Divergences beyond those are NOT tolerated here: the baseline must reproduce
the legacy behavior exactly until a proven root cause says otherwise.
"""

from __future__ import annotations

import json

import pytest

from backtest.fund_rotation.evaluation import EvaluationContext
from backtest.fund_rotation.metrics import compute_performance_metrics
from backtest.fund_rotation.pipeline import run_signal_pipeline
from backtest.fund_rotation.runner import (
    CancellationToken,
    ExecutionConfig,
    FundRotationBacktestRunner,
    SubRunStatus,
)
from backtest.fund_rotation.strategies.correlation_all_members.config import (
    CorrelationAllMembersConfig,
)
from backtest.fund_rotation.strategies.correlation_all_members.strategy import (
    CorrelationAllMembersStrategy,
)
from backtest.fund_rotation.universe import filter_etf_universe
from src.stockpred.fund_rotation.data_snapshot import PinnedFundDataSnapshot
from tests.fund_rotation.test_phase0_golden import (
    METRIC_TOL,
    MONEY_TOL,
    NAV_TOL,
    WEIGHT_TOL,
    _curve_diffs,
    _row_diffs,
    _series_to_map,
    build_config,
    build_golden_data,
)


def _run_legacy():
    fund_daily, fund_adj, dim_fund = build_golden_data()
    result = run_signal_pipeline(build_config(), fund_daily, fund_adj, dim_fund)
    return result


def _run_new():
    fund_daily, fund_adj, dim_fund = build_golden_data()
    legacy_cfg = build_config()
    cfg = CorrelationAllMembersConfig.from_legacy(legacy_cfg)

    universe = tuple(sorted(
        {str(c) for c in filter_etf_universe(dim_fund)["ts_code"]}
    ))
    trading_dates = tuple(sorted({str(d) for d in fund_daily["trade_date"]}))
    snapshot = PinnedFundDataSnapshot(
        fund_version=0, fund_adj_version=0, dim_version=0,
        universe_codes=universe, trading_dates=trading_dates,
        fingerprint="parity-test",
    )
    evaluation = EvaluationContext.from_range(
        trading_dates, legacy_cfg.start_date, legacy_cfg.end_date,
    )
    execution = ExecutionConfig(
        initial_capital=legacy_cfg.initial_capital,
        commission_rate=legacy_cfg.commission_rate,
        commission_min=legacy_cfg.commission_min,
        other_fee_rate=legacy_cfg.other_fee_rate,
        max_participation_rate=legacy_cfg.max_participation_rate,
        adv_lookback=legacy_cfg.adv_lookback,
        adv_min_observations=legacy_cfg.adv_min_observations,
        base_slippage_bps=legacy_cfg.base_slippage_bps,
        max_slippage_bps=legacy_cfg.max_slippage_bps,
    )
    runner = FundRotationBacktestRunner(fund_daily, fund_adj, dim_fund, run_id="parity")
    return runner.run(
        strategy=CorrelationAllMembersStrategy(),
        config=cfg,
        snapshot=snapshot,
        evaluation=evaluation,
        execution=execution,
        cancellation=CancellationToken(),
    )


def _approx(value, tol: dict):
    return pytest.approx(value, rel=tol["rtol"], abs=tol["atol"])


@pytest.fixture(scope="module")
def legacy_result():
    return _run_legacy()


@pytest.fixture(scope="module")
def runner_result():
    return _run_new()


def test_baseline_run_succeeds(runner_result):
    assert runner_result.status is SubRunStatus.SUCCEEDED, (
        f"{runner_result.error_code}: {runner_result.error_message}"
    )


def test_rebalance_schedule_and_targets_match(legacy_result, runner_result):
    """Discrete schedule exact; weights atol 1e-12 (§35.1)."""
    old = legacy_result.weekly_targets
    new = runner_result.weekly_targets
    assert sorted(old) == sorted(new), (
        f"rebalance weeks differ: legacy={sorted(old)[:3]}... runner={sorted(new)[:3]}..."
    )
    for week in old:
        ow, nw = old[week], new[week]
        assert set(ow) == set(nw), f"targets[{week}] codes differ"
        for code in ow:
            assert ow[code] == _approx(nw[code], WEIGHT_TOL), (
                f"targets[{week}][{code}]: {ow[code]} != {nw[code]}"
            )


def test_cluster_history_matches(legacy_result, runner_result):
    """Cluster diagnostics are surfaced via finalize and must match exactly."""
    artifacts = {a.role: a for a in runner_result.diagnostics.artifacts}
    assert "cluster_history" in artifacts
    new_history = json.loads(json.dumps(artifacts["cluster_history"].payload))
    old_history = [
        {
            "week": str(ch["week"]),
            "clusters": {str(c): int(cid) for c, cid in sorted(ch["clusters"].items())},
            "num_etfs": int(ch["num_etfs"]),
        }
        for ch in legacy_result.cluster_history
    ]
    assert new_history == old_history, "cluster history diverges from legacy"


def test_orders_and_trade_events_match(legacy_result, runner_result):
    """Row-wise comparison with §35.1 field-class tolerances."""
    from tests.fund_rotation.test_phase0_golden import _clean_row

    old_orders = [_clean_row(r) for r in legacy_result.orders]
    new_orders = [_clean_row(r) for r in runner_result.orders]
    assert len(old_orders) == len(new_orders), (
        f"orders length differs: legacy={len(old_orders)} runner={len(new_orders)}"
    )
    diffs = []
    for i, (e_row, a_row) in enumerate(zip(old_orders, new_orders)):
        diffs.extend(_row_diffs(f"orders[{i}]", e_row, a_row))
    assert not diffs, "order divergence:\n" + "\n".join(diffs[:30])

    old_events = [_clean_row(r) for r in legacy_result.trade_events]
    new_events = [_clean_row(r) for r in runner_result.trade_events]
    assert len(old_events) == len(new_events), (
        f"trade_events length differs: legacy={len(old_events)} runner={len(new_events)}"
    )
    diffs = []
    for i, (e_row, a_row) in enumerate(zip(old_events, new_events)):
        diffs.extend(_row_diffs(f"trade_events[{i}]", e_row, a_row))
    assert not diffs, "trade event divergence:\n" + "\n".join(diffs[:30])


def test_executed_equity_and_positions_match(legacy_result, runner_result):
    # The legacy pipeline reindexes executed_equity to the strategy/benchmark
    # common interval (135 days from the first fill); the Runner produces the
    # design-§24 full-interval equity (400 days). Values must agree exactly on
    # the legacy common interval, and the Runner must cover the full calendar.
    old_map = _series_to_map(legacy_result.executed_equity)
    new_map = _series_to_map(runner_result.executed_equity)
    assert set(old_map).issubset(set(new_map)), "legacy interval not covered"
    diffs = _curve_diffs(
        "executed_equity",
        old_map,
        {d: new_map[d] for d in old_map},
        NAV_TOL,
    )
    assert not diffs, "equity divergence:\n" + "\n".join(diffs[:30])
    assert runner_result.executed_equity.index[0] == "20220103"
    assert runner_result.executed_equity.index[-1] == "20230714"
    assert len(new_map) == 400

    old_pos = legacy_result.positions_history
    new_pos = runner_result.positions_history
    assert len(old_pos) == len(new_pos), "positions_history length differs"
    for i, (op, np_) in enumerate(zip(old_pos, new_pos)):
        assert op["trade_date"] == np_["trade_date"], f"positions[{i}] date differs"
        assert op["cash"] == _approx(np_["cash"], MONEY_TOL)
        assert op["equity"] == _approx(np_["equity"], MONEY_TOL)
        assert {str(c): int(q) for c, q in op["positions"].items()} == {
            str(c): int(q) for c, q in np_["positions"].items()
        }, f"positions[{i}] integer shares differ"


def test_strategy_metrics_match(legacy_result, runner_result):
    """Metrics parity on the full-interval executed equity.

    The legacy positions_history carries the pre-reindex daily equity over the
    full evaluation calendar; normalizing it by the initial capital reproduces
    the equity the Runner feeds to the common metric function.
    """
    import pandas as pd

    legacy_full_equity = pd.Series(
        [p["equity"] / build_config().initial_capital
         for p in legacy_result.positions_history],
        index=[p["trade_date"] for p in legacy_result.positions_history],
        name="executed_strategy",
    )
    expected = compute_performance_metrics(
        legacy_full_equity, periods_per_year=244, initial_nav=1.0,
    )
    actual = runner_result.strategy_metrics
    assert set(expected) == set(actual), (
        f"metric keys differ: legacy={sorted(expected)} runner={sorted(actual)}"
    )
    for key in expected:
        assert expected[key] == _approx(actual[key], METRIC_TOL), (
            f"metrics.{key}: {expected[key]} != {actual[key]}"
        )
