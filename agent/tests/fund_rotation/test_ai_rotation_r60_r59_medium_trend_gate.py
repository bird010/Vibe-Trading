from __future__ import annotations

import pandas as pd

from backtest.fund_rotation.strategies.ai_rotation_r60_r59_medium_trend_gate.strategy import (
    compute_adjusted_return_126d,
)


def test_r60_uses_127_causal_adjusted_closes_for_medium_return():
    dates = pd.bdate_range("2024-01-01", periods=127).strftime("%Y%m%d")
    bars = pd.DataFrame({"ts_code": "A", "trade_date": dates, "close": range(1, 128)})
    for field in ("open", "high", "low"):
        bars[field] = bars["close"]
    adjustments = pd.DataFrame(
        {"ts_code": "A", "trade_date": dates, "adj_factor": 1.0}
    )

    result = compute_adjusted_return_126d(bars, adjustments, dates[-1])

    assert result["status"] == "VALID"
    assert result["observations"] == 127
    assert result["return_126d"] == (127.0 / 1.0) - 1.0


def test_r60_distinguishes_insufficient_data_from_negative_trend():
    dates = pd.bdate_range("2024-01-01", periods=126).strftime("%Y%m%d")
    bars = pd.DataFrame({"ts_code": "A", "trade_date": dates, "close": range(1, 127)})
    for field in ("open", "high", "low"):
        bars[field] = bars["close"]
    adjustments = pd.DataFrame(
        {"ts_code": "A", "trade_date": dates, "adj_factor": 1.0}
    )

    result = compute_adjusted_return_126d(bars, adjustments, dates[-1])

    assert result["status"] == "INSUFFICIENT_OBSERVATIONS"
    assert result["return_126d"] is None
