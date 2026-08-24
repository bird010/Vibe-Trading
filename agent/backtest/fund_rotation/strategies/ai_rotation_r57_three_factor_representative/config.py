"""Frozen configuration for the article three-factor representative strategy."""

from __future__ import annotations

import math

from pydantic import ConfigDict, Field, field_validator, model_validator

from backtest.fund_rotation.strategies.correlation_representative.config import (
    CorrelationRepresentativeConfig,
)


class ArticleThreeFactorRepresentativeConfig(CorrelationRepresentativeConfig):
    model_config = ConfigDict(frozen=True, extra="forbid")

    top_n: int = Field(1, ge=1)
    bias_ma_days: int = Field(25, ge=2)
    bias_regression_days: int = Field(25, ge=2)
    slope_days: int = Field(25, ge=2)
    efficiency_days: int = Field(25, ge=2)
    bias_weight: float = 0.3
    slope_weight: float = 0.3
    efficiency_weight: float = 0.4
    rebalance_threshold: float = Field(1.5, gt=0)
    target_weight: float = 1.0
    minimum_complete_candidates: int = 2
    zscore_ddof: int = 0
    rebalance_freq: str = "D"

    @field_validator(
        "bias_weight", "slope_weight", "efficiency_weight", "rebalance_threshold",
        "target_weight", mode="before",
    )
    @classmethod
    def _finite_float(cls, value: object) -> float:
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("article parameters must be finite")
        return result

    @model_validator(mode="after")
    def _fixed_article_parameters(self) -> "ArticleThreeFactorRepresentativeConfig":
        fixed = {
            "top_n": 1, "bias_ma_days": 25, "bias_regression_days": 25,
            "slope_days": 25, "efficiency_days": 25, "bias_weight": 0.3,
            "slope_weight": 0.3, "efficiency_weight": 0.4,
            "rebalance_threshold": 1.5, "target_weight": 1.0,
            "minimum_complete_candidates": 2, "zscore_ddof": 0,
            "rebalance_freq": "D",
        }
        for name, expected in fixed.items():
            if getattr(self, name) != expected:
                raise ValueError(f"{name} is frozen at {expected!r}")
        if abs(self.bias_weight + self.slope_weight + self.efficiency_weight - 1.0) > 1e-12:
            raise ValueError("article factor weights must sum to 1")
        return self
