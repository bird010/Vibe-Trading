"""Reproducible statistical validation primitives for Champion validation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from statistics import NormalDist
import math
from typing import Any

import numpy as np

DEFAULT_BOOTSTRAP_SAMPLES = 10_000
DEFAULT_TRIAL_COUNT = 30
PASS = "PASS"
INCONCLUSIVE = "INCONCLUSIVE"
FAIL = "FAIL"


def time_block_bootstrap(
    returns: Sequence[float] | Any,
    benchmark_returns: Sequence[float] | Any | None = None,
    *,
    block_size: int = 12,
    samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    seed: int = 42,
    n_bootstrap: int | None = None,
    confidence: float = 0.95,
    periods_per_year: int = 52,
) -> dict[str, Any]:
    """Bootstrap complete paths while retaining contiguous time blocks."""

    if n_bootstrap is not None:
        samples = n_bootstrap
    if block_size <= 0 or samples <= 0 or periods_per_year <= 0:
        raise ValueError("block_size, samples and periods_per_year must be positive")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between 0 and 1")
    values = _finite_array(returns)
    benchmark = None if benchmark_returns is None else _finite_array(benchmark_returns)
    if benchmark is not None and len(benchmark) != len(values):
        raise ValueError("benchmark_returns must match returns length")
    if len(values) < 2:
        raise ValueError("at least two returns are required")
    rng = np.random.default_rng(seed)
    strategy_samples = {key: np.empty(samples, dtype=float) for key in ("cagr", "sharpe", "mdd")}
    excess_samples = np.empty(samples, dtype=float)
    for index in range(samples):
        sample_indices = _sample_indices(len(values), block_size, rng)
        path = values[sample_indices]
        metrics = _metrics(path, periods_per_year)
        for key in strategy_samples:
            strategy_samples[key][index] = metrics[key]
        if benchmark is not None:
            excess_samples[index] = _metrics(path - benchmark[sample_indices], periods_per_year)["cagr"]
        else:
            excess_samples[index] = metrics["cagr"]
    result = {
        "samples": int(samples),
        "n_bootstrap": int(samples),
        "block_size": int(block_size),
        "seed": int(seed),
        "ci": {key: _summary(values, confidence) for key, values in strategy_samples.items()},
        "point_estimates": _metrics(values, periods_per_year),
        "evidence": {
            "quantiles": {key: _quantiles(values, confidence) for key, values in strategy_samples.items()},
            "excess_return": _quantiles(excess_samples, confidence),
        },
    }
    result["ci"]["excess_return"] = _summary(excess_samples, confidence)
    return result


def compute_deflated_sharpe_ratio(
    sharpe: float,
    observations: int | None = None,
    trial_count: int = DEFAULT_TRIAL_COUNT,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
    benchmark_sharpe: float = 0.0,
    *,
    n_obs: int | None = None,
    n_trials: int | None = None,
) -> dict[str, float | int]:
    """Return the DSR probability using sample size, moments and trial count."""

    observations = observations if observations is not None else n_obs
    trial_count = trial_count if n_trials is None else n_trials
    if observations is None or observations < 2:
        raise ValueError("observations must be at least 2")
    if trial_count <= 0:
        raise ValueError("trial_count must be positive")
    values = (sharpe, skewness, kurtosis, benchmark_sharpe)
    if not all(math.isfinite(float(value)) for value in values):
        raise ValueError("DSR inputs must be finite")
    if kurtosis < 1.0:
        raise ValueError("kurtosis must use Pearson convention and be at least 1")
    normal = NormalDist()
    gamma = 0.5772156649015329
    if trial_count == 1:
        expected_max = 0.0
    else:
        z1 = normal.inv_cdf(1.0 - 1.0 / trial_count)
        z2 = normal.inv_cdf(1.0 - 1.0 / (trial_count * math.e))
        expected_max = (1.0 - gamma) * z1 + gamma * z2
    variance = (1.0 - skewness * sharpe + ((kurtosis - 1.0) / 4.0) * sharpe * sharpe) / (observations - 1)
    standard_error = math.sqrt(max(variance, 0.0))
    adjusted_sharpe = sharpe - benchmark_sharpe
    probability = 1.0 if standard_error == 0 and adjusted_sharpe > expected_max else 0.0 if standard_error == 0 else normal.cdf((adjusted_sharpe - expected_max) / standard_error)
    return {
        "probability": float(probability),
        "dsr_probability": float(probability),
        "expected_max_sharpe": float(expected_max),
        "standard_error": float(standard_error),
        "trial_count": int(trial_count),
        "observations": int(observations),
    }


def run_reality_check_or_spa(
    candidate_returns: Mapping[str, Sequence[float] | Any],
    benchmark_returns: Sequence[float] | Any | None = None,
    *,
    method: str = "SPA",
    trial_count: int = DEFAULT_TRIAL_COUNT,
    samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    block_size: int = 12,
    seed: int = 42,
) -> dict[str, Any]:
    """Run a block-bootstrap max-statistic Reality Check/SPA approximation."""

    if trial_count <= 0 or samples <= 0 or block_size <= 0:
        raise ValueError("trial_count, samples and block_size must be positive")
    if method.upper() not in {"SPA", "REALITY_CHECK", "WHITE_REALITY_CHECK"}:
        raise ValueError("method must be SPA or REALITY_CHECK")
    try:
        benchmark = None if benchmark_returns is None else _finite_array(benchmark_returns)
    except (TypeError, ValueError):
        return {
            "method": method.upper(),
            "p_value": 1.0,
            "observed_statistic": None,
            "valid_candidate_count": 0,
            "valid_candidates": [],
            "excluded_candidates": {},
            "trial_count": int(trial_count),
            "samples": int(samples),
            "reason_codes": ["INVALID_BENCHMARK_SERIES"],
        }
    valid: dict[str, np.ndarray] = {}
    excluded: dict[str, str] = {}
    expected_length = None if benchmark is None else len(benchmark)
    for name, series in candidate_returns.items():
        try:
            values = np.asarray(series, dtype=float).reshape(-1)
        except (TypeError, ValueError):
            excluded[str(name)] = "NON_NUMERIC_SERIES"
            continue
        if values.size < 2 or not np.all(np.isfinite(values)):
            excluded[str(name)] = "NON_FINITE_SERIES"
            continue
        if expected_length is None:
            expected_length = len(values)
        if len(values) != expected_length:
            excluded[str(name)] = "LENGTH_MISMATCH"
            continue
        valid[str(name)] = values if benchmark is None else values - benchmark
    if not valid:
        return {
            "method": method.upper(),
            "p_value": 1.0,
            "observed_statistic": None,
            "valid_candidate_count": 0,
            "valid_candidates": [],
            "excluded_candidates": excluded,
            "trial_count": int(trial_count),
            "samples": int(samples),
            "reason_codes": ["NO_VALID_CANDIDATE_SERIES"],
        }
    matrix = np.vstack(list(valid.values()))
    observed = float(np.max(np.mean(matrix, axis=1)))
    centered = matrix - np.mean(matrix, axis=1, keepdims=True)
    rng = np.random.default_rng(seed)
    exceedances = 0
    for _ in range(samples):
        indices = _sample_indices(matrix.shape[1], block_size, rng)
        statistic = float(np.max(np.mean(centered[:, indices], axis=1)))
        exceedances += int(statistic >= observed)
    p_value = (exceedances + 1.0) / (samples + 1.0)
    return {
        "method": method.upper(),
        "p_value": float(p_value),
        "observed_statistic": observed,
        "valid_candidate_count": len(valid),
        "valid_candidates": list(valid),
        "excluded_candidates": excluded,
        "trial_count": int(trial_count),
        "samples": int(samples),
        "seed": int(seed),
        "reason_codes": [],
    }


def validate_statistics(
    returns: Sequence[float] | Any,
    benchmark_returns: Sequence[float] | Any | None = None,
    *,
    candidate_returns: Mapping[str, Sequence[float] | Any] | None = None,
    trial_count: int = DEFAULT_TRIAL_COUNT,
    samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    block_size: int = 12,
    seed: int = 42,
    confidence: float = 0.95,
    dsr_threshold: float = 0.95,
    p_value_threshold: float = 0.10,
) -> dict[str, Any]:
    """Apply the frozen CI, DSR and Reality Check/SPA three-state gate."""

    try:
        strategy = _finite_array(returns)
        benchmark = np.zeros_like(strategy) if benchmark_returns is None else _finite_array(benchmark_returns)
        if len(strategy) != len(benchmark):
            raise ValueError("benchmark_returns must match returns length")
    except (TypeError, ValueError):
        return {"status": INCONCLUSIVE, "reason_codes": ["INVALID_RETURN_SERIES"]}
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between 0 and 1")
    excess = strategy - benchmark
    bootstrap = time_block_bootstrap(strategy, benchmark, block_size=block_size, samples=samples, seed=seed, confidence=confidence)
    observed = _metrics(excess, 52)
    centered = excess - np.mean(excess)
    skewness = float(np.mean(centered**3) / np.std(excess, ddof=0) ** 3) if np.std(excess) > 0 else 0.0
    kurtosis = float(np.mean(centered**4) / np.std(excess, ddof=0) ** 4) if np.std(excess) > 0 else 3.0
    dsr = compute_deflated_sharpe_ratio(
        observed["sharpe"],
        observations=len(excess),
        trial_count=trial_count,
        skewness=skewness,
        kurtosis=kurtosis,
    )
    candidates = candidate_returns or {"subject": strategy}
    reality = run_reality_check_or_spa(candidates, benchmark, trial_count=trial_count, samples=samples, block_size=block_size, seed=seed)
    lower = float(bootstrap["ci"]["excess_return"]["lower"])
    reason_codes: list[str] = []
    if observed["cagr"] <= 0:
        reason_codes.append("NON_POSITIVE_EXCESS_RETURN")
    if lower <= 0:
        reason_codes.append("EXCESS_RETURN_CI_NOT_ABOVE_ZERO")
    if dsr["probability"] < dsr_threshold:
        reason_codes.append("DSR_BELOW_THRESHOLD")
    if reality["p_value"] > p_value_threshold:
        reason_codes.append("REALITY_CHECK_P_ABOVE_THRESHOLD")
    if observed["cagr"] <= 0:
        status = FAIL
    elif not reason_codes:
        status = PASS
    else:
        status = INCONCLUSIVE
    return {
        "status": status,
        "reason_codes": reason_codes,
        "point_estimates": observed,
        "bootstrap": bootstrap,
        "dsr": dsr,
        "p_value": reality["p_value"],
        "reality_check": reality,
        "trial_count": int(trial_count),
        "confidence": float(confidence),
    }


def _finite_array(values: Any) -> np.ndarray:
    array = np.asarray(values, dtype=float).reshape(-1)
    if array.size == 0 or not np.all(np.isfinite(array)):
        raise ValueError("returns must be a non-empty finite series")
    return array


def _sample_indices(length: int, block_size: int, rng: np.random.Generator) -> np.ndarray:
    if length < 2:
        raise ValueError("at least two observations are required")
    actual_size = min(block_size, length)
    blocks = math.ceil(length / actual_size)
    starts = rng.integers(0, length - actual_size + 1, size=blocks)
    indices = np.concatenate([np.arange(start, start + actual_size) for start in starts])
    return indices[:length]


def _metrics(values: np.ndarray, periods_per_year: int) -> dict[str, float]:
    wealth = np.cumprod(1.0 + values)
    total_periods = len(values)
    cagr = float(wealth[-1] ** (periods_per_year / total_periods) - 1.0) if wealth[-1] > 0 else -1.0
    standard_deviation = float(np.std(values, ddof=1)) if len(values) > 1 else 0.0
    sharpe = float(np.mean(values) / standard_deviation * math.sqrt(periods_per_year)) if standard_deviation > 0 else 0.0
    drawdown = wealth / np.maximum.accumulate(wealth) - 1.0
    return {"cagr": cagr, "sharpe": sharpe, "mdd": float(np.min(drawdown))}


def _quantiles(values: np.ndarray, confidence: float = 0.95) -> dict[str, float]:
    alpha = (1.0 - confidence) * 100.0
    return {"lower": float(np.percentile(values, alpha / 2.0)), "upper": float(np.percentile(values, 100.0 - alpha / 2.0)), "mean": float(np.mean(values))}


def _summary(values: np.ndarray, confidence: float = 0.95) -> dict[str, float]:
    return _quantiles(values, confidence)
