"""Signal eligibility gate: validates and freezes the evaluable stock pool.

Implements design §8.2. Checks listing age, ST status, suspension,
adjustment coverage, and outputs per-stock rejection reasons.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class EligibilityResult:
    """Result of eligibility gate check for one evaluation date."""

    eligible_codes: list[str]
    rejected: dict[str, str] = field(default_factory=dict)  # code -> reason
    coverage_stats: dict[str, Any] = field(default_factory=dict)
    data_failure: bool = False


# Rejection reason codes per §21
REASON_NOT_LISTED = "NOT_LISTED"
REASON_ST = "ST"
REASON_SUSPENDED = "SUSPENDED"
REASON_NO_MARKET_DATA = "NO_MARKET_DATA"
REASON_ADJ_INCOMPLETE = "ADJ_INCOMPLETE"
REASON_EXCHANGE_MISMATCH = "EXCHANGE_MISMATCH"
REASON_NOT_IN_UNIVERSE = "NOT_IN_UNIVERSE"


@dataclass(frozen=True)
class SignalEligibilityGate:
    """Validates stock eligibility at signal time per §8.2.

    Rules:
    - Must be listed for at least min_listed_trade_days
    - Must not be ST (if exclude_st=True)
    - Must have market data on evaluation date (not suspended)
    - Must have sufficient adjustment factor coverage
    - Must be on allowed exchanges

    Data failures are NOT converted to legitimate empty positions.
    """

    min_listed_trade_days: int = 60
    exclude_st: bool = True
    allowed_exchanges: tuple[str, ...] = ("SSE", "SZSE")
    min_adj_coverage: float = 0.98
    min_market_coverage: float = 0.98

    def check(
        self,
        *,
        eval_date: str,
        universe: pd.DataFrame,
        candidates: list[str] | None = None,
        prices: pd.DataFrame | None = None,
        adjustment_factors: pd.DataFrame | None = None,
        market_calendar: list[str] | None = None,
        name_history: pd.DataFrame | None = None,
    ) -> EligibilityResult:
        """Run eligibility checks on the universe for one evaluation date.

        Args:
            eval_date: The signal evaluation date (YYYYMMDD)
            universe: DataFrame with columns [ts_code, list_date, exchange, ...]
            prices: Optional market data to check suspension
            name_history: Optional name history to check ST status

        Returns:
            EligibilityResult with eligible codes and rejection reasons
        """
        candidate_codes = sorted(set(candidates or universe.get("ts_code", pd.Series(dtype=str)).astype(str).tolist()))
        if universe.empty and not candidate_codes:
            return EligibilityResult(
                eligible_codes=[],
                data_failure=True,
                coverage_stats={"total": 0, "eligible": 0, "rejected": 0},
            )

        rejected: dict[str, str] = {}
        eligible: list[str] = []
        unverifiable_raw: set[str] = set()
        unverifiable_st: set[str] = set()
        universe_codes = set(universe.get("ts_code", pd.Series(dtype=str)).astype(str))
        for code in candidate_codes:
            if code not in universe_codes:
                rejected[code] = REASON_NOT_IN_UNIVERSE

        for _, row in universe.iterrows():
            code = str(row.get("ts_code", ""))
            if not code:
                continue

            # Check exchange
            exchange = str(row.get("exchange", ""))
            if self.allowed_exchanges and exchange not in self.allowed_exchanges:
                rejected[code] = REASON_EXCHANGE_MISMATCH
                continue

            # Check listing and delisting status using market-calendar dates.
            list_date = str(row.get("list_date", "")).strip()
            delist_date = str(row.get("delist_date", "")).strip()
            list_status = str(row.get("list_status", "")).strip().upper()
            valid_delist_date = delist_date.isdigit() and len(delist_date) == 8
            if (
                not list_date
                or list_date > eval_date
                or list_status not in {"L", "D"}
                or (list_status == "D" and not valid_delist_date)
                or (valid_delist_date and eval_date >= delist_date)
            ):
                rejected[code] = REASON_NOT_LISTED
                continue
            dates = market_calendar or []
            if not dates or min(dates) > list_date:
                rejected[code] = REASON_NOT_LISTED
                continue
            days_listed = sum(list_date <= date <= eval_date for date in dates)
            if days_listed < self.min_listed_trade_days:
                rejected[code] = REASON_NOT_LISTED
                continue

            # Check ST status
            if self.exclude_st:
                if name_history is None or name_history.empty:
                    rejected[code] = REASON_ST
                    unverifiable_st.add(code)
                    continue
                stock_history = name_history[name_history["ts_code"].astype(str) == code]
                if stock_history.empty or not (stock_history["effective_from"].astype(str) <= eval_date).any():
                    rejected[code] = REASON_ST
                    unverifiable_st.add(code)
                    continue
                if self._is_st(code, eval_date, name_history):
                    rejected[code] = REASON_ST
                    continue

            # Missing signal-day market data cannot be distinguished from a
            # suspension without the raw signal-day rows, so fail closed.
            if prices is None or prices.empty:
                rejected[code] = REASON_NO_MARKET_DATA
                unverifiable_raw.add(code)
                continue
            else:
                stock_prices = prices[
                    (prices["ts_code"].astype(str) == code)
                    & (prices["trade_date"].astype(str) == eval_date)
                ]
                if stock_prices.empty:
                    rejected[code] = REASON_NO_MARKET_DATA
                    unverifiable_raw.add(code)
                    continue
                vol = stock_prices.iloc[0].get("vol", None)
                if vol is None or pd.isna(vol):
                    rejected[code] = REASON_NO_MARKET_DATA
                    unverifiable_raw.add(code)
                    continue
                if float(vol) <= 0:
                    rejected[code] = REASON_SUSPENDED
                    continue

            # Adjustment coverage is a data-quality gate, not a tradability
            # preference.  One missing candidate at the 0.98 threshold fails
            # the date instead of silently changing the cross-section.
            if adjustment_factors is None or adjustment_factors.empty:
                rejected[code] = REASON_ADJ_INCOMPLETE
                continue
            factors = adjustment_factors[
                (adjustment_factors["ts_code"].astype(str) == code)
                & (adjustment_factors["trade_date"].astype(str) == eval_date)
            ]
            if factors.empty or pd.isna(pd.to_numeric(factors.iloc[0].get("adj_factor"), errors="coerce")):
                rejected[code] = REASON_ADJ_INCOMPLETE
                continue

            eligible.append(code)

        total = len(candidate_codes)
        adj_rejected = sum(reason == REASON_ADJ_INCOMPLETE for reason in rejected.values())
        coverage = (total - adj_rejected) / total if total > 0 else 0.0
        unverifiable_count = len(unverifiable_raw) + len(unverifiable_st)
        market_data_coverage = (total - unverifiable_count) / total if total > 0 else 0.0
        return EligibilityResult(
            eligible_codes=sorted(eligible),
            rejected=rejected,
            coverage_stats={
                "total": total,
                "eligible": len(eligible),
                "rejected": len(rejected),
                "rejection_rate": len(rejected) / total if total > 0 else 0.0,
                "adjustment_coverage": coverage,
                "market_data_coverage": market_data_coverage,
            },
            data_failure=(
                (adj_rejected > 0 and coverage < self.min_adj_coverage)
                or bool(set(rejected) - universe_codes)
                or market_data_coverage < self.min_market_coverage
                or not bool(market_calendar)
            ),
        )

    @staticmethod
    def _days_between(date_a: str, date_b: str) -> int:
        """Approximate trading days between two YYYYMMDD dates."""
        from datetime import datetime
        try:
            a = datetime.strptime(date_a[:8], "%Y%m%d")
            b = datetime.strptime(date_b[:8], "%Y%m%d")
            calendar_days = (b - a).days
            # Approximate trading days as 5/7 of calendar days
            return max(int(calendar_days * 5 / 7), 0)
        except (ValueError, TypeError):
            return 9999

    @staticmethod
    def _is_st(code: str, eval_date: str, name_history: pd.DataFrame) -> bool:
        """Check if stock is ST at eval_date based on name history."""
        if name_history.empty:
            return False
        stock_names = name_history[name_history["ts_code"].astype(str) == code]
        if stock_names.empty:
            return False
        # Get most recent name before eval_date
        stock_names = stock_names.copy()
        if "effective_from" in stock_names.columns:
            stock_names = stock_names[stock_names["effective_from"].astype(str) <= eval_date]
        if stock_names.empty:
            return False
        latest_name = str(stock_names.iloc[-1].get("security_name", ""))
        return "ST" in latest_name.upper()
