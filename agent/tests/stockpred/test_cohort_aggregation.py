"""Tests for HAC standard error and moving block bootstrap."""

from __future__ import annotations

import math

import numpy as np
import pytest

from backtest.stockpred.cohort.aggregation import (
    AggregateMetrics,
    BootstrapCI,
    QualityReport,
    aggregate_cohorts,
    bootstrap_block_length,
    hac_lag,
    hac_standard_error,
    moving_block_bootstrap,
)
from backtest.stockpred.cohort.contracts import CohortResult, CohortStatus


class TestHAC:
    def test_lag_formula_holding5_step5(self):
        # lag = max(ceil(5/5) - 1, 0) = 0
        assert hac_lag(holding_days=5, eval_step=5) == 0

    def test_lag_formula_holding10_step5(self):
        # lag = max(ceil(10/5) - 1, 0) = 1
        assert hac_lag(holding_days=10, eval_step=5) == 1

    def test_lag_formula_holding5_step1(self):
        # lag = max(ceil(5/1) - 1, 0) = 4
        assert hac_lag(holding_days=5, eval_step=1) == 4

    def test_lag_formula_holding1_step5(self):
        # lag = max(ceil(1/5) - 1, 0) = 0
        assert hac_lag(holding_days=1, eval_step=5) == 0

    def test_zero_lag_equals_simple_se(self):
        returns = np.array([0.01, -0.02, 0.03, 0.01, -0.01])
        se = hac_standard_error(returns, lag=0)
        simple_se = np.std(returns, ddof=0) / np.sqrt(len(returns))
        assert se == pytest.approx(simple_se, rel=1e-10)

    def test_positive_lag_larger_than_simple_for_autocorrelated(self):
        # Strongly autocorrelated series
        returns = np.array([0.05, 0.04, 0.03, 0.02, 0.01, -0.01, -0.02, -0.03, -0.04, -0.05])
        se_hac = hac_standard_error(returns, lag=2)
        se_simple = np.std(returns, ddof=0) / np.sqrt(len(returns))
        assert se_hac > se_simple

    def test_single_observation_returns_nan(self):
        returns = np.array([0.05])
        se = hac_standard_error(returns, lag=0)
        assert math.isnan(se) or se == 0.0

    def test_empty_returns(self):
        returns = np.array([])
        se = hac_standard_error(returns, lag=0)
        assert se == 0.0

    def test_variance_truncated_to_non_negative(self):
        # Alternating series can produce negative autocovariances
        returns = np.array([0.1, -0.1, 0.1, -0.1, 0.1, -0.1])
        se = hac_standard_error(returns, lag=3)
        assert se >= 0.0


class TestMovingBlockBootstrap:
    def test_ci_contains_mean(self):
        rng = np.random.default_rng(42)
        returns = rng.normal(0.01, 0.02, size=40)
        ci = moving_block_bootstrap(returns, block_length=2, seed=42, resamples=2000)
        assert ci.lower < np.mean(returns) < ci.upper

    def test_deterministic_with_same_seed(self):
        r = np.array([0.01, -0.02, 0.03, 0.01, -0.01, 0.02, 0.0, -0.03])
        ci1 = moving_block_bootstrap(r, block_length=2, seed=123)
        ci2 = moving_block_bootstrap(r, block_length=2, seed=123)
        assert ci1.lower == ci2.lower
        assert ci1.upper == ci2.upper

    def test_different_seed_different_result(self):
        r = np.array([0.01, -0.02, 0.03, 0.01, -0.01, 0.02, 0.0, -0.03] * 3)
        ci1 = moving_block_bootstrap(r, block_length=2, seed=1)
        ci2 = moving_block_bootstrap(r, block_length=2, seed=2)
        # Very unlikely to be exactly equal
        assert ci1.lower != ci2.lower or ci1.upper != ci2.upper

    def test_ci_width_reasonable(self):
        rng = np.random.default_rng(99)
        returns = rng.normal(0.0, 0.01, size=50)
        ci = moving_block_bootstrap(returns, block_length=3, seed=99, resamples=2000)
        # CI should be narrow for low-variance data
        assert ci.upper - ci.lower < 0.02

    def test_block_length_formula(self):
        # block_length = max(2, ceil(holding_days / eval_step))
        from backtest.stockpred.cohort.aggregation import bootstrap_block_length

        assert bootstrap_block_length(5, 5) == 2  # ceil(1)=1, max(2,1)=2
        assert bootstrap_block_length(10, 5) == 2  # ceil(2)=2
        assert bootstrap_block_length(15, 5) == 3  # ceil(3)=3
        assert bootstrap_block_length(5, 1) == 5  # ceil(5)=5

    def test_bootstrap_ci_fields(self):
        r = np.array([0.01, -0.02, 0.03, 0.01, -0.01, 0.02])
        ci = moving_block_bootstrap(r, block_length=2, seed=42)
        assert isinstance(ci, BootstrapCI)
        assert ci.confidence_level == pytest.approx(0.95)
        assert ci.resamples == 2000


# ---------------------------------------------------------------------------
# Cross-period aggregator and quality gate
# ---------------------------------------------------------------------------


def _cohort_result(ret: float, status: CohortStatus = CohortStatus.LIQUIDATED, **kwargs) -> CohortResult:
    defaults = dict(
        cohort_id="c",
        committed_capital_return=ret,
        executed_capital_return=ret * 1.1,
        raw_signal_return=ret * 1.2,
        horizon_mark_return=ret * 0.9,
        liquidation_return=ret,
        benchmark_return=0.01,
        target_horizon_excess_return=ret * 0.9 - 0.01,
        liquidation_policy_excess_return=ret - 0.01,
        fill_rate=0.95,
        idle_cash_ratio=0.05,
        cost_ratio=0.003,
        exit_delay_days=0,
        unliquidated_ratio=0.0,
        status=status,
    )
    defaults.update(kwargs)
    return CohortResult(**defaults)


class TestAggregator:
    def test_basic_statistics(self):
        results = [_cohort_result(r) for r in [0.01, 0.02, -0.01, 0.03, 0.0]]
        agg = aggregate_cohorts(results, holding_days=5, eval_step=5, evaluation_protocol_key="k" * 64)

        assert agg.metrics.mean_return == pytest.approx(np.mean([0.01, 0.02, -0.01, 0.03, 0.0]))
        assert agg.metrics.median_return == pytest.approx(np.median([0.01, 0.02, -0.01, 0.03, 0.0]))
        assert agg.metrics.win_rate == pytest.approx(3 / 5)  # 3 positive out of 5
        assert agg.metrics.valid_cohort_count == 5

    def test_percentiles(self):
        results = [_cohort_result(r) for r in np.linspace(-0.05, 0.05, 20)]
        agg = aggregate_cohorts(results, holding_days=5, eval_step=5, evaluation_protocol_key="k" * 64)

        assert agg.metrics.p5 < agg.metrics.p25 < agg.metrics.p75 < agg.metrics.p95

    def test_quality_gate_pass(self):
        results = [_cohort_result(0.01) for _ in range(50)]
        agg = aggregate_cohorts(results, holding_days=5, eval_step=5, evaluation_protocol_key="k" * 64)

        assert agg.quality.ranking_eligible

    def test_quality_gate_fail_insufficient_cohorts(self):
        results = [_cohort_result(0.01) for _ in range(10)]  # < min 30
        agg = aggregate_cohorts(results, holding_days=5, eval_step=5, evaluation_protocol_key="k" * 64)

        assert not agg.quality.ranking_eligible
        assert "min_cohort_count" in agg.quality.failures

    def test_quality_gate_fail_high_rejection(self):
        results = [_cohort_result(0.01, fill_rate=0.5) for _ in range(50)]
        agg = aggregate_cohorts(results, holding_days=5, eval_step=5, evaluation_protocol_key="k" * 64)

        assert not agg.quality.ranking_eligible
        assert "max_rejected_target_value_ratio" in agg.quality.failures

    def test_failed_cohorts_in_coverage_denominator(self):
        good = [_cohort_result(0.01) for _ in range(45)]
        failed = [_cohort_result(0.0, status=CohortStatus.FAILED_DATA) for _ in range(5)]
        agg = aggregate_cohorts(good + failed, holding_days=5, eval_step=5, evaluation_protocol_key="k" * 64)

        # Coverage = 45/50 = 0.9 < 0.95 threshold
        assert agg.quality.valid_eval_ratio == pytest.approx(0.9)
        assert not agg.quality.ranking_eligible

    def test_hac_se_included(self):
        results = [_cohort_result(r) for r in np.random.default_rng(1).normal(0.01, 0.02, 40)]
        agg = aggregate_cohorts(results, holding_days=5, eval_step=5, evaluation_protocol_key="k" * 64)

        assert agg.metrics.hac_se > 0

    def test_bootstrap_ci_included(self):
        results = [_cohort_result(r) for r in np.random.default_rng(2).normal(0.01, 0.02, 40)]
        agg = aggregate_cohorts(results, holding_days=5, eval_step=5, evaluation_protocol_key="k" * 64)

        assert agg.metrics.bootstrap_ci is not None
        assert agg.metrics.bootstrap_ci.lower < agg.metrics.bootstrap_ci.upper
