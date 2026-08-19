"""ADV20 capacity and slippage — §13.3.

Causal ADV: excludes execution day. Slippage formula:
slippage_bps = min(max_bps, base_bps + 200 * participation_rate).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ADVResult:
    """Result of causal ADV computation."""

    adv_value: float  # CNY
    observations: int
    is_valid: bool
    as_of_date: str
    lookback: int


def compute_adv20(
    market: pd.DataFrame,
    code: str,
    as_of_date: str,
    lookback: int = 20,
    min_obs: int = 10,
    amount_multiplier: float = 1000.0,
) -> ADVResult:
    """§13.3 — Compute causal ADV (average daily amount).

    Uses data strictly BEFORE as_of_date (excludes execution day).

    Args:
        market: DataFrame with [ts_code, trade_date, amount].
        code: ETF code.
        as_of_date: Execution date (excluded from window).
        lookback: Max days to look back.
        min_obs: Minimum valid observations.
        amount_multiplier: Convert stored amount unit to CNY.
            Tushare fund_daily stores amount in 千元 (thousands),
            so default 1000.0 converts to 元. Set to 1.0 if already in CNY.

    Returns:
        ADVResult with adv_value in CNY.
    """
    if market.empty or "ts_code" not in market.columns:
        return ADVResult(adv_value=0.0, observations=0, is_valid=False,
                         as_of_date=as_of_date, lookback=lookback)

    stock = market[market["ts_code"].astype(str) == str(code)].copy()
    if stock.empty:
        return ADVResult(adv_value=0.0, observations=0, is_valid=False,
                         as_of_date=as_of_date, lookback=lookback)

    # Strictly before execution day
    stock = stock[stock["trade_date"].astype(str) < as_of_date]
    stock = stock.sort_values("trade_date")

    # Take last `lookback` days
    window = stock.tail(lookback)

    # Filter valid amounts (non-null, non-negative) and convert to CNY
    amounts = window["amount"].dropna()
    amounts = amounts[amounts >= 0] * amount_multiplier
    obs = len(amounts)

    if obs < min_obs:
        return ADVResult(adv_value=0.0, observations=obs, is_valid=False,
                         as_of_date=as_of_date, lookback=lookback)

    adv = float(amounts.mean())
    return ADVResult(adv_value=adv, observations=obs, is_valid=True,
                     as_of_date=as_of_date, lookback=lookback)


def apply_capacity_and_slippage(
    requested_shares: int,
    price: float,
    adv_value: float,
    max_participation: float,
    lot_size: int,
    base_slippage_bps: float,
    max_slippage_bps: float,
) -> tuple[int, float, float]:
    """§13.3 — Apply ADV capacity cap and compute slippage.

    Args:
        requested_shares: Desired trade size.
        price: Execution price.
        adv_value: ADV in CNY (0 = invalid).
        max_participation: Max fraction of ADV (e.g. 0.05).
        lot_size: Minimum trade unit.
        base_slippage_bps: Base slippage in bps.
        max_slippage_bps: Cap on slippage.

    Returns:
        (filled_shares, participation_rate, slippage_bps)
    """
    if adv_value <= 0 or price <= 0 or requested_shares <= 0:
        return (0, 0.0, 0.0)

    # Capacity: max notional = participation * ADV
    max_notional = max_participation * adv_value
    max_shares = int(max_notional / price)
    # Round down to lot
    max_shares = (max_shares // lot_size) * lot_size

    filled = min(requested_shares, max_shares)
    if filled <= 0:
        return (0, 0.0, 0.0)

    # Participation rate of actual fill
    fill_notional = filled * price
    participation = fill_notional / adv_value

    # Slippage formula
    slippage_bps = min(max_slippage_bps, base_slippage_bps + 200.0 * participation)

    return (filled, participation, slippage_bps)


class ADVIndex:
    """Pre-computed causal ADV index for fast lookup.

    Semantics identical to compute_adv20: for a given (code, trade_date),
    returns the mean amount over the last `lookback` trading days STRICTLY
    BEFORE trade_date, requiring at least `min_obs` valid observations.

    Implementation: per-code sorted date array + rolling mean/count (no shift).
    Query uses searchsorted to find the last row with date < trade_date.
    """

    def __init__(
        self,
        adv_grouped: dict[str, pd.DataFrame],
        lookback: int,
        min_obs: int,
        amount_multiplier: float = 1000.0,
    ):
        self._lookback = lookback
        self._min_obs = min_obs
        self._amount_multiplier = amount_multiplier
        # Per-code compact arrays
        self._data: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        self._build(adv_grouped)

    def _build(self, adv_grouped: dict[str, pd.DataFrame]) -> None:
        for code, df in adv_grouped.items():
            if df.empty or "trade_date" not in df.columns or "amount" not in df.columns:
                continue
            sorted_df = df.sort_values("trade_date")
            dates = sorted_df["trade_date"].astype(str).values
            amounts = pd.to_numeric(sorted_df["amount"], errors="coerce").copy()
            # Invalid amounts (NaN, negative) -> NaN
            amounts[amounts < 0] = np.nan
            # Rolling mean and count (no shift)
            rolling_mean = amounts.rolling(self._lookback, min_periods=1).mean()
            rolling_count = amounts.rolling(self._lookback, min_periods=1).count()
            self._data[code] = (
                dates,
                (rolling_mean * self._amount_multiplier).values,
                rolling_count.values.astype(np.int64),
            )

    def get(self, code: str, trade_date: str) -> ADVResult:
        """Lookup causal ADV for (code, trade_date).

        Returns ADVResult with adv_value in CNY, strictly excluding trade_date.
        """
        entry = self._data.get(code)
        if entry is None:
            return ADVResult(
                adv_value=0.0, observations=0, is_valid=False,
                as_of_date=trade_date, lookback=self._lookback,
            )
        dates, means, counts = entry
        # searchsorted 'left': first index where dates[i] >= trade_date
        i = int(np.searchsorted(dates, trade_date, side="left")) - 1
        if i < 0:
            return ADVResult(
                adv_value=0.0, observations=0, is_valid=False,
                as_of_date=trade_date, lookback=self._lookback,
            )
        obs = int(counts[i])
        if obs < self._min_obs:
            return ADVResult(
                adv_value=0.0, observations=obs, is_valid=False,
                as_of_date=trade_date, lookback=self._lookback,
            )
        return ADVResult(
            adv_value=float(means[i]), observations=obs, is_valid=True,
            as_of_date=trade_date, lookback=self._lookback,
        )
