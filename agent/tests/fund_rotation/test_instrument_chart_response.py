"""Instrument chart response normalization contracts."""

from src.stockpred.fund_rotation.api_models import InstrumentChartResponse


def test_chart_response_retains_only_buy_sell_markers():
    response = InstrumentChartResponse(
        ts_code="510300.SH",
        run_id="run-1",
        trades=[
            {
                "trade_date": "20240108",
                "action": "BUY",
                "filled": 100,
                "price": 3.5,
            },
            {
                "trade_date": "20240109",
                "action": "SHARE_ADJUSTMENT",
                "event_type": "CORPORATE_ACTION",
                "filled": 110,
                "price": 0.0,
            },
            {
                "trade_date": "20240110",
                "action": "SELL",
                "filled": 100,
                "price": 3.6,
            },
        ],
    )

    assert [trade["action"] for trade in response.trades] == ["BUY", "SELL"]


def test_chart_response_normalizes_csv_and_lance_dates_to_same_key():
    response = InstrumentChartResponse(
        ts_code="510300.SH",
        run_id="run-1",
        signals=[{"week_ending": 20240105, "target_weight": 0.5}],
        trades=[
            {
                "trade_date": 20240108,
                "signal_date": "2024-01-05",
                "action": "buy",
                "filled": 100,
                "price": 3.5,
            }
        ],
        ohlcv=[
            {
                "trade_date": "2024-01-08T00:00:00",
                "open": 3.4,
                "high": 3.6,
                "low": 3.3,
                "close": 3.5,
                "vol": 1000,
            }
        ],
        positions=[{"trade_date": 20240108, "quantity": 100}],
        orders=[{"trade_date": 20240108, "direction": "BUY"}],
    )

    assert response.signals[0]["week_ending"] == "20240105"
    assert response.trades[0]["trade_date"] == "20240108"
    assert response.trades[0]["signal_date"] == "20240105"
    assert response.trades[0]["action"] == "BUY"
    assert response.ohlcv[0]["trade_date"] == "20240108"
    assert response.positions[0]["trade_date"] == "20240108"
    assert response.orders[0]["trade_date"] == "20240108"
