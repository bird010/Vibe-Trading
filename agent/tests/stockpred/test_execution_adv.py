"""Tests for causal ADV calculator."""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.stockpred.execution.adv import ADVResult, compute_causal_adv


def _market(days: int = 25, amount_cny: float = 1_000_000.0) -> pd.DataFrame:
    """Uniform market: one stock, N days, constant daily turnover in CNY."""
    dates = [f"202501{d:02d}" for d in range(1, days + 1)]
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ"] * days,
            "trade_date": dates,
            "amount": [amount_cny / 1000.0] * days,  # stored in 千元
            "vol": [10000.0] * days,
        }
    )


def test_adv20_uses_previous_20_days_including_as_of():
    mkt = _market(25, amount_cny=2_000_000.0)
    result = compute_causal_adv(mkt, "000001.SZ", as_of_date="20250121", lookback=20)
    # Window includes as_of_date: days 2..21 (20 days)
    assert result.adv_value == pytest.approx(2_000_000.0)
    assert result.observations == 20
    assert result.is_valid


def test_adv_window_is_last_n_dates_up_to_as_of():
    mkt = _market(25)
    result = compute_causal_adv(mkt, "000001.SZ", as_of_date="20250120", lookback=20)
    assert result.observations == 20
    # Window is days 1..20


def test_adv_insufficient_observations():
    mkt = _market(5)
    result = compute_causal_adv(
        mkt, "000001.SZ", as_of_date="20250105", lookback=20, min_observations=10
    )
    assert not result.is_valid
    assert result.observations == 5


def test_adv_suspended_day_counts_as_zero():
    mkt = _market(20)
    mkt.loc[mkt["trade_date"] == "20250110", "amount"] = 0.0
    mkt.loc[mkt["trade_date"] == "20250110", "vol"] = 0.0
    result = compute_causal_adv(mkt, "000001.SZ", as_of_date="20250120", lookback=20)
    expected = (19 * 1_000_000.0 + 0.0) / 20
    assert result.adv_value == pytest.approx(expected)
    assert result.observations == 20


def test_adv_missing_data_excluded_from_observations():
    mkt = _market(20)
    mkt.loc[mkt["trade_date"] == "20250110", "amount"] = float("nan")
    result = compute_causal_adv(mkt, "000001.SZ", as_of_date="20250120", lookback=20)
    assert result.observations == 19
    assert not result.is_valid  # unconfirmed missing data fails closed
    assert result.has_data_quality_issue


def test_adv_unknown_code_returns_invalid():
    mkt = _market(20)
    result = compute_causal_adv(mkt, "UNKNOWN.SZ", as_of_date="20250120", lookback=20)
    assert not result.is_valid
    assert result.observations == 0


def test_adv_as_of_date_not_in_market():
    mkt = _market(20)
    # as_of_date beyond available data
    result = compute_causal_adv(mkt, "000001.SZ", as_of_date="20250201", lookback=20)
    # Should use all available dates <= as_of_date
    assert result.observations == 20
    assert result.is_valid


def test_adv_unknown_market_calendar_gap_is_quality_failure():
    market = pd.DataFrame(
        [
            {"ts_code": "000001.SZ", "trade_date": "20250101", "amount": 1000.0, "vol": 10.0},
            {"ts_code": "000001.SZ", "trade_date": "20250102", "amount": 0.0, "vol": 0.0},
        ]
    )

    result = compute_causal_adv(
        market, "000001.SZ", as_of_date="20250103", lookback=3,
        min_observations=1, trade_dates=["20250101", "20250102", "20250103"],
    )

    assert not result.is_valid
    assert result.has_data_quality_issue


def test_adv_zero_amount_with_nonzero_volume_is_quality_failure():
    market = _market(20)
    market.loc[market["trade_date"] == "20250110", "amount"] = 0.0
    result = compute_causal_adv(market, "000001.SZ", as_of_date="20250120", lookback=20)
    assert not result.is_valid
    assert result.has_data_quality_issue


def test_adv_result_fields():
    mkt = _market(20)
    result = compute_causal_adv(mkt, "000001.SZ", as_of_date="20250120", lookback=20)
    assert isinstance(result, ADVResult)
    assert result.lookback == 20
    assert result.as_of_date == "20250120"
