"""Tests for correlation distance and iterative exclusion — §9.2-9.3."""

import numpy as np
import pandas as pd
import pytest

from backtest.fund_rotation.correlation import (
    compute_correlation_distance,
    iterative_exclude,
)


def _weekly_returns(data: dict[str, list[float]]) -> pd.DataFrame:
    """Build weekly returns DataFrame from dict of lists."""
    n = max(len(v) for v in data.values())
    for k, v in data.items():
        if len(v) < n:
            data[k] = v + [np.nan] * (n - len(v))
    return pd.DataFrame(data)


class TestCorrelationDistance:
    """§9.2 — distance(i,j) = sqrt(2*(1-corr(i,j)))."""

    def test_perfect_correlation_zero_distance(self):
        """corr=1 -> distance=0."""
        returns = _weekly_returns({"A": [0.01, 0.02, 0.03], "B": [0.01, 0.02, 0.03]})
        dist = compute_correlation_distance(returns, min_pairwise_weeks=3)
        np.testing.assert_allclose(dist.loc["A", "B"], 0.0, atol=1e-7)

    def test_perfect_negative_correlation_max_distance(self):
        """corr=-1 -> distance=2."""
        returns = _weekly_returns({"A": [0.01, 0.02, 0.03], "B": [-0.01, -0.02, -0.03]})
        dist = compute_correlation_distance(returns, min_pairwise_weeks=3)
        np.testing.assert_allclose(dist.loc["A", "B"], 2.0, atol=1e-10)

    def test_zero_correlation(self):
        """corr=0 -> distance=sqrt(2)."""
        # Orthogonal series: cov=0
        returns = _weekly_returns({"A": [1.0, -1.0, 1.0, -1.0], "B": [1.0, 1.0, -1.0, -1.0]})
        dist = compute_correlation_distance(returns, min_pairwise_weeks=4)
        expected = np.sqrt(2.0)
        np.testing.assert_allclose(dist.loc["A", "B"], expected, atol=1e-10)

    def test_diagonal_is_zero(self):
        returns = _weekly_returns({"A": [0.01, 0.02, 0.03], "B": [0.04, 0.05, 0.06]})
        dist = compute_correlation_distance(returns, min_pairwise_weeks=3)
        np.testing.assert_allclose(dist.loc["A", "A"], 0.0, atol=1e-10)
        np.testing.assert_allclose(dist.loc["B", "B"], 0.0, atol=1e-10)

    def test_symmetric(self):
        returns = _weekly_returns({"A": [0.01, 0.02, 0.03], "B": [0.04, -0.01, 0.06]})
        dist = compute_correlation_distance(returns, min_pairwise_weeks=3)
        np.testing.assert_allclose(dist.loc["A", "B"], dist.loc["B", "A"], atol=1e-10)

    def test_insufficient_pairwise_weeks_gives_nan(self):
        """Pairs with fewer common valid weeks than threshold -> NaN distance."""
        # A has data in weeks 0,1,2; B has data in weeks 2,3,4
        # Common weeks: only week 2 -> 1 < min_pairwise_weeks=3
        returns = pd.DataFrame({
            "A": [0.01, 0.02, 0.03, np.nan, np.nan],
            "B": [np.nan, np.nan, 0.05, 0.06, 0.07],
        })
        dist = compute_correlation_distance(returns, min_pairwise_weeks=3)
        assert np.isnan(dist.loc["A", "B"])

    def test_hand_calculation_4x4(self):
        """Verify with known 4-ETF example."""
        # Perfectly correlated pairs: (A,B) and (C,D); cross-pair corr=0
        returns = _weekly_returns({
            "A": [1.0, 2.0, 3.0, 4.0],
            "B": [1.0, 2.0, 3.0, 4.0],
            "C": [4.0, 3.0, 2.0, 1.0],
            "D": [4.0, 3.0, 2.0, 1.0],
        })
        dist = compute_correlation_distance(returns, min_pairwise_weeks=4)
        # A-B: corr=1, dist=0
        np.testing.assert_allclose(dist.loc["A", "B"], 0.0, atol=1e-10)
        # C-D: corr=1, dist=0
        np.testing.assert_allclose(dist.loc["C", "D"], 0.0, atol=1e-10)
        # A-C: corr=-1, dist=2
        np.testing.assert_allclose(dist.loc["A", "C"], 2.0, atol=1e-10)


class TestIterativeExclusion:
    """§9.3 — remove ETF with most invalid pairs until matrix complete."""

    def test_complete_matrix_no_exclusion(self):
        """All pairs valid -> no exclusion."""
        returns = _weekly_returns({
            "A": [0.01, 0.02, 0.03],
            "B": [0.04, 0.05, 0.06],
            "C": [0.07, 0.08, 0.09],
        })
        dist = compute_correlation_distance(returns, min_pairwise_weeks=3)
        kept, excluded = iterative_exclude(dist, k=2)
        assert set(kept) == {"A", "B", "C"}
        assert len(excluded) == 0

    def test_removes_etf_with_most_invalid_pairs(self):
        """ETF causing most NaN pairs is removed first."""
        # C has NaN with both A and B (2 invalid pairs)
        # A and B have valid pair between them
        returns = pd.DataFrame({
            "A": [0.01, 0.02, 0.03, 0.04],
            "B": [0.05, 0.06, 0.07, 0.08],
            "C": [0.09, np.nan, np.nan, np.nan],
        })
        dist = compute_correlation_distance(returns, min_pairwise_weeks=3)
        # C-A: only 1 common week (week 0) < 3 -> NaN
        # C-B: only 1 common week (week 0) < 3 -> NaN
        # A-B: 4 common weeks -> valid
        kept, excluded = iterative_exclude(dist, k=2)
        assert "C" not in kept
        assert "C" in [e.ts_code for e in excluded]
        assert set(kept) == {"A", "B"}

    def test_tie_broken_by_ts_code_lexicographic(self):
        """When multiple ETFs have same invalid count, remove lexicographically largest."""
        # D has 2 invalid pairs (with A and B); C also has 2 invalid pairs (with A and B)
        # A-B valid, C-D valid, but A-C, A-D, B-C, B-D all invalid
        # C and D tie at 2 invalid -> remove D first (lexicographically larger)
        returns = pd.DataFrame({
            "A": [0.01, 0.02, 0.03, 0.04, 0.05],
            "B": [0.06, 0.07, 0.08, 0.09, 0.10],
            "C": [0.11, 0.12, 0.13, np.nan, np.nan],
            "D": [0.14, 0.15, 0.16, np.nan, np.nan],
        })
        dist = compute_correlation_distance(returns, min_pairwise_weeks=4)
        # A-B: 5 common -> valid
        # A-C: 3 common < 4 -> NaN; A-D: 3 common < 4 -> NaN
        # B-C: 3 common < 4 -> NaN; B-D: 3 common < 4 -> NaN
        # C-D: 3 common < 4 -> NaN
        # Invalid counts: A=2(C,D), B=2(C,D), C=3(A,B,D), D=3(A,B,C)
        # Remove D first (count=3, tie with C, D > C lexicographically)
        kept, excluded = iterative_exclude(dist, k=2)
        assert "D" in [e.ts_code for e in excluded]
        # After removing D: C still has 2 invalid (A,B), remove C
        assert "C" in [e.ts_code for e in excluded]
        assert set(kept) == {"A", "B"}

    def test_fails_when_remaining_below_k(self):
        """If exclusion leaves fewer than K ETFs, raise error."""
        returns = pd.DataFrame({
            "A": [0.01, np.nan, np.nan],
            "B": [np.nan, 0.02, np.nan],
        })
        dist = compute_correlation_distance(returns, min_pairwise_weeks=3)
        # A-B: 0 common weeks -> NaN
        with pytest.raises(ValueError, match="[Ff]ewer than"):
            iterative_exclude(dist, k=2)

    def test_order_independence(self):
        """Shuffling column order produces same exclusion result."""
        returns1 = pd.DataFrame({
            "A": [0.01, 0.02, 0.03, 0.04],
            "B": [0.05, 0.06, 0.07, 0.08],
            "C": [0.09, np.nan, np.nan, np.nan],
        })
        returns2 = returns1[["C", "A", "B"]]  # shuffled columns
        dist1 = compute_correlation_distance(returns1, min_pairwise_weeks=3)
        dist2 = compute_correlation_distance(returns2, min_pairwise_weeks=3)
        kept1, excl1 = iterative_exclude(dist1, k=2)
        kept2, excl2 = iterative_exclude(dist2, k=2)
        assert set(kept1) == set(kept2)
        assert set(e.ts_code for e in excl1) == set(e.ts_code for e in excl2)

    def test_exclusion_records_carry_reason(self):
        returns = pd.DataFrame({
            "A": [0.01, 0.02, 0.03, 0.04],
            "B": [0.05, 0.06, 0.07, 0.08],
            "C": [0.09, np.nan, np.nan, np.nan],
        })
        dist = compute_correlation_distance(returns, min_pairwise_weeks=3)
        _, excluded = iterative_exclude(dist, k=2)
        assert len(excluded) == 1
        assert excluded[0].ts_code == "C"
        assert excluded[0].reason.value == "pairwise_exclusion"
