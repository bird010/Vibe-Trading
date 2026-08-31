"""Representative ETF selection — design §8.1/§8.2.

Fresh representatives are selected on reclustering dates using medoid
neighborhood, leave-one-out correlation and causal liquidity. Between
reclustering dates the selected representative is locked: correlation drift or
ADV rank changes do not trigger a switch. Only a hard tradability/liquidity
failure permits fallback along the frozen candidate order.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import AbstractSet

import numpy as np
import pandas as pd

NOT_TRADABLE = "NOT_TRADABLE"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
LOW_CLUSTER_CORR = "LOW_CLUSTER_CORR"
NO_ADV = "NO_ADV"
NO_ELIGIBLE_REPRESENTATIVE = "NO_ELIGIBLE_REPRESENTATIVE"
SINGLE_MEMBER_CLUSTER = "SINGLE_MEMBER_CLUSTER"


@dataclass(frozen=True)
class CandidateRecord:
    code: str
    distance_to_medoid: float
    leave_one_out_corr: float | None
    adv20: float | None
    excluded_reason: str


@dataclass(frozen=True)
class RepresentativeSelection:
    medoid: str
    candidates: tuple[CandidateRecord, ...]
    selected: str | None
    exclusion_reason: str
    lock_maintained: bool = False


def compute_medoid(distance: pd.DataFrame, members: Sequence[str]) -> str:
    def average_distance(code: str) -> float:
        others = [member for member in members if member != code]
        return (
            float(distance.loc[code, others].mean())
            if others
            else 0.0
        )

    return min(members, key=lambda code: (average_distance(code), code))


def candidate_neighborhood(
    distance: pd.DataFrame,
    members: Sequence[str],
    *,
    medoid: str,
    candidate_count: int,
) -> list[str]:
    others = sorted(
        (member for member in members if member != medoid),
        key=lambda code: (float(distance.loc[medoid, code]), code),
    )
    return [medoid] + others[: max(candidate_count - 1, 0)]


def leave_one_out_cluster_index(
    weekly_window: pd.DataFrame,
    members: Sequence[str],
    *,
    exclude: str,
) -> pd.Series:
    rest = [
        member
        for member in members
        if member != exclude and member in weekly_window.columns
    ]
    if not rest:
        return pd.Series(dtype=float)
    return weekly_window[rest].mean(axis=1)


def _candidate_records(
    *,
    distance: pd.DataFrame,
    weekly_window: pd.DataFrame,
    members: Sequence[str],
    adv20: Mapping[str, float],
    candidate_count: int,
    min_cluster_corr: float,
    eligible: AbstractSet[str],
    enforce_correlation: bool,
    relaxed_selection: bool = False,
) -> tuple[str, tuple[CandidateRecord, ...]]:
    medoid = compute_medoid(distance, members)
    neighborhood = candidate_neighborhood(
        distance,
        members,
        medoid=medoid,
        candidate_count=candidate_count,
    )
    records: list[CandidateRecord] = []
    for code in neighborhood:
        distance_to_medoid = float(distance.loc[medoid, code])
        corr: float | None = None
        adv_value: float | None = None
        reason = ""

        raw_adv = adv20.get(code)
        if code not in eligible:
            reason = NOT_TRADABLE
        elif raw_adv is None or not np.isfinite(raw_adv) or raw_adv <= 0:
            reason = NO_ADV
        else:
            adv_value = float(raw_adv)
            series = (
                weekly_window[code]
                if code in weekly_window.columns
                else pd.Series(dtype=float)
            )
            index = leave_one_out_cluster_index(
                weekly_window,
                members,
                exclude=code,
            )
            if series.notna().sum() >= 2 and not index.empty:
                corr_value = series.corr(index)
                if corr_value is not None and np.isfinite(corr_value):
                    corr = float(corr_value)
            if enforce_correlation:
                if corr is None:
                    reason = INSUFFICIENT_DATA
                elif corr < min_cluster_corr and not relaxed_selection:
                    reason = LOW_CLUSTER_CORR

        records.append(
            CandidateRecord(
                code=code,
                distance_to_medoid=distance_to_medoid,
                leave_one_out_corr=corr,
                adv20=adv_value,
                excluded_reason=reason,
            )
        )
    return medoid, tuple(records)


def select_representative(
    *,
    distance: pd.DataFrame,
    weekly_window: pd.DataFrame,
    members: Sequence[str],
    adv20: Mapping[str, float],
    candidate_count: int,
    min_cluster_corr: float,
    eligible: AbstractSet[str],
    tie_break: Mapping[str, tuple[int, int]] | None = None,
    relaxed_selection: bool = False,
) -> RepresentativeSelection:
    """Select a fresh representative on a reclustering date."""
    if not members:
        return RepresentativeSelection(
            medoid="",
            candidates=(),
            selected=None,
            exclusion_reason=SINGLE_MEMBER_CLUSTER,
        )
    if len(members) == 1 and not relaxed_selection:
        medoid = str(members[0]) if members else ""
        return RepresentativeSelection(
            medoid=medoid,
            candidates=(),
            selected=None,
            exclusion_reason=SINGLE_MEMBER_CLUSTER,
        )
    if len(members) == 1:
        code = str(members[0])
        raw_adv = adv20.get(code)
        adv_value = (
            float(raw_adv)
            if raw_adv is not None and np.isfinite(raw_adv) and raw_adv > 0
            else None
        )
        reason = ""
        if code not in eligible:
            reason = NOT_TRADABLE
        elif adv_value is None:
            reason = NO_ADV
        record = CandidateRecord(
            code=code,
            distance_to_medoid=0.0,
            leave_one_out_corr=None,
            adv20=adv_value,
            excluded_reason=reason,
        )
        return RepresentativeSelection(
            medoid=code,
            candidates=(record,),
            selected=code if not reason else None,
            exclusion_reason="" if not reason else NO_ELIGIBLE_REPRESENTATIVE,
        )

    medoid, records = _candidate_records(
        distance=distance,
        weekly_window=weekly_window,
        members=members,
        adv20=adv20,
        candidate_count=candidate_count,
        min_cluster_corr=min_cluster_corr,
        eligible=eligible,
        enforce_correlation=True,
        relaxed_selection=relaxed_selection,
    )
    viable = [record for record in records if not record.excluded_reason]
    if not viable:
        return RepresentativeSelection(
            medoid=medoid,
            candidates=records,
            selected=None,
            exclusion_reason=NO_ELIGIBLE_REPRESENTATIVE,
        )

    def tie_values(code: str) -> tuple[int, int]:
        if tie_break is not None and code in tie_break:
            return tie_break[code]
        return (0, 0)

    best = min(
        viable,
        key=lambda record: (
            -float(record.adv20 or 0.0),
            -tie_values(record.code)[0],
            -tie_values(record.code)[1],
            record.code,
        ),
    )
    return RepresentativeSelection(
        medoid=medoid,
        candidates=records,
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
    tie_break: Mapping[str, tuple[int, int]] | None = None,
    relaxed_selection: bool = False,
) -> RepresentativeSelection:
    """Keep a locked representative unless a hard failure occurs.

    With ``current=None`` this performs a fresh reclustering-date selection.
    Otherwise correlation and liquidity ranking are diagnostic only. A current
    representative remains locked when it is tradable and has positive causal
    liquidity. On hard failure the first tradable/liquid member in the frozen
    neighborhood becomes the fallback; correlation is not re-gated between
    reclusters.
    """
    if current is None:
        return select_representative(
            distance=distance,
            weekly_window=weekly_window,
            members=members,
            adv20=adv20,
            candidate_count=candidate_count,
            min_cluster_corr=min_cluster_corr,
            eligible=eligible,
            tie_break=tie_break,
            relaxed_selection=relaxed_selection,
        )
    if not members:
        return RepresentativeSelection(
            medoid="",
            candidates=(),
            selected=None,
            exclusion_reason=SINGLE_MEMBER_CLUSTER,
            lock_maintained=False,
        )
    if len(members) == 1:
        medoid = str(members[0]) if members else ""
        code = medoid
        raw_adv = adv20.get(code)
        hard_valid = (
            code in eligible
            and raw_adv is not None
            and np.isfinite(raw_adv)
            and raw_adv > 0
        )
        record = CandidateRecord(
            code=code,
            distance_to_medoid=0.0,
            leave_one_out_corr=None,
            adv20=float(raw_adv) if hard_valid else None,
            excluded_reason="" if hard_valid else (
                NOT_TRADABLE if code not in eligible else NO_ADV
            ),
        )
        return RepresentativeSelection(
            medoid=medoid,
            candidates=(record,),
            selected=code if hard_valid else None,
            exclusion_reason="" if hard_valid else NO_ELIGIBLE_REPRESENTATIVE,
            lock_maintained=hard_valid,
        )

    medoid, records = _candidate_records(
        distance=distance,
        weekly_window=weekly_window,
        members=members,
        adv20=adv20,
        candidate_count=candidate_count,
        min_cluster_corr=min_cluster_corr,
        eligible=eligible,
        enforce_correlation=False,
    )
    current_record = next(
        (record for record in records if record.code == current),
        None,
    )
    if current_record is not None and not current_record.excluded_reason:
        return RepresentativeSelection(
            medoid=medoid,
            candidates=records,
            selected=current,
            exclusion_reason="",
            lock_maintained=True,
        )

    fallback = next(
        (record for record in records if not record.excluded_reason),
        None,
    )
    if fallback is None:
        return RepresentativeSelection(
            medoid=medoid,
            candidates=records,
            selected=None,
            exclusion_reason=NO_ELIGIBLE_REPRESENTATIVE,
            lock_maintained=False,
        )
    return RepresentativeSelection(
        medoid=medoid,
        candidates=records,
        selected=fallback.code,
        exclusion_reason="",
        lock_maintained=False,
    )
