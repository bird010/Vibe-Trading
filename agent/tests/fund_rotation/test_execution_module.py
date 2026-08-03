"""Characterization tests for the common execution module (Phase 2 Task 2).

§12/§32.3 — freeze the public execution API behavior (order blocking reasons,
common cash scaling/slippage, sell-before-buy ordering, valuation, order
serialization) and pin the legacy pipeline aliases to the same objects.
"""

import pandas as pd
import pytest

from backtest.fund_rotation import execution, pipeline
from backtest.fund_rotation.capacity import ADVIndex
from backtest.fund_rotation.config import FundRotationConfig
from backtest.fund_rotation.etf_rules import ChinaETFExecutionRules
from backtest.fund_rotation.executor import PortfolioExecutor
from backtest.fund_rotation.orders import OrderManager
from backtest.fund_rotation.execution import (
    PipelineResult,
    build_execution_context,
    execute_with_capacity,
    mark_to_market,
    run_execution_loop,
    serialize_orders,
)


def _bar(price: float = 10.0) -> dict:
    return {
        "open": price, "close": price, "high": price, "low": price,
        "pre_close": price, "vol": 1_000_000,
    }


def test_pipeline_aliases_are_the_same_objects():
    """The legacy pipeline must delegate, not re-implement (§32.3)."""
    assert pipeline.PipelineResult is execution.PipelineResult
    assert pipeline.ExecutionContext is execution.ExecutionContext
    assert pipeline.ExecutionProfiler is execution.ExecutionProfiler
    assert pipeline._build_execution_context is execution.build_execution_context
    assert pipeline._run_execution_loop is execution.run_execution_loop
    assert pipeline._execute_with_capacity is execution.execute_with_capacity
    assert pipeline._mark_to_market is execution.mark_to_market
    assert pipeline._first_actual_fill_date is execution.first_actual_fill_date
    assert pipeline._serialize_orders is execution.serialize_orders
    assert (
        pipeline._align_theoretical_to_common_dates
        is execution.align_theoretical_to_common_dates
    )


def test_public_execute_with_capacity_freezes_adv_block_reason():
    """Insufficient ADV history blocks instead of granting unlimited capacity."""
    market = pd.DataFrame([
        {"ts_code": "510300.SH", "trade_date": "20240118", "amount": 10_000.0},
    ])
    config = FundRotationConfig(k=1, top_n=1, adv_min_observations=10)
    rules = ChinaETFExecutionRules()
    executor = PortfolioExecutor(100_000, rules)
    orders = OrderManager()
    orders.create_orders({"510300.SH": 10_000}, "SIG-1")

    adv_index = ADVIndex(
        {"510300.SH": market},
        lookback=config.adv_lookback, min_obs=config.adv_min_observations,
    )
    result = execute_with_capacity(
        executor, orders, {"510300.SH": 1.0}, {"510300.SH": _bar()},
        "20240122", config, adv_index, rules,
    )

    assert result.events[0]["status"] == "BLOCKED"
    assert result.events[0]["reason"] == "insufficient_adv_history"
    assert executor.cash == 100_000


def test_public_common_cash_scaling_freezes_slippage():
    """Common cash scaling fills both buys and reports slippage from the fill."""
    dates = pd.bdate_range("2024-01-02", periods=12).strftime("%Y%m%d")
    market = pd.DataFrame([
        {"ts_code": code, "trade_date": date, "amount": 100.0}
        for code in ("A", "B") for date in dates
    ])
    config = FundRotationConfig(
        k=1, top_n=1, initial_capital=6_000,
        adv_min_observations=10, max_participation_rate=0.05,
    )
    rules = ChinaETFExecutionRules()
    executor = PortfolioExecutor(6_000, rules)
    orders = OrderManager()
    orders.create_orders({"A": 1_000, "B": 1_000}, "SIG-1")

    adv_index = ADVIndex(
        {code: market[market["ts_code"] == code].reset_index(drop=True) for code in ("A", "B")},
        lookback=config.adv_lookback, min_obs=config.adv_min_observations,
    )
    result = execute_with_capacity(
        executor, orders, {"A": 0.5, "B": 0.5},
        {"A": _bar(), "B": _bar()}, "20240122", config, adv_index, rules,
    )

    filled_events = [event for event in result.events if event["filled"] > 0]
    assert len(filled_events) == 2
    for event in filled_events:
        expected_participation = event["filled"] * 10.0 / 100_000.0
        assert event["participation_rate"] == expected_participation
        assert event["slippage_bps"] == min(
            config.max_slippage_bps,
            config.base_slippage_bps + 200 * expected_participation,
        )


def _rotation_market() -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = pd.bdate_range("2024-01-02", "2024-02-02").strftime("%Y%m%d").tolist()
    rows, adj = [], []
    for code in ("A", "B"):
        for date in dates:
            rows.append({
                "ts_code": code, "trade_date": date,
                "open": 10.0, "close": 10.0, "high": 10.1, "low": 9.9,
                "pre_close": 10.0, "vol": 1_000_000, "amount": 10_000_000.0,
            })
            adj.append({"ts_code": code, "trade_date": date, "adj_factor": 1.0})
    return pd.DataFrame(rows), pd.DataFrame(adj)


def test_public_run_execution_loop_sells_before_buys_on_rotation_day():
    """§12 — on a rotation day every sell attempt precedes every buy attempt."""
    market, adj = _rotation_market()
    result = PipelineResult(weekly_targets={
        "20240105": {"A": 1.0},   # executes 20240108 (strictly after signal)
        "20240119": {"B": 1.0},   # executes 20240122
    })
    config = FundRotationConfig(
        k=1, top_n=1, initial_capital=100_000,
        adv_min_observations=3, max_participation_rate=0.05,
        start_date="20240102", end_date="20240131",
    )
    ctx = build_execution_context(market, adj, config)
    run_execution_loop(result, config, ctx)

    first_buy = [e for e in result.trade_events
                 if e["trade_date"] == "20240108" and e["action"] == "BUY"]
    assert first_buy and first_buy[0]["ts_code"] == "A"
    assert first_buy[0]["filled"] > 0

    rotation = [e for e in result.trade_events if e["trade_date"] == "20240122"]
    sell_indexes = [i for i, e in enumerate(rotation) if e["action"] == "SELL"]
    buy_indexes = [i for i, e in enumerate(rotation) if e["action"] == "BUY"]
    assert sell_indexes and buy_indexes
    assert rotation[sell_indexes[0]]["ts_code"] == "A"
    assert rotation[buy_indexes[-1]]["ts_code"] == "B"
    assert max(sell_indexes) < min(buy_indexes), "sells must precede buys"

    # Full-interval daily equity is produced over the evaluation calendar.
    assert result.executed_equity.index[0] == "20240102"
    assert result.executed_equity.index[-1] == "20240131"
    assert result.executed_equity.notna().all()


def test_should_cancel_stops_execution_loop_at_daily_checkpoint():
    """§26.1 — the execution loop honors the cancellation checkpoint and
    preserves events/equity collected before the stop."""
    market, adj = _rotation_market()
    result = PipelineResult(weekly_targets={"20240105": {"A": 1.0}})
    config = FundRotationConfig(
        k=1, top_n=1, initial_capital=100_000,
        adv_min_observations=3, max_participation_rate=0.05,
        start_date="20240102", end_date="20240131",
    )
    ctx = build_execution_context(market, adj, config)
    checks = {"n": 0}

    def cancel_after_eight_days() -> bool:
        checks["n"] += 1
        return checks["n"] > 8

    run_execution_loop(result, config, ctx, should_cancel=cancel_after_eight_days)

    # Days 20240102..20240111 (8 trading days, incl. the 20240108 fill)
    # complete; the loop breaks at the start of 20240112.
    assert len(result.executed_equity) == 8
    assert result.executed_equity.index[-1] == "20240111"
    assert result.trade_events  # fills executed before the stop are preserved
    assert result.orders  # residual state serialized for audit


def test_mark_to_market_freezes_valuation():
    rules = ChinaETFExecutionRules()
    executor = PortfolioExecutor(50_000, rules)
    executor._positions["510300.SH"] = {"size": 1_000}
    equity = mark_to_market(
        executor, "20240122", {("20240122", "510300.SH"): 12.5},
    )
    assert equity == pytest.approx(50_000 + 1_000 * 12.5)


def test_serialize_orders_freezes_attempt_schema():
    orders = OrderManager()
    orders.create_orders({"A": 500}, "SIG-1")
    rows = serialize_orders(orders)
    assert len(rows) == 1
    expected_fields = {
        "order_id", "event_id", "ts_code", "direction", "requested", "filled",
        "attempt_number", "trade_date", "attempt_filled", "attempt_status",
        "reason", "cumulative_filled_at_attempt", "attempt_quantity_basis",
        "current_quantity_basis", "remaining", "final_status",
        "corporate_action_adjustments",
    }
    assert expected_fields.issubset(rows[0])
    assert rows[0]["order_id"] == "SIG-1-A"
    assert rows[0]["requested"] == 500
    assert rows[0]["attempt_status"] == "NOT_ATTEMPTED"
