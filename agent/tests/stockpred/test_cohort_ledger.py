"""Tests for per-cohort ledger with fund identity invariant."""

from __future__ import annotations

from dataclasses import replace

import pytest

from backtest.stockpred.cohort.contracts import CohortStatus, ExecutionEvent
from backtest.stockpred.cohort.ledger import CohortLedger


def _buy_event(code: str, qty: int, price: float, fees: float = 0.0, status: str = "FILLED", cohort_id: str = "c1") -> ExecutionEvent:
    return ExecutionEvent(
        order_id=f"buy_{code}",
        cohort_id=cohort_id,
        trade_date="20250103",
        code=code,
        side="BUY",
        requested_quantity=qty,
        executed_quantity=qty if status != "REJECTED" else 0,
        executed_value=qty * price if status != "REJECTED" else 0.0,
        price=price,
        fee_components={"commission": fees, "stamp_duty": 0, "transfer_fee": 0, "slippage": 0, "market_impact": 0},
        status=status,
        reason_code="capacity" if status == "REJECTED" else None,
        remaining_quantity=qty if status == "REJECTED" else 0,
        requested_value=qty * price,
    )


def _sell_event(code: str, qty: int, price: float, fees: float = 0.0, status: str = "FILLED", cohort_id: str = "c1") -> ExecutionEvent:
    return ExecutionEvent(
        order_id=f"sell_{code}",
        cohort_id=cohort_id,
        trade_date="20250108",
        code=code,
        side="SELL",
        requested_quantity=qty,
        executed_quantity=qty,
        executed_value=qty * price,
        price=price,
        fee_components={"commission": fees, "stamp_duty": fees * 0.5, "transfer_fee": 0, "slippage": 0, "market_impact": 0},
        status=status,
        remaining_quantity=0,
        requested_value=qty * price,
    )


def test_initial_state():
    ledger = CohortLedger(cohort_id="c1", committed_capital=1_000_000.0, evaluation_date="20250102")
    assert ledger.available_cash == 1_000_000.0
    assert ledger.status == CohortStatus.PLANNED
    assert ledger.positions == {}


def test_entry_deducts_cash_and_fees():
    ledger = CohortLedger(cohort_id="c1", committed_capital=1_000_000.0, evaluation_date="20250102")
    ledger.apply_entry(_buy_event("A", 1000, 10.0, fees=50.0))

    # Cash = 1M - 10K - 50 = 989,950
    assert ledger.available_cash == pytest.approx(989_950.0)
    assert ledger.positions == {"A": 1000}
    assert ledger.total_fees_paid == pytest.approx(50.0)


def test_cash_never_negative():
    ledger = CohortLedger(cohort_id="c1", committed_capital=10_000.0, evaluation_date="20250102")
    # Try to buy more than available
    ledger.apply_entry(_buy_event("A", 2000, 10.0, fees=100.0))

    # Should not go negative - the ledger should reject or cap
    assert ledger.available_cash >= 0


def test_rejected_entry_no_change():
    ledger = CohortLedger(cohort_id="c1", committed_capital=1_000_000.0, evaluation_date="20250102")
    ledger.apply_entry(_buy_event("A", 1000, 10.0, status="REJECTED"))

    assert ledger.available_cash == 1_000_000.0
    assert ledger.positions == {}


def test_partial_fill():
    ledger = CohortLedger(cohort_id="c1", committed_capital=1_000_000.0, evaluation_date="20250102")
    event = ExecutionEvent(
        order_id="buy_A", cohort_id="c1", trade_date="20250103", code="A", side="BUY",
        requested_quantity=2000, executed_quantity=1000, executed_value=10_000.0,
        price=10.0, fee_components={"commission": 50.0}, status="PARTIAL",
        remaining_quantity=1000,
    )
    ledger.apply_entry(event)

    assert ledger.positions == {"A": 1000}
    assert ledger.available_cash == pytest.approx(1_000_000.0 - 10_000.0 - 50.0)


def test_exit_adds_proceeds_minus_fees():
    ledger = CohortLedger(cohort_id="c1", committed_capital=1_000_000.0, evaluation_date="20250102")
    ledger.apply_entry(_buy_event("A", 1000, 10.0, fees=50.0))
    assert ledger.apply_exit(_sell_event("A", 1000, 12.0, fees=80.0)) is True

    # _sell_event: commission=80, stamp_duty=40, total_fees=120
    # Net proceeds = 12K - 120 = 11,880
    # Cash = 989,950 + 11,880 = 1,001,830
    assert ledger.available_cash == pytest.approx(989_950.0 + 12_000.0 - 120.0)
    assert ledger.positions == {"A": 0} or ledger.positions == {}
    assert ledger.total_exit_proceeds == pytest.approx(12_000.0 - 120.0)


def test_rejected_exit_is_accepted_without_mutating_position_or_cash():
    ledger = CohortLedger(cohort_id="c1", committed_capital=10_000.0, evaluation_date="20250102")
    ledger.apply_entry(_buy_event("A", 100, 10.0))
    before = (ledger.available_cash, ledger.positions.copy(), ledger.total_fees_paid)
    event = ExecutionEvent(
        order_id="rejected_sell_A", cohort_id="c1", trade_date="20250108", code="A", side="SELL",
        requested_quantity=100, requested_value=1_200.0, executed_quantity=0, executed_value=0.0,
        price=12.0, status="REJECTED", remaining_quantity=100,
    )

    assert ledger.apply_exit(event) is True

    assert (ledger.available_cash, ledger.positions, ledger.total_fees_paid) == before


def test_fund_identity_holds():
    ledger = CohortLedger(cohort_id="c1", committed_capital=1_000_000.0, evaluation_date="20250102")
    ledger.apply_entry(_buy_event("A", 1000, 10.0, fees=50.0))
    ledger.apply_entry(_buy_event("B", 500, 20.0, fees=30.0))
    ledger.apply_exit(_sell_event("A", 1000, 12.0, fees=80.0))

    # Fund identity: committed = cash + position_cost + fees_paid + exit_net
    # Actually: committed = cash + residual_position_value_at_cost + total_fees + exit_proceeds - pnl
    # Simpler: committed = cash + sum(positions * entry_price) + fees - exit_proceeds + exit_proceeds
    # The invariant: committed = cash + position_value_at_cost + total_fees_paid - total_exit_proceeds + total_exit_proceeds
    # Actually the simplest: committed = cash + position_cost_basis + total_fees - net_exit_gains
    # Let's use: committed = available_cash + position_cost + total_fees_paid - total_exit_proceeds + position_market_adjustment
    # For simplicity, verify: committed = cash + (positions at entry cost) + fees - exit_proceeds + exit_proceeds
    # The real invariant per §3.3: C = idle_cash + position_value + fees + exited_cash + residual
    assert ledger.fund_identity_holds(entry_prices={"A": 10.0, "B": 20.0})


def test_cross_cohort_isolation():
    l1 = CohortLedger(cohort_id="c1", committed_capital=1_000_000.0, evaluation_date="20250102")
    l2 = CohortLedger(cohort_id="c2", committed_capital=1_000_000.0, evaluation_date="20250103")

    l1.apply_entry(_buy_event("A", 1000, 10.0))

    assert l1.positions == {"A": 1000}
    assert l2.positions == {}
    assert l2.available_cash == 1_000_000.0


def test_status_transitions():
    ledger = CohortLedger(cohort_id="c1", committed_capital=1_000_000.0, evaluation_date="20250102")
    assert ledger.status == CohortStatus.PLANNED

    ledger.apply_entry(_buy_event("A", 1000, 10.0))
    assert ledger.status == CohortStatus.HOLDING

    ledger.begin_exit()
    assert ledger.status == CohortStatus.EXITING

    ledger.apply_exit(_sell_event("A", 1000, 12.0))
    ledger.finalize_exit()
    assert ledger.status == CohortStatus.LIQUIDATED


def test_unliquidated_when_position_remains():
    ledger = CohortLedger(cohort_id="c1", committed_capital=1_000_000.0, evaluation_date="20250102")
    ledger.apply_entry(_buy_event("A", 1000, 10.0))
    ledger.begin_exit()
    # Only sell half
    ledger.apply_exit(_sell_event("A", 500, 12.0))
    ledger.finalize_exit()

    assert ledger.status == CohortStatus.UNLIQUIDATED
    assert ledger.positions.get("A", 0) == 500


def test_fully_rejected_order_reduces_fill_rate():
    ledger = CohortLedger(cohort_id="cohort_test", committed_capital=10_000.0, evaluation_date="20250102")
    event = ExecutionEvent(
        order_id="rejected_A", cohort_id="cohort_test", trade_date="20250103", code="A", side="BUY",
        requested_quantity=1_000, requested_value=10_000.0, executed_quantity=0, executed_value=0.0,
        price=0.0, status="REJECTED", reason_code="suspended", remaining_quantity=1_000,
    )

    ledger.apply_entry(event)

    assert ledger.fill_rate == 0.0


def test_cross_cohort_and_oversell_fail_execution():
    cross = CohortLedger(cohort_id="c1", committed_capital=10_000.0, evaluation_date="20250102")
    cross.apply_entry(_buy_event("A", 100, 10.0, cohort_id="cohort_test"))
    assert cross.status == CohortStatus.FAILED_EXECUTION

    oversold = CohortLedger(cohort_id="cohort_test", committed_capital=10_000.0, evaluation_date="20250102")
    oversold.apply_entry(_buy_event("A", 100, 10.0, cohort_id="cohort_test"))
    assert oversold.apply_exit(_sell_event("A", 200, 10.0, cohort_id="cohort_test")) is False
    assert oversold.status == CohortStatus.FAILED_EXECUTION


def test_valid_unknown_quantity_rejected_entry_is_accepted_without_mutation():
    ledger = CohortLedger(cohort_id="c1", committed_capital=10_000.0, evaluation_date="20250102")
    event = ExecutionEvent(
        order_id="unknown_entry", cohort_id="c1", trade_date="20250103", code="A", side="BUY",
        requested_quantity=0, requested_value=10_000.0, executed_quantity=0, executed_value=0.0,
        price=0.0, status="REJECTED", remaining_quantity=0, requested_quantity_known=False,
    )

    ledger.apply_entry(event)

    assert ledger.status == CohortStatus.PLANNED
    assert ledger.available_cash == 10_000.0
    assert ledger.positions == {}


@pytest.mark.parametrize(
    "event",
    [
        ExecutionEvent(
            order_id="unknown_filled", cohort_id="c1", trade_date="20250103", code="A", side="BUY",
            requested_quantity=100, requested_value=1_000.0, executed_quantity=100, executed_value=1_000.0,
            price=10.0, status="FILLED", remaining_quantity=0, requested_quantity_known=False,
        ),
        ExecutionEvent(
            order_id="unknown_rejected_with_quantity", cohort_id="c1", trade_date="20250103", code="A", side="BUY",
            requested_quantity=100, requested_value=1_000.0, executed_quantity=0, executed_value=0.0,
            price=10.0, status="REJECTED", remaining_quantity=100, requested_quantity_known=False,
        ),
        ExecutionEvent(
            order_id="non_bool_known", cohort_id="c1", trade_date="20250103", code="A", side="BUY",
            requested_quantity=0, requested_value=0.0, executed_quantity=0, executed_value=0.0,
            price=0.0, status="REJECTED", remaining_quantity=0, requested_quantity_known=1,  # type: ignore[arg-type]
        ),
    ],
)
def test_invalid_requested_quantity_known_combinations_fail_closed(event: ExecutionEvent):
    ledger = CohortLedger(cohort_id="c1", committed_capital=10_000.0, evaluation_date="20250102")

    ledger.apply_entry(event)

    assert ledger.status == CohortStatus.FAILED_EXECUTION


def test_cash_insufficient_entry_fails_execution():
    ledger = CohortLedger(cohort_id="c1", committed_capital=100.0, evaluation_date="20250102")
    ledger.apply_entry(_buy_event("A", 100, 10.0))
    assert ledger.status == CohortStatus.FAILED_EXECUTION


@pytest.mark.parametrize(
    "event",
    [
        replace(_buy_event("A", 100, 10.0), executed_value=999.0),
        replace(_buy_event("A", 100, 10.0), fee_components={"commission": -1.0}),
        replace(_buy_event("A", 100, 10.0), executed_value=float("nan")),
        replace(_buy_event("A", 100, 10.0), status="PARTIAL", remaining_quantity=0),
    ],
)
def test_malformed_entry_fails_without_mutating_ledger(event: ExecutionEvent):
    ledger = CohortLedger(cohort_id="c1", committed_capital=10_000.0, evaluation_date="20250102")
    before = (ledger.available_cash, ledger.positions.copy(), ledger.total_fees_paid)

    ledger.apply_entry(event)

    assert ledger.status == CohortStatus.FAILED_EXECUTION
    assert (ledger.available_cash, ledger.positions, ledger.total_fees_paid) == before


@pytest.mark.parametrize(
    "event",
    [
        replace(_sell_event("A", 100, 12.0), executed_value=1_199.0),
        replace(_sell_event("A", 100, 12.0), fee_components={"commission": 1_201.0}),
        replace(_sell_event("A", 100, 12.0), remaining_quantity=1),
    ],
)
def test_malformed_exit_fails_without_mutating_ledger(event: ExecutionEvent):
    ledger = CohortLedger(cohort_id="c1", committed_capital=10_000.0, evaluation_date="20250102")
    ledger.apply_entry(_buy_event("A", 100, 10.0))
    before = (ledger.available_cash, ledger.positions.copy(), ledger.total_fees_paid, ledger.total_exit_proceeds)

    ledger.apply_exit(event)

    assert ledger.status == CohortStatus.FAILED_EXECUTION
    assert (ledger.available_cash, ledger.positions, ledger.total_fees_paid, ledger.total_exit_proceeds) == before


def test_failed_ledger_ignores_later_valid_events():
    ledger = CohortLedger(cohort_id="c1", committed_capital=10_000.0, evaluation_date="20250102")
    ledger.apply_entry(replace(_buy_event("A", 100, 10.0), executed_value=999.0))
    before = (ledger.available_cash, ledger.positions.copy(), ledger.total_fees_paid)

    ledger.apply_entry(_buy_event("A", 100, 10.0))
    ledger.apply_exit(_sell_event("A", 100, 12.0))

    assert (ledger.available_cash, ledger.positions, ledger.total_fees_paid) == before
