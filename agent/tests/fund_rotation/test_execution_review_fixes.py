"""Regression tests for the reviewed ETF daily execution contract."""

import pandas as pd

from backtest.fund_rotation.config import FundRotationConfig
from backtest.fund_rotation.etf_rules import ChinaETFExecutionRules
from backtest.fund_rotation.executor import PortfolioExecutor
from backtest.fund_rotation.orders import OrderManager
from backtest.fund_rotation.capacity import ADVIndex
from backtest.fund_rotation.pipeline import (
    PipelineResult,
    ExecutionContext,
    _build_execution_context,
    _execute_with_capacity,
    _run_execution_loop,
)


def _bar(price: float = 10.0, *, close: float | None = None, pre_close: float = 10.0) -> dict:
    return {
        "open": price,
        "close": close if close is not None else price,
        "high": close if close is not None else price,
        "low": price,
        "pre_close": pre_close,
        "vol": 1_000_000,
    }


def test_invalid_adv_history_blocks_instead_of_granting_unlimited_capacity():
    market = pd.DataFrame([
        {"ts_code": "510300.SH", "trade_date": "20240118", "amount": 10_000.0},
    ])
    config = FundRotationConfig(k=1, top_n=1, adv_min_observations=10)
    rules = ChinaETFExecutionRules()
    executor = PortfolioExecutor(100_000, rules)
    orders = OrderManager()
    orders.create_orders({"510300.SH": 10_000}, "SIG-1")

    adv_index = ADVIndex(
        {"510300.SH": market}, lookback=config.adv_lookback, min_obs=config.adv_min_observations,
    )
    result = _execute_with_capacity(
        executor, orders, {"510300.SH": 1.0}, {"510300.SH": _bar()},
        "20240122", config, adv_index, rules,
    )

    assert result.events[0]["status"] == "BLOCKED"
    assert result.events[0]["reason"] == "insufficient_adv_history"
    assert result.events[0]["adv_observations"] == 1
    assert executor.cash == 100_000


def _daily_market() -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = pd.bdate_range("2024-01-02", "2024-01-24").strftime("%Y%m%d").tolist()
    rows = []
    adj = []
    for date in dates:
        blocked = date == "20240122"
        post_adjustment = date >= "20240124"
        price = 20.0 if post_adjustment else (11.0 if blocked else 10.0)
        rows.append({
            "ts_code": "510300.SH", "trade_date": date,
            "open": price, "close": price,
            "high": price if blocked else price + 0.1,
            "low": price if blocked else price - 0.1,
            "pre_close": 10.0 if blocked else price,
            "vol": 1_000_000, "amount": 1_000_000.0,
        })
        adj.append({
            "ts_code": "510300.SH", "trade_date": date,
            "adj_factor": 0.5 if date >= "20240124" else 1.0,
        })
    return pd.DataFrame(rows), pd.DataFrame(adj)


def test_residual_order_retries_daily_and_factor_change_adjusts_shares():
    market, adj = _daily_market()
    result = PipelineResult(weekly_targets={"20240119": {"510300.SH": 1.0}})
    config = FundRotationConfig(
        k=1, top_n=1, initial_capital=100_000,
        adv_min_observations=10, max_participation_rate=0.05,
    )

    ctx = _build_execution_context(market, adj, config)
    _run_execution_loop(result, config, ctx)

    events = [e for e in result.trade_events if e["ts_code"] == "510300.SH"]
    assert events[0]["trade_date"] == "20240122"
    assert events[0]["status"] == "BLOCKED"
    assert events[1]["trade_date"] == "20240123"
    assert events[1]["status"] in {"FILLED", "PARTIAL"}

    holdings = {
        p["trade_date"]: p["positions"].get("510300.SH", 0)
        for p in result.positions_history
    }
    assert holdings["20240124"] * 2 == holdings["20240123"]
    assert all(
        {"adv20", "adv_observations", "participation_rate", "unfilled",
         "post_holding", "remaining"}.issubset(event)
        for event in events
    )


def test_common_cash_scale_reports_slippage_from_actual_fill():
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
    result = _execute_with_capacity(
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
