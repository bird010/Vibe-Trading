"""Tests for raw signal label and single-cohort return metrics."""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.stockpred.cohort.contracts import CohortStatus, TargetSnapshot
from backtest.stockpred.cohort.metrics import (
    compute_cohort_result,
    compute_raw_signal_return,
)
from backtest.stockpred.cohort.ledger import CohortLedger


def _market(codes: list[str], days: int = 30, base_price: float = 10.0) -> pd.DataFrame:
    """Market with adj_open increasing by 1% per day for each code."""
    dates = [f"202501{d:02d}" for d in range(1, days + 1)]
    rows = []
    for code in codes:
        for i, date in enumerate(dates):
            price = base_price * (1.01 ** i)
            rows.append(
                {
                    "ts_code": code,
                    "trade_date": date,
                    "adj_open": price,
                    "adj_close": price * 1.001,
                    "open": price,
                    "close": price * 1.001,
                    "vol": 100000.0,
                }
            )
    return pd.DataFrame(rows)


def _trade_dates(days: int = 30) -> list[str]:
    return [f"202501{d:02d}" for d in range(1, days + 1)]


def _target(codes: list[str], eval_date: str = "20250110") -> TargetSnapshot:
    n = len(codes)
    weight = 1.0 / n if n > 0 else 0.0
    return TargetSnapshot(
        cohort_id="cohort_test",
        evaluation_date=eval_date,
        committed_capital=1_000_000.0,
        selected_codes=tuple(codes),
        target_weights={c: weight for c in codes},
        target_values={c: 1_000_000.0 / n for c in codes} if n > 0 else {},
    )


def test_raw_return_open_to_open():
    mkt = _market(["A"], days=30, base_price=10.0)
    target = _target(["A"], eval_date="20250110")
    trade_dates = _trade_dates(30)

    result = compute_raw_signal_return(target, mkt, trade_dates, holding_days=5)

    # Entry = first trade date after 20250110 = 20250111 (index 10)
    # Exit = index 10 + 5 = index 15 = 20250116
    # return = adj_open[15] / adj_open[10] - 1 = 1.01^5 - 1
    expected = 1.01**5 - 1
    assert result.raw_signal_return == pytest.approx(expected, rel=1e-6)
    assert result.raw_label_coverage == pytest.approx(1.0)


def test_raw_return_ignores_limit_and_suspension():
    mkt = _market(["A"], days=30)
    # Make entry day look suspended (vol=0) - raw label should still compute
    mkt.loc[(mkt["ts_code"] == "A") & (mkt["trade_date"] == "20250111"), "vol"] = 0.0
    target = _target(["A"], eval_date="20250110")
    trade_dates = _trade_dates(30)

    result = compute_raw_signal_return(target, mkt, trade_dates, holding_days=5)

    # Should still compute return (ignores tradability)
    assert result.raw_signal_return is not None
    assert result.raw_label_coverage == pytest.approx(1.0)


def test_missing_entry_price_gives_nan_label():
    mkt = _market(["A"], days=30)
    # Remove entry day data
    mkt = mkt[~((mkt["ts_code"] == "A") & (mkt["trade_date"] == "20250111"))]
    target = _target(["A"], eval_date="20250110")
    trade_dates = _trade_dates(30)

    result = compute_raw_signal_return(target, mkt, trade_dates, holding_days=5)

    assert result.raw_signal_return is None or result.raw_label_coverage < 1.0


def test_missing_exit_price_gives_nan_label():
    mkt = _market(["A"], days=30)
    # Remove exit day data (20250116)
    mkt = mkt[~((mkt["ts_code"] == "A") & (mkt["trade_date"] == "20250116"))]
    target = _target(["A"], eval_date="20250110")
    trade_dates = _trade_dates(30)

    result = compute_raw_signal_return(target, mkt, trade_dates, holding_days=5)

    # Coverage should be 0 since the only target has no exit price
    assert result.raw_label_coverage == pytest.approx(0.0)


def test_weighted_sum_multiple_stocks():
    mkt = _market(["A", "B"], days=30, base_price=10.0)
    target = _target(["A", "B"], eval_date="20250110")
    trade_dates = _trade_dates(30)

    result = compute_raw_signal_return(target, mkt, trade_dates, holding_days=5)

    # Both have same return (same base_price and growth)
    expected = 1.01**5 - 1
    assert result.raw_signal_return == pytest.approx(expected, rel=1e-6)


def test_no_renormalization_when_partial_missing():
    mkt = _market(["A", "B"], days=30)
    # Remove B's exit day
    mkt = mkt[~((mkt["ts_code"] == "B") & (mkt["trade_date"] == "20250116"))]
    target = _target(["A", "B"], eval_date="20250110")
    trade_dates = _trade_dates(30)

    result = compute_raw_signal_return(target, mkt, trade_dates, holding_days=5)

    # Coverage = 0.5 (only A has complete label)
    assert result.raw_label_coverage == pytest.approx(0.5)
    # Return should be A's return * 0.5 weight (NOT renormalized to full weight)
    expected_a = 1.01**5 - 1
    assert result.raw_signal_return == pytest.approx(expected_a * 0.5, rel=1e-6)


def test_empty_target():
    mkt = _market(["A"], days=30)
    target = _target([], eval_date="20250110")
    trade_dates = _trade_dates(30)

    result = compute_raw_signal_return(target, mkt, trade_dates, holding_days=5)

    assert result.raw_signal_return is None
    assert result.raw_label_coverage == 0.0


def test_coverage_below_threshold_status():
    mkt = _market(["A", "B"], days=30)
    # Remove both exit days
    mkt = mkt[~(mkt["trade_date"] == "20250116")]
    target = _target(["A", "B"], eval_date="20250110")
    trade_dates = _trade_dates(30)

    result = compute_raw_signal_return(
        target, mkt, trade_dates, holding_days=5, min_coverage=0.95
    )

    assert result.status == "insufficient_data"


# ---------------------------------------------------------------------------
# Single-cohort return metrics
# ---------------------------------------------------------------------------


class TestCohortResult:
    def test_horizon_uses_adjusted_open_return_on_original_notional(self):
        ledger = CohortLedger(cohort_id="c1", committed_capital=1_000.0, evaluation_date="20250101")
        from backtest.stockpred.cohort.contracts import ExecutionEvent
        from backtest.stockpred.cohort.engine import compute_horizon_mark_value

        ledger.apply_entry(ExecutionEvent(
            order_id="buy_A", cohort_id="c1", trade_date="20250102", code="A", side="BUY",
            requested_quantity=100, executed_quantity=100, executed_value=1_000.0,
            price=10.0, status="FILLED",
        ))
        market = pd.DataFrame(
            {
                "ts_code": ["A", "A"],
                "trade_date": ["20250102", "20250107"],
                "open": [10.0, 12.0],
                "adj_open": [5.0, 6.0],
            }
        )

        value = compute_horizon_mark_value(
            ledger=ledger,
            market=market,
            target_exit_date="20250107",
        )

        assert value == pytest.approx(1_200.0)

    def test_unliquidated_terminal_value_deducts_sell_cost_and_haircut(self):
        from backtest.stockpred.cohort.benchmark import ExitEvent
        from backtest.stockpred.cohort.contracts import ExecutionEvent
        from backtest.stockpred.cohort.engine import make_terminal_exit_event
        from backtest.stockpred.execution.valuation import ValuationPolicy

        ledger = CohortLedger(cohort_id="c1", committed_capital=10_000.0, evaluation_date="20250101")
        ledger.apply_entry(ExecutionEvent(
            order_id="buy_A", cohort_id="c1", trade_date="20250102", code="A", side="BUY",
            requested_quantity=1_000, executed_quantity=1_000, executed_value=10_000.0,
            price=10.0, status="FILLED",
        ))
        ledger.begin_exit()
        ledger.finalize_exit()
        terminal = ValuationPolicy().terminal_value(
            quantity=1_000,
            last_valid_price=10.0,
            stale_days=0,
            limit_band_rate=0.10,
            adv=5_000_000.0,
        )

        result = compute_cohort_result(
            ledger=ledger,
            raw_signal_return=None,
            horizon_mark_return=0.0,
            target_horizon_benchmark_return=0.0,
            liquidation_benchmark_return=0.0,
            exit_delay_days=0,
            unliquidated_ratio=1.0,
            terminal_value=terminal.terminal_value,
        )
        terminal_event = make_terminal_exit_event(
            date="20250103",
            ledger=ledger,
            code="A",
            quantity=1_000,
        )

        assert terminal.terminal_value < terminal.last_valid_mark_value
        assert result.committed_capital_return == pytest.approx(
            (terminal.terminal_value - 10_000.0) / 10_000.0
        )
        assert terminal_event == ExitEvent(date="20250103", proportion=1.0, is_terminal=True)

    def test_committed_capital_return(self):
        ledger = CohortLedger(cohort_id="c1", committed_capital=1_000_000.0, evaluation_date="20250102")
        # Simulate: buy 1000@10 (fee 50), sell 1000@12 (fee 120)
        from backtest.stockpred.cohort.contracts import ExecutionEvent

        ledger.apply_entry(ExecutionEvent(
            order_id="buy_A", cohort_id="c1", trade_date="20250103", code="A", side="BUY",
            requested_quantity=1000, executed_quantity=1000, executed_value=10_000.0,
            price=10.0, fee_components={"commission": 50.0}, status="FILLED",
        ))
        ledger.begin_exit()
        ledger.apply_exit(ExecutionEvent(
            order_id="sell_A", cohort_id="c1", trade_date="20250108", code="A", side="SELL",
            requested_quantity=1000, executed_quantity=1000, executed_value=12_000.0,
            price=12.0, fee_components={"commission": 80.0, "stamp_duty": 40.0}, status="FILLED",
        ))
        ledger.finalize_exit()

        result = compute_cohort_result(
            ledger=ledger,
            raw_signal_return=0.20,
            horizon_mark_return=0.18,
            target_horizon_benchmark_return=0.05,
            liquidation_benchmark_return=0.04,
            exit_delay_days=0,
            unliquidated_ratio=0.0,
        )

        # Final cash = 1M - 10K - 50 + 12K - 120 = 1,001,830
        # committed_capital_return = (1,001,830 - 1M) / 1M = 0.00183
        assert result.committed_capital_return == pytest.approx(0.00183, rel=1e-3)
        assert result.status == CohortStatus.LIQUIDATED

    def test_all_rejected_cohort(self):
        ledger = CohortLedger(cohort_id="c1", committed_capital=1_000_000.0, evaluation_date="20250102")
        # No entries applied - all rejected

        result = compute_cohort_result(
            ledger=ledger,
            raw_signal_return=0.10,
            horizon_mark_return=0.0,
            target_horizon_benchmark_return=0.02,
            liquidation_benchmark_return=0.0,
            exit_delay_days=0,
            unliquidated_ratio=0.0,
        )

        assert result.committed_capital_return == pytest.approx(0.0)
        assert result.idle_cash_ratio == pytest.approx(1.0)
        assert result.fill_rate == pytest.approx(0.0)

    def test_failed_data_cohort(self):
        ledger = CohortLedger(cohort_id="c1", committed_capital=1_000_000.0, evaluation_date="20250102")
        ledger.status = CohortStatus.FAILED_DATA

        result = compute_cohort_result(
            ledger=ledger,
            raw_signal_return=None,
            horizon_mark_return=0.0,
            target_horizon_benchmark_return=0.0,
            liquidation_benchmark_return=0.0,
            exit_delay_days=0,
            unliquidated_ratio=0.0,
        )

        assert result.status == CohortStatus.FAILED_DATA
        assert result.committed_capital_return is None
