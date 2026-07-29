"""Valuation policy: horizon mark, stale price handling, terminal haircut.

Implements design §8.5 and §27.10.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite

import pandas as pd

from backtest.stockpred.execution.costs import DEFAULT_COST_POLICY, CostPolicy


@dataclass(frozen=True)
class ValuationResult:
    """Result of horizon mark valuation."""

    price: float
    stale_days: int
    quality_failure: bool
    valuation_date: str = ""


@dataclass(frozen=True)
class TerminalValuation:
    """Terminal valuation for UNLIQUIDATED positions per §27.10."""

    last_valid_mark_value: float
    estimated_sell_fees: float
    liquidity_haircut: float
    haircut_rate: float
    terminal_value: float
    stress_scenarios: list[dict[str, float]] = field(default_factory=list)


def compute_liquidity_haircut_rate(
    *,
    stale_days: int,
    limit_band_rate: float,
    base_haircut_rate: float = 0.10,
    stale_penalty_per_day: float = 0.005,
    max_stale_penalty: float = 0.10,
    max_total_haircut: float = 0.30,
) -> float:
    """Compute liquidity haircut rate per §27.10.

    liquidity_haircut_rate = min(
        max(base_haircut_rate, limit_band_rate) + min(stale_days * 0.5%, 10%),
        30%
    )
    """
    stale_penalty = min(stale_days * stale_penalty_per_day, max_stale_penalty)
    rate = max(base_haircut_rate, limit_band_rate) + stale_penalty
    return min(rate, max_total_haircut)


@dataclass(frozen=True)
class ValuationPolicy:
    """Valuation policy per §8.5."""

    stale_price_limit_days: int = 5
    cost_policy: CostPolicy = DEFAULT_COST_POLICY

    def horizon_mark(
        self, code: str, target_exit_date: str, market: pd.DataFrame
    ) -> ValuationResult:
        """Value a position at the target exit date using adj_open.

        If no data on target date, use last valid price and report stale_days.
        If stale beyond limit, flag quality_failure.
        """
        stock = market[market["ts_code"].astype(str) == str(code)].copy()
        if stock.empty:
            return ValuationResult(price=0.0, stale_days=999, quality_failure=True)

        stock["trade_date"] = stock["trade_date"].astype(str)
        stock = stock.sort_values("trade_date")

        # Try exact date first
        exact = stock[stock["trade_date"] == target_exit_date]
        if not exact.empty:
            price_col = "adj_open" if "adj_open" in exact.columns else "open"
            price = float(exact.iloc[0][price_col])
            return ValuationResult(
                price=price,
                stale_days=0,
                quality_failure=not isfinite(price) or price <= 0,
                valuation_date=target_exit_date,
            )

        # Fall back to last valid date before target
        before = stock[stock["trade_date"] < target_exit_date]
        if before.empty:
            return ValuationResult(price=0.0, stale_days=999, quality_failure=True)

        price_col = "adj_open" if "adj_open" in before.columns else "open"
        before = before[pd.to_numeric(before[price_col], errors="coerce").map(
            lambda value: isfinite(value) and value > 0
        )]
        if before.empty:
            return ValuationResult(price=0.0, stale_days=999, quality_failure=True)

        last_row = before.iloc[-1]
        price = float(last_row[price_col])
        last_date = str(last_row["trade_date"])

        # Compute stale days (calendar days approximation)
        stale_days = self._date_diff_days(last_date, target_exit_date)
        quality_failure = stale_days > self.stale_price_limit_days

        return ValuationResult(
            price=price,
            stale_days=stale_days,
            quality_failure=quality_failure,
            valuation_date=last_date,
        )

    def terminal_value(
        self,
        *,
        quantity: int,
        last_valid_price: float,
        stale_days: int,
        limit_band_rate: float,
        adv: float,
    ) -> TerminalValuation:
        """Compute terminal valuation for unliquidated position per §27.10.

        terminal_value = last_valid_mark_value - estimated_sell_fees - liquidity_haircut
        """
        mark_value = quantity * last_valid_price

        # Estimated sell fees
        fees = self.cost_policy.estimate_sell_fees(quantity, last_valid_price, adv)
        sell_fees = fees.total

        # Liquidity haircut
        haircut_rate = compute_liquidity_haircut_rate(
            stale_days=stale_days, limit_band_rate=limit_band_rate
        )
        liquidity_haircut = mark_value * haircut_rate

        terminal = mark_value - sell_fees - liquidity_haircut

        # Stress scenarios
        stress = []
        for rate in [0.05, 0.10, 0.20, 0.30]:
            stress_value = mark_value - sell_fees - mark_value * rate
            stress.append({"rate": rate, "terminal_value": stress_value})

        return TerminalValuation(
            last_valid_mark_value=mark_value,
            estimated_sell_fees=sell_fees,
            liquidity_haircut=liquidity_haircut,
            haircut_rate=haircut_rate,
            terminal_value=max(terminal, 0.0),
            stress_scenarios=stress,
        )

    @staticmethod
    def _date_diff_days(date_a: str, date_b: str) -> int:
        """Approximate calendar day difference between YYYYMMDD dates."""
        try:
            from datetime import datetime

            a = datetime.strptime(date_a[:8], "%Y%m%d")
            b = datetime.strptime(date_b[:8], "%Y%m%d")
            return abs((b - a).days)
        except (ValueError, TypeError):
            return 999
