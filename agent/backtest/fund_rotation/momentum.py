"""Cluster momentum and target weight construction — §10.

Momentum: equal-weight virtual index, compound over window.
Selection: Top-N with absolute threshold, fixed slots.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_cluster_momentum(
    weekly_returns: pd.DataFrame,
    clusters: dict[str, int],
    momentum_window: int,
) -> dict[int, float]:
    """§10.1 — Compute compound momentum for each cluster.

    cluster_return[t] = mean(valid member returns at t)
    cluster_momentum = product(1 + cluster_return[t]) - 1

    Args:
        weekly_returns: Wide DataFrame (rows=weeks, cols=ts_codes).
        clusters: ts_code -> cluster_id mapping.
        momentum_window: Number of trailing weeks to use.

    Returns:
        Dict cluster_id -> momentum value.
    """
    # Use only the last momentum_window weeks
    recent = weekly_returns.iloc[-momentum_window:] if len(weekly_returns) >= momentum_window else weekly_returns

    # Group members by cluster
    cluster_ids = sorted(set(clusters.values()))
    result: dict[int, float] = {}

    for cid in cluster_ids:
        members = [code for code, c in clusters.items() if c == cid and code in recent.columns]
        if not members:
            result[cid] = np.nan
            continue

        # Equal-weight mean per week (ignoring NaN)
        member_returns = recent[members]
        weekly_mean = member_returns.mean(axis=1, skipna=True)

        # If any week has NO valid members (all NaN), the cluster lacks
        # comparable momentum for this period -> mark as invalid (NaN).
        if weekly_mean.isna().any():
            result[cid] = np.nan
            continue

        # Compound: product(1 + r) - 1
        momentum = float(np.prod(1.0 + weekly_mean.values) - 1.0)
        result[cid] = momentum

    return result


def select_top_clusters(
    momentum: dict[int, float],
    top_n: int,
    threshold: float,
    cluster_members: dict[int, list[str]] | None = None,
) -> list[int]:
    """§10.2 — Select top-N clusters by momentum with absolute threshold.

    Only clusters with momentum strictly > threshold qualify.
    Ties broken by minimum ts_code in cluster (smaller wins).

    Args:
        momentum: cluster_id -> momentum value.
        top_n: Maximum number of clusters to select.
        threshold: Absolute momentum threshold (strict >).
        cluster_members: Optional cluster_id -> member codes for tie-breaking.

    Returns:
        List of selected cluster_ids in descending momentum order.
    """
    # Filter by threshold (strict >)
    qualified = {cid: mom for cid, mom in momentum.items() if mom > threshold and not np.isnan(mom)}

    if not qualified:
        return []

    # Sort by (-momentum, min_ts_code) for deterministic ordering
    def sort_key(item: tuple[int, float]) -> tuple[float, str]:
        cid, mom = item
        min_code = ""
        if cluster_members and cid in cluster_members:
            members = cluster_members[cid]
            min_code = min(members) if members else ""
        return (-mom, min_code)

    sorted_clusters = sorted(qualified.items(), key=sort_key)
    return [cid for cid, _ in sorted_clusters[:top_n]]


def build_target_weights(
    selected_clusters: list[int],
    cluster_members: dict[int, list[str]],
    top_n: int,
) -> dict[str, float]:
    """§10.2 — Build ETF-level target weights from selected clusters.

    Each selected cluster gets 1/top_n of the portfolio.
    Within each cluster, members are equal-weighted.
    Unallocated weight is implicit cash.

    Args:
        selected_clusters: List of selected cluster IDs.
        cluster_members: cluster_id -> list of eligible member ts_codes.
        top_n: Total number of slots (fixed denominator).

    Returns:
        Dict ts_code -> target_weight. Empty dict if no clusters selected.
    """
    if not selected_clusters:
        return {}

    weights: dict[str, float] = {}
    slot_weight = 1.0 / top_n

    for cid in selected_clusters:
        members = cluster_members.get(cid, [])
        if not members:
            continue
        member_weight = slot_weight / len(members)
        for code in members:
            weights[code] = weights.get(code, 0.0) + member_weight

    return weights
