"""Baseline signal helpers — Phase 2 Task 4 (design §32.2).

Pure helpers shared by the correlation_all_members baseline session: the
weekly rebalance schedule (ISO week-endings, identical grouping to
``compute_weekly_returns``) and per-signal-date market eligibility
(positive close AND positive adj_factor on the signal date, §8.2).
"""

from __future__ import annotations

from typing import Sequence

import pandas as pd

from backtest.fund_rotation.evaluation import iso_week_endings
from backtest.fund_rotation.universe import ExclusionReason, ExclusionRecord

__all__ = [
    "ensure_instrument_pool",
    "iso_week_endings",
    "market_eligible_codes",
    "signal_date_eligible",
]


# iso_week_endings lives in the common evaluation module (shared with the
# Runner's warmup boundary); re-exported here for the baseline session.


def ensure_instrument_pool(view) -> "pd.DataFrame":
    """Build the strategy's instrument pool from the causal view.

    Mirrors the legacy pool pre-filter: static universe from
    ``eligible_universe`` minus codes whose adj records do not cover every
    daily record seen so far (INSUFFICIENT_ADJ_COVERAGE, read causally).
    """
    instruments = view.eligible_universe()
    dim = pd.DataFrame([
        {"ts_code": i.ts_code, "name": i.name, "list_date": i.list_date}
        for i in instruments
    ])
    bars = view.daily_bars(["close"])
    adj = view.fund_adjustments()
    if not dim.empty and not bars.empty and not adj.empty:
        daily_keys = bars[["ts_code", "trade_date"]].astype(str).drop_duplicates()
        adj_keys = adj[["ts_code", "trade_date"]].astype(str).drop_duplicates()
        coverage = daily_keys.merge(
            adj_keys, on=["ts_code", "trade_date"], how="left", indicator=True,
        )
        incomplete = set(
            coverage.loc[coverage["_merge"] == "left_only", "ts_code"].unique()
        )
        if incomplete:
            dim = dim[~dim["ts_code"].astype(str).isin(incomplete)]
    return dim.reset_index(drop=True)


def market_eligible_codes(view, codes: Sequence[str], signal_date: str) -> set[str]:
    """§8.2 — codes with a positive close AND positive adj_factor exactly on
    the signal date, read through the causal data view."""
    kept, _rejected = signal_date_eligible(view, codes, signal_date)
    return set(kept)


def signal_date_eligible(
    view, codes: Sequence[str], signal_date: str,
) -> tuple[list[str], list[ExclusionRecord]]:
    """§8.2 — ordered market eligibility with exclusion records.

    Mirrors the legacy pipeline semantics: a code missing a positive close on
    the signal date is NO_VALID_CLOSE; otherwise a missing/non-positive
    adj_factor is INSUFFICIENT_ADJ_COVERAGE.
    """
    bars = view.daily_bars(["close"])
    sig_bars = bars[bars["trade_date"].astype(str) == signal_date]
    close_by_code = {
        str(code): pd.to_numeric(close, errors="coerce")
        for code, close in zip(sig_bars["ts_code"], sig_bars["close"])
    }
    adj = view.fund_adjustments()
    sig_adj = adj[adj["trade_date"].astype(str) == signal_date]
    adj_by_code = {
        str(code): pd.to_numeric(factor, errors="coerce")
        for code, factor in zip(sig_adj["ts_code"], sig_adj["adj_factor"])
    }
    kept: list[str] = []
    rejected: list[ExclusionRecord] = []
    for code in codes:
        close = close_by_code.get(str(code))
        factor = adj_by_code.get(str(code))
        if close is None or pd.isna(close) or float(close) <= 0:
            rejected.append(ExclusionRecord(
                ts_code=str(code), reason=ExclusionReason.NO_VALID_CLOSE,
                details="missing or non-positive close on signal date",
                signal_date=signal_date,
            ))
        elif factor is None or pd.isna(factor) or float(factor) <= 0:
            rejected.append(ExclusionRecord(
                ts_code=str(code), reason=ExclusionReason.INSUFFICIENT_ADJ_COVERAGE,
                details="missing or non-positive adj_factor on signal date",
                signal_date=signal_date,
            ))
        else:
            kept.append(str(code))
    return kept, rejected
