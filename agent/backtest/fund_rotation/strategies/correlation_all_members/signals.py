"""Shared PIT signal helpers for correlation fund-rotation strategies.

The instrument pool is resolved for each signal date. It is never cached as a
run-wide eligible set: listing status, current-window market data and adjustment
coverage can change during a backtest.
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


def ensure_instrument_pool(
    view,
    *,
    lookback_trade_days: int | None = None,
) -> "pd.DataFrame":
    """Resolve the historically visible pool with window-scoped adj coverage.

    ``view.eligible_universe()`` already enforces ``list_date <= signal_date``.
    Adjustment completeness is checked only for the declared strategy window,
    not against unrelated older history. A fund can therefore enter after
    listing/warmup and can recover after a temporary data gap once the active
    window is complete again.
    """
    instruments = view.eligible_universe()
    dim = pd.DataFrame(
        [
            {
                "ts_code": instrument.ts_code,
                "name": instrument.name,
                "list_date": instrument.list_date,
            }
            for instrument in instruments
        ]
    )
    if dim.empty:
        return pd.DataFrame(columns=["ts_code", "name", "list_date"])

    bars = view.daily_bars(["close"], lookback=lookback_trade_days)
    adj = view.fund_adjustments(lookback=lookback_trade_days)
    if bars.empty:
        return dim.iloc[0:0].reset_index(drop=True)

    daily_keys = (
        bars[["ts_code", "trade_date"]]
        .astype(str)
        .drop_duplicates()
    )
    if adj.empty:
        return dim.iloc[0:0].reset_index(drop=True)
    valid_adj = adj.copy()
    valid_adj["adj_factor"] = pd.to_numeric(
        valid_adj["adj_factor"], errors="coerce"
    )
    valid_adj = valid_adj[
        valid_adj["adj_factor"].notna()
        & (valid_adj["adj_factor"] > 0)
    ]
    adj_keys = (
        valid_adj[["ts_code", "trade_date"]]
        .astype(str)
        .drop_duplicates()
    )
    coverage = daily_keys.merge(
        adj_keys,
        on=["ts_code", "trade_date"],
        how="left",
        indicator=True,
    )
    incomplete = set(
        coverage.loc[
            coverage["_merge"] == "left_only",
            "ts_code",
        ].astype(str)
    )
    available = set(daily_keys["ts_code"].astype(str)) - incomplete
    return (
        dim[dim["ts_code"].astype(str).isin(available)]
        .reset_index(drop=True)
    )


def market_eligible_codes(
    view,
    codes: Sequence[str],
    signal_date: str,
) -> set[str]:
    kept, _rejected = signal_date_eligible(view, codes, signal_date)
    return set(kept)


def signal_date_eligible(
    view,
    codes: Sequence[str],
    signal_date: str,
) -> tuple[list[str], list[ExclusionRecord]]:
    """Return ordered codes with positive close and adj factor on signal day."""
    bars = view.daily_bars(["close"], lookback=None)
    sig_bars = bars[bars["trade_date"].astype(str) == signal_date]
    close_by_code = {
        str(code): pd.to_numeric(close, errors="coerce")
        for code, close in zip(sig_bars["ts_code"], sig_bars["close"])
    }
    adj = view.fund_adjustments(lookback=None)
    sig_adj = adj[adj["trade_date"].astype(str) == signal_date]
    adj_by_code = {
        str(code): pd.to_numeric(factor, errors="coerce")
        for code, factor in zip(sig_adj["ts_code"], sig_adj["adj_factor"])
    }
    kept: list[str] = []
    rejected: list[ExclusionRecord] = []
    for raw_code in codes:
        code = str(raw_code)
        close = close_by_code.get(code)
        factor = adj_by_code.get(code)
        if close is None or pd.isna(close) or float(close) <= 0:
            rejected.append(
                ExclusionRecord(
                    ts_code=code,
                    reason=ExclusionReason.NO_VALID_CLOSE,
                    details="missing or non-positive close on signal date",
                    signal_date=signal_date,
                )
            )
        elif factor is None or pd.isna(factor) or float(factor) <= 0:
            rejected.append(
                ExclusionRecord(
                    ts_code=code,
                    reason=ExclusionReason.INSUFFICIENT_ADJ_COVERAGE,
                    details=(
                        "missing or non-positive adj_factor on signal date"
                    ),
                    signal_date=signal_date,
                )
            )
        else:
            kept.append(code)
    return kept, rejected
