from __future__ import annotations

import numpy as np
import pytest

from backtest.fund_rotation.champion_validation.statistical_validation import (
    compute_deflated_sharpe_ratio,
    run_reality_check_or_spa,
    time_block_bootstrap,
    validate_statistics,
)


def test_time_block_bootstrap_has_fixed_default_and_is_reproducible():
    returns = np.array([0.01, -0.005, 0.02, 0.0, 0.01, -0.002] * 4)

    first = time_block_bootstrap(returns, block_size=3, seed=17)
    second = time_block_bootstrap(returns, block_size=3, seed=17)

    assert first == second
    assert first["samples"] == 10000
    assert first["ci"]["cagr"]["lower"] <= first["ci"]["cagr"]["upper"]
    assert "mdd" in first["ci"]


def test_deflated_sharpe_ratio_validates_inputs_and_returns_probability():
    result = compute_deflated_sharpe_ratio(
        sharpe=2.0,
        observations=260,
        trial_count=30,
        skewness=0.0,
        kurtosis=3.0,
    )

    assert 0.0 <= result["probability"] <= 1.0
    assert result["trial_count"] == 30
    with pytest.raises(ValueError, match="trial_count"):
        compute_deflated_sharpe_ratio(1.0, observations=20, trial_count=0, skewness=0.0, kurtosis=3.0)


def test_reality_check_excludes_invalid_series_without_changing_fixed_trial_count():
    result = run_reality_check_or_spa(
        {
            "candidate-a": np.array([0.02, 0.01, 0.03, 0.02]),
            "candidate-b": np.array([0.01, 0.00, 0.02, 0.01]),
            "invalid": np.array([0.01, np.nan, 0.01, 0.01]),
        },
        trial_count=30,
        samples=200,
        block_size=2,
        seed=9,
    )

    assert result["valid_candidate_count"] == 2
    assert result["trial_count"] == 30
    assert "invalid" in result["excluded_candidates"]
    assert 0.0 <= result["p_value"] <= 1.0


def test_reality_check_excludes_length_mismatches_as_input_validation():
    result = run_reality_check_or_spa(
        {"valid": np.array([0.01, 0.02, 0.01]), "short": np.array([0.01, 0.02])},
        trial_count=30,
        samples=20,
        block_size=2,
        seed=1,
    )

    assert result["valid_candidate_count"] == 1
    assert result["excluded_candidates"]["short"] == "LENGTH_MISMATCH"


def test_validate_statistics_returns_three_state_outcomes():
    positive = np.array([0.02, 0.015, 0.025, 0.018, 0.022, 0.019] * 10)
    uncertain = np.array([0.03, -0.03, 0.02, -0.02, 0.01, 0.0] * 10)
    negative = np.full(60, -0.01)

    passed = validate_statistics(positive, benchmark_returns=np.zeros(60), samples=200, block_size=3, seed=4)
    maybe = validate_statistics(uncertain, benchmark_returns=np.zeros(60), samples=200, block_size=3, seed=4)
    failed = validate_statistics(negative, benchmark_returns=np.zeros(60), samples=200, block_size=3, seed=4)

    assert passed["status"] == "PASS"
    assert maybe["status"] == "INCONCLUSIVE"
    assert failed["status"] == "FAIL"
    assert {"bootstrap", "dsr", "p_value", "reason_codes"} <= set(passed)
