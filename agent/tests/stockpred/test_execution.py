from __future__ import annotations

import pandas as pd
import pytest

from agent.backtest.stockpred_graph.execution import (
    TRADE_COLUMNS,
    apply_capacity_limit,
    build_daily_ledger,
    estimate_one_way_cost_bps,
    execute_target_portfolio,
)


def _market(
    *,
    entry_open: float = 10.0,
    target_exit_open: float = 11.0,
    target_down_limit: float = 9.0,
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": ["A", "A", "A", "A"],
            "trade_date": ["20250102", "20250103", "20250106", "20250107"],
            "open": [9.5, entry_open, target_exit_open, 10.5],
            "close": [9.5, entry_open, target_exit_open, 10.5],
            "adj_open": [9.5, entry_open, target_exit_open, 10.5],
            "adj_close": [9.5, entry_open, target_exit_open, 10.5],
            "vol": [1000.0] * 4,
            "amount": [1000.0] * 4,
            "up_limit": [10.5, 11.0, 12.0, 11.5],
            "down_limit": [8.5, 9.0, target_down_limit, 9.5],
        }
    )


def _targets() -> pd.DataFrame:
    return pd.DataFrame({"ts_code": ["A"], "target_weight": [1.0]})


def test_limit_up_blocks_next_open_entry() -> None:
    trades = execute_target_portfolio(
        _market(entry_open=11.0),
        _targets(),
        signal_date="20250102",
        holding_days=1,
        capital=1_000_000,
        max_participation=0.05,
    )

    assert list(trades.columns) == TRADE_COLUMNS
    assert trades.iloc[0]["status"] == "REJECTED"
    assert trades.iloc[0]["reason"] == "limit_up"


def test_limit_down_delays_exit() -> None:
    trades = execute_target_portfolio(
        _market(target_exit_open=9.0, target_down_limit=9.0),
        _targets(),
        signal_date="20250102",
        holding_days=1,
        capital=1_000_000,
        max_participation=0.05,
    )

    sell = trades[trades["side"] == "SELL"].iloc[0]
    assert sell["timestamp"] == "2025-01-07"
    assert sell["exit_delay_days"] == 1


def test_cost_formula_includes_sell_stamp_duty() -> None:
    assert estimate_one_way_cost_bps(
        trade_value=50_000,
        daily_amount_cny=1_000_000,
        side="sell",
    ) == 40.0


def test_capacity_limit_and_unfilled_cash_are_not_reallocated() -> None:
    capacity = apply_capacity_limit(
        requested_value=1_000_000,
        daily_amount_cny=10_000_000,
        max_participation=0.05,
    )
    assert capacity.executed_value == 500_000

    market = pd.concat(
        [
            _market().assign(amount=10.0),
            _market().assign(ts_code="B", amount=10_000.0),
        ],
        ignore_index=True,
    )
    targets = pd.DataFrame(
        {"ts_code": ["A", "B"], "target_weight": [0.5, 0.5]}
    )
    trades = execute_target_portfolio(
        market,
        targets,
        signal_date="20250102",
        holding_days=1,
        capital=1_000_000,
        max_participation=0.05,
    )
    buys = trades[trades["side"] == "BUY"].set_index("code")

    assert buys.loc["A", "requested_value"] == 500_000
    assert buys.loc["A", "executed_value"] == 500
    assert buys.loc["A", "status"] == "PARTIAL"
    assert buys.loc["B", "requested_value"] == 500_000
    assert buys.loc["B", "executed_value"] == 500_000


def test_capacity_limited_sell_retries_on_next_sellable_day() -> None:
    market = _market().assign(amount=10.0)
    # 追加一个下一交易日，足以成交剩余仓位。
    extra = market.tail(1).assign(trade_date="20250108")
    market = pd.concat([market, extra], ignore_index=True)
    trades = execute_target_portfolio(
        market, _targets(), signal_date="20250102", holding_days=1,
        capital=1_000_000, max_participation=0.05,
    )
    sells = trades[trades["side"] == "SELL"]
    assert list(sells["status"]) == ["PARTIAL", "FILLED"]
    _, equity = build_daily_ledger(trades, market, initial_capital=1_000_000)
    assert equity.iloc[-1]["market_value"] == pytest.approx(0.0)


def test_capacity_limited_sell_partial_at_market_end_keeps_residual() -> None:
    """容量不足且无下一日时，最后 SELL 必须为 PARTIAL，账本保留正 market_value。"""
    # 三天：entry 在第二天，exit 在第三天（最后一天），无后续可卖日
    market = pd.DataFrame(
        {
            "ts_code": ["A", "A", "A"],
            "trade_date": ["20250102", "20250103", "20250106"],
            "open": [9.5, 10.0, 10.5],
            "close": [9.5, 10.0, 10.5],
            "adj_open": [9.5, 10.0, 10.5],
            "adj_close": [9.5, 10.0, 10.5],
            "vol": [1000.0, 1000.0, 1000.0],
            "amount": [10.0, 10.0, 10.0],
            "up_limit": [10.5, 11.0, 11.5],
            "down_limit": [8.5, 9.0, 9.5],
        }
    )
    trades = execute_target_portfolio(
        market, _targets(), signal_date="20250102", holding_days=1,
        capital=1_000_000, max_participation=0.05,
    )
    sells = trades[trades["side"] == "SELL"]
    assert len(sells) >= 1
    assert sells.iloc[-1]["status"] == "PARTIAL"
    _, equity = build_daily_ledger(trades, market, initial_capital=1_000_000)
    assert equity.iloc[-1]["market_value"] > 0


def test_daily_ledger_conserves_cash_positions_and_equity() -> None:
    market = _market()
    trades = execute_target_portfolio(
        market,
        _targets(),
        signal_date="20250102",
        holding_days=1,
        capital=1_000_000,
        max_participation=0.05,
    )

    positions, equity = build_daily_ledger(
        trades,
        market,
        initial_capital=1_000_000,
    )

    assert not positions.empty
    assert not equity.empty
    assert equity.iloc[-1]["equity"] == pytest.approx(
        equity.iloc[-1]["cash"] + equity.iloc[-1]["market_value"]
    )
    assert equity.iloc[-1]["nav"] == pytest.approx(
        equity.iloc[-1]["equity"] / 1_000_000
    )
