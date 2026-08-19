"""Point-in-time price adjustment and quality gates."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.stockpred.contracts import StockPredDataError


@dataclass(frozen=True)
class AdjustmentQuality:
    coverage: float
    missing_rows: int
    missing_stocks: int
    passed: bool


def apply_qfq(prices: pd.DataFrame, adjustment_factors: pd.DataFrame) -> pd.DataFrame:
    """Apply StockPred's forward-adjustment formula without raw-price fallback."""
    result = prices.copy()
    if adjustment_factors.empty:
        result["adj_close"] = np.nan
        if "open" in result.columns:
            result["adj_open"] = np.nan
        result["adj_factor_missing"] = True
        return result

    result = result.merge(
        adjustment_factors[["ts_code", "trade_date", "adj_factor"]],
        on=["ts_code", "trade_date"],
        how="left",
        validate="many_to_one",
    )
    latest_factor = (
        result.sort_values("trade_date")
        .groupby("ts_code", sort=False)["adj_factor"]
        .transform("last")
    )
    missing = (
        result["adj_factor"].isna()
        | latest_factor.isna()
        | (result["adj_factor"] <= 0)
        | (latest_factor <= 0)
    )
    result["adj_factor_missing"] = missing
    result["adj_close"] = result["close"] * result["adj_factor"] / latest_factor
    result.loc[missing, "adj_close"] = np.nan
    if "open" in result.columns:
        result["adj_open"] = result["open"] * result["adj_factor"] / latest_factor
        result.loc[missing, "adj_open"] = np.nan
    return result.drop(columns=["adj_factor"])


def summarize_adjustment_quality(
    prices: pd.DataFrame,
    *,
    expected_stocks: int,
    min_coverage: float,
) -> AdjustmentQuality:
    """Summarize coverage by stocks having any missing adjustment row."""
    if expected_stocks <= 0:
        return AdjustmentQuality(1.0, 0, 0, True)
    if prices.empty:
        return AdjustmentQuality(0.0, 0, expected_stocks, False)

    missing = prices["adj_factor_missing"].fillna(True).astype(bool)
    missing_stocks = int(prices.loc[missing, "ts_code"].nunique())
    coverage = max(0.0, (expected_stocks - missing_stocks) / expected_stocks)
    return AdjustmentQuality(
        coverage=float(coverage),
        missing_rows=int(missing.sum()),
        missing_stocks=missing_stocks,
        passed=coverage >= float(min_coverage),
    )


def require_adjustment_quality(
    prices: pd.DataFrame,
    *,
    expected_stocks: int,
    min_coverage: float = 0.98,
) -> AdjustmentQuality:
    """Return quality or fail the run when coverage is below the threshold."""
    quality = summarize_adjustment_quality(
        prices,
        expected_stocks=expected_stocks,
        min_coverage=min_coverage,
    )
    if not quality.passed:
        raise StockPredDataError(
            "STOCKPRED_ADJUSTMENT_COVERAGE",
            (
                f"adjustment coverage {quality.coverage:.2%} is below "
                f"required {min_coverage:.2%}"
            ),
        )
    return quality
