"""Tests for forward_returns_on_next_trade_day in correlation experiment."""

from __future__ import annotations

import pandas as pd
import pytest

from scripts.correlation_experiment import forward_returns_on_next_trade_day


def _prices() -> pd.DataFrame:
    """Three trade days, two stocks. T=20250102, next-T=20250103."""
    return pd.DataFrame(
        {
            "ts_code": ["A", "A", "A", "B", "B", "B"],
            "trade_date": [
                "20250101", "20250102", "20250103",
                "20250101", "20250102", "20250103",
            ],
            "close": [9.0, 10.0, 11.0, 19.0, 20.0, 22.0],
        }
    )


def _factors() -> pd.DataFrame:
    """Factor cross-section at T=20250102."""
    return pd.DataFrame(
        {"ts_code": ["A", "B"], "score": [1.0, 2.0]}
    ).set_index("ts_code")["score"]


def test_forward_returns_uses_next_trade_day_close() -> None:
    """Return must be close[next_T] / close[T] - 1, not close[T] / close[T-1] - 1."""
    prices = _prices()
    factors = _factors()
    trade_dates = ["20250101", "20250102", "20250103"]

    result = forward_returns_on_next_trade_day(
        prices, factors, eval_date="20250102", trade_dates=trade_dates
    )

    # A: 11/10 - 1 = 0.1, B: 22/20 - 1 = 0.1
    assert result.loc["A"] == pytest.approx(0.1)
    assert result.loc["B"] == pytest.approx(0.1)


def test_forward_returns_not_same_period_return() -> None:
    """Must NOT compute close[T]/close[T-1]-1 (which would be 10/9-1 for A)."""
    prices = _prices()
    factors = _factors()
    trade_dates = ["20250101", "20250102", "20250103"]

    result = forward_returns_on_next_trade_day(
        prices, factors, eval_date="20250102", trade_dates=trade_dates
    )

    # Same-period return for A would be 10/9-1 ≈ 0.111; forward is 0.1
    assert result.loc["A"] != pytest.approx(10.0 / 9.0 - 1.0)


def test_forward_returns_empty_when_no_next_trade_day() -> None:
    """If eval_date is the last trade day, return empty series."""
    prices = _prices()
    factors = _factors()
    trade_dates = ["20250101", "20250102"]  # No next day after 20250102

    result = forward_returns_on_next_trade_day(
        prices, factors, eval_date="20250102", trade_dates=trade_dates
    )

    assert result.empty


def test_forward_returns_empty_when_price_missing() -> None:
    """If next-T price is missing for a stock, exclude it."""
    prices = pd.DataFrame(
        {
            "ts_code": ["A", "A", "B", "B"],
            "trade_date": ["20250102", "20250103", "20250102", "20250103"],
            "close": [10.0, 11.0, 20.0, float("nan")],
        }
    )
    factors = _factors()
    trade_dates = ["20250102", "20250103"]

    result = forward_returns_on_next_trade_day(
        prices, factors, eval_date="20250102", trade_dates=trade_dates
    )

    assert "A" in result.index
    assert "B" not in result.index


def test_forward_returns_empty_when_factor_price_disjoint() -> None:
    """If factor codes and price codes don't overlap, return empty."""
    prices = _prices()
    factors = pd.Series({"X": 1.0, "Y": 2.0}, name="score")
    trade_dates = ["20250101", "20250102", "20250103"]

    result = forward_returns_on_next_trade_day(
        prices, factors, eval_date="20250102", trade_dates=trade_dates
    )

    assert result.empty


def test_forward_returns_uses_adj_close_not_raw_close() -> None:
    """On ex-dividend day, raw close drops but adj_close reflects true return.

    Stock A: raw close 10→9 (looks like -10%), but adj_close 10→10.5 (true +5%).
    The function MUST use adj_close to avoid polluting IC with dividend artifacts.
    """
    prices = pd.DataFrame(
        {
            "ts_code": ["A", "A"],
            "trade_date": ["20250102", "20250103"],
            "close": [10.0, 9.0],        # raw: -10% (misleading)
            "adj_close": [10.0, 10.5],   # adjusted: +5% (true economic return)
        }
    )
    factors = pd.Series({"A": 1.0}, name="score")
    trade_dates = ["20250102", "20250103"]

    result = forward_returns_on_next_trade_day(
        prices, factors, eval_date="20250102", trade_dates=trade_dates
    )

    # Must use adj_close: 10.5/10 - 1 = 0.05, NOT raw close 9/10 - 1 = -0.1
    assert result.loc["A"] == pytest.approx(0.05)
