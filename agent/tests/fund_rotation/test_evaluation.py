"""Phase 0 Task 5 — formal evaluation calendar, equity index validation, and
initial_nav metric semantics (design §24/§32.1)."""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.fund_rotation.evaluation import (
    EvaluationContext,
    TargetSnapshot,
    schedule_targets,
    validate_equity_index,
)
from backtest.fund_rotation.metrics import compute_performance_metrics

# ── EvaluationContext calendar construction ──

class TestEvaluationContext:
    def test_from_range_keeps_only_trading_days_within_bounds(self):
        calendar = ["20240101", "20240102", "20240103", "20240104", "20240105"]
        ctx = EvaluationContext.from_range(calendar, start_date="20240102", end_date="20240104")
        assert ctx.trading_dates == (
            pd.Timestamp("20240102"), pd.Timestamp("20240103"), pd.Timestamp("20240104"),
        )
        assert ctx.initial_nav == 1.0

    def test_from_range_is_sorted_and_unique(self):
        calendar = ["20240105", "20240103", "20240103", "20240101"]
        ctx = EvaluationContext.from_range(calendar, start_date="20240101", end_date="20240105")
        assert ctx.trading_dates == (
            pd.Timestamp("20240101"), pd.Timestamp("20240103"), pd.Timestamp("20240105"),
        )

    def test_initial_nav_is_configurable(self):
        ctx = EvaluationContext.from_range(["20240101"], "20240101", "20240101", initial_nav=2.0)
        assert ctx.initial_nav == 2.0


# ── validate_equity_index ──

def _equity(dates, values=None):
    values = values if values is not None else [1.0] * len(dates)
    return pd.Series(values, index=list(dates), name="equity")


class TestValidateEquityIndex:
    def test_matching_index_passes(self):
        ctx = EvaluationContext.from_range(
            ["20240101", "20240102", "20240103"], "20240101", "20240103",
        )
        equity = _equity(["20240101", "20240102", "20240103"])
        validate_equity_index(equity, ctx)  # no raise

    def test_missing_day_is_rejected(self):
        ctx = EvaluationContext.from_range(
            ["20240101", "20240102", "20240103"], "20240101", "20240103",
        )
        equity = _equity(["20240101", "20240103"])  # missing 20240102
        with pytest.raises(ValueError, match="evaluation calendar"):
            validate_equity_index(equity, ctx)

    def test_extra_day_is_rejected(self):
        ctx = EvaluationContext.from_range(
            ["20240101", "20240102"], "20240101", "20240102",
        )
        equity = _equity(["20240101", "20240102", "20240103"])  # extra
        with pytest.raises(ValueError, match="evaluation calendar"):
            validate_equity_index(equity, ctx)

    def test_duplicate_date_is_rejected(self):
        ctx = EvaluationContext.from_range(
            ["20240101", "20240102"], "20240101", "20240102",
        )
        equity = _equity(["20240101", "20240101", "20240102"])
        with pytest.raises(ValueError, match="duplicate"):
            validate_equity_index(equity, ctx)

    def test_unordered_index_is_rejected(self):
        ctx = EvaluationContext.from_range(
            ["20240101", "20240102"], "20240101", "20240102",
        )
        equity = _equity(["20240102", "20240101"])
        with pytest.raises(ValueError, match="increasing"):
            validate_equity_index(equity, ctx)

    def test_does_not_silently_intersect(self):
        """A shifted-but-overlapping index must fail, not be shortened."""
        ctx = EvaluationContext.from_range(
            ["20240101", "20240102", "20240103"], "20240101", "20240103",
        )
        equity = _equity(["20240102", "20240103", "20240104"])  # shifted
        with pytest.raises(ValueError):
            validate_equity_index(equity, ctx)


# ── initial_nav metric semantics ──

class TestInitialNavMetrics:
    def test_first_period_return_measured_against_initial_nav(self):
        # First day NAV 0.98 -> first period return must be 0.98/1.0 - 1 = -0.02.
        cumulative = pd.Series(
            [0.98, 0.99, 1.00], index=["20240101", "20240102", "20240103"],
        )
        metrics = compute_performance_metrics(cumulative, periods_per_year=244, initial_nav=1.0)
        # total return measured from the initial_nav anchor (not the first point)
        assert metrics["total_return"] == pytest.approx(1.00 / 1.0 - 1.0)
        # the first-day loss is captured as the worst period
        assert metrics["worst_period"] == pytest.approx(0.98 / 1.0 - 1.0)
        # one return per evaluation day (the dateless anchor is not a trading day)
        assert metrics["num_periods"] == 3

    def test_first_day_drop_drives_max_drawdown(self):
        # A first-day drop from the 1.0 anchor must register as drawdown.
        cumulative = pd.Series([0.95, 0.95, 0.95], index=["20240101", "20240102", "20240103"])
        metrics = compute_performance_metrics(cumulative, periods_per_year=244, initial_nav=1.0)
        assert metrics["max_drawdown"] == pytest.approx(0.95 / 1.0 - 1.0)

    def test_input_series_is_not_mutated_to_force_first_day_one(self):
        cumulative = pd.Series([0.98, 1.02], index=["20240101", "20240102"])
        before = cumulative.copy()
        compute_performance_metrics(cumulative, periods_per_year=244, initial_nav=1.0)
        # The function must not rewrite the first day to 1.0.
        pd.testing.assert_series_equal(cumulative, before)

    def test_single_day_series_uses_anchor(self):
        cumulative = pd.Series([0.99], index=["20240101"])
        metrics = compute_performance_metrics(cumulative, periods_per_year=244, initial_nav=1.0)
        assert metrics["total_return"] == pytest.approx(0.99 / 1.0 - 1.0)
        assert metrics["num_periods"] == 1

    def test_recovery_never_recovers_from_anchor_drawdown(self):
        # A first-day drop from the 1.0 anchor that never recovers: recovery = -1.
        cumulative = pd.Series([0.95, 0.95, 0.95], index=["20240101", "20240102", "20240103"])
        metrics = compute_performance_metrics(cumulative, periods_per_year=244, initial_nav=1.0)
        assert metrics["max_drawdown"] == pytest.approx(-0.05)
        assert metrics["max_drawdown_recovery_periods"] == -1

    def test_recovery_counts_from_anchor_trough(self):
        # Drop to 0.95 then back to the 1.0 peak one day later: recovery = 1 period.
        cumulative = pd.Series([0.95, 1.00, 1.05], index=["20240101", "20240102", "20240103"])
        metrics = compute_performance_metrics(cumulative, periods_per_year=244, initial_nav=1.0)
        assert metrics["max_drawdown"] == pytest.approx(-0.05)
        assert metrics["max_drawdown_recovery_periods"] == 1


# ── schedule_targets ──

def _ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s)


class TestScheduleTargets:
    EVAL_DATES = [_ts("20240102"), _ts("20240103"), _ts("20240104"), _ts("20240105")]

    def test_in_evaluation_signal_executes_next_eval_day(self):
        targets = [TargetSnapshot(_ts("20240102"), {"A": 0.5})]
        schedule = schedule_targets(targets, self.EVAL_DATES)
        # Signal on 20240102 -> first eval day strictly after = 20240103.
        assert list(schedule.keys()) == [_ts("20240103")]
        assert schedule[_ts("20240103")].weights == {"A": 0.5}

    def test_pre_evaluation_signal_executes_at_first_eval_day(self):
        # Signal before the first evaluation day builds the initial position at
        # the interval open (design §24).
        targets = [TargetSnapshot(_ts("20231220"), {"A": 1.0})]
        schedule = schedule_targets(targets, self.EVAL_DATES)
        assert list(schedule.keys()) == [_ts("20240102")]  # first eval day
        assert schedule[_ts("20240102")].weights == {"A": 1.0}

    def test_later_signal_supersedes_on_same_exec_day(self):
        targets = [
            TargetSnapshot(_ts("20240102"), {"A": 0.5}),
            TargetSnapshot(_ts("20240102"), {"B": 0.7}),  # same signal date, later
        ]
        schedule = schedule_targets(targets, self.EVAL_DATES)
        assert schedule[_ts("20240103")].weights == {"B": 0.7}

    def test_signal_after_last_eval_day_is_not_scheduled(self):
        targets = [TargetSnapshot(_ts("20240110"), {"A": 1.0})]
        schedule = schedule_targets(targets, self.EVAL_DATES)
        assert schedule == {}

    def test_empty_weights_means_cash(self):
        targets = [TargetSnapshot(_ts("20240102"), {})]
        schedule = schedule_targets(targets, self.EVAL_DATES)
        assert schedule[_ts("20240103")].weights == {}

    def test_multiple_signals_map_to_distinct_exec_days(self):
        targets = [
            TargetSnapshot(_ts("20240102"), {"A": 0.5}),
            TargetSnapshot(_ts("20240103"), {"B": 0.5}),
        ]
        schedule = schedule_targets(targets, self.EVAL_DATES)
        assert set(schedule.keys()) == {_ts("20240103"), _ts("20240104")}
