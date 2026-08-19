"""Tests for cohort target builder."""

from __future__ import annotations

import pytest

from backtest.stockpred.cohort.contracts import SignalSnapshot, TargetSnapshot
from backtest.stockpred.cohort.targets import build_cohort_targets
from backtest.stockpred.execution.costs import DEFAULT_COST_POLICY


def _signals(scores: list[tuple[str, float]]) -> SignalSnapshot:
    """Build a signal snapshot from (code, score) pairs."""
    return SignalSnapshot(
        evaluation_date="20250102",
        strategy_id="test",
        strategy_version="a" * 64,
        data_snapshot_id="snap1",
        signals=[{"ts_code": code, "score": score} for code, score in scores],
    )


def test_top_n_selection_by_score():
    snap = _signals([("C", 3.0), ("A", 1.0), ("B", 2.0), ("D", 4.0)])
    target = build_cohort_targets(snap, committed_capital=1_000_000.0, top_n=2)

    assert target.selected_codes == ("D", "C")  # top 2 by score


def test_deterministic_tie_break_by_code():
    snap = _signals([("B", 5.0), ("A", 5.0), ("C", 5.0)])
    target = build_cohort_targets(snap, committed_capital=1_000_000.0, top_n=2)

    # Same score -> sort by ts_code ascending
    assert target.selected_codes == ("A", "B")


def test_equal_weight():
    snap = _signals([("A", 10.0), ("B", 9.0), ("C", 8.0), ("D", 7.0)])
    target = build_cohort_targets(snap, committed_capital=1_000_000.0, top_n=4)

    for code in target.selected_codes:
        assert target.target_weights[code] == pytest.approx(0.25)


def test_target_values_sum_less_than_committed_due_to_fee_reservation():
    snap = _signals([("A", 10.0), ("B", 9.0)])
    target = build_cohort_targets(
        snap, committed_capital=1_000_000.0, top_n=2, fee_policy=DEFAULT_COST_POLICY
    )

    total_target = sum(target.target_values.values())
    # Target values should be less than committed capital (fee pre-reserved)
    assert total_target < 1_000_000.0
    # But not by too much (fees are small)
    assert total_target > 990_000.0


def test_empty_signals_produces_empty_target():
    snap = _signals([])
    target = build_cohort_targets(snap, committed_capital=1_000_000.0, top_n=50)

    assert target.selected_codes == ()
    assert target.target_weights == {}
    assert target.target_values == {}


def test_fewer_signals_than_top_n():
    snap = _signals([("A", 10.0), ("B", 9.0)])
    target = build_cohort_targets(snap, committed_capital=1_000_000.0, top_n=50)

    # Only 2 available, weight = 1/2 each (not 1/50)
    assert len(target.selected_codes) == 2
    assert target.target_weights["A"] == pytest.approx(0.5)
    assert target.target_weights["B"] == pytest.approx(0.5)


def test_target_snapshot_is_immutable():
    snap = _signals([("A", 10.0)])
    target = build_cohort_targets(snap, committed_capital=1_000_000.0, top_n=1)

    with pytest.raises(AttributeError):
        target.committed_capital = 0.0  # type: ignore[misc]


def test_no_previous_holdings_consideration():
    # Even if same codes appear, each call is independent
    snap = _signals([("A", 10.0), ("B", 9.0), ("C", 8.0)])
    t1 = build_cohort_targets(snap, committed_capital=1_000_000.0, top_n=2)
    t2 = build_cohort_targets(snap, committed_capital=1_000_000.0, top_n=2)

    assert t1.selected_codes == t2.selected_codes
    assert t1.target_weights == t2.target_weights


def test_cohort_id_in_target():
    snap = _signals([("A", 10.0)])
    target = build_cohort_targets(
        snap, committed_capital=1_000_000.0, top_n=1, cohort_id="cohort_test123"
    )
    assert target.cohort_id == "cohort_test123"
