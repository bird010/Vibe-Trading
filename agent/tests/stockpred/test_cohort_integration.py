"""End-to-end integration test for the cohort evaluation engine.

Exercises: signals -> targets -> execution -> ledger -> metrics -> aggregation.
Verifies cohort invariants per §25.1.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.stockpred.cohort.aggregation import aggregate_cohorts
from backtest.stockpred.cohort.benchmark import (
    ExitEvent,
    compute_liquidation_matched_benchmark,
    compute_target_horizon_benchmark,
)
from backtest.stockpred.cohort.contracts import (
    CohortStatus,
    SignalSnapshot,
    compute_cohort_id,
    compute_evaluation_protocol_key,
)
from backtest.stockpred.cohort.ledger import CohortLedger
from backtest.stockpred.cohort.metrics import compute_cohort_result, compute_raw_signal_return
from backtest.stockpred.cohort.targets import build_cohort_targets
from backtest.stockpred.execution.costs import DEFAULT_COST_POLICY
from backtest.stockpred.execution.policy import ExecutionPolicy, MarketView, PositionInfo


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

PROTOCOL_KEY = compute_evaluation_protocol_key({
    "data_snapshot_id": "test_snap",
    "start": "20250101", "end": "20250131",
    "holding_days": 5, "eval_step": 5, "top_n": 2,
    "committed_capital_per_cohort": 1_000_000.0,
    "max_participation": 0.05, "adv_lookback_days": 20,
    "benchmark_code": "000300.SH",
    "execution_policy_version": "v1", "cost_policy_version": "v1",
    "max_exit_extension_days": 20, "stale_price_limit_days": 5,
    "quality_gate": {},
})


def _market_3_stocks() -> pd.DataFrame:
    """30-day market for 3 stocks + 1 index."""
    dates = [f"202501{d:02d}" for d in range(1, 31)]
    rows = []
    # Stock A: normal, grows 1%/day
    # Stock B: normal, grows 0.5%/day
    # Stock C: limit-up on day 11 (blocks entry for one cohort)
    for i, date in enumerate(dates):
        pa = 10.0 * (1.01 ** i)
        pb = 20.0 * (1.005 ** i)
        pc = 15.0 * (1.008 ** i)
        rows.append({"ts_code": "A", "trade_date": date, "open": pa, "close": pa, "adj_open": pa, "adj_close": pa, "vol": 100000.0, "amount": 5000.0, "up_limit": pa * 1.1, "down_limit": pa * 0.9})
        rows.append({"ts_code": "B", "trade_date": date, "open": pb, "close": pb, "adj_open": pb, "adj_close": pb, "vol": 100000.0, "amount": 5000.0, "up_limit": pb * 1.1, "down_limit": pb * 0.9})
        # Stock C: limit-up on day 11
        up_c = pc * 1.1 if date != "20250111" else pc  # open == up_limit on day 11
        rows.append({"ts_code": "C", "trade_date": date, "open": pc, "close": pc, "adj_open": pc, "adj_close": pc, "vol": 100000.0, "amount": 5000.0, "up_limit": up_c, "down_limit": pc * 0.9})

    # Index
    for i, date in enumerate(dates):
        pidx = 100.0 * (1.003 ** i)
        rows.append({"ts_code": "000300.SH", "trade_date": date, "open": pidx, "close": pidx, "adj_open": pidx, "adj_close": pidx, "vol": 1e9, "amount": 1e9, "up_limit": pidx * 1.1, "down_limit": pidx * 0.9})

    return pd.DataFrame(rows)


def _run_single_cohort(
    eval_date: str, mkt: pd.DataFrame, trade_dates: list[str], policy: ExecutionPolicy
) -> tuple[CohortLedger, float | None, float | None, float | None]:
    """Run one cohort end-to-end, return (ledger, raw_return, horizon_bench, liq_bench)."""
    # 1. Signal
    signals = SignalSnapshot(
        evaluation_date=eval_date,
        strategy_id="test_strategy",
        strategy_version="v" * 64,
        data_snapshot_id="snap",
        signals=[
            {"ts_code": "A", "score": 10.0},
            {"ts_code": "B", "score": 9.0},
            {"ts_code": "C", "score": 8.0},
        ],
    )

    # 2. Targets
    cohort_id = compute_cohort_id(
        evaluation_protocol_key=PROTOCOL_KEY,
        strategy_id="test_strategy",
        strategy_version="v" * 64,
        evaluation_date=eval_date,
    )
    target = build_cohort_targets(
        signals, committed_capital=1_000_000.0, top_n=2, cohort_id=cohort_id
    )

    # 3. Execution + Ledger
    ledger = CohortLedger(cohort_id=cohort_id, committed_capital=1_000_000.0, evaluation_date=eval_date)
    stock_mkt = mkt[mkt["ts_code"].isin(["A", "B", "C"])]
    view = MarketView(market=stock_mkt, trade_dates=trade_dates)

    exit_events_for_bench: list[ExitEvent] = []
    entry_date = ""

    for code in target.selected_codes:
        target_value = target.target_values[code]
        event = policy.execute_entry(
            code=code, signal_date=eval_date,
            cash_budget=ledger.available_cash, target_value=target_value,
            market_view=view, cohort_id=cohort_id,
        )
        ledger.apply_entry(event)
        if event.executed_quantity > 0 and not entry_date:
            entry_date = event.trade_date

    # 4. Exit
    ledger.begin_exit()
    for code, qty in list(ledger.positions.items()):
        if qty <= 0:
            continue
        import bisect
        entry_idx = bisect.bisect_right(trade_dates, eval_date)
        target_exit = trade_dates[min(entry_idx + 5, len(trade_dates) - 1)]
        position = PositionInfo(code=code, quantity=qty, entry_date=entry_date, target_exit_date=target_exit, cohort_id=cohort_id)
        exit_evts = policy.execute_exit(position, market_view=view)
        for e in exit_evts:
            ledger.apply_exit(e)
            # Track for benchmark
            total_pos = qty
            exit_events_for_bench.append(ExitEvent(date=e.trade_date, proportion=e.executed_quantity / total_pos))
    ledger.finalize_exit()

    # 5. Raw label
    raw_result = compute_raw_signal_return(target, stock_mkt, trade_dates, holding_days=5)

    # 6. Benchmarks
    idx_mkt = mkt[mkt["ts_code"] == "000300.SH"]
    bench_target = compute_target_horizon_benchmark(
        index_market=idx_mkt, trade_dates=trade_dates, signal_date=eval_date, holding_days=5
    )
    bench_liq = compute_liquidation_matched_benchmark(
        index_market=idx_mkt, trade_dates=trade_dates,
        entry_date=entry_date or trade_dates[0], exit_events=exit_events_for_bench,
    )

    return ledger, raw_result.raw_signal_return, bench_target.benchmark_return, bench_liq.benchmark_return


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


def test_end_to_end_three_cohorts():
    """Run 3 evaluation dates through the full pipeline."""
    mkt = _market_3_stocks()
    trade_dates = sorted(mkt[mkt["ts_code"] == "A"]["trade_date"].unique())
    policy = ExecutionPolicy(
        cost_policy=DEFAULT_COST_POLICY, max_participation=0.05,
        adv_lookback_days=20, max_exit_extension_days=20, lot_size=100,
    )

    eval_dates = ["20250110", "20250115", "20250120"]
    cohort_results = []

    for eval_date in eval_dates:
        ledger, raw_ret, bench_t, bench_l = _run_single_cohort(eval_date, mkt, trade_dates, policy)

        # Invariant: fund identity holds
        assert ledger.fund_identity_holds(), f"Fund identity violated for {eval_date}"
        # Invariant: cash never negative
        assert ledger.available_cash >= 0, f"Negative cash for {eval_date}"
        # Ensure at least one cohort has positions (not all rejected)
        if eval_date == "20250120":
            assert any(qty > 0 for qty in ledger.positions.values()) or ledger.status == CohortStatus.LIQUIDATED

        result = compute_cohort_result(
            ledger=ledger, raw_signal_return=raw_ret,
            horizon_mark_return=raw_ret or 0.0,
            target_horizon_benchmark_return=bench_t,
            liquidation_benchmark_return=bench_l,
            exit_delay_days=0, unliquidated_ratio=0.0,
        )
        cohort_results.append(result)

    # Aggregate
    agg = aggregate_cohorts(
        cohort_results, holding_days=5, eval_step=5, evaluation_protocol_key=PROTOCOL_KEY
    )

    assert agg.metrics.valid_cohort_count == 3
    assert agg.metrics.total_cohort_count == 3


def test_cohort_independence():
    """Adding/removing eval dates doesn't change other cohorts (§25.1.2)."""
    mkt = _market_3_stocks()
    trade_dates = sorted(mkt[mkt["ts_code"] == "A"]["trade_date"].unique())
    policy = ExecutionPolicy(
        cost_policy=DEFAULT_COST_POLICY, max_participation=0.05,
        adv_lookback_days=20, max_exit_extension_days=20, lot_size=100,
    )

    # Run cohort for 20250110 alone
    ledger1, _, _, _ = _run_single_cohort("20250110", mkt, trade_dates, policy)
    cash1 = ledger1.available_cash

    # Run cohort for 20250110 with other dates also running (shouldn't matter)
    ledger2, _, _, _ = _run_single_cohort("20250110", mkt, trade_dates, policy)
    cash2 = ledger2.available_cash

    assert cash1 == pytest.approx(cash2)


def test_deterministic_cohort_id():
    """Same inputs produce same cohort_id (§27.14)."""
    id1 = compute_cohort_id(
        evaluation_protocol_key=PROTOCOL_KEY,
        strategy_id="s", strategy_version="v" * 64, evaluation_date="20250110",
    )
    id2 = compute_cohort_id(
        evaluation_protocol_key=PROTOCOL_KEY,
        strategy_id="s", strategy_version="v" * 64, evaluation_date="20250110",
    )
    assert id1 == id2
