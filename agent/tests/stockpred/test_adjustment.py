from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.stockpred.contracts import StockPredDataError
from src.stockpred.graph.adjustment import (
    apply_qfq,
    require_adjustment_quality,
    summarize_adjustment_quality,
)


def test_qfq_keeps_missing_factor_as_nan() -> None:
    prices = pd.DataFrame(
        {
            "ts_code": ["A"],
            "trade_date": ["20260102"],
            "open": [10.0],
            "close": [11.0],
        }
    )

    result = apply_qfq(
        prices,
        pd.DataFrame(columns=["ts_code", "trade_date", "adj_factor"]),
    )

    assert pd.isna(result.loc[0, "adj_open"])
    assert pd.isna(result.loc[0, "adj_close"])
    assert bool(result.loc[0, "adj_factor_missing"])


def test_qfq_anchors_each_stock_to_its_latest_factor() -> None:
    prices = pd.DataFrame(
        {
            "ts_code": ["A", "A"],
            "trade_date": ["20260102", "20260103"],
            "open": [10.0, 12.0],
            "close": [11.0, 13.0],
        }
    )
    factors = pd.DataFrame(
        {
            "ts_code": ["A", "A"],
            "trade_date": ["20260102", "20260103"],
            "adj_factor": [1.0, 2.0],
        }
    )

    result = apply_qfq(prices, factors)

    np.testing.assert_allclose(result["adj_open"], [5.0, 12.0])
    np.testing.assert_allclose(result["adj_close"], [5.5, 13.0])
    assert not result["adj_factor_missing"].any()


def test_adjustment_quality_counts_stocks_with_any_missing_factor() -> None:
    prices = pd.DataFrame(
        {
            "ts_code": ["A", "A", "B"],
            "adj_factor_missing": [False, True, False],
        }
    )

    quality = summarize_adjustment_quality(
        prices,
        expected_stocks=2,
        min_coverage=0.98,
    )

    assert quality.coverage == 0.5
    assert quality.missing_rows == 1
    assert quality.missing_stocks == 1
    assert not quality.passed


def test_quality_gate_rejects_below_98_percent() -> None:
    prices = pd.DataFrame(
        {
            "ts_code": [f"S{i:03d}" for i in range(100)],
            "adj_factor_missing": [False] * 97 + [True] * 3,
        }
    )

    with pytest.raises(StockPredDataError) as exc_info:
        require_adjustment_quality(prices, expected_stocks=100)

    assert exc_info.value.code == "STOCKPRED_ADJUSTMENT_COVERAGE"


def test_quality_gate_accepts_empty_expected_universe() -> None:
    quality = require_adjustment_quality(
        pd.DataFrame(columns=["ts_code", "adj_factor_missing"]),
        expected_stocks=0,
    )

    assert quality.coverage == 1.0
    assert quality.passed
