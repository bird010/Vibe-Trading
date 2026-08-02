"""Tests for cluster momentum and target weights — §10."""

import numpy as np
import pandas as pd
import pytest

from backtest.fund_rotation.momentum import (
    compute_cluster_momentum,
    select_top_clusters,
    build_target_weights,
)


class TestClusterMomentum:
    """§10.1 — equal-weight virtual index, compound momentum."""

    def test_single_member_momentum(self):
        """One member: cluster return = member return."""
        weekly_returns = pd.DataFrame({
            "A": [0.01, 0.02, 0.03, 0.04],
        }, index=["20240105", "20240112", "20240119", "20240126"])
        clusters = {"A": 1}
        mom = compute_cluster_momentum(weekly_returns, clusters, momentum_window=4)
        # product(1+r) - 1 = 1.01*1.02*1.03*1.04 - 1
        expected = 1.01 * 1.02 * 1.03 * 1.04 - 1.0
        np.testing.assert_allclose(mom[1], expected, rtol=1e-10)

    def test_equal_weight_two_members(self):
        """Two members: cluster return = mean of member returns each week."""
        weekly_returns = pd.DataFrame({
            "A": [0.02, 0.04],
            "B": [0.04, 0.02],
        }, index=["20240105", "20240112"])
        clusters = {"A": 1, "B": 1}
        mom = compute_cluster_momentum(weekly_returns, clusters, momentum_window=2)
        # Week 1: mean(0.02, 0.04) = 0.03; Week 2: mean(0.04, 0.02) = 0.03
        # momentum = 1.03 * 1.03 - 1
        expected = 1.03 * 1.03 - 1.0
        np.testing.assert_allclose(mom[1], expected, rtol=1e-10)

    def test_momentum_window_subset(self):
        """Only last N weeks used for momentum."""
        weekly_returns = pd.DataFrame({
            "A": [0.10, 0.01, 0.02, 0.03],
        }, index=["20240105", "20240112", "20240119", "20240126"])
        clusters = {"A": 1}
        mom = compute_cluster_momentum(weekly_returns, clusters, momentum_window=2)
        # Only last 2 weeks: 0.02, 0.03
        expected = 1.02 * 1.03 - 1.0
        np.testing.assert_allclose(mom[1], expected, rtol=1e-10)

    def test_multiple_clusters(self):
        weekly_returns = pd.DataFrame({
            "A": [0.01, 0.02],
            "B": [0.03, 0.04],
            "C": [-0.01, -0.02],
        }, index=["20240105", "20240112"])
        clusters = {"A": 1, "B": 1, "C": 2}
        mom = compute_cluster_momentum(weekly_returns, clusters, momentum_window=2)
        # Cluster 1: mean(A,B) each week
        c1_w1 = (0.01 + 0.03) / 2  # 0.02
        c1_w2 = (0.02 + 0.04) / 2  # 0.03
        expected_1 = (1 + c1_w1) * (1 + c1_w2) - 1
        # Cluster 2: just C
        expected_2 = (1 - 0.01) * (1 - 0.02) - 1
        np.testing.assert_allclose(mom[1], expected_1, rtol=1e-10)
        np.testing.assert_allclose(mom[2], expected_2, rtol=1e-10)

    def test_nan_member_excluded_from_mean(self):
        """NaN return in a week: only valid members counted."""
        weekly_returns = pd.DataFrame({
            "A": [0.02, 0.04],
            "B": [np.nan, 0.02],
        }, index=["20240105", "20240112"])
        clusters = {"A": 1, "B": 1}
        mom = compute_cluster_momentum(weekly_returns, clusters, momentum_window=2)
        # Week 1: only A valid -> 0.02; Week 2: mean(0.04, 0.02) = 0.03
        expected = 1.02 * 1.03 - 1.0
        np.testing.assert_allclose(mom[1], expected, rtol=1e-10)


class TestSelectTopClusters:
    """§10.2 — Top-N selection with absolute threshold."""

    def test_top3_from_5(self):
        momentum = {1: 0.05, 2: 0.03, 3: 0.01, 4: -0.02, 5: 0.04}
        selected = select_top_clusters(momentum, top_n=3, threshold=0.0)
        assert selected == [1, 5, 2]  # descending: 0.05, 0.04, 0.03

    def test_threshold_filters_negative(self):
        momentum = {1: 0.05, 2: -0.01, 3: 0.02}
        selected = select_top_clusters(momentum, top_n=3, threshold=0.0)
        assert 2 not in selected
        assert selected == [1, 3]

    def test_threshold_strict_greater(self):
        """Threshold is strict: momentum must be > threshold, not >=."""
        momentum = {1: 0.0, 2: 0.01}
        selected = select_top_clusters(momentum, top_n=3, threshold=0.0)
        assert 1 not in selected  # 0.0 is NOT > 0.0
        assert 2 in selected

    def test_tie_broken_by_min_ts_code(self):
        """Equal momentum: cluster with smaller min ts_code wins."""
        momentum = {1: 0.05, 2: 0.05}
        cluster_members = {1: ["B", "C"], 2: ["A", "D"]}
        selected = select_top_clusters(
            momentum, top_n=1, threshold=0.0,
            cluster_members=cluster_members,
        )
        # Cluster 2 has min code "A" < cluster 1 min code "B"
        assert selected == [2]

    def test_fewer_than_top_n_qualified(self):
        momentum = {1: 0.05, 2: -0.01, 3: -0.02}
        selected = select_top_clusters(momentum, top_n=3, threshold=0.0)
        assert selected == [1]


class TestBuildTargetWeights:
    """§10.2 — fixed three slots, equal weight within cluster."""

    def test_three_clusters_equal_weight(self):
        """3 selected clusters -> each 1/3, members equal within."""
        selected = [1, 2, 3]
        cluster_members = {
            1: ["A", "B"],
            2: ["C"],
            3: ["D", "E", "F"],
        }
        weights = build_target_weights(selected, cluster_members, top_n=3)
        # Cluster 1: 1/3 split between A, B
        np.testing.assert_allclose(weights["A"], 1 / 6, rtol=1e-10)
        np.testing.assert_allclose(weights["B"], 1 / 6, rtol=1e-10)
        # Cluster 2: 1/3 to C
        np.testing.assert_allclose(weights["C"], 1 / 3, rtol=1e-10)
        # Cluster 3: 1/3 split between D, E, F
        np.testing.assert_allclose(weights["D"], 1 / 9, rtol=1e-10)
        np.testing.assert_allclose(weights["E"], 1 / 9, rtol=1e-10)
        np.testing.assert_allclose(weights["F"], 1 / 9, rtol=1e-10)
        # Total invested = 1.0
        np.testing.assert_allclose(sum(weights.values()), 1.0, rtol=1e-10)

    def test_two_clusters_cash_one_third(self):
        """2 selected -> each 1/3, cash 1/3."""
        selected = [1, 2]
        cluster_members = {1: ["A"], 2: ["B"]}
        weights = build_target_weights(selected, cluster_members, top_n=3)
        np.testing.assert_allclose(weights["A"], 1 / 3, rtol=1e-10)
        np.testing.assert_allclose(weights["B"], 1 / 3, rtol=1e-10)
        assert sum(weights.values()) == pytest.approx(2 / 3, rel=1e-10)

    def test_one_cluster_invest_one_third(self):
        selected = [1]
        cluster_members = {1: ["A", "B"]}
        weights = build_target_weights(selected, cluster_members, top_n=3)
        np.testing.assert_allclose(weights["A"], 1 / 6, rtol=1e-10)
        np.testing.assert_allclose(weights["B"], 1 / 6, rtol=1e-10)
        assert sum(weights.values()) == pytest.approx(1 / 3, rel=1e-10)

    def test_zero_clusters_all_cash(self):
        selected = []
        cluster_members = {}
        weights = build_target_weights(selected, cluster_members, top_n=3)
        assert weights == {}

    def test_weights_sum_never_exceeds_one(self):
        selected = [1, 2, 3]
        cluster_members = {1: ["A"], 2: ["B"], 3: ["C"]}
        weights = build_target_weights(selected, cluster_members, top_n=3)
        assert sum(weights.values()) <= 1.0 + 1e-10
