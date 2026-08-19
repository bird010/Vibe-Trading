"""Tests for hierarchical clustering — §9.4."""

import numpy as np
import pandas as pd
import pytest

from backtest.fund_rotation.clustering import hierarchical_cluster


def _distance_matrix(data: dict[tuple[str, str], float], codes: list[str]) -> pd.DataFrame:
    """Build a distance matrix from pairwise distances."""
    n = len(codes)
    mat = pd.DataFrame(np.zeros((n, n)), index=codes, columns=codes)
    for (a, b), d in data.items():
        mat.loc[a, b] = d
        mat.loc[b, a] = d
    return mat


class TestHierarchicalCluster:
    """§9.4 — average-linkage, fixed K."""

    def test_two_clear_groups(self):
        """Two groups of 2 with large inter-group distance."""
        codes = ["A", "B", "C", "D"]
        dist = _distance_matrix({
            ("A", "B"): 0.1,
            ("C", "D"): 0.1,
            ("A", "C"): 1.8,
            ("A", "D"): 1.8,
            ("B", "C"): 1.8,
            ("B", "D"): 1.8,
        }, codes)
        clusters = hierarchical_cluster(dist, k=2)
        # A and B should be in same cluster; C and D in same cluster
        assert clusters["A"] == clusters["B"]
        assert clusters["C"] == clusters["D"]
        assert clusters["A"] != clusters["C"]

    def test_k_equals_n(self):
        """K=N -> each ETF in its own cluster."""
        codes = ["A", "B", "C"]
        dist = _distance_matrix({
            ("A", "B"): 0.5,
            ("A", "C"): 0.8,
            ("B", "C"): 0.6,
        }, codes)
        clusters = hierarchical_cluster(dist, k=3)
        assert len(set(clusters.values())) == 3

    def test_k_equals_1(self):
        """K=1 -> all in one cluster."""
        codes = ["A", "B", "C"]
        dist = _distance_matrix({
            ("A", "B"): 0.5,
            ("A", "C"): 0.8,
            ("B", "C"): 0.6,
        }, codes)
        clusters = hierarchical_cluster(dist, k=1)
        assert len(set(clusters.values())) == 1

    def test_deterministic(self):
        """Same input always produces same output."""
        codes = ["A", "B", "C", "D", "E"]
        dist = _distance_matrix({
            ("A", "B"): 0.1, ("A", "C"): 0.9, ("A", "D"): 0.9, ("A", "E"): 0.9,
            ("B", "C"): 0.9, ("B", "D"): 0.9, ("B", "E"): 0.9,
            ("C", "D"): 0.1, ("C", "E"): 0.9,
            ("D", "E"): 0.9,
        }, codes)
        r1 = hierarchical_cluster(dist, k=2)
        r2 = hierarchical_cluster(dist, k=2)
        assert r1 == r2

    def test_cluster_labels_are_ints(self):
        codes = ["A", "B", "C"]
        dist = _distance_matrix({
            ("A", "B"): 0.5, ("A", "C"): 0.8, ("B", "C"): 0.6,
        }, codes)
        clusters = hierarchical_cluster(dist, k=2)
        for v in clusters.values():
            assert isinstance(v, int)

    def test_all_codes_present(self):
        codes = ["X", "Y", "Z"]
        dist = _distance_matrix({
            ("X", "Y"): 0.3, ("X", "Z"): 0.7, ("Y", "Z"): 0.5,
        }, codes)
        clusters = hierarchical_cluster(dist, k=2)
        assert set(clusters.keys()) == {"X", "Y", "Z"}

    def test_order_independence(self):
        """Shuffling distance matrix index/columns gives same clustering."""
        codes1 = ["A", "B", "C", "D"]
        dist1 = _distance_matrix({
            ("A", "B"): 0.1, ("A", "C"): 1.5, ("A", "D"): 1.5,
            ("B", "C"): 1.5, ("B", "D"): 1.5,
            ("C", "D"): 0.1,
        }, codes1)
        codes2 = ["D", "C", "B", "A"]
        dist2 = _distance_matrix({
            ("A", "B"): 0.1, ("A", "C"): 1.5, ("A", "D"): 1.5,
            ("B", "C"): 1.5, ("B", "D"): 1.5,
            ("C", "D"): 0.1,
        }, codes2)
        r1 = hierarchical_cluster(dist1, k=2)
        r2 = hierarchical_cluster(dist2, k=2)
        # Same grouping regardless of input order
        assert (r1["A"] == r1["B"]) == (r2["A"] == r2["B"])
        assert (r1["C"] == r1["D"]) == (r2["C"] == r2["D"])
