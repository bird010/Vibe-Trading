"""Formal evaluation calendar and equity index validation — Phase 0 Task 5.

Design §24/§32.1: the formal evaluation interval is the set of actual market
trading days between ``start_date`` and ``end_date`` (inclusive). All strategies
share the same evaluation dates; the interval must not begin at each strategy's
first fill. Metrics anchor on ``initial_nav=1.0`` before the first period, so the
first day's return is measured against 1.0 rather than dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import pandas as pd


@dataclass(frozen=True)
class TargetSnapshot:
    """One target-weight decision: the signal date and its target weights.

    ``weights`` maps ts_code -> target weight (treated as read-only). A signal
    with empty weights means "hold cash" (design §7.1 SET_TARGETS to cash).
    """

    signal_date: pd.Timestamp
    weights: Mapping[str, float]


def schedule_targets(
    targets: Sequence[TargetSnapshot],
    evaluation_dates: Sequence[pd.Timestamp],
) -> dict[pd.Timestamp, TargetSnapshot]:
    """Map each target to its execution date (design §24).

    Execution happens at the first evaluation trading day STRICTLY AFTER the
    signal date. A signal dated before the first evaluation day therefore
    executes at the first evaluation day (the pre-evaluation target builds the
    initial position at the interval open). When several signals map to the same
    execution day, the latest signal supersedes the earlier ones.

    Args:
        targets: target-weight decisions (any order).
        evaluation_dates: the formal evaluation trading calendar.

    Returns:
        Mapping of execution_date -> the TargetSnapshot to execute then.
    """
    eval_dates = sorted(evaluation_dates)
    schedule: dict[pd.Timestamp, TargetSnapshot] = {}
    for snap in sorted(targets, key=lambda s: s.signal_date):
        exec_date = next((d for d in eval_dates if d > snap.signal_date), None)
        if exec_date is not None:
            schedule[exec_date] = snap  # later signal supersedes
    return schedule


@dataclass(frozen=True)
class EvaluationContext:
    """Formal evaluation interval (trading days) and the initial NAV anchor."""

    trading_dates: tuple[pd.Timestamp, ...]
    initial_nav: float = 1.0

    @classmethod
    def from_range(
        cls,
        trading_calendar,
        start_date: str,
        end_date: str,
        initial_nav: float = 1.0,
    ) -> "EvaluationContext":
        """Build the evaluation calendar from trading days within [start, end].

        ``trading_calendar`` is an iterable of trading days (str YYYYMMDD or
        Timestamp). Only days with start_date <= d <= end_date are kept, sorted
        ascending and de-duplicated.
        """
        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date)
        dates = sorted({pd.Timestamp(d) for d in trading_calendar} & set(_day_range(start, end)))
        return cls(trading_dates=tuple(dates), initial_nav=initial_nav)


def _day_range(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    """Inclusive list of calendar days between start and end (for filtering)."""
    return [start + pd.Timedelta(days=i) for i in range((end - start).days + 1)]


def iso_week_endings(calendar) -> list[str]:
    """Last actual trading day of each ISO week, ascending.

    Mirrors the week grouping of ``compute_weekly_returns`` (ISO year-week,
    last trading day kept). Shared by the Runner's warmup boundary and the
    weekly baseline schedule so holiday-shortened weeks cannot shift decision
    dates (§6).
    """
    endings: dict[tuple[int, int], str] = {}
    for day in calendar:
        ts = pd.Timestamp(day)
        iso_year, iso_week, _ = ts.isocalendar()
        key = (int(iso_year), int(iso_week))
        ds = ts.strftime("%Y%m%d")
        if key not in endings or ds > endings[key]:
            endings[key] = ds
    return sorted(endings.values())


def _as_timestamps(index) -> list[pd.Timestamp]:
    return [pd.Timestamp(d) for d in index]


def validate_equity_index(equity: pd.Series, context: EvaluationContext) -> None:
    """Strictly validate an equity series index against the evaluation calendar.

    The index must contain exactly the evaluation trading days — no missing day,
    no extra day, no duplicate, strictly increasing. This deliberately does NOT
    only check first/last nor silently shorten via intersection (design §27/§34).

    Raises:
        ValueError: if the index does not exactly match the evaluation calendar.
    """
    expected = list(context.trading_dates)
    actual = _as_timestamps(equity.index)

    if len(actual) != len(set(actual)):
        duplicates = sorted({d for d in actual if actual.count(d) > 1})
        raise ValueError(f"equity index has duplicate dates: {duplicates[:3]}...")

    if actual != sorted(actual):
        raise ValueError("equity index is not strictly increasing")

    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        raise ValueError(
            "equity index does not match the evaluation calendar "
            f"(expected {len(expected)} days, got {len(actual)}): "
            f"missing={missing[:3]}{'...' if len(missing) > 3 else ''} "
            f"extra={extra[:3]}{'...' if len(extra) > 3 else ''}"
        )
