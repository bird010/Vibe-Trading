"""Generic, serializable contracts for strategy-provided scores."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Protocol, Sequence


class ScoreDirection(str, Enum):
    HIGHER_BETTER = "HIGHER_BETTER"
    LOWER_BETTER = "LOWER_BETTER"


@dataclass(frozen=True)
class StrategyScore:
    """One score value with enough metadata for generic ranking/evidence."""

    value: float | None
    eligible: bool
    subject_id: str | None = None
    display_label: str = "策略得分"
    model_label: str = "Strategy Score"
    frequency: str = "UNKNOWN"
    scope: str = "INSTRUMENT"
    direction: ScoreDirection = ScoreDirection.HIGHER_BETTER
    model_id: str = "strategy_score"
    model_version: str = "1"
    components: Mapping[str, float | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.value, bool):
            raise TypeError("score value must be numeric or None")
        if self.value is not None and not math.isfinite(float(self.value)):
            raise ValueError("score value must be finite or None")
        if self.eligible and self.value is None:
            raise ValueError("eligible score must have a value")

    @property
    def label(self) -> str:
        """Legacy alias for the user-facing display label."""
        return self.display_label


class ScoreModel(Protocol):
    """Metadata-only model contract; strategies own their input features."""

    id: str
    label: str
    version: str


def rank_scores(
    scores: Mapping[object, StrategyScore],
    *,
    cluster_members: Mapping[object, Sequence[str]] | None = None,
) -> list[object]:
    """Order eligible scores using the strategy's canonical tie-break."""
    eligible = [
        (cluster_id, score)
        for cluster_id, score in scores.items()
        if score.eligible and score.value is not None
    ]

    def tie_code(subject: object, score: StrategyScore) -> str:
        members = cluster_members.get(subject, ()) if cluster_members else ()
        if members:
            return min(members)
        return score.subject_id or str(subject)

    direction = next(
        (score.direction for _, score in eligible),
        ScoreDirection.HIGHER_BETTER,
    )
    if direction is ScoreDirection.LOWER_BETTER:
        ordered = sorted(eligible, key=lambda item: (float(item[1].value), tie_code(item[0], item[1])))
    else:
        ordered = sorted(eligible, key=lambda item: (-float(item[1].value), tie_code(item[0], item[1])))
    return [cluster_id for cluster_id, _ in ordered]


def select_top_scores(
    scores: Mapping[object, StrategyScore],
    *,
    top_n: int,
    cluster_members: Mapping[object, Sequence[str]] | None = None,
) -> list[object]:
    """Select the first N scores from the canonical ranking."""
    return rank_scores(scores, cluster_members=cluster_members)[: max(top_n, 0)]
