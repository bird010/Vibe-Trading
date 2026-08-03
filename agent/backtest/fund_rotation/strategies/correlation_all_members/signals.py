"""Baseline signal helpers — Phase 2 Task 4 (design §32.2).

Pure helpers shared by the correlation_all_members baseline session: the
weekly rebalance schedule (ISO week-endings, identical grouping to
``compute_weekly_returns``) and per-signal-date market eligibility
(positive close AND positive adj_factor on the signal date, §8.2).
"""

from __future__ import annotations

from typing import Iterable, Sequence

import pandas as pd


def iso_week_endings(calendar: Iterable[str]) -> list[str]:
    """Last actual trading day of each ISO week, ascending.

    Mirrors the week grouping of ``compute_weekly_returns`` (ISO year-week,
    last trading day kept) so the decision schedule matches the legacy
    week-ending index exactly.
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


def market_eligible_codes(view, codes: Sequence[str], signal_date: str) -> set[str]:
    """§8.2 — codes with a positive close AND positive adj_factor exactly on
    the signal date, read through the causal data view."""
    bars = view.daily_bars(["close"])
    sig_bars = bars[bars["trade_date"].astype(str) == signal_date]
    close_ok = {
        str(code)
        for code, close in zip(sig_bars["ts_code"], sig_bars["close"])
        if pd.to_numeric(close, errors="coerce") is not None
        and float(pd.to_numeric(close, errors="coerce")) > 0
    }
    adj = view.fund_adjustments()
    sig_adj = adj[adj["trade_date"].astype(str) == signal_date]
    adj_ok = {
        str(code)
        for code, factor in zip(sig_adj["ts_code"], sig_adj["adj_factor"])
        if float(pd.to_numeric(factor, errors="coerce")) > 0
    }
    return set(codes) & close_ok & adj_ok
