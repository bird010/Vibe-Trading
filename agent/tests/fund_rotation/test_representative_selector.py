"""Phase 3 Tasks 3-4 — representative ETF selector tests (design §8.1/§8.2).

Medoid neighborhood candidates, leave-one-out cluster-index correlation gate,
causal ADV20 liquidity selection with stable tie-breaks, representative
locking and hard-failure fallback. Diagnostics keep the two correlations
distinct (distance_to_medoid vs leave_one_out_corr — never one ambiguous
`correlation` field).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.fund_rotation.strategies.correlation_representative.representative import (
    CandidateRecord,
    RepresentativeSelection,
    candidate_neighborhood,
    compute_medoid,
    leave_one_out_cluster_index,
    maintain_representative_lock,
    select_representative,
)


def _distance(rows: dict[str, dict[str, float]]) -> pd.DataFrame:
    codes = sorted(rows)
    return pd.DataFrame(
        [[rows[a][b] for b in codes] for a in codes], index=codes, columns=codes,
    )


def _weekly_window(data: dict[str, np.ndarray], n: int = 30) -> pd.DataFrame:
    return pd.DataFrame(data, index=[f"2023{w:04d}" for w in range(1, n + 1)])


# ── medoid (§8.1) ──

class TestMedoid:
    def test_medoid_minimizes_average_distance_to_members(self):
        dist = _distance({
            "A": {"A": 0.0, "B": 0.1, "C": 0.1, "D": 0.9},
            "B": {"B": 0.0, "A": 0.1, "C": 0.2, "D": 0.8},
            "C": {"C": 0.0, "A": 0.1, "B": 0.2, "D": 0.85},
            "D": {"D": 0.0, "A": 0.9, "B": 0.8, "C": 0.85},
        })
        assert compute_medoid(dist, ["A", "B", "C", "D"]) == "A"

    def test_medoid_is_a_real_member_not_a_synthetic_centroid(self):
        dist = _distance({
            "X": {"X": 0.0, "Y": 0.5, "Z": 0.5},
            "Y": {"Y": 0.0, "X": 0.5, "Z": 0.4},
            "Z": {"Z": 0.0, "X": 0.5, "Y": 0.4},
        })
        medoid = compute_medoid(dist, ["X", "Y", "Z"])
        assert medoid in {"X", "Y", "Z"}

    def test_medoid_ties_broken_by_code(self):
        dist = _distance({
            "B": {"B": 0.0, "A": 0.3, "C": 0.3},
            "A": {"A": 0.0, "B": 0.3, "C": 0.3},
            "C": {"C": 0.0, "A": 0.3, "B": 0.3},
        })
        assert compute_medoid(dist, ["A", "B", "C"]) == "A"


# ── candidate neighborhood (§8.1) ──

class TestNeighborhood:
    def test_candidates_nearest_to_medoid_first_with_medoid_included(self):
        dist = _distance({
            "M": {"M": 0.0, "N1": 0.1, "N2": 0.2, "F1": 0.8, "F2": 0.9},
            "N1": {"N1": 0.0, "M": 0.1, "N2": 0.3, "F1": 0.7, "F2": 0.8},
            "N2": {"N2": 0.0, "M": 0.2, "N1": 0.3, "F1": 0.6, "F2": 0.7},
            "F1": {"F1": 0.0, "M": 0.8, "N1": 0.7, "N2": 0.6, "F2": 0.1},
            "F2": {"F2": 0.0, "M": 0.9, "N1": 0.8, "N2": 0.7, "F1": 0.1},
        })
        neighborhood = candidate_neighborhood(
            dist, ["M", "N1", "N2", "F1", "F2"], medoid="M", candidate_count=3,
        )
        assert neighborhood == ["M", "N1", "N2"]

    def test_small_cluster_uses_all_members(self):
        dist = _distance({
            "M": {"M": 0.0, "N1": 0.1},
            "N1": {"N1": 0.0, "M": 0.1},
        })
        neighborhood = candidate_neighborhood(
            dist, ["M", "N1"], medoid="M", candidate_count=5,
        )
        assert neighborhood == ["M", "N1"]

    def test_distance_ties_broken_by_code(self):
        dist = _distance({
            "M": {"M": 0.0, "B": 0.2, "A": 0.2},
            "A": {"A": 0.0, "M": 0.2, "B": 0.1},
            "B": {"B": 0.0, "M": 0.2, "A": 0.1},
        })
        neighborhood = candidate_neighborhood(
            dist, ["M", "A", "B"], medoid="M", candidate_count=3,
        )
        assert neighborhood == ["M", "A", "B"]


# ── leave-one-out cluster index (§8.2) ──

class TestLeaveOneOutIndex:
    def test_index_excludes_candidate_and_is_equal_weight(self):
        window = _weekly_window({
            "A": np.array([0.01, 0.02, -0.01]),
            "B": np.array([0.03, 0.00, 0.01]),
            "C": np.array([-0.02, 0.01, 0.02]),
        }, n=3)
        index = leave_one_out_cluster_index(window, ["A", "B", "C"], exclude="A")
        expected = window[["B", "C"]].mean(axis=1)
        assert index.equals(expected)

    def test_single_member_cluster_has_no_index(self):
        window = _weekly_window({"A": np.array([0.01, 0.02])}, n=2)
        index = leave_one_out_cluster_index(window, ["A"], exclude="A")
        assert index.empty


# ── full selection (§8.2) ──

def _correlated_window(seed: int = 7, n: int = 40) -> pd.DataFrame:
    """Four highly correlated members + one weakly correlated outsider."""
    rng = np.random.default_rng(seed)
    factor = rng.normal(0.0, 0.02, n)
    data = {}
    for code in ("A", "B", "C", "D"):
        data[code] = factor + rng.normal(0.0, 0.002, n)
    data["OUT"] = rng.normal(0.0, 0.02, n)  # independent
    return _weekly_window(data, n=n)


class TestSelectRepresentative:
    def test_selects_max_adv_among_correlated_candidates(self):
        window = _correlated_window()
        members = ["A", "B", "C", "D"]
        dist = _distance({
            "A": {"A": 0.0, "B": 0.1, "C": 0.12, "D": 0.15},
            "B": {"B": 0.0, "A": 0.1, "C": 0.11, "D": 0.14},
            "C": {"C": 0.0, "A": 0.12, "B": 0.11, "D": 0.13},
            "D": {"D": 0.0, "A": 0.15, "B": 0.14, "C": 0.13},
        })
        selection = select_representative(
            distance=dist, weekly_window=window, members=members,
            adv20={"A": 1_000.0, "B": 5_000.0, "C": 3_000.0, "D": 500.0},
            candidate_count=5, min_cluster_corr=0.85,
            eligible=frozenset(members),
        )
        assert isinstance(selection, RepresentativeSelection)
        assert selection.medoid == "B"  # smallest average distance (0.1167)
        assert selection.selected == "B"  # medoid also has the highest ADV20
        assert selection.exclusion_reason == ""
        # Diagnostics: both metrics recorded distinctly per candidate.
        by_code = {c.code: c for c in selection.candidates}
        assert isinstance(by_code["B"], CandidateRecord)
        assert by_code["B"].leave_one_out_corr == pytest.approx(1.0, abs=0.05)
        assert by_code["B"].adv20 == 5_000.0
        assert by_code["B"].distance_to_medoid == pytest.approx(0.0)
        assert by_code["A"].distance_to_medoid == pytest.approx(0.1)
        assert by_code["B"].excluded_reason == ""

    def test_low_leave_one_out_correlation_is_excluded_with_reason(self):
        window = _correlated_window()
        members = ["A", "B", "C", "OUT"]
        dist = _distance({
            "A": {"A": 0.0, "B": 0.10, "C": 0.12, "OUT": 0.60},
            "B": {"B": 0.0, "A": 0.10, "C": 0.11, "OUT": 0.62},
            "C": {"C": 0.0, "A": 0.12, "B": 0.11, "OUT": 0.58},
            "OUT": {"OUT": 0.0, "A": 0.60, "B": 0.62, "C": 0.58},
        })
        selection = select_representative(
            distance=dist, weekly_window=window, members=members,
            adv20={"A": 1_000.0, "B": 2_000.0, "C": 1_500.0, "OUT": 9_000.0},
            candidate_count=5, min_cluster_corr=0.85,
            eligible=frozenset(members),
        )
        # OUT has the best ADV but fails the leave-one-out correlation gate.
        assert selection.selected == "B"
        out_record = next(c for c in selection.candidates if c.code == "OUT")
        assert out_record.excluded_reason == "LOW_CLUSTER_CORR"
        assert out_record.leave_one_out_corr is not None
        assert out_record.leave_one_out_corr < 0.85

    def test_untradable_candidate_excluded_before_correlation(self):
        window = _correlated_window()
        members = ["A", "B", "C"]
        dist = _distance({
            "A": {"A": 0.0, "B": 0.1, "C": 0.12},
            "B": {"B": 0.0, "A": 0.1, "C": 0.11},
            "C": {"C": 0.0, "A": 0.12, "B": 0.11},
        })
        selection = select_representative(
            distance=dist, weekly_window=window, members=members,
            adv20={"A": 1_000.0, "B": 2_000.0, "C": 9_000.0},
            candidate_count=5, min_cluster_corr=0.85,
            eligible=frozenset({"A", "B"}),  # C suspended / data invalid
        )
        assert selection.selected == "B"
        c_record = next(c for c in selection.candidates if c.code == "C")
        assert c_record.excluded_reason == "NOT_TRADABLE"
        assert c_record.leave_one_out_corr is None  # never computed

    def test_missing_adv_excludes_candidate(self):
        window = _correlated_window()
        members = ["A", "B", "C"]
        dist = _distance({
            "A": {"A": 0.0, "B": 0.1, "C": 0.12},
            "B": {"B": 0.0, "A": 0.1, "C": 0.11},
            "C": {"C": 0.0, "A": 0.12, "B": 0.11},
        })
        selection = select_representative(
            distance=dist, weekly_window=window, members=members,
            adv20={"A": 1_000.0, "B": 2_000.0},  # C has no ADV history
            candidate_count=5, min_cluster_corr=0.85,
            eligible=frozenset(members),
        )
        assert selection.selected == "B"
        c_record = next(c for c in selection.candidates if c.code == "C")
        assert c_record.excluded_reason == "NO_ADV"

    def test_insufficient_window_data_excludes_candidate(self):
        window = _correlated_window(n=40)
        # B has almost no history in the PIT window.
        window["B"] = np.nan
        window.iloc[0, window.columns.get_loc("B")] = 0.01
        members = ["A", "B", "C", "D"]
        dist = _distance({
            "A": {"A": 0.0, "B": 0.1, "C": 0.12, "D": 0.15},
            "B": {"B": 0.0, "A": 0.1, "C": 0.11, "D": 0.14},
            "C": {"C": 0.0, "A": 0.12, "B": 0.11, "D": 0.13},
            "D": {"D": 0.0, "A": 0.15, "B": 0.14, "C": 0.13},
        })
        selection = select_representative(
            distance=dist, weekly_window=window, members=members,
            adv20={"A": 1_000.0, "B": 9_000.0, "C": 2_000.0, "D": 500.0},
            candidate_count=5, min_cluster_corr=0.85,
            eligible=frozenset(members),
        )
        b_record = next(c for c in selection.candidates if c.code == "B")
        assert b_record.excluded_reason == "INSUFFICIENT_DATA"
        assert b_record.leave_one_out_corr is None  # no fabricated value

    def test_adv_tie_broken_by_code(self):
        window = _correlated_window()
        members = ["A", "B", "C"]
        dist = _distance({
            "A": {"A": 0.0, "B": 0.1, "C": 0.12},
            "B": {"B": 0.0, "A": 0.1, "C": 0.11},
            "C": {"C": 0.0, "A": 0.12, "B": 0.11},
        })
        selection = select_representative(
            distance=dist, weekly_window=window, members=members,
            adv20={"A": 1_000.0, "B": 3_000.0, "C": 3_000.0},
            candidate_count=5, min_cluster_corr=0.85,
            eligible=frozenset(members),
        )
        assert selection.selected == "B"  # same ADV -> lexicographically first

    def test_adv_tie_three_level_break_valid_days_then_listing_then_code(self):
        """§8.2: ADV tie -> more valid trading days -> longer listing -> code."""
        window = _correlated_window()
        members = ["A", "B", "C"]
        dist = _distance({
            "A": {"A": 0.0, "B": 0.1, "C": 0.12},
            "B": {"B": 0.0, "A": 0.1, "C": 0.11},
            "C": {"C": 0.0, "A": 0.12, "B": 0.11},
        })
        common = dict(
            distance=dist, weekly_window=window, members=members,
            adv20={"A": 1_000.0, "B": 3_000.0, "C": 3_000.0},
            candidate_count=5, min_cluster_corr=0.85,
            eligible=frozenset(members),
        )
        # 1) C has more valid trading days -> beats B despite code order.
        first = select_representative(
            tie_break={"B": (180, 900), "C": (195, 500)}, **common,
        )
        assert first.selected == "C"
        # 2) Equal valid days -> longer listing history wins.
        second = select_representative(
            tie_break={"B": (190, 900), "C": (190, 1200)}, **common,
        )
        assert second.selected == "C"
        # 3) Equal days and listing -> code (regression to level-3 tie-break).
        third = select_representative(
            tie_break={"B": (190, 900), "C": (190, 900)}, **common,
        )
        assert third.selected == "B"

    def test_no_viable_candidate_reports_no_eligible_representative(self):
        window = _correlated_window()
        members = ["A", "B"]
        dist = _distance({
            "A": {"A": 0.0, "B": 0.1},
            "B": {"B": 0.0, "A": 0.1},
        })
        selection = select_representative(
            distance=dist, weekly_window=window, members=members,
            adv20={},  # no liquidity at all
            candidate_count=5, min_cluster_corr=0.85,
            eligible=frozenset(members),
        )
        assert selection.selected is None
        assert selection.exclusion_reason == "NO_ELIGIBLE_REPRESENTATIVE"
        assert selection.medoid == "A"  # diagnostics still present

    def test_single_member_cluster_does_not_fabricate_correlation(self):
        window = _correlated_window()
        selection = select_representative(
            distance=_distance({"A": {"A": 0.0}}),
            weekly_window=window, members=["A"],
            adv20={"A": 9_999.0},
            candidate_count=5, min_cluster_corr=0.85,
            eligible=frozenset({"A"}),
        )
        assert selection.selected is None
        assert selection.exclusion_reason == "SINGLE_MEMBER_CLUSTER"
        assert selection.candidates == ()  # nothing scored, nothing fabricated

    def test_relaxed_singleton_selects_itself_when_hard_checks_pass(self):
        window = _correlated_window()
        selection = select_representative(
            distance=_distance({"A": {"A": 0.0}}),
            weekly_window=window, members=["A"],
            adv20={"A": 9_999.0},
            candidate_count=5, min_cluster_corr=0.85,
            eligible=frozenset({"A"}),
            relaxed_selection=True,
        )
        assert selection.selected == "A"
        assert selection.exclusion_reason == ""
        assert selection.candidates[0].leave_one_out_corr is None

    def test_relaxed_low_corr_is_eligible_but_missing_corr_is_not(self):
        window = _correlated_window(n=40)
        window["B"] = np.nan
        members = ["A", "B", "C"]
        dist = _distance({
            "A": {"A": 0.0, "B": 0.1, "C": 0.12},
            "B": {"B": 0.0, "A": 0.1, "C": 0.11},
            "C": {"C": 0.0, "A": 0.12, "B": 0.11},
        })
        selection = select_representative(
            distance=dist, weekly_window=window, members=members,
            adv20={"A": 1_000.0, "B": 9_000.0, "C": 2_000.0},
            candidate_count=5, min_cluster_corr=0.85,
            eligible=frozenset(members),
            relaxed_selection=True,
        )
        b_record = next(c for c in selection.candidates if c.code == "B")
        assert b_record.leave_one_out_corr is None
        assert b_record.excluded_reason == "INSUFFICIENT_DATA"
        assert selection.selected != "B"

    def test_relaxed_low_corr_candidate_is_not_excluded(self):
        window = _correlated_window()
        members = ["A", "B", "C", "OUT"]
        dist = _distance({
            "A": {"A": 0.0, "B": 0.10, "C": 0.12, "OUT": 0.20},
            "B": {"B": 0.0, "A": 0.10, "C": 0.11, "OUT": 0.20},
            "C": {"C": 0.0, "A": 0.12, "B": 0.11, "OUT": 0.20},
            "OUT": {"OUT": 0.0, "A": 0.20, "B": 0.20, "C": 0.20},
        })
        selection = select_representative(
            distance=dist, weekly_window=window, members=members,
            adv20={"A": 1_000.0, "B": 2_000.0, "C": 1_500.0, "OUT": 9_000.0},
            candidate_count=5, min_cluster_corr=0.85,
            eligible=frozenset(members), relaxed_selection=True,
        )
        out_record = next(c for c in selection.candidates if c.code == "OUT")
        assert out_record.leave_one_out_corr < 0.85
        assert out_record.excluded_reason == ""
        assert selection.selected == "OUT"


# ── lock & fallback (§8.2, Task 4) ──

def _abc_distance() -> pd.DataFrame:
    return _distance({
        "A": {"A": 0.0, "B": 0.1, "C": 0.12},
        "B": {"B": 0.0, "A": 0.1, "C": 0.11},
        "C": {"C": 0.0, "A": 0.12, "B": 0.11},
    })


class TestLockAndFallback:
    def test_relaxed_singleton_lock_is_maintained_until_hard_failure(self):
        window = _correlated_window()
        common = dict(
            distance=_distance({"A": {"A": 0.0}}),
            weekly_window=window,
            members=["A"],
            adv20={"A": 9_999.0},
            candidate_count=5,
            min_cluster_corr=0.85,
            relaxed_selection=True,
        )
        first = maintain_representative_lock(
            eligible=frozenset({"A"}), current=None, **common,
        )
        second = maintain_representative_lock(
            eligible=frozenset({"A"}), current="A", **common,
        )
        failed = maintain_representative_lock(
            eligible=frozenset(), current="A", **common,
        )
        assert first.selected == "A"
        assert second.selected == "A"
        assert second.lock_maintained is True
        assert failed.selected is None
        assert failed.lock_maintained is False

    def test_lock_maintained_despite_adv_overtaking(self):
        """§8.2 — ADV rank changes alone never trigger a switch."""
        window = _correlated_window()
        selection = maintain_representative_lock(
            distance=_abc_distance(), weekly_window=window,
            members=["A", "B", "C"],
            adv20={"A": 1_000.0, "B": 2_000.0, "C": 9_000.0},
            candidate_count=5, min_cluster_corr=0.85,
            eligible=frozenset({"A", "B", "C"}),
            current="B",
        )
        assert selection.selected == "B"  # locked, not C despite 9k ADV
        assert selection.lock_maintained is True
        assert selection.exclusion_reason == ""

    def test_small_adv_fluctuation_does_not_churn(self):
        window = _correlated_window()
        first = maintain_representative_lock(
            distance=_abc_distance(), weekly_window=window,
            members=["A", "B", "C"],
            adv20={"A": 1_000.0, "B": 2_000.0, "C": 1_999.0},
            candidate_count=5, min_cluster_corr=0.85,
            eligible=frozenset({"A", "B", "C"}),
            current=None,
        )
        assert first.selected == "B"
        # Next period: C narrowly overtakes B -> lock still holds B.
        second = maintain_representative_lock(
            distance=_abc_distance(), weekly_window=window,
            members=["A", "B", "C"],
            adv20={"A": 1_000.0, "B": 1_998.0, "C": 2_001.0},
            candidate_count=5, min_cluster_corr=0.85,
            eligible=frozenset({"A", "B", "C"}),
            current=first.selected,
        )
        assert second.selected == "B"
        assert second.lock_maintained is True

    def test_suspension_falls_back_along_saved_candidate_order(self):
        window = _correlated_window()
        selection = maintain_representative_lock(
            distance=_abc_distance(), weekly_window=window,
            members=["A", "B", "C"],
            adv20={"A": 1_000.0, "B": 2_000.0, "C": 3_000.0},
            candidate_count=5, min_cluster_corr=0.85,
            eligible=frozenset({"A", "C"}),  # B suspended / not tradable
            current="B",
        )
        # Neighborhood order is [B(medoid), A, C]; the fallback follows the
        # pre-saved order (§8.2), not a fresh ADV ranking -> A, not C.
        assert selection.selected == "A"
        assert selection.lock_maintained is False
        b_record = next(c for c in selection.candidates if c.code == "B")
        assert b_record.excluded_reason == "NOT_TRADABLE"

    def test_data_failure_falls_back_along_saved_order(self):
        window = _correlated_window()
        selection = maintain_representative_lock(
            distance=_abc_distance(), weekly_window=window,
            members=["A", "B", "C"],
            adv20={"A": 1_000.0, "C": 3_000.0},  # B lost its ADV history
            candidate_count=5, min_cluster_corr=0.85,
            eligible=frozenset({"A", "B", "C"}),
            current="B",
        )
        assert selection.selected == "A"  # saved-order fallback, not C
        assert selection.lock_maintained is False
        b_record = next(c for c in selection.candidates if c.code == "B")
        assert b_record.excluded_reason == "NO_ADV"

    def test_no_remaining_candidate_keeps_cash_slot(self):
        """§8.2 — hard failure with no fallback: the slot stays cash; never
        redistributed to other clusters nor escalated to decision INVALID."""
        window = _correlated_window()
        selection = maintain_representative_lock(
            distance=_abc_distance(), weekly_window=window,
            members=["A", "B", "C"],
            adv20={},  # everyone lost liquidity
            candidate_count=5, min_cluster_corr=0.85,
            eligible=frozenset({"A", "B", "C"}),
            current="B",
        )
        assert selection.selected is None
        assert selection.lock_maintained is False
        assert selection.exclusion_reason == "NO_ELIGIBLE_REPRESENTATIVE"

    def test_current_dropped_from_neighborhood_falls_back(self):
        """Representative leaves the candidate neighborhood (members change or
        M truncation) -> treated as hard failure, fall back inside the
        neighborhood."""
        window = _correlated_window()
        dist = _distance({
            "A": {"A": 0.0, "B": 0.10, "C": 0.32, "D": 0.33},
            "B": {"B": 0.0, "A": 0.10, "C": 0.30, "D": 0.31},
            "C": {"C": 0.0, "A": 0.32, "B": 0.30, "D": 0.10},
            "D": {"D": 0.0, "A": 0.33, "B": 0.31, "C": 0.10},
        })
        selection = maintain_representative_lock(
            distance=dist, weekly_window=window,
            members=["A", "B", "C", "D"],
            adv20={"A": 1_000.0, "B": 2_000.0, "C": 1_500.0, "D": 1_200.0},
            candidate_count=2,          # medoid B + nearest A only
            min_cluster_corr=0.85,
            eligible=frozenset({"A", "B", "C", "D"}),
            current="C",                # C is outside the M=2 neighborhood
        )
        assert selection.medoid == "B"
        assert selection.lock_maintained is False
        assert selection.selected == "A" or selection.selected == "B"
        assert selection.selected != "C"
