"""Formal evaluation calendar and equity index validation — Phase 0 Task 5.

Design §24/§32.1: the formal evaluation interval is the set of actual market
trading days between ``start_date`` and ``end_date`` (inclusive). All strategies
share the same evaluation dates; the interval must not begin at each strategy's
first fill. Metrics anchor on ``initial_nav=1.0`` before the first period, so the
first day's return is measured against 1.0 rather than dropped.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


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
