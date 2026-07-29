from __future__ import annotations

import pandas as pd
import pytest

from agent.backtest.stockpred_graph.oracle_parity import build_oracle_parity_view
from src.stockpred.graph.backtest_config import GraphBacktestConfig


class _Gateway:
    def index_daily(self, index_code: str, start: str, end: str) -> pd.DataFrame:  # noqa: ARG002
        return pd.DataFrame(
            {
                "ts_code": ["000300.SH", "000300.SH", "000300.SH"],
                "trade_date": ["20250102", "20250103", "20250106"],
                "close": [100.0, 110.0, 121.0],
            }
        )


def _signals() -> pd.DataFrame:
    common = {
        "direction": "强",
        "stage": "上升",
        "industry": "电子",
        "industry_momentum_rank": 1,
        "crowding_score": 0.1,
        "confidence": 0.8,
        "stop_loss_pct": -5.0,
        "take_profit_pct": 10.0,
        "action": "买入",
        "position_weight": 0.5,
        "base_score": 90.0,
        "rotation_phase": "领涨",
        "retreat_severity": 0.0,
        "industry_turning_severity": 0.0,
    }
    return pd.DataFrame(
        [
            {
                **common,
                "trade_date": "20250102",
                "eval_date": "20250102",
                "ts_code": "A",
                "score": 90.0,
            },
            {
                **common,
                "trade_date": "20250102",
                "eval_date": "20250102",
                "ts_code": "B",
                "score": 80.0,
                "direction": "弱",
                "action": "卖出",
            },
            {
                **common,
                "trade_date": "20250103",
                "eval_date": "20250103",
                "ts_code": "A",
                "score": 95.0,
            },
        ]
    )


def _market() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    prices = {
        "A": {"20250102": 9.0, "20250103": 10.0, "20250106": 12.0},
        "B": {"20250102": 11.0, "20250103": 10.0, "20250106": 8.0},
    }
    for code, by_date in prices.items():
        for trade_date, price in by_date.items():
            rows.append(
                {
                    "ts_code": code,
                    "trade_date": trade_date,
                    "open": price,
                    "close": price,
                    "adj_open": price,
                    "adj_close": price,
                    "vol": 100.0,
                    "amount": 100_000.0,
                    "up_limit": price * 1.1,
                    "down_limit": price * 0.9,
                }
            )
    return pd.DataFrame(rows)


def test_oracle_parity_view_filters_to_executable_forward_return_rows() -> None:
    config = GraphBacktestConfig(
        start="2025-01-02",
        end="2025-01-03",
        mode="research",
        top_n=1,
        eval_step=1,
        forward_days=1,
    )

    view = build_oracle_parity_view(
        _signals(),
        market=_market(),
        config=config,
        gateway=_Gateway(),
    )

    assert view.signals[["trade_date", "ts_code"]].values.tolist() == [
        ["20250102", "A"],
        ["20250102", "B"],
    ]
    assert view.selected["ts_code"].tolist() == ["A"]
    assert view.trades[["timestamp", "code", "side", "price"]].values.tolist() == [
        ["2025-01-03", "A", "BUY", 10.0],
        ["2025-01-06", "A", "SELL", 12.0],
    ]
    assert view.equity.to_dict(orient="records") == [
        {"time": "2025-01-02", "equity": 1.2}
    ]
    assert view.metrics["total_evaluated"] == 2
    assert view.metrics["top_n_actual_return"] == pytest.approx(0.2)
    assert view.metrics["median_actual_return"] == 0.0
    assert view.metrics["benchmark_return"] == pytest.approx(0.1)
    assert view.metrics["direction_accuracy"] == 1.0
    assert view.metrics["action_accuracy"] == 1.0
