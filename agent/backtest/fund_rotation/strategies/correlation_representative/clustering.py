"""Internal clustering for the correlation_representative strategy.

Phase 3 Task 2 (design §8/§9). Clustering stays inside this strategy package
— the public layer never sees a clusterer. The pipeline is: correlation
distance → iterative pairwise exclusion (recorded) → average-linkage
hierarchical clustering → deterministic label normalization.

Average linkage has no stochastic component; label normalization (size
descending, then lexicographically smallest member) guarantees identical
input → identical output labels without any seed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import pandas as pd

from backtest.fund_rotation.clustering import hierarchical_cluster
from backtest.fund_rotation.correlation import (
    compute_correlation_distance,
    iterative_exclude,
)
from backtest.fund_rotation.universe import ExclusionRecord


@dataclass(frozen=True)
class ClusterOutcome:
    """One clustering pass: normalized labels plus the exclusion trail."""

    clusters: dict[str, int]
    kept_codes: tuple[str, ...]
    pairwise_excluded: tuple[ExclusionRecord, ...]
    distance: pd.DataFrame


def normalize_cluster_labels(clusters: Mapping[str, int]) -> dict[str, int]:
    """Relabel clusters deterministically: largest cluster first (label 1),
    ties broken by the lexicographically smallest member."""
    groups: dict[int, list[str]] = {}
    for code, label in clusters.items():
        groups.setdefault(label, []).append(code)
    ordered = sorted(groups.values(), key=lambda members: (-len(members), min(members)))
    return {
        code: index + 1
        for index, members in enumerate(ordered)
        for code in members
    }


def correlation_cluster(
    window_returns: pd.DataFrame,
    codes: Sequence[str],
    *,
    k: int,
    min_pairwise_weeks: int,
) -> ClusterOutcome:
    """Cluster ``codes`` over the PIT weekly-return window.

    Incomplete pairs are iteratively excluded (each exclusion recorded) until
    the remaining distance matrix is complete; raises ValueError when fewer
    than ``k`` codes survive.

    Codes absent from ``window_returns`` are dropped silently here: they are
    the upstream caller's responsibility (eligibility / min_valid_weeks gates
    record them before this function is invoked, mirroring the legacy
    pipeline).
    """
    window = window_returns[[c for c in codes if c in window_returns.columns]]
    distance = compute_correlation_distance(window, min_pairwise_weeks=min_pairwise_weeks)
    kept, excluded = iterative_exclude(distance, k=k)
    sub_distance = distance.loc[kept, kept]
    raw_labels = hierarchical_cluster(sub_distance, k=k)
    return ClusterOutcome(
        clusters=normalize_cluster_labels(raw_labels),
        kept_codes=tuple(sorted(kept)),
        pairwise_excluded=tuple(excluded),
        distance=distance,
    )
