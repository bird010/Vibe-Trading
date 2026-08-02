"""Hierarchical clustering — §9.4.

Average-linkage agglomerative clustering, cut at fixed K.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform


def hierarchical_cluster(
    distance: pd.DataFrame,
    k: int,
) -> dict[str, int]:
    """§9.4 — Average-linkage hierarchical clustering cut at K.

    Args:
        distance: Square distance matrix (index=columns=ts_codes).
            Must be complete (no NaN).
        k: Number of clusters.

    Returns:
        Dict mapping ts_code -> cluster_label (int, 1-based).
    """
    # Sort codes for deterministic ordering
    codes = sorted(distance.index.tolist())
    n = len(codes)

    if k >= n:
        # Each ETF in its own cluster
        return {code: i + 1 for i, code in enumerate(codes)}

    if k == 1:
        return {code: 1 for code in codes}

    # Reindex to sorted order
    mat = distance.loc[codes, codes].values

    # Convert to condensed form for scipy
    condensed = squareform(mat, checks=False)

    # Average linkage
    Z = linkage(condensed, method="average")

    # Cut at k clusters
    labels = fcluster(Z, t=k, criterion="maxclust")

    return {code: int(labels[i]) for i, code in enumerate(codes)}
