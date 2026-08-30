"""R73: R39 with a pre-registered equal-weight multi-horizon rank."""

from .strategy import (
    AiRotationR73R39MultiHorizonRankSession,
    AiRotationR73R39MultiHorizonRankStrategy,
    DESCRIPTOR,
    aggregate_multi_horizon_rank_scores,
    rank_period_scores,
)

__all__ = [
    "AiRotationR73R39MultiHorizonRankSession",
    "AiRotationR73R39MultiHorizonRankStrategy",
    "DESCRIPTOR",
    "aggregate_multi_horizon_rank_scores",
    "rank_period_scores",
]
