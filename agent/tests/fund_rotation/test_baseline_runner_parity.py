"""Phase 2 Task 4 — baseline strategy through the common Runner (parity).

The legacy adapter and direct Runner must match on targets, execution evidence
and common strategy metrics. Runner-only relative/excess diagnostics are
validated as an intentional additive contract rather than treated as a parity
failure.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from backtest.fund_rotation import pipeline as pipeline_module
from backtest.fund_rotation.evaluation import EvaluationContext
from backtest.fund_rotation.metrics import compute_performance_metrics
from backtest.fund_rotation.pipeline import run_signal_pipeline
from backtest.fund_rotation.pit_universe import PITQueryMode
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
from tests.fund_rotation.conftest import make_test_market_rule_inputs


def _run_legacy():
    fund_daily, fund_adj, dim_fund = build_golden_data()
    with patch.object(
        pipeline_module,
        "FundRotationBacktestRunner",
        _rule_aware_runner_class,
    ):
        return run_signal_pipeline(build_config(), fund_daily, fund_adj, dim_fund)


class _RuleAwareRunner(FundRotationBacktestRunner):
    def run(self, *args, snapshot, **kwargs):
        if int(getattr(snapshot, "dim_version", 0)) < 1:
            snapshot = PinnedFundDataSnapshot(
                fund_version=snapshot.fund_version,
                fund_adj_version=snapshot.fund_adj_version,
                dim_version=1,
                universe_codes=tuple(snapshot.universe_codes),
                trading_dates=tuple(snapshot.trading_dates),
                fingerprint=snapshot.fingerprint,
            )
        return super().run(*args, snapshot=snapshot, **kwargs)


def _rule_aware_runner_class(fund_daily, fund_adj, dim_fund, *args, **kwargs):
    codes = tuple(sorted(fund_daily["ts_code"].astype(str).unique()))
    rule_resolver, rule_instruments = make_test_market_rule_inputs(codes)
    return _RuleAwareRunner(
        fund_daily,
        fund_adj,
        dim_fund,
        *args,
        market_rule_resolver=rule_resolver,
        market_rule_instruments=rule_instruments,
        market_rule_mode=PITQueryMode.AS_WAS_KNOWN,
        **kwargs,
    )


def _run_new():
    fund_daily, fund_adj, dim_fund = build_golden_data()
    legacy_cfg = build_config()
    cfg = CorrelationAllMembersConfig.from_legacy(legacy_cfg)

    universe = tuple(sorted(
        {str(code) for code in filter_etf_universe(dim_fund)["ts_code"]}
    ))
    trading_dates = tuple(sorted({str(date) for date in fund_daily["trade_date"]}))
    snapshot = PinnedFundDataSnapshot(
        fund_version=0,
        fund_adj_version=0,
        dim_version=1,
        universe_codes=universe,
        trading_dates=trading_dates,
        fingerprint="parity-test",
    )
    evaluation = EvaluationContext.from_range(
        trading_dates,
        legacy_cfg.start_date,
        legacy_cfg.end_date,
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
    rule_resolver, rule_instruments = make_test_market_rule_inputs(universe)
    runner = FundRotationBacktestRunner(
        fund_daily,
        fund_adj,
        dim_fund,
        market_rule_resolver=rule_resolver,
        market_rule_instruments=rule_instruments,
        market_rule_mode=PITQueryMode.AS_WAS_KNOWN,
        run_id="parity",
    )
    return runner.run(
        strategy=CorrelationAllMembersStrategy(),
        config=cfg,
        snapshot=snapshot,
        evaluation=evaluation,
        execution=execution,
        cancellation=CancellationToken(),
    )


def _approx(value, tolerance: dict):
    return pytest.approx(
        value,
        rel=tolerance["rtol"],
        abs=tolerance["atol"],
    )


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
    old = legacy_result.weekly_targets
    new = runner_result.weekly_targets
    assert sorted(old) == sorted(new), (
        f"rebalance weeks differ: legacy={sorted(old)[:3]}... "
        f"runner={sorted(new)[:3]}..."
    )
    for week in old:
        old_weights, new_weights = old[week], new[week]
        assert set(old_weights) == set(new_weights), (
            f"targets[{week}] codes differ"
        )
        for code in old_weights:
            assert old_weights[code] == _approx(
                new_weights[code],
                WEIGHT_TOL,
            )


def test_cluster_history_matches(legacy_result, runner_result):
    artifacts = {
        artifact.role: artifact
        for artifact in runner_result.diagnostics.artifacts
    }
    assert "cluster_history" in artifacts
    new_history = json.loads(
        json.dumps(artifacts["cluster_history"].payload)
    )
    old_history = [
        {
            "week": str(entry["week"]),
            "clusters": {
                str(code): int(cluster_id)
                for code, cluster_id in sorted(entry["clusters"].items())
            },
            "num_etfs": int(entry["num_etfs"]),
        }
        for entry in legacy_result.cluster_history
    ]
    assert new_history == old_history


def test_orders_and_trade_events_match(legacy_result, runner_result):
    from tests.fund_rotation.test_phase0_golden import _clean_row

    old_orders = [_clean_row(row) for row in legacy_result.orders]
    new_orders = [_clean_row(row) for row in runner_result.orders]
    assert len(old_orders) == len(new_orders)
    diffs: list[str] = []
    for index, (expected, actual) in enumerate(zip(old_orders, new_orders)):
        diffs.extend(_row_diffs(f"orders[{index}]", expected, actual))
    assert not diffs, "order divergence:\n" + "\n".join(diffs[:30])

    old_events = [_clean_row(row) for row in legacy_result.trade_events]
    new_events = [_clean_row(row) for row in runner_result.trade_events]
    assert len(old_events) == len(new_events)
    diffs = []
    for index, (expected, actual) in enumerate(zip(old_events, new_events)):
        diffs.extend(
            _row_diffs(f"trade_events[{index}]", expected, actual)
        )
    assert not diffs, "trade event divergence:\n" + "\n".join(diffs[:30])


def test_executed_equity_and_positions_match(legacy_result, runner_result):
    old_map = _series_to_map(legacy_result.executed_equity)
    new_map = _series_to_map(runner_result.executed_equity)
    assert set(old_map).issubset(set(new_map))
    diffs = _curve_diffs(
        "executed_equity",
        old_map,
        {date: new_map[date] for date in old_map},
        NAV_TOL,
    )
    assert not diffs, "equity divergence:\n" + "\n".join(diffs[:30])
    assert runner_result.executed_equity.index[0] == "20220103"
    assert runner_result.executed_equity.index[-1] == "20230714"
    assert len(new_map) == 400

    old_positions = legacy_result.positions_history
    new_positions = runner_result.positions_history
    assert len(old_positions) == len(new_positions)
    for index, (old, new) in enumerate(zip(old_positions, new_positions)):
        assert old["trade_date"] == new["trade_date"]
        assert old["cash"] == _approx(new["cash"], MONEY_TOL)
        assert old["equity"] == _approx(new["equity"], MONEY_TOL)
        assert {
            str(code): int(quantity)
            for code, quantity in old["positions"].items()
        } == {
            str(code): int(quantity)
            for code, quantity in new["positions"].items()
        }, f"positions[{index}] integer shares differ"


def test_strategy_metrics_match(legacy_result, runner_result):
    """Core performance metrics remain parity-bound; relative metrics add data."""
    import pandas as pd

    legacy_full_equity = pd.Series(
        [
            position["equity"] / build_config().initial_capital
            for position in legacy_result.positions_history
        ],
        index=[
            position["trade_date"]
            for position in legacy_result.positions_history
        ],
        name="executed_strategy",
    )
    expected = compute_performance_metrics(
        legacy_full_equity,
        periods_per_year=244,
        initial_nav=1.0,
    )
    actual = runner_result.strategy_metrics
    assert set(expected).issubset(actual), (
        f"missing core metrics: {sorted(set(expected) - set(actual))}"
    )
    for key, expected_value in expected.items():
        assert expected_value == _approx(actual[key], METRIC_TOL), (
            f"metrics.{key}: {expected_value} != {actual[key]}"
        )

    assert {
        "excess_total_return",
        "annualized_excess_return",
        "tracking_error",
        "information_ratio",
        "relative_max_drawdown",
    }.issubset(actual)
