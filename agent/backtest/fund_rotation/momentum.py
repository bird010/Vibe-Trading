"""Cluster momentum and target weight construction — §10.

Momentum: equal-weight virtual index, compound over window.
Selection: Top-N with absolute threshold, fixed slots.
"""

from __future__ import annotations

import math

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
        Dict cluster_id -> momentum value. A non-finite value means the
        cluster has no comparable momentum over the complete window.
    """
    recent = (
        weekly_returns.iloc[-momentum_window:]
        if len(weekly_returns) >= momentum_window
        else weekly_returns
    )

    cluster_ids = sorted(set(clusters.values()))
    result: dict[int, float] = {}

    for cid in cluster_ids:
        members = [
            code
            for code, cluster_id in clusters.items()
            if cluster_id == cid and code in recent.columns
        ]
        if not members:
            result[cid] = np.nan
            continue

        member_returns = recent[members]
        weekly_mean = member_returns.mean(axis=1, skipna=True)

        # A cluster is not comparable when any week in the requested window
        # has no valid member return. Keep NaN as an internal calculation
        # sentinel; strategy diagnostics must translate it to explicit null.
        if weekly_mean.isna().any():
            result[cid] = np.nan
            continue

        result[cid] = float(np.prod(1.0 + weekly_mean.values) - 1.0)

    return result


def select_top_clusters(
    momentum: dict[int, float],
    top_n: int,
    threshold: float,
    cluster_members: dict[int, list[str]] | None = None,
) -> list[int]:
    """§10.2 — Select top-N clusters by momentum with absolute threshold.

    Only finite clusters with momentum strictly above ``threshold`` qualify.
    Ties are broken by the minimum ts_code in each cluster.
    """
    qualified: dict[int, float] = {}
    for cluster_id, raw_momentum in momentum.items():
        if (
            isinstance(raw_momentum, bool)
            or not isinstance(
                raw_momentum,
                (int, float, np.integer, np.floating),
            )
        ):
            raise TypeError(
                f"momentum for cluster {cluster_id} must be numeric, "
                f"got {type(raw_momentum).__name__}"
            )
        value = float(raw_momentum)
        if math.isfinite(value) and value > threshold:
            qualified[cluster_id] = value

    if not qualified:
        return []

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
