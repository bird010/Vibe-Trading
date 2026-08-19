"""Causal Average Daily Volume (ADV) calculator.

Computes rolling ADV using only data visible at the decision point,
per design §27.1. The amount column is in 千元 (thousands CNY).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class ADVResult:
    """Result of a causal ADV computation."""

    adv_value: float  # in CNY
    observations: int
    is_valid: bool
    as_of_date: str
    lookback: int
    has_data_quality_issue: bool = False

    @property
    def capacity(self) -> float:
        """Raw ADV value for capacity calculation (before participation rate)."""
        return self.adv_value if self.is_valid else 0.0


def compute_causal_adv(
    market: pd.DataFrame,
    code: str,
    *,
    as_of_date: str,
    lookback: int = 20,
    min_observations: int = 10,
    trade_dates: list[str] | None = None,
) -> ADVResult:
    """Compute causal ADV for a stock as of a given date.

    Rules (§27.1):
    - Window = last `lookback` trade dates up to and including as_of_date
    - amount column is in 千元; multiply by 1000 for CNY
    - Suspended days (vol=0, amount=0) count as zero in the mean
    - NaN amount (unconfirmed data quality issue) excluded from observations
    - observations < min_observations -> is_valid=False
    """
    stock = market[market["ts_code"].astype(str) == str(code)].copy()
    if stock.empty:
        return ADVResult(
            adv_value=0.0,
            observations=0,
            is_valid=False,
            as_of_date=as_of_date,
            lookback=lookback,
        )

    stock["trade_date"] = stock["trade_date"].astype(str)
    stock = stock[stock["trade_date"] <= as_of_date]
    stock = stock.sort_values("trade_date").drop_duplicates("trade_date", keep="last")

    # Take last `lookback` dates
    if trade_dates is not None:
        window_dates = sorted(str(date) for date in trade_dates if str(date) <= as_of_date)[-lookback:]
        stock = stock[stock["trade_date"].isin(window_dates)]
    else:
        stock = stock.tail(lookback)
        window_dates = stock["trade_date"].tolist()

    if stock.empty:
        return ADVResult(
            adv_value=0.0,
            observations=0,
            is_valid=False,
            as_of_date=as_of_date,
            lookback=lookback,
        )

    # Convert amount from 千元 to CNY
    missing_dates = set(window_dates) - set(stock["trade_date"])
    amount_cny = pd.to_numeric(stock["amount"], errors="coerce") * 1000.0

    # Identify data quality issues: NaN amount (not confirmed suspension)
    nan_mask = amount_cny.isna()
    volume = pd.to_numeric(stock["vol"], errors="coerce")
    inconsistent_zero = (amount_cny == 0) & (volume != 0)
    has_quality_issue = bool(nan_mask.any() or missing_dates or inconsistent_zero.any())

    # Valid observations exclude NaN
    valid_amounts = amount_cny.dropna()
    observations = len(valid_amounts)

    if observations == 0:
        return ADVResult(
            adv_value=0.0,
            observations=0,
            is_valid=False,
            as_of_date=as_of_date,
            lookback=lookback,
            has_data_quality_issue=True,
        )

    # Mean includes zeros (suspended days) but excludes NaN
    adv_value = float(valid_amounts.mean())

    return ADVResult(
        adv_value=adv_value,
        observations=observations,
        is_valid=observations >= min_observations and not has_quality_issue,
        as_of_date=as_of_date,
        lookback=lookback,
        has_data_quality_issue=has_quality_issue,
    )
