import pandas as pd
import pytest

from backtest.fund_rotation.ideal_executor import run_daily_ideal_account
from backtest.fund_rotation.pipeline import _align_theoretical_to_common_dates


def _market(rows, factors=None):
    daily = pd.DataFrame([
        {
            "trade_date": date, "ts_code": code,
            "open": open_price, "close": close_price,
        }
        for date, code, open_price, close_price in rows
    ])
    factor_lookup = factors or {}
    adj = pd.DataFrame([
        {
            "trade_date": date, "ts_code": code,
            "adj_factor": factor_lookup.get((date, code), 1.0),
        }
        for date, code, _open, _close in rows
    ])
    return daily, adj


def test_signal_after_friday_close_does_not_capture_monday_gap():
    daily, adj = _market([
        ("20240105", "ETF", 100.0, 100.0),
        ("20240108", "ETF", 110.0, 110.0),
    ])

    equity = run_daily_ideal_account(
        {"20240105": {"ETF": 1.0}}, daily, adj,
    )

    assert equity.loc["20240108"] == pytest.approx(1.0)


def test_symbols_execute_independently_and_missing_open_remains_residual_cash():
    daily, adj = _market([
        ("20240105", "A", 100.0, 100.0),
        ("20240105", "B", 100.0, 100.0),
        ("20240108", "A", 100.0, 110.0),
        ("20240108", "B", None, 100.0),
        ("20240109", "A", 110.0, 110.0),
        ("20240109", "B", 100.0, 120.0),
    ])

    equity = run_daily_ideal_account(
        {"20240105": {"A": 0.5, "B": 0.5}}, daily, adj,
    )

    assert equity.loc["20240108"] == pytest.approx(1.05)
    assert equity.loc["20240109"] == pytest.approx(1.15)


def test_rebalance_open_preserves_old_overnight_then_switches_to_new_intraday():
    daily, adj = _market([
        ("20240104", "A", 100.0, 100.0),
        ("20240104", "B", 200.0, 200.0),
        ("20240105", "A", 100.0, 110.0),
        ("20240105", "B", 200.0, 200.0),
        ("20240108", "A", 121.0, 121.0),
        ("20240108", "B", 200.0, 220.0),
        ("20240109", "A", 121.0, 121.0),
        ("20240109", "B", 220.0, 242.0),
    ])

    equity = run_daily_ideal_account({
        "20240104": {"A": 1.0},
        "20240105": {"B": 1.0},
    }, daily, adj)

    assert equity.loc["20240105"] == pytest.approx(1.10)  # A open -> close
    assert equity.loc["20240108"] == pytest.approx(1.331)  # A overnight, then B open -> close
    assert equity.loc["20240109"] == pytest.approx(1.4641)  # B adjusted close -> close


def test_adjusted_prices_neutralize_corporate_action_share_change():
    daily, adj = _market([
        ("20240104", "ETF", 100.0, 100.0),
        ("20240105", "ETF", 100.0, 100.0),
        ("20240108", "ETF", 50.0, 50.0),
    ], factors={
        ("20240104", "ETF"): 1.0,
        ("20240105", "ETF"): 1.0,
        ("20240108", "ETF"): 2.0,
    })

    equity = run_daily_ideal_account(
        {"20240104": {"ETF": 1.0}}, daily, adj,
    )

    assert equity.loc["20240108"] == pytest.approx(1.0)


def test_missing_open_blocks_old_holding_reduction_but_not_close_valuation():
    daily, adj = _market([
        ("20240105", "A", 100.0, 100.0),
        ("20240105", "B", 20.0, 20.0),
        ("20240108", "A", 100.0, 100.0),
        ("20240108", "B", 20.0, 20.0),
        ("20240109", "A", None, 110.0),
        ("20240109", "B", 20.0, 20.0),
        ("20240110", "A", 110.0, 110.0),
        ("20240110", "B", 20.0, 22.0),
    ])

    equity = run_daily_ideal_account({
        "20240105": {"A": 1.0},
        "20240108": {"B": 1.0},
    }, daily, adj)

    assert equity.loc["20240109"] == pytest.approx(1.10)
    assert equity.loc["20240110"] == pytest.approx(1.20)


def test_new_signal_cancels_older_residual_before_symbol_recovers():
    daily, adj = _market([
        ("20240105", "A", 10.0, 10.0),
        ("20240105", "B", None, 1.0),
        ("20240108", "A", 10.0, 10.0),
        ("20240108", "B", None, 1.0),
        ("20240109", "A", 10.0, 11.0),
        ("20240109", "B", None, 1.0),
        ("20240110", "A", 11.0, 11.0),
        ("20240110", "B", 1.0, 2.0),
    ])

    equity = run_daily_ideal_account({
        "20240105": {"B": 1.0},
        "20240108": {"A": 1.0},
    }, daily, adj)

    assert equity.loc["20240109"] == pytest.approx(1.10)
    assert equity.loc["20240110"] == pytest.approx(1.10)


def test_existing_holding_with_missing_close_uses_last_confirmed_close_not_open():
    daily, adj = _market([
        ("20240105", "A", 100.0, 100.0),
        ("20240108", "A", 100.0, 100.0),
        ("20240109", "A", 200.0, None),
    ])

    equity = run_daily_ideal_account(
        {"20240105": {"A": 1.0}}, daily, adj,
    )

    assert equity.loc["20240109"] == pytest.approx(1.0)
    assert equity.attrs["stale_valuations"][-1] == {
        "trade_date": "20240109", "ts_code": "A", "mark_price": 100.0,
        "last_valid_close_date": "20240108", "stale_days": 1,
        "anchor_source": "last_valid_close",
    }


def test_new_position_without_close_uses_confirmed_execution_open_as_anchor():
    daily, adj = _market([
        ("20240105", "A", 100.0, 100.0),
        ("20240108", "A", 120.0, None),
    ])

    equity = run_daily_ideal_account(
        {"20240105": {"A": 1.0}}, daily, adj,
    )

    assert equity.loc["20240108"] == pytest.approx(1.0)
    assert equity.attrs["stale_valuations"][-1] == {
        "trade_date": "20240108", "ts_code": "A", "mark_price": 120.0,
        "last_valid_close_date": "20240108", "stale_days": 0,
        "anchor_source": "execution_open",
    }


def test_ideal_account_is_cropped_to_the_common_execution_interval():
    daily, adj = _market([
        ("20240105", "ETF", 100.0, 100.0),
        ("20240108", "ETF", 110.0, 110.0),
        ("20240109", "ETF", 110.0, 111.0),
    ])
    ideal = run_daily_ideal_account(
        {"20240105": {"ETF": 1.0}}, daily, adj,
    )
    common = pd.Index(["20240108", "20240109"])

    cropped = _align_theoretical_to_common_dates(ideal, common)

    assert cropped.index.equals(common)
    assert "20240105" not in cropped.index
