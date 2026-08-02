"""Tests for ADV20 capacity and slippage — §13.3."""

import numpy as np
import pandas as pd
import pytest

from backtest.fund_rotation.capacity import compute_adv20, apply_capacity_and_slippage, ADVIndex


def _market_df(dates: list[str], amounts: list[float], code: str = "A") -> pd.DataFrame:
    """Build market DataFrame with amount column (in CNY)."""
    return pd.DataFrame({
        "ts_code": [code] * len(dates),
        "trade_date": dates,
        "amount": amounts,
        "vol": [1000000] * len(dates),
    })


class TestADV20:
    """§13.3 — causal ADV20, excludes execution day."""

    def test_basic_adv20(self):
        """20 days of 1M CNY each -> ADV20 = 1M."""
        dates = [f"2024{m:02d}{d:02d}" for m, d in [(1, i) for i in range(1, 21)]]
        amounts = [1_000_000.0] * 20
        df = _market_df(dates, amounts)
        result = compute_adv20(df, code="A", as_of_date="20240122", lookback=20, min_obs=10, amount_multiplier=1.0)
        assert result.adv_value == pytest.approx(1_000_000.0)
        assert result.is_valid is True

    def test_excludes_execution_day(self):
        """ADV20 must not include the execution day itself."""
        dates = [f"2024010{i}" for i in range(1, 10)] + ["20240110", "20240111"]
        amounts = [1_000_000.0] * 10 + [999_999_999.0]  # Execution day has huge amount
        df = _market_df(dates, amounts)
        # as_of = 20240111 (execution day), lookback should use days BEFORE it
        result = compute_adv20(df, code="A", as_of_date="20240111", lookback=20, min_obs=10, amount_multiplier=1.0)
        # Should use the 10 days before 20240111, all at 1M
        assert result.adv_value == pytest.approx(1_000_000.0)

    def test_insufficient_observations_invalid(self):
        """Fewer than min_obs -> invalid."""
        dates = ["20240101", "20240102", "20240103"]
        amounts = [1_000_000.0] * 3
        df = _market_df(dates, amounts)
        result = compute_adv20(df, code="A", as_of_date="20240104", lookback=20, min_obs=10)
        assert result.is_valid is False

    def test_at_least_10_valid(self):
        """Exactly 10 observations -> valid."""
        dates = [f"202401{i:02d}" for i in range(1, 11)]
        amounts = [2_000_000.0] * 10
        df = _market_df(dates, amounts)
        result = compute_adv20(df, code="A", as_of_date="20240112", lookback=20, min_obs=10, amount_multiplier=1.0)
        assert result.is_valid is True
        assert result.adv_value == pytest.approx(2_000_000.0)


class TestCapacityAndSlippage:
    """§13.3 — participation rate cap and slippage formula."""

    def test_within_capacity_no_truncation(self):
        """Order within 5% ADV -> full fill."""
        filled, participation, slippage_bps = apply_capacity_and_slippage(
            requested_shares=1000,
            price=10.0,
            adv_value=1_000_000.0,
            max_participation=0.05,
            lot_size=100,
            base_slippage_bps=5.0,
            max_slippage_bps=30.0,
        )
        # Max notional = 5% * 1M = 50000; at 10/share = 5000 shares max
        assert filled == 1000
        assert participation == pytest.approx(1000 * 10.0 / 1_000_000.0)

    def test_exceeds_capacity_truncated_to_lot(self):
        """Order exceeding capacity -> truncated to lot size."""
        filled, participation, slippage_bps = apply_capacity_and_slippage(
            requested_shares=10000,
            price=10.0,
            adv_value=1_000_000.0,
            max_participation=0.05,
            lot_size=100,
            base_slippage_bps=5.0,
            max_slippage_bps=30.0,
        )
        # Max = 50000 / 10 = 5000 shares, rounded to 100 -> 5000
        assert filled == 5000
        assert filled % 100 == 0

    def test_slippage_formula(self):
        """slippage_bps = min(30, 5 + 200 * participation_rate)."""
        # participation = 0.02 (2%)
        _, _, slippage_bps = apply_capacity_and_slippage(
            requested_shares=2000,
            price=10.0,
            adv_value=1_000_000.0,
            max_participation=0.05,
            lot_size=100,
            base_slippage_bps=5.0,
            max_slippage_bps=30.0,
        )
        # participation = 2000*10/1M = 0.02
        expected = min(30.0, 5.0 + 200.0 * 0.02)  # = 9.0
        assert slippage_bps == pytest.approx(expected)

    def test_slippage_capped_at_max(self):
        """Slippage capped at max_slippage_bps."""
        _, _, slippage_bps = apply_capacity_and_slippage(
            requested_shares=5000,
            price=10.0,
            adv_value=1_000_000.0,
            max_participation=0.05,
            lot_size=100,
            base_slippage_bps=5.0,
            max_slippage_bps=30.0,
        )
        # participation = 5000*10/1M = 0.05
        # formula: 5 + 200*0.05 = 15, still under 30
        assert slippage_bps == pytest.approx(15.0)

    def test_invalid_adv_zero_capacity(self):
        """Invalid ADV -> zero capacity -> zero fill."""
        filled, _, _ = apply_capacity_and_slippage(
            requested_shares=1000,
            price=10.0,
            adv_value=0.0,  # invalid
            max_participation=0.05,
            lot_size=100,
            base_slippage_bps=5.0,
            max_slippage_bps=30.0,
        )
        assert filled == 0


class TestADVIndexConsistency:
    """ADVIndex must produce identical results to compute_adv20."""

    def _build_grouped(self, codes_data: dict[str, tuple[list[str], list[float]]]) -> dict[str, pd.DataFrame]:
        grouped = {}
        for code, (dates, amounts) in codes_data.items():
            grouped[code] = pd.DataFrame({
                "ts_code": [code] * len(dates),
                "trade_date": dates,
                "amount": amounts,
            })
        return grouped

    def test_normal_consistency(self):
        """Normal dates: ADVIndex matches compute_adv20 exactly."""
        dates = [f"202401{d:02d}" for d in range(1, 26)]
        amounts = [float(i * 100) for i in range(1, 26)]
        grouped = self._build_grouped({"A": (dates, amounts)})
        idx = ADVIndex(grouped, lookback=20, min_obs=10, amount_multiplier=1.0)

        for trade_date in dates:
            old = compute_adv20(grouped["A"], "A", trade_date, lookback=20, min_obs=10, amount_multiplier=1.0)
            new = idx.get("A", trade_date)
            assert new.is_valid == old.is_valid, f"date={trade_date}"
            assert new.observations == old.observations, f"date={trade_date}"
            if old.is_valid:
                assert new.adv_value == pytest.approx(old.adv_value, rel=1e-12), f"date={trade_date}"

    def test_first_10_days_insufficient(self):
        """First 9 days have < 10 observations -> invalid."""
        dates = [f"202401{d:02d}" for d in range(1, 26)]
        amounts = [1000.0] * 25
        grouped = self._build_grouped({"B": (dates, amounts)})
        idx = ADVIndex(grouped, lookback=20, min_obs=10, amount_multiplier=1.0)

        # Day 10 as execution date: only 9 prior days -> invalid
        old = compute_adv20(grouped["B"], "B", "20240110", lookback=20, min_obs=10, amount_multiplier=1.0)
        new = idx.get("B", "20240110")
        assert old.is_valid is False
        assert new.is_valid is False
        assert new.observations == old.observations

    def test_nan_amount_excluded(self):
        """NaN amounts are excluded from mean and count."""
        dates = [f"202401{d:02d}" for d in range(1, 16)]
        amounts = [1000.0] * 15
        amounts[5] = float("nan")  # Day 6 is NaN
        amounts[10] = -50.0  # Day 11 is negative -> also invalid
        grouped = self._build_grouped({"C": (dates, amounts)})
        idx = ADVIndex(grouped, lookback=20, min_obs=10, amount_multiplier=1.0)

        for trade_date in dates:
            old = compute_adv20(grouped["C"], "C", trade_date, lookback=20, min_obs=10, amount_multiplier=1.0)
            new = idx.get("C", trade_date)
            assert new.is_valid == old.is_valid, f"date={trade_date}"
            assert new.observations == old.observations, f"date={trade_date}"
            if old.is_valid:
                assert new.adv_value == pytest.approx(old.adv_value, rel=1e-12), f"date={trade_date}"

    def test_missing_code(self):
        """Code not in index -> invalid."""
        grouped = self._build_grouped({"A": (["20240101"], [1000.0])})
        idx = ADVIndex(grouped, lookback=20, min_obs=10, amount_multiplier=1.0)
        result = idx.get("UNKNOWN", "20240102")
        assert result.is_valid is False
        assert result.observations == 0

    def test_execution_date_not_in_data(self):
        """Execution date not present in code's dates (e.g. holiday)."""
        dates = ["20240101", "20240102", "20240103", "20240105"]  # 04 missing
        amounts = [100.0, 200.0, 300.0, 400.0]
        grouped = self._build_grouped({"D": (dates, amounts)})
        idx = ADVIndex(grouped, lookback=20, min_obs=2, amount_multiplier=1.0)

        # Execute on 20240104 (not in data): should use data up to 20240103
        old = compute_adv20(grouped["D"], "D", "20240104", lookback=20, min_obs=2, amount_multiplier=1.0)
        new = idx.get("D", "20240104")
        assert new.is_valid == old.is_valid
        assert new.observations == old.observations
        if old.is_valid:
            assert new.adv_value == pytest.approx(old.adv_value, rel=1e-12)

    def test_amount_multiplier(self):
        """amount_multiplier=1000 (Tushare 千元 -> 元)."""
        dates = [f"202401{d:02d}" for d in range(1, 15)]
        amounts = [1.0] * 14  # 1 千元
        grouped = self._build_grouped({"E": (dates, amounts)})
        idx = ADVIndex(grouped, lookback=20, min_obs=10, amount_multiplier=1000.0)

        old = compute_adv20(grouped["E"], "E", "20240115", lookback=20, min_obs=10, amount_multiplier=1000.0)
        new = idx.get("E", "20240115")
        assert new.is_valid is True
        assert new.adv_value == pytest.approx(1000.0)  # 1 * 1000
        assert new.adv_value == pytest.approx(old.adv_value, rel=1e-12)
