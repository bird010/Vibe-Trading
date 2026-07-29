from __future__ import annotations

import warnings

import pandas as pd
import pytest

from backtest.stockpred_graph.performance import (
    build_symbol_metrics_from_market,
    build_symbol_metrics,
    calculate_performance_metrics,
)


def test_build_symbol_metrics_from_market_matches_existing_ohlcv_mapping() -> None:
    trades = pd.DataFrame(
        [
            {"timestamp": "2025-01-01", "code": "A", "side": "BUY", "executed_value": 100.0, "qty": 10.0, "cost_bps": 0.0, "status": "FILLED"},
            {"timestamp": "2025-01-02", "code": "A", "side": "SELL", "executed_value": 120.0, "qty": 10.0, "cost_bps": 0.0, "status": "FILLED"},
        ]
    )
    market = pd.DataFrame(
        {
            "ts_code": ["A", "A", "B"],
            "trade_date": ["20250101", "20250102", "20250101"],
            "adj_close": [10.0, 12.0, 5.0],
        }
    )
    expected = build_symbol_metrics(
        trades,
        {code: frame.reset_index(drop=True) for code, frame in market.groupby("ts_code")},
    )

    assert build_symbol_metrics_from_market(trades, market) == expected


def test_symbol_metrics_allow_empty_trade_frame_with_market_data() -> None:
    market = pd.DataFrame(
        {"ts_code": ["A"], "trade_date": ["20250101"], "adj_close": [10.0]}
    )

    assert build_symbol_metrics_from_market(pd.DataFrame(), market) == []
    assert build_symbol_metrics(pd.DataFrame(), {"A": market}) == []


def test_calculate_performance_metrics_uses_daily_nav_and_completed_trades() -> None:
    equity = pd.DataFrame(
        {
            "time": ["2025-01-01", "2025-01-02", "2025-01-03"],
            "nav": [1.0, 1.1, 1.0],
        }
    )
    trades = pd.DataFrame(
        [
            {
                "timestamp": "2025-01-01",
                "code": "A",
                "side": "BUY",
                "executed_value": 100.0,
                "qty": 10.0,
                "price": 10.0,
                "cost_bps": 0.0,
                "status": "FILLED",
                "signal_date": "2025-01-01",
                "exit_delay_days": 0,
            },
            {
                "timestamp": "2025-01-03",
                "code": "A",
                "side": "SELL",
                "executed_value": 110.0,
                "qty": 10.0,
                "price": 11.0,
                "cost_bps": 0.0,
                "status": "FILLED",
                "signal_date": "2025-01-01",
                "exit_delay_days": 0,
            },
        ]
    )

    metrics = calculate_performance_metrics(equity, trades)

    assert metrics["total_return"] == 0.0
    assert metrics["max_drawdown"] == pytest.approx(-1 / 11)
    assert metrics["trade_count"] == 1.0
    assert metrics["win_rate"] == 1.0
    assert "profit_loss_ratio" not in metrics
    assert metrics["avg_holding_days"] == 2.0


def test_calculate_performance_metrics_omits_annual_return_for_non_positive_ending_nav() -> None:
    equity = pd.DataFrame({"nav": [1.0, 0.8, 0.7, 0.5, 0.2, -0.1]})

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        metrics = calculate_performance_metrics(equity, pd.DataFrame())

    assert "annual_return" not in metrics
    assert "calmar" not in metrics
    assert metrics["max_drawdown"] == pytest.approx(-1.1)


def test_build_symbol_metrics_uses_filled_trades_and_ignores_rejected_orders() -> None:
    trades = pd.DataFrame(
        [
            {
                "timestamp": "2025-01-01",
                "code": "A",
                "side": "BUY",
                "executed_value": 100.0,
                "qty": 10.0,
                "price": 10.0,
                "cost_bps": 0.0,
                "status": "FILLED",
                "signal_date": "2025-01-01",
                "exit_delay_days": 0,
            },
            {
                "timestamp": "2025-01-02",
                "code": "A",
                "side": "SELL",
                "executed_value": 120.0,
                "qty": 10.0,
                "price": 12.0,
                "cost_bps": 0.0,
                "status": "FILLED",
                "signal_date": "2025-01-01",
                "exit_delay_days": 0,
            },
            {
                "timestamp": "2025-01-02",
                "code": "B",
                "side": "BUY",
                "executed_value": 0.0,
                "qty": 0.0,
                "price": None,
                "cost_bps": 0.0,
                "status": "REJECTED",
                "signal_date": "2025-01-02",
                "exit_delay_days": 0,
            },
        ]
    )
    prices = {
        "A": pd.DataFrame(
            {
                "ts_code": ["A", "A"],
                "trade_date": ["20250101", "20250102"],
                "adj_close": [10.0, 12.0],
            }
        )
    }

    metrics = build_symbol_metrics(trades, prices)

    assert metrics[0]["symbol"] == "A"
    assert metrics[0]["total_return"] == pytest.approx(0.2)
    assert {row["symbol"] for row in metrics} == {"A"}


def test_zero_value_filled_events_are_excluded_from_trade_and_symbol_metrics() -> None:
    trades = pd.DataFrame(
        [
            {
                "timestamp": "2025-01-01",
                "code": "A",
                "side": "BUY",
                "executed_value": 0.0,
                "qty": 10.0,
                "cost_bps": 0.0,
                "status": "FILLED",
            },
            {
                "timestamp": "2025-01-02",
                "code": "A",
                "side": "SELL",
                "executed_value": 0.0,
                "qty": 10.0,
                "cost_bps": 0.0,
                "status": "PARTIAL",
            },
        ]
    )
    equity = pd.DataFrame({"time": ["2025-01-01", "2025-01-02"], "nav": [1.0, 1.0]})
    prices = {
        "A": pd.DataFrame(
            {
                "ts_code": ["A", "A"],
                "trade_date": ["20250101", "20250102"],
                "adj_close": [10.0, 10.0],
            }
        )
    }

    metrics = calculate_performance_metrics(equity, trades)

    assert "trade_count" not in metrics
    assert "win_rate" not in metrics
    assert "avg_holding_days" not in metrics
    assert build_symbol_metrics(trades, prices) == []


def test_symbol_without_ohlcv_keeps_completed_trade_statistics() -> None:
    trades = pd.DataFrame(
        [
            {
                "timestamp": "2025-01-01",
                "code": "NO_BARS",
                "side": "BUY",
                "executed_value": 100.0,
                "qty": 10.0,
                "cost_bps": 0.0,
                "status": "FILLED",
            },
            {
                "timestamp": "2025-01-02",
                "code": "NO_BARS",
                "side": "SELL",
                "executed_value": 120.0,
                "qty": 10.0,
                "cost_bps": 0.0,
                "status": "FILLED",
            },
        ]
    )

    assert build_symbol_metrics(trades, {}) == [
        {
            "symbol": "NO_BARS",
            "trade_count": 1.0,
            "win_rate": 1.0,
            "avg_holding_days": 1.0,
        }
    ]


def test_symbol_nav_includes_first_day_trading_cost_in_return_baseline() -> None:
    trades = pd.DataFrame(
        [
            {
                "timestamp": "2025-01-01",
                "code": "A",
                "side": "BUY",
                "executed_value": 100.0,
                "qty": 10.0,
                "cost_bps": 100.0,
                "status": "FILLED",
            },
            {
                "timestamp": "2025-01-02",
                "code": "A",
                "side": "SELL",
                "executed_value": 100.0,
                "qty": 10.0,
                "cost_bps": 0.0,
                "status": "FILLED",
            },
        ]
    )
    prices = {
        "A": pd.DataFrame(
            {
                "trade_date": ["20250101", "20250102"],
                "adj_close": [10.0, 10.0],
            }
        )
    }

    [metrics] = build_symbol_metrics(trades, prices)

    assert metrics["total_return"] == pytest.approx(-1.0 / 101.0)


def test_unmatched_sell_quantity_does_not_change_symbol_nav() -> None:
    trades = pd.DataFrame(
        [
            {
                "timestamp": "2025-01-01",
                "code": "A",
                "side": "BUY",
                "executed_value": 100.0,
                "qty": 10.0,
                "cost_bps": 0.0,
                "status": "FILLED",
            },
            {
                "timestamp": "2025-01-02",
                "code": "A",
                "side": "SELL",
                "executed_value": 200.0,
                "qty": 20.0,
                "cost_bps": 0.0,
                "status": "FILLED",
            },
        ]
    )
    prices = {
        "A": pd.DataFrame(
            {
                "trade_date": ["20250101", "20250102", "20250103"],
                "adj_close": [10.0, 10.0, 20.0],
            }
        )
    }

    [metrics] = build_symbol_metrics(trades, prices)

    assert metrics["total_return"] == 0.0


def test_profit_loss_ratio_uses_average_win_over_average_loss() -> None:
    trades = pd.DataFrame(
        [
            {
                "timestamp": "2025-01-01",
                "code": code,
                "side": "BUY",
                "executed_value": 100.0,
                "qty": 10.0,
                "cost_bps": 0.0,
                "status": "FILLED",
            }
            for code in ("A", "B", "C")
        ]
        + [
            {
                "timestamp": "2025-01-02",
                "code": code,
                "side": "SELL",
                "executed_value": value,
                "qty": 10.0,
                "cost_bps": 0.0,
                "status": "FILLED",
            }
            for code, value in (("A", 110.0), ("B", 130.0), ("C", 90.0))
        ]
    )
    equity = pd.DataFrame({"nav": [100.0, 100.0]})

    metrics = calculate_performance_metrics(equity, trades)

    assert metrics["profit_loss_ratio"] == pytest.approx(2.0)
