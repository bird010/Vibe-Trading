"""Tests for FundRotationConfig — §2 baseline parameters."""

import pytest

from backtest.fund_rotation.config import FundRotationConfig


class TestDefaults:
    """Verify all §2 baseline parameter defaults."""

    def test_k_default(self):
        cfg = FundRotationConfig()
        assert cfg.k == 8

    def test_top_n_default(self):
        cfg = FundRotationConfig()
        assert cfg.top_n == 3

    def test_momentum_window_default(self):
        cfg = FundRotationConfig()
        assert cfg.momentum_window_weeks == 4

    def test_rebalance_freq_default(self):
        cfg = FundRotationConfig()
        assert cfg.rebalance_freq == "W"

    def test_recluster_interval_default(self):
        cfg = FundRotationConfig()
        assert cfg.recluster_interval_weeks == 26

    def test_correlation_lookback_default(self):
        cfg = FundRotationConfig()
        assert cfg.correlation_lookback_weeks == 52

    def test_min_training_weeks_default(self):
        cfg = FundRotationConfig()
        assert cfg.min_training_weeks == 52

    def test_min_valid_weeks_default(self):
        cfg = FundRotationConfig()
        assert cfg.min_valid_weeks == 20

    def test_min_pairwise_weeks_default(self):
        cfg = FundRotationConfig()
        assert cfg.min_pairwise_weeks == 20

    def test_initial_capital_default(self):
        cfg = FundRotationConfig()
        assert cfg.initial_capital == 1_000_000.0

    def test_momentum_threshold_default(self):
        cfg = FundRotationConfig()
        assert cfg.momentum_threshold == 0.0

    def test_commission_rate_default(self):
        cfg = FundRotationConfig()
        assert cfg.commission_rate == 0.00025

    def test_commission_min_default(self):
        cfg = FundRotationConfig()
        assert cfg.commission_min == 5.0

    def test_max_participation_rate_default(self):
        cfg = FundRotationConfig()
        assert cfg.max_participation_rate == 0.05

    def test_adv_lookback_default(self):
        cfg = FundRotationConfig()
        assert cfg.adv_lookback == 20

    def test_adv_min_observations_default(self):
        cfg = FundRotationConfig()
        assert cfg.adv_min_observations == 10

    def test_base_slippage_bps_default(self):
        cfg = FundRotationConfig()
        assert cfg.base_slippage_bps == 5.0

    def test_max_slippage_bps_default(self):
        cfg = FundRotationConfig()
        assert cfg.max_slippage_bps == 30.0

    def test_lot_size_default(self):
        cfg = FundRotationConfig()
        assert cfg.lot_size == 100


class TestCustomValues:
    """Config accepts overrides for all parameters."""

    def test_custom_k(self):
        cfg = FundRotationConfig(k=5)
        assert cfg.k == 5

    def test_custom_dates(self):
        cfg = FundRotationConfig(start_date="2020-01-01", end_date="2025-12-31")
        assert cfg.start_date == "2020-01-01"
        assert cfg.end_date == "2025-12-31"

    def test_custom_capital(self):
        cfg = FundRotationConfig(initial_capital=10_000_000.0)
        assert cfg.initial_capital == 10_000_000.0


class TestValidation:
    """Config rejects invalid parameter combinations."""

    def test_k_less_than_1_raises(self):
        with pytest.raises(ValueError, match="k"):
            FundRotationConfig(k=0)

    def test_top_n_greater_than_k_raises(self):
        with pytest.raises(ValueError, match="top_n"):
            FundRotationConfig(k=3, top_n=5)

    def test_negative_capital_raises(self):
        with pytest.raises(ValueError, match="initial_capital"):
            FundRotationConfig(initial_capital=-100)

    def test_zero_momentum_window_raises(self):
        with pytest.raises(ValueError, match="momentum_window"):
            FundRotationConfig(momentum_window_weeks=0)

    def test_min_pairwise_greater_than_correlation_lookback_raises(self):
        with pytest.raises(ValueError, match="min_pairwise_weeks"):
            FundRotationConfig(correlation_lookback_weeks=10, min_pairwise_weeks=15)

    def test_start_after_end_raises(self):
        with pytest.raises(ValueError, match="start_date"):
            FundRotationConfig(start_date="2025-01-01", end_date="2020-01-01")


class TestImmutability:
    """Config should be frozen (immutable)."""

    def test_frozen(self):
        cfg = FundRotationConfig()
        with pytest.raises(AttributeError):
            cfg.k = 10  # type: ignore[misc]
