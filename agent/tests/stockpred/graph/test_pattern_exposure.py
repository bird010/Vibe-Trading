"""Unit tests for src.stockpred.graph.pattern_exposure."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.stockpred.graph.pattern_exposure import (
    EXPOSURE_MATCH_COLUMNS,
    match_exposure_controls,
    predictable_reason_codes,
    reason_exposure_mask,
    reason_signal_available_mask,
    summarize_reason_exposures,
)


# ---------------------------------------------------------------------------
# predictable_reason_codes
# ---------------------------------------------------------------------------


class TestPredictableReasonCodes:
    def test_crowded_reversal(self) -> None:
        row = {"crowding_score": 0.85}
        assert "crowded_reversal" in predictable_reason_codes(row)

    def test_crowded_reversal_below_threshold(self) -> None:
        row = {"crowding_score": 0.7}
        assert "crowded_reversal" not in predictable_reason_codes(row)

    def test_flow_price_divergence(self) -> None:
        row = {"net_big_inflow_5d": -100.0, "f_rel_str": 16.0}
        assert "flow_price_divergence" in predictable_reason_codes(row)

    def test_flow_price_divergence_positive_inflow(self) -> None:
        row = {"net_big_inflow_5d": 100.0, "f_rel_str": 16.0}
        assert "flow_price_divergence" not in predictable_reason_codes(row)

    def test_margin_unwind(self) -> None:
        row = {"margin_balance_change_5d": -0.15}
        assert "margin_unwind" in predictable_reason_codes(row)

    def test_insider_selling(self) -> None:
        row = {"holder_sell_ratio_90d": 0.05}
        assert "insider_selling" in predictable_reason_codes(row)

    def test_pledge_pressure(self) -> None:
        row = {"pledge_amount_180d": 500.0}
        assert "pledge_pressure" in predictable_reason_codes(row)

    def test_valuation_fundamental(self) -> None:
        row = {"financial_profit_growth": -0.1, "pe_ttm_percentile": 0.9}
        assert "valuation_fundamental" in predictable_reason_codes(row)

    def test_graph_propagation_error(self) -> None:
        row = {"f_neighbor": 16.0, "f_rel_str": 8.0, "f_moneyflow": 9.0}
        assert "graph_propagation_error" in predictable_reason_codes(row)

    def test_retail_distribution_weak_momentum(self) -> None:
        row = {"f_short_mom": 8.0, "holder_num_change": 100.0}
        assert "retail_distribution_weak_momentum" in predictable_reason_codes(row)

    def test_data_quality(self) -> None:
        row = {"missing_feature_count": 5.0}
        assert "data_quality" in predictable_reason_codes(row)

    def test_multiple_reasons(self) -> None:
        row = {"crowding_score": 0.9, "pledge_amount_180d": 200.0}
        codes = predictable_reason_codes(row)
        assert "crowded_reversal" in codes
        assert "pledge_pressure" in codes

    def test_empty_row_returns_empty(self) -> None:
        assert predictable_reason_codes({}) == ()

    def test_non_finite_values_ignored(self) -> None:
        row = {"crowding_score": float("nan"), "pledge_amount_180d": float("inf")}
        assert predictable_reason_codes(row) == ()


# ---------------------------------------------------------------------------
# reason_signal_available_mask
# ---------------------------------------------------------------------------


class TestReasonSignalAvailableMask:
    def test_all_required_fields_present(self) -> None:
        df = pd.DataFrame({"crowding_score": [0.5, 0.9]})
        mask = reason_signal_available_mask(df, "crowded_reversal")
        assert mask.all()

    def test_missing_column_returns_all_false(self) -> None:
        df = pd.DataFrame({"other_col": [1, 2]})
        mask = reason_signal_available_mask(df, "crowded_reversal")
        assert not mask.any()

    def test_nan_values_marked_unavailable(self) -> None:
        df = pd.DataFrame({"crowding_score": [0.5, np.nan]})
        mask = reason_signal_available_mask(df, "crowded_reversal")
        assert mask.iloc[0]
        assert not mask.iloc[1]

    def test_unknown_reason_returns_all_false(self) -> None:
        df = pd.DataFrame({"crowding_score": [0.5]})
        mask = reason_signal_available_mask(df, "nonexistent_reason")
        assert not mask.any()

    def test_missing_flag_column_excludes_row(self) -> None:
        df = pd.DataFrame({
            "crowding_score": [0.5, 0.9],
            "crowding_score_missing": [False, True],
        })
        mask = reason_signal_available_mask(df, "crowded_reversal")
        assert mask.iloc[0]
        assert not mask.iloc[1]


# ---------------------------------------------------------------------------
# reason_exposure_mask
# ---------------------------------------------------------------------------


class TestReasonExposureMask:
    def test_exposed_rows_identified(self) -> None:
        df = pd.DataFrame({
            "crowding_score": [0.9, 0.3, 0.85],
        })
        mask = reason_exposure_mask(df, "crowded_reversal")
        assert mask.iloc[0]
        assert not mask.iloc[1]
        assert mask.iloc[2]

    def test_unknown_reason_all_false(self) -> None:
        df = pd.DataFrame({"crowding_score": [0.9]})
        mask = reason_exposure_mask(df, "unknown")
        assert not mask.any()


# ---------------------------------------------------------------------------
# match_exposure_controls
# ---------------------------------------------------------------------------


def _make_cases(n_exposed: int = 2, n_controls: int = 6) -> pd.DataFrame:
    """Build a minimal cases DataFrame with exposed and control stocks."""
    rows: list[dict[str, object]] = []
    # Exposed stocks: high crowding
    for i in range(n_exposed):
        rows.append({
            "ts_code": f"EXP{i:04d}.SZ",
            "trade_date": "20260105",
            "industry": "IND_A",
            "circ_mv": 1000.0 + i * 100,
            "score": 80.0 + i,
            "crowding_score": 0.9,
        })
    # Control stocks: low crowding
    for i in range(n_controls):
        rows.append({
            "ts_code": f"CTL{i:04d}.SZ",
            "trade_date": "20260105",
            "industry": "IND_A" if i % 2 == 0 else "IND_B",
            "circ_mv": 1000.0 + i * 50,
            "score": 70.0 + i,
            "crowding_score": 0.2,
        })
    return pd.DataFrame(rows)


class TestMatchExposureControls:
    def test_returns_correct_columns(self) -> None:
        cases = _make_cases()
        result = match_exposure_controls(cases, "crowded_reversal")
        assert list(result.columns) == EXPOSURE_MATCH_COLUMNS

    def test_matches_controls_per_exposed(self) -> None:
        cases = _make_cases(n_exposed=2, n_controls=6)
        result = match_exposure_controls(cases, "crowded_reversal", controls_per_case=3)
        # Each exposed should get up to 3 controls
        for exposed_code in ["EXP0000.SZ", "EXP0001.SZ"]:
            matched = result[result["exposed_ts_code"] == exposed_code]
            assert len(matched) <= 3

    def test_same_industry_preferred(self) -> None:
        cases = _make_cases(n_exposed=1, n_controls=6)
        result = match_exposure_controls(cases, "crowded_reversal", controls_per_case=2)
        # At least one control should be from same industry
        assert result["same_industry"].any()

    def test_invalid_controls_per_case_raises(self) -> None:
        cases = _make_cases()
        with pytest.raises(ValueError, match="positive integer"):
            match_exposure_controls(cases, "crowded_reversal", controls_per_case=0)

    def test_missing_required_columns_raises(self) -> None:
        df = pd.DataFrame({"ts_code": ["A"], "trade_date": ["20260105"]})
        with pytest.raises(ValueError, match="missing required columns"):
            match_exposure_controls(df, "crowded_reversal")

    def test_no_exposed_returns_empty(self) -> None:
        # All low crowding → no exposed
        cases = _make_cases()
        cases["crowding_score"] = 0.1
        result = match_exposure_controls(cases, "crowded_reversal")
        assert result.empty


# ---------------------------------------------------------------------------
# summarize_reason_exposures
# ---------------------------------------------------------------------------


class TestSummarizeReasonExposures:
    def _make_panel(self, n_dates: int = 10, n_stocks: int = 8) -> pd.DataFrame:
        """Build a panel with time_fold and return columns."""
        rows: list[dict[str, object]] = []
        dates = [f"202601{d:02d}" for d in range(1, n_dates + 1)]
        for date_index, trade_date in enumerate(dates):
            for i in range(n_stocks):
                rows.append({
                    "ts_code": f"STK{i:04d}.SZ",
                    "trade_date": trade_date,
                    "time_fold": f"fold_{date_index % 3}",
                    "fwd_ret_20d": -0.05 if i < 2 else 0.03,
                    "industry": "IND_A",
                    "circ_mv": 1000.0 + i * 100,
                    "score": 80.0 + i,
                    # First 2 stocks are exposed (high crowding)
                    "crowding_score": 0.9 if i < 2 else 0.2,
                })
        return pd.DataFrame(rows)

    def test_returns_exposure_columns(self) -> None:
        panel = self._make_panel()
        result = summarize_reason_exposures(
            panel, ["crowded_reversal"], return_col="fwd_ret_20d"
        )
        assert "reason_code" in result.columns
        assert "exposure_eligible" in result.columns
        assert len(result) == 1

    def test_invalid_trade_date_raises(self) -> None:
        panel = self._make_panel()
        panel.loc[0, "trade_date"] = "not_a_date"
        with pytest.raises(ValueError, match="invalid trade_date"):
            summarize_reason_exposures(
                panel, ["crowded_reversal"], return_col="fwd_ret_20d"
            )

    def test_duplicate_ts_code_date_raises(self) -> None:
        panel = self._make_panel(n_dates=1, n_stocks=2)
        panel = pd.concat([panel, panel.iloc[[0]]], ignore_index=True)
        with pytest.raises(ValueError, match="unique"):
            summarize_reason_exposures(
                panel, ["crowded_reversal"], return_col="fwd_ret_20d"
            )

    def test_missing_return_col_raises(self) -> None:
        panel = self._make_panel()
        with pytest.raises(ValueError, match="missing required columns"):
            summarize_reason_exposures(
                panel, ["crowded_reversal"], return_col="nonexistent_col"
            )
