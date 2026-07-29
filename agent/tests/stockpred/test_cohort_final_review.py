"""Regression coverage for the StockPred Cohort R3 final review."""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.stockpred.cohort.benchmark import compute_liquidation_matched_benchmark, compute_target_horizon_benchmark
from backtest.stockpred.cohort.contracts import TargetSnapshot
from backtest.stockpred.cohort.contracts import CohortResult, CohortStatus
from backtest.stockpred.cohort.artifacts import _write_cohort_returns
from backtest.stockpred.cohort.aggregation import aggregate_cohorts
from backtest.stockpred.cohort.eligibility import SignalEligibilityGate
from backtest.stockpred.cohort.metrics import compute_raw_signal_return
from backtest.stockpred.cohort.engine import CohortBacktestConfig, _failed_cohort


def test_historically_listed_currently_delisted_stock_remains_eligible_before_delisting() -> None:
    universe = pd.DataFrame([
        {
            "ts_code": "000001.SZ",
            "list_date": "20240101",
            "delist_date": "20260101",
            "list_status": "D",
            "exchange": "SZSE",
        }
    ])
    result = SignalEligibilityGate(min_listed_trade_days=0).check(
        eval_date="20250115",
        universe=universe,
        prices=pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20250115", "vol": 1.0}]),
        adjustment_factors=pd.DataFrame([
            {"ts_code": "000001.SZ", "trade_date": "20250115", "adj_factor": 1.0}
        ]),
        market_calendar=["20240101", "20250115"],
        name_history=pd.DataFrame([
            {"ts_code": "000001.SZ", "effective_from": "20240101", "security_name": "Normal"}
        ]),
    )

    assert result.eligible_codes == ["000001.SZ"]
    assert not result.data_failure


@pytest.mark.parametrize(
    ("delist_date", "evaluation_date"),
    [("20250115", "20250115"), ("", "20250114")],
)
def test_delisted_or_unverifiable_stock_fails_closed(delist_date: str, evaluation_date: str) -> None:
    universe = pd.DataFrame([{
        "ts_code": "000001.SZ", "list_date": "20240101", "delist_date": delist_date,
        "list_status": "D", "exchange": "SZSE",
    }])
    result = SignalEligibilityGate(min_listed_trade_days=0).check(
        eval_date=evaluation_date, universe=universe,
        prices=pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": evaluation_date, "vol": 1.0}]),
        adjustment_factors=pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": evaluation_date, "adj_factor": 1.0}]),
        market_calendar=["20240101", evaluation_date],
        name_history=pd.DataFrame([{"ts_code": "000001.SZ", "effective_from": "20240101", "security_name": "Normal"}]),
    )

    assert result.rejected == {"000001.SZ": "NOT_LISTED"}


def test_benchmark_without_adjusted_open_fails_closed() -> None:
    result = compute_target_horizon_benchmark(
        index_market=pd.DataFrame([
            {"trade_date": "20250102", "open": 10.0},
            {"trade_date": "20250103", "open": 11.0},
        ]),
        trade_dates=["20250101", "20250102", "20250103"],
        signal_date="20250101",
        holding_days=1,
    )

    assert result.benchmark_return is None


def test_raw_label_without_adjusted_open_fails_closed() -> None:
    result = compute_raw_signal_return(
        TargetSnapshot(
            cohort_id="c1",
            evaluation_date="20250101",
            committed_capital=100.0,
            selected_codes=("000001.SZ",),
            target_weights={"000001.SZ": 1.0},
        ),
        pd.DataFrame([
            {"ts_code": "000001.SZ", "trade_date": "20250102", "open": 10.0},
            {"ts_code": "000001.SZ", "trade_date": "20250103", "open": 11.0},
        ]),
        ["20250101", "20250102", "20250103"],
        holding_days=1,
        min_coverage=1.0,
    )

    assert result.raw_label_coverage == 0.0
    assert result.raw_signal_return is None
    assert result.status == "insufficient_data"


def test_cohort_config_rejects_zero_top_n() -> None:
    with pytest.raises(ValueError):
        CohortBacktestConfig(start="20250101", end="20250131", top_n=0)


def test_cohort_config_defaults_to_total_return_benchmark() -> None:
    assert CohortBacktestConfig(start="20250101", end="20250131").benchmark_code == "H00300.CSI"


def test_cohort_csv_preserves_evaluation_date_and_data_quality(tmp_path) -> None:
    result = CohortResult(
        cohort_id="c1", committed_capital_return=None, executed_capital_return=None,
        raw_signal_return=None, horizon_mark_return=None, liquidation_return=None,
        benchmark_return=None, target_horizon_excess_return=None,
        liquidation_policy_excess_return=None, fill_rate=0.0, idle_cash_ratio=1.0,
        cost_ratio=0.0, exit_delay_days=0, unliquidated_ratio=0.0,
        status=CohortStatus.FAILED_DATA,
        data_quality={"reason": "benchmark_data_insufficient"},
    )
    _write_cohort_returns(tmp_path, [result])
    df = pd.read_csv(tmp_path / "cohort_returns.csv")

    assert {"evaluation_date", "data_quality"}.issubset(df.columns)


def test_cohort_result_exposes_stale_valuation_audit_fields() -> None:
    result = CohortResult(
        cohort_id="c1", committed_capital_return=0.0, executed_capital_return=0.0,
        raw_signal_return=None, horizon_mark_return=0.0, liquidation_return=0.0,
        benchmark_return=0.0, target_horizon_excess_return=0.0,
        liquidation_policy_excess_return=0.0, fill_rate=1.0, idle_cash_ratio=0.0,
        cost_ratio=0.0, exit_delay_days=0, unliquidated_ratio=0.0,
        status=CohortStatus.LIQUIDATED,
    )

    assert hasattr(result, "uses_stale_valuation")
    assert hasattr(result, "max_stale_days")


def test_stale_valuation_ratio_above_two_percent_fails_quality_gate() -> None:
    results = [
        CohortResult(
            cohort_id=f"c{i}", committed_capital_return=0.0, executed_capital_return=0.0,
            raw_signal_return=None, horizon_mark_return=0.0, liquidation_return=0.0,
            benchmark_return=0.0, target_horizon_excess_return=0.0,
            liquidation_policy_excess_return=0.0, fill_rate=1.0, idle_cash_ratio=0.0,
            cost_ratio=0.0, exit_delay_days=0, unliquidated_ratio=0.0,
            status=CohortStatus.LIQUIDATED, uses_stale_valuation=i < 2,
        )
        for i in range(50)
    ]

    aggregate = aggregate_cohorts(
        results, holding_days=5, eval_step=10, evaluation_protocol_key="k" * 64,
        quality_gate={"min_cohort_count_base": 1},
    )

    assert "max_stale_valuation_ratio" in aggregate.quality.failures


def test_failed_cohort_preserves_raw_label_audit_fields() -> None:
    result = _failed_cohort(
        "c1", "raw_label_coverage_below_minimum", evaluation_date="20250115",
        raw_label_coverage=0.5, raw_label_status="insufficient_data",
    )

    assert result.raw_label_coverage == 0.5
    assert result.raw_label_status == "insufficient_data"


def test_raw_label_early_data_shortage_is_insufficient_even_at_zero_threshold() -> None:
    result = compute_raw_signal_return(
        TargetSnapshot(cohort_id="c1", evaluation_date="20250101", committed_capital=1.0,
                       selected_codes=("A",), target_weights={"A": 1.0}),
        pd.DataFrame(), ["20250101"], holding_days=1, min_coverage=0.0,
    )
    assert result.status == "insufficient_data"


@pytest.mark.parametrize("bad_price", [None, pd.NA, "not-a-number"])
def test_non_numeric_adjusted_prices_fail_closed_without_raising(bad_price: object) -> None:
    index = pd.DataFrame([
        {"trade_date": "20250102", "adj_open": bad_price},
        {"trade_date": "20250103", "adj_open": 11.0},
    ])
    target = compute_target_horizon_benchmark(
        index_market=index, trade_dates=["20250101", "20250102", "20250103"],
        signal_date="20250101", holding_days=1,
    )
    liquidation = compute_liquidation_matched_benchmark(
        index_market=index, trade_dates=["20250101", "20250102", "20250103"],
        entry_date="20250102", exit_events=[],
    )
    raw = compute_raw_signal_return(
        TargetSnapshot(cohort_id="c1", evaluation_date="20250101", committed_capital=1.0,
                       selected_codes=("A",), target_weights={"A": 1.0}),
        pd.DataFrame([
            {"ts_code": "A", "trade_date": "20250102", "adj_open": bad_price},
            {"ts_code": "A", "trade_date": "20250103", "adj_open": 11.0},
        ]), ["20250101", "20250102", "20250103"], holding_days=1, min_coverage=1.0,
    )

    assert target.benchmark_return is None
    assert liquidation.benchmark_return is None
    assert raw.raw_signal_return is None
    assert raw.raw_label_coverage == 0.0
    assert raw.status == "insufficient_data"
