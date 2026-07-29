from __future__ import annotations

import pandas as pd
import pytest
from pydantic import ValidationError

from agent.backtest.stockpred_graph.runner import GraphBacktestRunner
from src.stockpred.contracts import StockPredDataError
from src.stockpred.graph.backtest_config import GraphBacktestConfig
from src.stockpred.graph.service import GraphSignalConfig


class _Gateway:
    dates = [f"202501{day:02d}" for day in range(1, 32)]

    def trade_dates(self, start: str, end: str) -> list[str]:
        return [date for date in self.dates if start <= date <= end]

    def prices(self, start: str, end: str, codes=None) -> pd.DataFrame:
        selected_codes = list(codes) if codes is not None else ["A", "B", "C"]
        return pd.DataFrame(
            [
                {
                    "ts_code": code,
                    "trade_date": date,
                    "open": 10.0 + code_index,
                    "high": 11.0 + code_index,
                    "low": 9.0 + code_index,
                    "close": 10.0 + code_index,
                    "pct_chg": 0.0,
                    "vol": 1000.0,
                    "amount": 10_000.0,
                }
                for code_index, code in enumerate(selected_codes)
                for date in self.dates
                if start <= date <= end
            ]
        )

    def adjustment_factors(self, start: str, end: str, codes=None) -> pd.DataFrame:
        prices = self.prices(start, end, codes)
        return prices[["ts_code", "trade_date"]].assign(adj_factor=1.0)

    def stock_limits(self, start: str, end: str, codes=None) -> pd.DataFrame:
        prices = self.prices(start, end, codes)
        return prices[["ts_code", "trade_date"]].assign(
            up_limit=100.0,
            down_limit=0.01,
        )


class _SignalService:
    def __init__(self, *, empty_dates: set[str] | None = None) -> None:
        self.empty_dates = empty_dates or set()
        self.calls: list[tuple[str, object]] = []

    def evaluate(self, eval_date: str, config: object) -> pd.DataFrame:
        self.calls.append((eval_date, config))
        if eval_date in self.empty_dates:
            return pd.DataFrame()
        return pd.DataFrame(
            {
                "trade_date": [eval_date] * 3,
                "ts_code": ["B", "A", "C"],
                "score": [0.8, 0.9, 0.7],
            }
        )


def test_parity_mode_rejects_execution_override() -> None:
    with pytest.raises(ValidationError, match="parity mode"):
        GraphBacktestConfig(
            start="2025-01-01",
            end="2025-01-31",
            mode="parity",
            top_n=20,
        )


def test_parity_defaults_match_frozen_oracle_market_scope() -> None:
    expected = ("SSE", "SZSE")

    assert GraphBacktestConfig(start="2025-01-01", end="2025-01-31").allowed_exchanges == expected
    assert GraphSignalConfig().allowed_exchanges == expected


def test_research_mode_allows_unlocked_selection_parameters() -> None:
    config = GraphBacktestConfig(
        start="2025-01-01",
        end="2025-01-31",
        mode="research",
        top_n=20,
        eval_step=2,
    )

    assert config.top_n == 20
    assert config.eval_step == 2


def test_runner_evaluates_every_fifth_open_day_and_reports_progress() -> None:
    gateway = _Gateway()
    signal_service = _SignalService()
    progress: list[tuple[int, int, str]] = []

    result = GraphBacktestRunner(gateway, signal_service).run(
        GraphBacktestConfig(start="2025-01-01", end="2025-01-20"),
        on_progress=lambda done, total, date: progress.append((done, total, date)),
    )

    expected_dates = gateway.trade_dates("20250101", "20250120")[::5]
    assert result.eval_dates == expected_dates
    assert progress == [
        (index, len(expected_dates), date)
        for index, date in enumerate(expected_dates, start=1)
    ]
    assert result.selected.groupby("trade_date").size().tolist() == [3] * len(expected_dates)
    assert not result.trades.empty
    assert not result.positions.empty
    assert not result.equity.empty
    assert result.metrics["valid_eval_ratio"] == 1.0

def test_runner_collects_portfolio_and_symbol_performance_metrics() -> None:
    result = GraphBacktestRunner(_Gateway(), _SignalService()).run(
        GraphBacktestConfig(start="2025-01-01", end="2025-01-20")
    )

    assert {"annual_volatility", "max_drawdown", "trade_count"} <= set(result.metrics)
    assert {row["symbol"] for row in result.symbol_metrics} == {"A", "B", "C"}


def test_runner_fails_when_valid_evaluation_ratio_is_below_threshold() -> None:
    gateway = _Gateway()
    eval_dates = gateway.trade_dates("20250101", "20250120")[::5]
    signal_service = _SignalService(empty_dates=set(eval_dates[1:]))

    with pytest.raises(StockPredDataError) as exc_info:
        GraphBacktestRunner(gateway, signal_service).run(
            GraphBacktestConfig(start="2025-01-01", end="2025-01-20")
        )

    assert exc_info.value.code == "STOCKPRED_VALID_EVAL_RATIO"
