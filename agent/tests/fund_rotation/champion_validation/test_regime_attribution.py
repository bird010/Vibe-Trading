from __future__ import annotations

import pytest

from backtest.fund_rotation.champion_validation.regime_attribution import (
    classify_regimes,
    compute_regime_and_concentration,
)


def _features() -> list[dict[str, object]]:
    return [
        {
            "date": "2024-01-01",
            "fold": "fold-1",
            "benchmark_return_26w": 0.10,
            "volatility_13w": 0.10,
            "trend_strength": 0.20,
            "correlation": 0.30,
        },
        {
            "date": "2024-01-08",
            "fold": "fold-1",
            "benchmark_return_26w": -0.10,
            "volatility_13w": 0.20,
            "trend_strength": 0.40,
            "correlation": 0.50,
        },
        {
            "date": "2024-01-15",
            "fold": "fold-1",
            "benchmark_return_26w": 0.05,
            "volatility_13w": 0.15,
            "trend_strength": 0.35,
            "correlation": 0.45,
        },
    ]


def test_classify_regimes_fits_thresholds_on_fold_train_only():
    labels = classify_regimes(_features(), {"fold-1": [True, True, False]})

    assert labels[2].market == "Bull"
    assert labels[2].volatility == "Low Vol"
    assert labels[2].trend == "Trend"
    assert labels[2].correlation == "High Correlation"
    assert labels[2].thresholds["volatility_13w"] == pytest.approx(0.15)
    assert labels[2].can_drive_trading is False


def test_classify_regimes_rejects_feature_known_after_decision_time():
    feature = _features()[0] | {"available_at": "2024-01-02"}

    with pytest.raises(ValueError, match="decision time"):
        classify_regimes([feature], {"fold-1": [True]})


def test_regime_and_concentration_reports_all_dimensions_and_caps_status():
    observations = [
        {"date": "2024-01-01", "return": 0.06, "excess_return": 0.05, "etf": "A", "regime": "Bull", "year": 2024, "fold": "f1", "cluster": "c1", "trade_id": "t1", "pnl_contribution": 60.0, "exposure": 1.0, "cash_ratio": 0.0},
        {"date": "2024-01-08", "return": 0.02, "excess_return": 0.01, "etf": "B", "regime": "Bull", "year": 2024, "fold": "f1", "cluster": "c1", "trade_id": "t2", "pnl_contribution": 20.0, "exposure": 1.0, "cash_ratio": 0.0},
        {"date": "2024-01-15", "return": 0.02, "excess_return": 0.01, "etf": "C", "regime": "Bear", "year": 2024, "fold": "f2", "cluster": "c2", "trade_id": "t3", "pnl_contribution": 20.0, "exposure": 0.5, "cash_ratio": 0.5},
    ]

    result = compute_regime_and_concentration(observations)

    assert result["status"] == "INCONCLUSIVE"
    assert "CONCENTRATION_OVER_50_PERCENT" in result["reason_codes"]
    assert {"regime", "etf", "year", "fold", "cluster"} <= set(result["groups"])
    assert result["groups"]["regime"]["Bull"]["excess_return"] == pytest.approx(0.06)
    assert result["concentration"]["pnl_hhi"] == pytest.approx(0.44)
    assert result["concentration"]["effective_sources"] == pytest.approx(1 / 0.44)


def test_regime_and_concentration_is_pass_when_no_registered_source_dominates():
    observations = [
        {"date": f"2024-01-{index:02d}", "return": 0.01, "excess_return": 0.01, "etf": etf, "regime": regime, "year": year, "fold": fold, "cluster": cluster, "trade_id": f"t{index}", "pnl_contribution": 25.0, "exposure": 1.0, "cash_ratio": 0.0}
        for index, (etf, regime, year, fold, cluster) in enumerate((("A", "Bull", 2023, "f1", "c1"), ("B", "Bear", 2024, "f2", "c2"), ("C", "Bull", 2023, "f1", "c3"), ("D", "Bear", 2024, "f2", "c4")), start=1)
    ]

    result = compute_regime_and_concentration(observations)

    assert result["status"] == "PASS"
    assert result["reason_codes"] == []
