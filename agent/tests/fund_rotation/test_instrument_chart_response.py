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
