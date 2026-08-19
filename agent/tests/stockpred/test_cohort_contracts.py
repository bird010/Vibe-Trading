"""Tests for cohort domain contracts and deterministic identity."""

from __future__ import annotations

import pytest

from backtest.stockpred.cohort.contracts import (
    CohortResult,
    CohortState,
    CohortStatus,
    ExecutionEvent,
    OrderIntent,
    SignalSnapshot,
    TargetSnapshot,
    compute_cohort_id,
    compute_evaluation_protocol_key,
)


def test_cohort_id_is_deterministic():
    args = dict(
        evaluation_protocol_key="a" * 64,
        strategy_id="graph_v1",
        strategy_version="b" * 64,
        evaluation_date="20250102",
    )
    id1 = compute_cohort_id(**args)
    id2 = compute_cohort_id(**args)
    assert id1 == id2
    assert id1.startswith("cohort_")
    assert len(id1) == 7 + 24  # "cohort_" + 24 hex chars


def test_cohort_id_changes_with_evaluation_date():
    base = dict(evaluation_protocol_key="a" * 64, strategy_id="s", strategy_version="b" * 64)
    assert compute_cohort_id(**base, evaluation_date="20250102") != compute_cohort_id(
        **base, evaluation_date="20250103"
    )


def test_cohort_id_changes_with_strategy():
    base = dict(evaluation_protocol_key="a" * 64, strategy_version="b" * 64, evaluation_date="20250102")
    assert compute_cohort_id(**base, strategy_id="s1") != compute_cohort_id(**base, strategy_id="s2")


def test_evaluation_protocol_key_deterministic():
    config = {
        "data_snapshot_id": "snap1",
        "start": "20250101",
        "end": "20250630",
        "holding_days": 5,
        "eval_step": 5,
        "top_n": 50,
        "committed_capital_per_cohort": 10_000_000.0,
        "max_participation": 0.05,
        "adv_lookback_days": 20,
        "benchmark_code": "000300.SH",
        "execution_policy_version": "exec_v1",
        "cost_policy_version": "cost_v1",
        "max_exit_extension_days": 20,
        "stale_price_limit_days": 5,
        "quality_gate": {},
    }
    k1 = compute_evaluation_protocol_key(config)
    k2 = compute_evaluation_protocol_key(config)
    assert k1 == k2
    assert len(k1) == 64


def test_evaluation_protocol_key_changes_with_holding_days():
    config = {
        "data_snapshot_id": "s",
        "start": "20250101",
        "end": "20250630",
        "holding_days": 5,
        "eval_step": 5,
        "top_n": 50,
        "committed_capital_per_cohort": 1e7,
        "max_participation": 0.05,
        "adv_lookback_days": 20,
        "benchmark_code": "000300.SH",
        "execution_policy_version": "v1",
        "cost_policy_version": "v1",
        "max_exit_extension_days": 20,
        "stale_price_limit_days": 5,
        "quality_gate": {},
    }
    k1 = compute_evaluation_protocol_key(config)
    config2 = {**config, "holding_days": 10}
    k2 = compute_evaluation_protocol_key(config2)
    assert k1 != k2


def test_evaluation_protocol_key_order_independent():
    config_a = {"b_key": 1, "a_key": 2}
    config_b = {"a_key": 2, "b_key": 1}
    assert compute_evaluation_protocol_key(config_a) == compute_evaluation_protocol_key(config_b)


def test_cohort_state_initial_status_is_planned():
    state = CohortState.create(
        cohort_id="cohort_abc", committed_capital=1_000_000.0, evaluation_date="20250102"
    )
    assert state.status == CohortStatus.PLANNED
    assert state.available_cash == 1_000_000.0
    assert state.positions == {}


def test_cohort_state_committed_capital_immutable():
    state = CohortState.create(
        cohort_id="cohort_x", committed_capital=5_000_000.0, evaluation_date="20250110"
    )
    assert state.committed_capital == 5_000_000.0


def test_signal_snapshot_requires_minimum_fields():
    snap = SignalSnapshot(
        evaluation_date="20250102",
        strategy_id="graph_v1",
        strategy_version="a" * 64,
        data_snapshot_id="snap1",
        signals=[],
    )
    assert snap.evaluation_date == "20250102"
    assert snap.signals == []


def test_target_snapshot_is_frozen():
    target = TargetSnapshot(
        cohort_id="cohort_abc",
        evaluation_date="20250102",
        committed_capital=1_000_000.0,
        selected_codes=("000001.SZ", "000002.SZ"),
        target_weights={"000001.SZ": 0.5, "000002.SZ": 0.5},
        target_values={"000001.SZ": 500_000.0, "000002.SZ": 500_000.0},
    )
    with pytest.raises(AttributeError):
        target.committed_capital = 999.0  # type: ignore[misc]


def test_order_intent_fields():
    order = OrderIntent(
        order_id="ord_1",
        cohort_id="cohort_abc",
        signal_date="20250102",
        eligible_from="20250103",
        code="000001.SZ",
        side="BUY",
        requested_quantity=1000,
        requested_value=10_000.0,
    )
    assert order.side == "BUY"
    assert order.requested_quantity == 1000


def test_execution_event_fields():
    event = ExecutionEvent(
        order_id="ord_1",
        cohort_id="cohort_abc",
        trade_date="20250103",
        code="000001.SZ",
        side="BUY",
        requested_quantity=1000,
        executed_quantity=800,
        executed_value=8000.0,
        price=10.0,
        fee_components={"commission": 12.0, "stamp_duty": 0.0, "transfer_fee": 0.8, "slippage": 5.0, "market_impact": 2.0},
        status="PARTIAL",
        reason_code="capacity",
        remaining_quantity=200,
    )
    assert event.executed_quantity == 800
    assert event.status == "PARTIAL"


def test_cohort_result_fields():
    result = CohortResult(
        cohort_id="cohort_abc",
        committed_capital_return=0.05,
        executed_capital_return=0.06,
        raw_signal_return=0.07,
        horizon_mark_return=0.04,
        liquidation_return=0.045,
        benchmark_return=0.02,
        target_horizon_excess_return=0.02,
        liquidation_policy_excess_return=0.025,
        fill_rate=0.9,
        idle_cash_ratio=0.1,
        cost_ratio=0.003,
        exit_delay_days=2,
        unliquidated_ratio=0.0,
        status=CohortStatus.LIQUIDATED,
    )
    assert result.committed_capital_return == 0.05
    assert result.status == CohortStatus.LIQUIDATED
