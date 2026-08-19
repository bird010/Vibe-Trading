"""Robustness and stability metrics — §14.3.

ARI/NMI cluster stability, time-block bootstrap.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score


def compute_cluster_stability(
    cluster_history: list[dict],
) -> dict[str, float]:
    """§14.2 — ARI and NMI between consecutive reclustering events.

    Args:
        cluster_history: List of {"week": str, "clusters": {code: cluster_id}}.

    Returns:
        Dict with mean_ari, mean_nmi, and per-pair values.
    """
    if len(cluster_history) < 2:
        return {"mean_ari": 0.0, "mean_nmi": 0.0, "num_comparisons": 0}

    ari_values = []
    nmi_values = []

    for i in range(1, len(cluster_history)):
        prev_clusters = cluster_history[i - 1]["clusters"]
        curr_clusters = cluster_history[i]["clusters"]

        # Common codes between consecutive reclusters
        common_codes = sorted(set(prev_clusters.keys()) & set(curr_clusters.keys()))
        if len(common_codes) < 2:
            continue

        labels_prev = [prev_clusters[c] for c in common_codes]
        labels_curr = [curr_clusters[c] for c in common_codes]

        ari_values.append(adjusted_rand_score(labels_prev, labels_curr))
        nmi_values.append(normalized_mutual_info_score(labels_prev, labels_curr))

    return {
        "mean_ari": float(np.mean(ari_values)) if ari_values else 0.0,
        "mean_nmi": float(np.mean(nmi_values)) if nmi_values else 0.0,
        "num_comparisons": len(ari_values),
        "ari_values": ari_values,
        "nmi_values": nmi_values,
    }


def compute_intra_cluster_correlation(
    cluster_history: list[dict],
    correlation_matrix: pd.DataFrame | None = None,
) -> dict[str, float]:
    """§14.2 — Mean intra-cluster correlation at last recluster.

    Args:
        cluster_history: List of cluster snapshots.
        correlation_matrix: Optional precomputed correlation matrix.

    Returns:
        Dict with mean_intra_corr and per-cluster values.
    """
    if not cluster_history or correlation_matrix is None:
        return {"mean_intra_corr": 0.0}

    last = cluster_history[-1]["clusters"]
    # Group by cluster
    clusters: dict[int, list[str]] = {}
    for code, cid in last.items():
        clusters.setdefault(cid, []).append(code)

    cluster_corrs = {}
    all_corrs = []
    for cid, members in sorted(clusters.items()):
        if len(members) < 2:
            continue
        pairs = []
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                if a in correlation_matrix.index and b in correlation_matrix.columns:
                    pairs.append(float(correlation_matrix.loc[a, b]))
        if pairs:
            mean_corr = float(np.mean(pairs))
            cluster_corrs[str(cid)] = mean_corr
            all_corrs.extend(pairs)

    return {
        "mean_intra_corr": float(np.mean(all_corrs)) if all_corrs else 0.0,
        "per_cluster": cluster_corrs,
    }


def time_block_bootstrap(
    weekly_returns: pd.Series,
    block_size: int = 12,
    n_bootstrap: int = 100,
    seed: int = 42,
) -> dict[str, float]:
    """§14.3 — Block bootstrap preserving local time correlation.

    Args:
        weekly_returns: Series of weekly returns.
        block_size: Number of consecutive weeks per block.
        n_bootstrap: Number of bootstrap samples.
        seed: Random seed for reproducibility.

    Returns:
        Dict with bootstrap confidence intervals for key metrics.
    """
    returns = weekly_returns.dropna().values
    n = len(returns)
    if n < block_size * 2:
        return {"bootstrap_sharpe_mean": 0.0, "bootstrap_sharpe_ci_low": 0.0, "bootstrap_sharpe_ci_high": 0.0}

    rng = np.random.default_rng(seed)
    n_blocks = n // block_size
    sharpe_samples = []
    return_samples = []

    for _ in range(n_bootstrap):
        # Sample blocks with replacement
        block_starts = rng.integers(0, n - block_size + 1, size=n_blocks)
        sampled = np.concatenate([returns[s:s + block_size] for s in block_starts])

        # Compute metrics on sampled path
        cumulative = np.cumprod(1.0 + sampled)
        total_ret = cumulative[-1] - 1.0
        vol = float(np.std(sampled, ddof=1)) * np.sqrt(52)
        sharpe = (total_ret * 52 / len(sampled)) / vol if vol > 1e-12 else 0.0

        sharpe_samples.append(sharpe)
        return_samples.append(total_ret)

    sharpe_arr = np.array(sharpe_samples)
    return_arr = np.array(return_samples)

    return {
        "bootstrap_sharpe_mean": float(np.mean(sharpe_arr)),
        "bootstrap_sharpe_std": float(np.std(sharpe_arr)),
        "bootstrap_sharpe_ci_low": float(np.percentile(sharpe_arr, 2.5)),
        "bootstrap_sharpe_ci_high": float(np.percentile(sharpe_arr, 97.5)),
        "bootstrap_return_mean": float(np.mean(return_arr)),
        "bootstrap_return_ci_low": float(np.percentile(return_arr, 2.5)),
        "bootstrap_return_ci_high": float(np.percentile(return_arr, 97.5)),
        "n_bootstrap": n_bootstrap,
        "block_size": block_size,
    }
