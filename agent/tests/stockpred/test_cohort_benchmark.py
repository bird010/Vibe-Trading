"""Tests for dual benchmark: target horizon and liquidation matched."""

from __future__ import annotations

import inspect

import pandas as pd
import pytest

from backtest.stockpred.cohort.benchmark import (
    ExitEvent,
    compute_liquidation_matched_benchmark,
    compute_target_horizon_benchmark,
)
from backtest.stockpred.cohort.contracts import ExecutionEvent
from backtest.stockpred.cohort.ledger import CohortLedger


def test_benchmark_helpers_default_to_total_return_index() -> None:
    assert (
        inspect.signature(compute_target_horizon_benchmark)
        .parameters["benchmark_code"]
        .default
        == "H00300.CSI"
    )
    assert (
        inspect.signature(compute_liquidation_matched_benchmark)
        .parameters["benchmark_code"]
        .default
        == "H00300.CSI"
    )


def _index_market(days: int = 30, base: float = 100.0) -> pd.DataFrame:
    """Benchmark index growing 0.5% per day."""
    dates = [f"202501{d:02d}" for d in range(1, days + 1)]
    prices = [base * (1.005 ** i) for i in range(days)]
    return pd.DataFrame(
        {
            "ts_code": ["000300.SH"] * days,
            "trade_date": dates,
            "adj_open": prices,
            "adj_close": [p * 1.001 for p in prices],
        }
    )


def _trade_dates(days: int = 30) -> list[str]:
    return [f"202501{d:02d}" for d in range(1, days + 1)]


class TestTargetHorizonBenchmark:
    def test_fixed_horizon_return(self):
        idx = _index_market(30, base=100.0)
        trade_dates = _trade_dates(30)

        result = compute_target_horizon_benchmark(
            index_market=idx,
            trade_dates=trade_dates,
            signal_date="20250110",
            holding_days=5,
        )

        # Entry = 20250111 (idx 10), exit = idx 15 = 20250116
        # return = 100*1.005^15 / 100*1.005^10 - 1 = 1.005^5 - 1
        expected = 1.005**5 - 1
        assert result.benchmark_return == pytest.approx(expected, rel=1e-6)

    def test_ignores_strategy_delays(self):
        idx = _index_market(30)
        trade_dates = _trade_dates(30)

        # Even if strategy exits late, benchmark uses fixed target date
        result = compute_target_horizon_benchmark(
            index_market=idx,
            trade_dates=trade_dates,
            signal_date="20250110",
            holding_days=5,
        )

        # Same as above regardless of actual exit
        expected = 1.005**5 - 1
        assert result.benchmark_return == pytest.approx(expected, rel=1e-6)

    def test_no_data_returns_none(self):
        idx = pd.DataFrame(columns=["ts_code", "trade_date", "adj_open"])
        trade_dates = _trade_dates(30)

        result = compute_target_horizon_benchmark(
            index_market=idx,
            trade_dates=trade_dates,
            signal_date="20250110",
            holding_days=5,
        )

        assert result.benchmark_return is None


class TestLiquidationMatchedBenchmark:
    def test_full_exit_benchmark_weight_uses_entry_cost(self):
        ledger = CohortLedger(cohort_id="c1", committed_capital=10_000.0, evaluation_date="20250101")
        entry = ExecutionEvent(
            order_id="buy", cohort_id="c1", trade_date="20250102", code="A", side="BUY",
            requested_quantity=100, executed_quantity=100, executed_value=1_000.0,
            price=10.0, status="FILLED",
        )
        exit_event = ExecutionEvent(
            order_id="sell", cohort_id="c1", trade_date="20250103", code="A", side="SELL",
            requested_quantity=100, executed_quantity=100, executed_value=2_000.0,
            price=20.0, status="FILLED",
        )
        ledger.apply_entry(entry)
        ledger.apply_exit(exit_event)

        result = compute_liquidation_matched_benchmark(
            index_market=pd.DataFrame(
                {
                    "ts_code": ["000300.SH", "000300.SH"],
                    "trade_date": ["20250102", "20250103"],
                    "adj_open": [100.0, 200.0],
                }
            ),
            trade_dates=["20250102", "20250103"],
            entry_date="20250102",
            exit_events=[
                ExitEvent(
                    date="20250103",
                    proportion=ledger.initial_entry_cost("A", 100) / ledger.committed_capital,
                )
            ],
        )

        assert result.benchmark_return == pytest.approx(0.1)

    def test_single_full_exit(self):
        idx = _index_market(30, base=100.0)
        trade_dates = _trade_dates(30)

        # Strategy enters on 20250111, exits fully on 20250116
        exits = [ExitEvent(date="20250116", proportion=1.0)]

        result = compute_liquidation_matched_benchmark(
            index_market=idx,
            trade_dates=trade_dates,
            entry_date="20250111",
            exit_events=exits,
        )

        # Benchmark enters same day, exits same day
        expected = 1.005**5 - 1
        assert result.benchmark_return == pytest.approx(expected, rel=1e-6)

    def test_partial_exit_multiple_dates(self):
        idx = _index_market(30, base=100.0)
        trade_dates = _trade_dates(30)

        # 50% exits on day 16, 50% on day 18
        exits = [
            ExitEvent(date="20250116", proportion=0.5),
            ExitEvent(date="20250118", proportion=0.5),
        ]

        result = compute_liquidation_matched_benchmark(
            index_market=idx,
            trade_dates=trade_dates,
            entry_date="20250111",
            exit_events=exits,
        )

        # First half: 1.005^5 - 1
        # Second half: 1.005^7 - 1
        ret1 = 1.005**5 - 1
        ret2 = 1.005**7 - 1
        expected = 0.5 * ret1 + 0.5 * ret2
        assert result.benchmark_return == pytest.approx(expected, rel=1e-6)

    def test_unliquidated_residual_valued_at_terminal_date(self):
        idx = _index_market(30, base=100.0)
        trade_dates = _trade_dates(30)

        # 50% exits on day 16, 50% never exits (terminal = day 25)
        exits = [
            ExitEvent(date="20250116", proportion=0.5),
            ExitEvent(date="20250125", proportion=0.5, is_terminal=True),
        ]

        result = compute_liquidation_matched_benchmark(
            index_market=idx,
            trade_dates=trade_dates,
            entry_date="20250111",
            exit_events=exits,
        )

        ret1 = 1.005**5 - 1
        ret2 = 1.005**14 - 1  # day 25 is index 24, entry is index 10, diff=14
        expected = 0.5 * ret1 + 0.5 * ret2
        assert result.benchmark_return == pytest.approx(expected, rel=1e-6)

    def test_empty_exits_with_valid_index_returns_zero(self):
        idx = _index_market(30)
        trade_dates = _trade_dates(30)

        result = compute_liquidation_matched_benchmark(
            index_market=idx,
            trade_dates=trade_dates,
            entry_date="20250111",
            exit_events=[],
        )

        assert result.benchmark_return == 0.0
