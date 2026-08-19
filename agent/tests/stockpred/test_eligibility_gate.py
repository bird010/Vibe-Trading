"""Tests for SignalEligibilityGate coverage-threshold semantics.

Desired behaviour (fix for eligibility_data_failure killing entire cohorts):
- Individual stocks with missing data are still rejected (fail-closed per stock).
- The entire evaluation date is only marked data_failure when market-data
  coverage drops below a threshold (default 98%).
- Normal rejections (NOT_LISTED, SUSPENDED) never contribute to data_failure.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.stockpred.cohort.eligibility import (
    REASON_NO_MARKET_DATA,
    REASON_NOT_LISTED,
    REASON_ST,
    REASON_SUSPENDED,
    SignalEligibilityGate,
)


def _make_universe(n: int, prefix: str = "00") -> pd.DataFrame:
    """Create a universe of n stocks listed well before eval_date."""
    rows = []
    for i in range(n):
        rows.append({
            "ts_code": f"{prefix}{i:04d}.SZ",
            "list_date": "20230101",
            "delist_date": "",
            "list_status": "L",
            "exchange": "SZSE",
        })
    return pd.DataFrame(rows)


def _make_prices(codes: list[str], eval_date: str) -> pd.DataFrame:
    """Create signal-day prices for given codes (vol > 0)."""
    return pd.DataFrame([
        {"ts_code": c, "trade_date": eval_date, "vol": 100000.0}
        for c in codes
    ])


def _make_adj_factors(codes: list[str], eval_date: str) -> pd.DataFrame:
    return pd.DataFrame([
        {"ts_code": c, "trade_date": eval_date, "adj_factor": 1.0}
        for c in codes
    ])


def _make_name_history(codes: list[str], eval_date: str) -> pd.DataFrame:
    """Name history proving non-ST status for given codes."""
    return pd.DataFrame([
        {"ts_code": c, "security_name": "Normal Corp", "effective_from": "20230101"}
        for c in codes
    ])


EVAL_DATE = "20240429"
# Calendar must start before list_date to pass the min(dates) > list_date check.
CALENDAR = [f"2023{m:02d}{d:02d}" for m in range(1, 13) for d in range(1, 29)] + \
           [f"2024{m:02d}{d:02d}" for m in range(1, 5) for d in range(1, 29)]


class TestCoverageThresholdGate:
    """data_failure should use coverage ratio, not any-single-stock logic."""

    def test_small_missing_data_does_not_fail_date(self):
        """21 out of 100 stocks missing market data (79% coverage < 98%) → data_failure=True.

        But 2 out of 100 missing (98% coverage) → data_failure=False.
        """
        n = 100
        universe = _make_universe(n)
        all_codes = universe["ts_code"].tolist()

        # 2 stocks have no market data (coverage = 98/100 = 98%)
        codes_with_data = all_codes[:98]
        prices = _make_prices(codes_with_data, EVAL_DATE)
        adj = _make_adj_factors(all_codes, EVAL_DATE)
        name_hist = _make_name_history(all_codes, EVAL_DATE)

        gate = SignalEligibilityGate(min_listed_trade_days=10)
        result = gate.check(
            eval_date=EVAL_DATE,
            universe=universe,
            candidates=all_codes,
            prices=prices,
            adjustment_factors=adj,
            market_calendar=CALENDAR,
            name_history=name_hist,
        )

        # The 2 missing stocks are individually rejected
        assert len(result.rejected) == 2
        assert all(r == REASON_NO_MARKET_DATA for r in result.rejected.values())
        # But the date itself is NOT a data failure (coverage >= 98%)
        assert result.data_failure is False
        assert len(result.eligible_codes) == 98

    def test_large_missing_data_fails_date(self):
        """When coverage drops below threshold, data_failure=True."""
        n = 100
        universe = _make_universe(n)
        all_codes = universe["ts_code"].tolist()

        # 10 stocks have no market data (coverage = 90/100 = 90% < 98%)
        codes_with_data = all_codes[:90]
        prices = _make_prices(codes_with_data, EVAL_DATE)
        adj = _make_adj_factors(all_codes, EVAL_DATE)
        name_hist = _make_name_history(all_codes, EVAL_DATE)

        gate = SignalEligibilityGate(min_listed_trade_days=10)
        result = gate.check(
            eval_date=EVAL_DATE,
            universe=universe,
            candidates=all_codes,
            prices=prices,
            adjustment_factors=adj,
            market_calendar=CALENDAR,
            name_history=name_hist,
        )

        assert len(result.rejected) == 10
        assert result.data_failure is True

    def test_normal_rejections_do_not_cause_data_failure(self):
        """NOT_LISTED and SUSPENDED are normal exclusions, not data failures."""
        n = 50
        universe = _make_universe(n)
        all_codes = universe["ts_code"].tolist()

        # All have prices and name history
        prices = _make_prices(all_codes, EVAL_DATE)
        adj = _make_adj_factors(all_codes, EVAL_DATE)
        name_hist = _make_name_history(all_codes, EVAL_DATE)

        # Make 3 stocks suspended (vol=0) — normal exclusion
        prices.loc[prices["ts_code"].isin(all_codes[:3]), "vol"] = 0.0

        gate = SignalEligibilityGate(min_listed_trade_days=10)
        result = gate.check(
            eval_date=EVAL_DATE,
            universe=universe,
            candidates=all_codes,
            prices=prices,
            adjustment_factors=adj,
            market_calendar=CALENDAR,
            name_history=name_hist,
        )

        assert len(result.rejected) == 3
        assert all(r == REASON_SUSPENDED for r in result.rejected.values())
        assert result.data_failure is False
        assert len(result.eligible_codes) == 47

    def test_unverifiable_st_counts_toward_coverage(self):
        """Stocks with unverifiable ST status reduce coverage but don't
        auto-fail the date unless threshold is breached."""
        n = 100
        universe = _make_universe(n)
        all_codes = universe["ts_code"].tolist()

        prices = _make_prices(all_codes, EVAL_DATE)
        adj = _make_adj_factors(all_codes, EVAL_DATE)
        # Name history only covers 97 stocks → 3 unverifiable ST
        name_hist = _make_name_history(all_codes[:97], EVAL_DATE)

        gate = SignalEligibilityGate(min_listed_trade_days=10)
        result = gate.check(
            eval_date=EVAL_DATE,
            universe=universe,
            candidates=all_codes,
            prices=prices,
            adjustment_factors=adj,
            market_calendar=CALENDAR,
            name_history=name_hist,
        )

        # 3 stocks rejected for ST unverifiability
        st_rejected = {c: r for c, r in result.rejected.items() if r == REASON_ST}
        assert len(st_rejected) == 3
        # Coverage = 97/100 = 97% < 98% → data_failure
        assert result.data_failure is True

    def test_coverage_stats_include_market_data_coverage(self):
        """coverage_stats should report market_data_coverage for diagnostics."""
        n = 50
        universe = _make_universe(n)
        all_codes = universe["ts_code"].tolist()

        # 1 stock missing market data
        prices = _make_prices(all_codes[:49], EVAL_DATE)
        adj = _make_adj_factors(all_codes, EVAL_DATE)
        name_hist = _make_name_history(all_codes, EVAL_DATE)

        gate = SignalEligibilityGate(min_listed_trade_days=10)
        result = gate.check(
            eval_date=EVAL_DATE,
            universe=universe,
            candidates=all_codes,
            prices=prices,
            adjustment_factors=adj,
            market_calendar=CALENDAR,
            name_history=name_hist,
        )

        assert "market_data_coverage" in result.coverage_stats
        # 49/50 = 0.98
        assert result.coverage_stats["market_data_coverage"] == pytest.approx(0.98)
