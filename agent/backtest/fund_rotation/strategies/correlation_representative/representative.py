"""Representative ETF selection — design §8.1/§8.2 (Phase 3 Tasks 3-4).

Strategy-internal selector. Per cluster:

1. medoid = the real member minimizing average distance to the other members
   (same distance definition as clustering; no synthetic centroid);
2. candidates = the medoid plus the ``M`` nearest members (ties by ts_code);
   small clusters use all members. The medoid only forms the neighborhood —
   it is NOT the correlation-gate reference;
3. each candidate is scored against the leave-one-out equal-weight cluster
   index built from the SAME PIT window; candidates fail on: not tradable,
   insufficient data, leave-one-out correlation below threshold, or missing
   causal ADV20;
4. the survivor with the largest causal ADV20 (decision-date visible only)
   wins; ADV ties break by ts_code.

Diagnostics keep ``distance_to_medoid`` and ``leave_one_out_corr`` as
distinct fields — never one ambiguous ``correlation`` value.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import AbstractSet

import numpy as np
import pandas as pd

# Stable exclusion reason codes (recorded in diagnostics, §12).
NOT_TRADABLE = "NOT_TRADABLE"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
LOW_CLUSTER_CORR = "LOW_CLUSTER_CORR"
NO_ADV = "NO_ADV"
NO_ELIGIBLE_REPRESENTATIVE = "NO_ELIGIBLE_REPRESENTATIVE"
SINGLE_MEMBER_CLUSTER = "SINGLE_MEMBER_CLUSTER"


@dataclass(frozen=True)
class CandidateRecord:
    """Diagnostics for one candidate (excluded_reason empty when viable)."""

    code: str
    distance_to_medoid: float
    leave_one_out_corr: float | None
    adv20: float | None
    excluded_reason: str


@dataclass(frozen=True)
class RepresentativeSelection:
    """Outcome for one cluster: medoid, scored candidates, final pick.

    ``lock_maintained`` is True when a previously locked representative stayed
    qualified and was kept despite ADV rank changes (§8.2).
    """

    medoid: str
    candidates: tuple[CandidateRecord, ...]
    selected: str | None
    exclusion_reason: str  # "" on success
    lock_maintained: bool = False


def compute_medoid(distance: pd.DataFrame, members: Sequence[str]) -> str:
    """§8.1 — member with the smallest average distance to all other members.

    Ties break by ts_code (lexicographically smallest).
    """
    def average_distance(code: str) -> float:
        others = [m for m in members if m != code]
        return float(distance.loc[code, others].mean()) if others else 0.0

    return min(members, key=lambda c: (average_distance(c), c))


def candidate_neighborhood(
    distance: pd.DataFrame,
    members: Sequence[str],
    *,
    medoid: str,
    candidate_count: int,
) -> list[str]:
    """§8.1 — medoid first, then nearest members; small clusters use all."""
    others = sorted(
        (m for m in members if m != medoid),
        key=lambda c: (float(distance.loc[medoid, c]), c),
    )
    return [medoid] + others[: max(candidate_count - 1, 0)]


def leave_one_out_cluster_index(
    weekly_window: pd.DataFrame,
    members: Sequence[str],
    *,
    exclude: str,
) -> pd.Series:
    """§8.2 — equal-weight weekly index of the cluster WITHOUT the candidate,
    built from the same PIT window."""
    rest = [m for m in members if m != exclude and m in weekly_window.columns]
    if not rest:
        return pd.Series(dtype=float)
    return weekly_window[rest].mean(axis=1)


def select_representative(
    *,
    distance: pd.DataFrame,
    weekly_window: pd.DataFrame,
    members: Sequence[str],
    adv20: Mapping[str, float],
    candidate_count: int,
    min_cluster_corr: float,
    eligible: AbstractSet[str],
) -> RepresentativeSelection:
    """§8.2 — score the neighborhood and pick the most liquid survivor.

    ``adv20`` must contain only decision-date-visible (causal) values; the
    caller guarantees no execution-day turnover enters it.
    """
    if len(members) < 2:
        # Single-member clusters cannot produce a leave-one-out index; the
        # correlation is never fabricated — the session applies its gate rule.
        medoid = str(members[0]) if members else ""
        return RepresentativeSelection(
            medoid=medoid,
            candidates=(),
            selected=None,
            exclusion_reason=SINGLE_MEMBER_CLUSTER,
        )

    medoid = compute_medoid(distance, members)
    neighborhood = candidate_neighborhood(
        distance, members, medoid=medoid, candidate_count=candidate_count,
    )

    records: list[CandidateRecord] = []
    for code in neighborhood:
        distance_to_medoid = float(distance.loc[medoid, code])
        corr: float | None = None
        adv_value: float | None = None
        reason = ""

        if code not in eligible:
            reason = NOT_TRADABLE
        else:
            series = (
                weekly_window[code]
                if code in weekly_window.columns
                else pd.Series(dtype=float)
            )
            index = leave_one_out_cluster_index(
                weekly_window, members, exclude=code,
            )
            if series.notna().sum() < 2 or index.empty:
                reason = INSUFFICIENT_DATA
            else:
                corr_value = series.corr(index)
                if corr_value is None or not np.isfinite(corr_value):
                    reason = INSUFFICIENT_DATA
                else:
                    corr = float(corr_value)
                    raw_adv = adv20.get(code)
                    if raw_adv is None or not np.isfinite(raw_adv) or raw_adv <= 0:
                        reason = NO_ADV
                    else:
                        adv_value = float(raw_adv)
                        if corr < min_cluster_corr:
                            reason = LOW_CLUSTER_CORR

        records.append(CandidateRecord(
            code=code,
            distance_to_medoid=distance_to_medoid,
            leave_one_out_corr=corr,
            adv20=adv_value,
            excluded_reason=reason,
        ))

    viable = [r for r in records if not r.excluded_reason]
    if not viable:
        return RepresentativeSelection(
            medoid=medoid,
            candidates=tuple(records),
            selected=None,
            exclusion_reason=NO_ELIGIBLE_REPRESENTATIVE,
        )

    # Largest causal ADV20; ties break by ts_code.
    best = min(viable, key=lambda r: (-r.adv20, r.code))
    return RepresentativeSelection(
        medoid=medoid,
        candidates=tuple(records),
        selected=best.code,
        exclusion_reason="",
    )


def maintain_representative_lock(
    *,
    distance: pd.DataFrame,
    weekly_window: pd.DataFrame,
    members: Sequence[str],
    adv20: Mapping[str, float],
    candidate_count: int,
    min_cluster_corr: float,
    eligible: AbstractSet[str],
    current: str | None,
) -> RepresentativeSelection:
    """§8.2 — lock maintenance and hard-failure fallback.

    A locked representative is kept as long as it stays qualified: ADV rank
    changes alone never trigger a switch. Only a hard failure (untradable,
    data/adj invalidation, lost liquidity) demotes it, falling back along the
    pre-saved candidate (neighborhood) order — NOT by re-ranking ADV. When no
    candidate survives, the cluster slot stays cash (the caller keeps the
    slot's weight as cash; it is never redistributed to other clusters nor
    escalated to the decision action INVALID).

    ``current=None`` performs the fresh (reclustering) selection.
    """
    base = select_representative(
        distance=distance,
        weekly_window=weekly_window,
        members=members,
        adv20=adv20,
        candidate_count=candidate_count,
        min_cluster_corr=min_cluster_corr,
        eligible=eligible,
    )
    if base.exclusion_reason == SINGLE_MEMBER_CLUSTER:
        return base
    if current is None:
        return base

    current_record = next(
        (r for r in base.candidates if r.code == current), None,
    )
    if current_record is not None and not current_record.excluded_reason:
        # Still qualified: keep the lock regardless of ADV movements.
        return RepresentativeSelection(
            medoid=base.medoid,
            candidates=base.candidates,
            selected=current,
            exclusion_reason="",
            lock_maintained=True,
        )

    # Hard failure: demote along the pre-saved candidate order.
    fallback = next(
        (r for r in base.candidates if not r.excluded_reason), None,
    )
    if fallback is None:
        return RepresentativeSelection(
            medoid=base.medoid,
            candidates=base.candidates,
            selected=None,
            exclusion_reason=NO_ELIGIBLE_REPRESENTATIVE,
            lock_maintained=False,
        )
    return RepresentativeSelection(
        medoid=base.medoid,
        candidates=base.candidates,
        selected=fallback.code,
        exclusion_reason="",
        lock_maintained=False,
    )
