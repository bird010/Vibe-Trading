"""Cross-period aggregation: HAC standard error, moving block bootstrap, quality gate.

Implements design §14, §15, and §27.6.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from backtest.stockpred.cohort.contracts import CohortResult, CohortStatus


# ---------------------------------------------------------------------------
# HAC (Newey-West) standard error
# ---------------------------------------------------------------------------


def hac_lag(*, holding_days: int, eval_step: int) -> int:
    """Compute HAC lag per §14: max(ceil(holding_days / eval_step) - 1, 0)."""
    return max(math.ceil(holding_days / max(eval_step, 1)) - 1, 0)


def hac_standard_error(returns: np.ndarray, *, lag: int) -> float:
    """Newey-West HAC standard error of the mean per §27.6.

    LRV = gamma_0 + 2 * sum(w_k * gamma_k, k=1..L)
    w_k = 1 - k / (L + 1)
    SE = sqrt(LRV / n)
    gamma_k uses fixed denominator n.
    LRV truncated to non-negative.
    """
    n = len(returns)
    if n == 0:
        return 0.0
    if n == 1:
        return 0.0

    x = np.asarray(returns, dtype=float)
    mean = np.mean(x)
    demeaned = x - mean

    # gamma_0 (variance with fixed denominator n)
    gamma_0 = float(np.sum(demeaned**2) / n)

    if lag <= 0:
        lrv = gamma_0
    else:
        lrv = gamma_0
        for k in range(1, lag + 1):
            # Autocovariance at lag k
            gamma_k = float(np.sum(demeaned[k:] * demeaned[:-k]) / n)
            # Bartlett kernel weight
            w_k = 1.0 - k / (lag + 1.0)
            lrv += 2.0 * w_k * gamma_k

    # Truncate to non-negative
    lrv = max(lrv, 0.0)

    return math.sqrt(lrv / n)


# ---------------------------------------------------------------------------
# Moving Block Bootstrap
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BootstrapCI:
    """Bootstrap confidence interval."""

    lower: float
    upper: float
    mean: float
    confidence_level: float = 0.95
    resamples: int = 2000
    block_length: int = 2


def bootstrap_block_length(holding_days: int, eval_step: int) -> int:
    """Block length = max(2, ceil(holding_days / eval_step))."""
    return max(2, math.ceil(holding_days / max(eval_step, 1)))


def moving_block_bootstrap(
    returns: np.ndarray,
    *,
    block_length: int,
    seed: int,
    resamples: int = 2000,
    confidence_level: float = 0.95,
) -> BootstrapCI:
    """Moving block bootstrap for mean confidence interval per §27.6.

    Deterministic given seed. CI = percentile method.
    """
    x = np.asarray(returns, dtype=float)
    n = len(x)
    sample_mean = float(np.mean(x)) if n > 0 else 0.0

    if n <= block_length or n < 3:
        # Too few observations for meaningful bootstrap
        return BootstrapCI(
            lower=sample_mean,
            upper=sample_mean,
            mean=sample_mean,
            confidence_level=confidence_level,
            resamples=resamples,
            block_length=block_length,
        )

    rng = np.random.default_rng(seed)
    n_blocks = math.ceil(n / block_length)
    bootstrap_means = np.empty(resamples)

    for i in range(resamples):
        # Sample random block start indices
        starts = rng.integers(0, n - block_length + 1, size=n_blocks)
        # Build resampled series from blocks
        blocks = [x[s : s + block_length] for s in starts]
        resampled = np.concatenate(blocks)[:n]
        bootstrap_means[i] = np.mean(resampled)

    alpha = 1.0 - confidence_level
    lower = float(np.percentile(bootstrap_means, 100 * alpha / 2))
    upper = float(np.percentile(bootstrap_means, 100 * (1 - alpha / 2)))

    return BootstrapCI(
        lower=lower,
        upper=upper,
        mean=sample_mean,
        confidence_level=confidence_level,
        resamples=resamples,
        block_length=block_length,
    )


# ---------------------------------------------------------------------------
# Cross-period aggregator and quality gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AggregateMetrics:
    """Aggregated metrics across all cohorts per §14."""

    mean_return: float
    median_return: float
    std_return: float
    win_rate: float
    p5: float
    p25: float
    p75: float
    p95: float
    mean_excess_return: float
    positive_excess_ratio: float
    mean_fill_rate: float
    mean_idle_cash_ratio: float
    mean_cost_ratio: float
    mean_unliquidated_ratio: float
    valid_cohort_count: int
    total_cohort_count: int
    hac_se: float
    bootstrap_ci: BootstrapCI | None
    # Signal stability diagnostics per §27.3
    adjacent_cohort_overlap_ratio: float = 0.0
    symbol_selection_frequency: float = 0.0


@dataclass(frozen=True)
class QualityReport:
    """Quality gate report per §15."""

    ranking_eligible: bool
    valid_eval_ratio: float
    failures: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AggregationResult:
    """Complete aggregation output."""

    metrics: AggregateMetrics
    quality: QualityReport


# Default quality gate thresholds per §15
_DEFAULT_QUALITY_GATE = {
    "min_valid_eval_ratio": 0.95,
    "min_cohort_count_base": 30,
    "max_data_failure_ratio": 0.05,
    "max_rejected_target_value_ratio": 0.30,
    "max_unliquidated_cohort_ratio": 0.05,
    "max_stale_valuation_ratio": 0.02,
}


def aggregate_cohorts(
    results: list[CohortResult],
    *,
    holding_days: int,
    eval_step: int,
    evaluation_protocol_key: str,
    quality_gate: dict[str, Any] | None = None,
    selected_codes_per_cohort: list[list[str]] | None = None,
) -> AggregationResult:
    """Aggregate cohort results into cross-period statistics per §14 and §15."""
    gate = {**_DEFAULT_QUALITY_GATE, **(quality_gate or {})}
    total = len(results)

    # Separate valid and failed cohorts
    valid = [r for r in results if r.status not in (CohortStatus.FAILED_DATA, CohortStatus.FAILED_EXECUTION)]
    failed_count = total - len(valid)

    returns = np.array([r.committed_capital_return for r in valid], dtype=float)
    n = len(returns)

    if n == 0:
        empty_metrics = AggregateMetrics(
            mean_return=0.0, median_return=0.0, std_return=0.0, win_rate=0.0,
            p5=0.0, p25=0.0, p75=0.0, p95=0.0,
            mean_excess_return=0.0, positive_excess_ratio=0.0,
            mean_fill_rate=0.0, mean_idle_cash_ratio=1.0, mean_cost_ratio=0.0,
            mean_unliquidated_ratio=0.0, valid_cohort_count=0, total_cohort_count=total,
            hac_se=0.0, bootstrap_ci=None,
        )
        quality = QualityReport(ranking_eligible=False, valid_eval_ratio=0.0, failures=["no_valid_cohorts"])
        return AggregationResult(metrics=empty_metrics, quality=quality)

    # Basic statistics
    mean_ret = float(np.mean(returns))
    median_ret = float(np.median(returns))
    std_ret = float(np.std(returns, ddof=1)) if n > 1 else 0.0
    win_rate = float(np.sum(returns > 0) / n)

    p5, p25, p75, p95 = np.percentile(returns, [5, 25, 75, 95]).tolist()

    # Excess returns
    excess = np.array([r.target_horizon_excess_return for r in valid])
    mean_excess = float(np.mean(excess))
    pos_excess_ratio = float(np.sum(excess > 0) / n)

    # Execution metrics
    mean_fill = float(np.mean([r.fill_rate for r in valid]))
    mean_idle = float(np.mean([r.idle_cash_ratio for r in valid]))
    mean_cost = float(np.mean([r.cost_ratio for r in valid]))
    mean_unliq = float(np.mean([r.unliquidated_ratio for r in valid]))

    # HAC
    lag = hac_lag(holding_days=holding_days, eval_step=eval_step)
    hac_se = hac_standard_error(returns, lag=lag)

    # Bootstrap
    seed = int(hashlib.sha256((evaluation_protocol_key + "bootstrap").encode()).hexdigest()[:8], 16)
    block_len = bootstrap_block_length(holding_days, eval_step)
    bootstrap_ci = moving_block_bootstrap(returns, block_length=block_len, seed=seed) if n > block_len else None

    # Signal stability diagnostics per §27.3
    overlap_ratio = 0.0
    selection_freq = 0.0
    if selected_codes_per_cohort and len(selected_codes_per_cohort) >= 2:
        overlaps = []
        for i in range(1, len(selected_codes_per_cohort)):
            prev_set = set(selected_codes_per_cohort[i - 1])
            curr_set = set(selected_codes_per_cohort[i])
            if prev_set or curr_set:
                union = prev_set | curr_set
                inter = prev_set & curr_set
                overlaps.append(len(inter) / len(union) if union else 0.0)
        overlap_ratio = float(np.mean(overlaps)) if overlaps else 0.0
        # Selection frequency: how often each symbol appears across all cohorts
        all_symbols: dict[str, int] = {}
        for codes in selected_codes_per_cohort:
            for c in codes:
                all_symbols[c] = all_symbols.get(c, 0) + 1
        total_cohorts = len(selected_codes_per_cohort)
        if all_symbols:
            selection_freq = float(np.mean([v / total_cohorts for v in all_symbols.values()]))

    metrics = AggregateMetrics(
        mean_return=mean_ret, median_return=median_ret, std_return=std_ret,
        win_rate=win_rate, p5=p5, p25=p25, p75=p75, p95=p95,
        mean_excess_return=mean_excess, positive_excess_ratio=pos_excess_ratio,
        mean_fill_rate=mean_fill, mean_idle_cash_ratio=mean_idle,
        mean_cost_ratio=mean_cost, mean_unliquidated_ratio=mean_unliq,
        valid_cohort_count=n, total_cohort_count=total,
        hac_se=hac_se, bootstrap_ci=bootstrap_ci,
        adjacent_cohort_overlap_ratio=overlap_ratio,
        symbol_selection_frequency=selection_freq,
    )

    # Quality gate
    valid_ratio = n / total if total > 0 else 0.0
    data_failure_ratio = failed_count / total if total > 0 else 0.0
    rejected_ratio = 1.0 - mean_fill  # approximation: unfilled portion
    unliq_ratio = float(np.mean([1.0 if r.status == CohortStatus.UNLIQUIDATED else 0.0 for r in valid])) if valid else 0.0
    stale_ratio = float(np.mean([1.0 if r.uses_stale_valuation else 0.0 for r in valid])) if valid else 0.0

    failures: list[str] = []
    if valid_ratio < gate["min_valid_eval_ratio"]:
        failures.append("min_valid_eval_ratio")
    # Dynamic min_cohort_count: max(30, ceil(244 / eval_step)) per §15
    min_cohorts = max(gate["min_cohort_count_base"], math.ceil(244 / max(eval_step, 1)))
    if n < min_cohorts:
        failures.append("min_cohort_count")
    if data_failure_ratio > gate["max_data_failure_ratio"]:
        failures.append("max_data_failure_ratio")
    if rejected_ratio > gate["max_rejected_target_value_ratio"]:
        failures.append("max_rejected_target_value_ratio")
    if unliq_ratio > gate["max_unliquidated_cohort_ratio"]:
        failures.append("max_unliquidated_cohort_ratio")
    if stale_ratio > gate["max_stale_valuation_ratio"]:
        failures.append("max_stale_valuation_ratio")

    quality = QualityReport(
        ranking_eligible=len(failures) == 0,
        valid_eval_ratio=valid_ratio,
        failures=failures,
    )

    return AggregationResult(metrics=metrics, quality=quality)
