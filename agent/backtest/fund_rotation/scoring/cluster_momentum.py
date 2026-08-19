"""Strategy Score adapter for the existing cluster momentum calculation."""

from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np

from backtest.fund_rotation.momentum import compute_cluster_momentum

from .contracts import ScoreDirection, StrategyScore


class ClusterMomentumScoreModel:
    id = "cluster_momentum"
    label = "Cluster Momentum"
    display_label = "策略得分（周频）"
    version = "1"

    def from_values(
        self,
        values: Mapping[int, object],
        *,
        minimum_eligible_score: float = 0.0,
    ) -> dict[int, StrategyScore]:
        scores: dict[int, StrategyScore] = {}
        for cluster_id, raw_value in values.items():
            if isinstance(raw_value, bool):
                raise TypeError(f"score for cluster {cluster_id} must be numeric")
            if not isinstance(raw_value, (int, float, np.integer, np.floating)):
                raise TypeError(f"score for cluster {cluster_id} must be numeric")
            value = float(raw_value)
            finite_value = value if math.isfinite(value) else None
            scores[cluster_id] = StrategyScore(
                value=finite_value,
                eligible=(
                    finite_value is not None
                    and finite_value > minimum_eligible_score
                ),
                subject_id=f"cluster:{cluster_id}",
                display_label=self.display_label,
                model_label=self.label,
                frequency="WEEKLY",
                scope="CLUSTER",
                direction=ScoreDirection.HIGHER_BETTER,
                model_id=self.id,
                model_version=self.version,
                components={"momentum": finite_value},
            )
        return scores

    def score(
        self,
        weekly_returns,
        clusters: Mapping[str, int],
        momentum_window: int,
        *,
        minimum_eligible_score: float = 0.0,
    ) -> dict[int, StrategyScore]:
        values = compute_cluster_momentum(
            weekly_returns,
            dict(clusters),
            momentum_window,
        )
        return self.from_values(
            values,
            minimum_eligible_score=minimum_eligible_score,
        )
