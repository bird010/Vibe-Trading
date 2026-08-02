"""Correlation distance and iterative pairwise exclusion — §9.2-9.3.

Distance: sqrt(2 * (1 - corr(i,j)))
Exclusion: iteratively remove ETF with most invalid pairs until complete.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.fund_rotation.universe import ExclusionReason, ExclusionRecord


def compute_correlation_distance(
    weekly_returns: pd.DataFrame,
    min_pairwise_weeks: int,
) -> pd.DataFrame:
    """§9.2 — Compute pairwise correlation distance matrix.

    distance(i, j) = sqrt(2 * (1 - corr(i, j)))

    Pairs with fewer than min_pairwise_weeks common valid observations
    get NaN distance.

    Args:
        weekly_returns: Wide DataFrame, rows=weeks, cols=ts_codes.
        min_pairwise_weeks: Minimum common valid weeks for a pair.

    Returns:
        Square DataFrame distance matrix (index=columns=ts_codes).
    """
    codes = list(weekly_returns.columns)
    n = len(codes)

    if n == 0:
        return pd.DataFrame()

    # Vectorized: compute pairwise common observation counts
    # notna_mask: (weeks x codes) boolean
    notna_mask = weekly_returns.notna().values.astype(np.float64)
    # common_count[i,j] = number of weeks where both i and j have valid data
    common_count = notna_mask.T @ notna_mask  # (codes x codes)

    # Vectorized: compute full correlation matrix via pandas
    # pandas .corr() handles NaN pairwise (uses common valid obs)
    corr_matrix = weekly_returns.corr().values  # (codes x codes)

    # Build distance matrix
    # Where common_count < min_pairwise_weeks -> NaN
    # Where corr is not finite (constant series) -> NaN
    # Otherwise: sqrt(2 * (1 - corr))
    with np.errstate(invalid="ignore"):
        dist_values = np.sqrt(2.0 * (1.0 - corr_matrix))

    # Mask invalid pairs
    invalid_mask = (common_count < min_pairwise_weeks) | (~np.isfinite(corr_matrix))
    dist_values[invalid_mask] = np.nan

    # Diagonal is always 0
    np.fill_diagonal(dist_values, 0.0)

    return pd.DataFrame(dist_values, index=codes, columns=codes)


def iterative_exclude(
    distance: pd.DataFrame,
    k: int,
) -> tuple[list[str], list[ExclusionRecord]]:
    """§9.3 — Iteratively remove ETFs with most invalid pairs.

    O(n²) incremental algorithm:
    1. Compute NaN count per ETF from the full matrix (vectorized).
    2. Remove the ETF with the most invalid pairs.
    3. Tie-break: remove lexicographically largest ts_code.
    4. Incrementally update NaN counts (subtract removed row's connections).
    5. Repeat until no NaN pairs remain.
    6. If remaining < k, raise ValueError.

    Args:
        distance: Square distance matrix (may contain NaN).
        k: Minimum required ETFs.

    Returns:
        (kept_codes, exclusion_records)

    Raises:
        ValueError: If remaining ETFs < k after exclusion.
    """
    codes = list(distance.index)
    n = len(codes)
    code_to_idx = {c: i for i, c in enumerate(codes)}

    # Work with numpy for speed
    dist_arr = distance.values  # (n x n)
    nan_mask = np.isnan(dist_arr)
    np.fill_diagonal(nan_mask, False)

    # Active set and per-row NaN counts
    active = np.ones(n, dtype=bool)
    nan_counts = nan_mask.sum(axis=1).astype(np.int64)

    excluded: list[ExclusionRecord] = []

    while True:
        # Max NaN count among active codes
        masked_counts = np.where(active, nan_counts, -1)
        max_count = int(masked_counts.max())
        if max_count <= 0:
            break

        # Tie-break: lexicographically largest code among those with max_count
        candidates_idx = np.where(active & (nan_counts == max_count))[0]
        candidate_codes = [codes[i] for i in candidates_idx]
        to_remove_code = max(candidate_codes)
        to_remove_idx = code_to_idx[to_remove_code]

        # Deactivate and update counts incrementally
        active[to_remove_idx] = False
        # For each still-active code that had NaN with removed code, decrement
        removed_connections = nan_mask[to_remove_idx] & active
        nan_counts[active] -= removed_connections[active].astype(np.int64)

        excluded.append(ExclusionRecord(
            ts_code=to_remove_code,
            reason=ExclusionReason.PAIRWISE_EXCLUSION,
            details=f"invalid_pairs={max_count}",
        ))

        active_count = int(active.sum())
        if active_count < k:
            raise ValueError(
                f"Fewer than k={k} ETFs remain after pairwise exclusion "
                f"({active_count} left)"
            )

    kept_codes = sorted([codes[i] for i in range(n) if active[i]])
    return kept_codes, excluded
