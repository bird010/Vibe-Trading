"""Strategy-only config for correlation_all_members.

Execution costs, capacity, initial capital and evaluation dates belong to the
batch-level execution/evaluation contracts. They are intentionally absent from
this model and from its JSON Schema and resolved-config hash.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CorrelationAllMembersConfig(BaseModel):
    """Algorithm parameters owned by the all-members strategy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    k: int = Field(8, ge=1, description="聚类簇数量 K")
    top_n: int = Field(3, ge=1, description="动量 Top-N 入选簇数")
    momentum_window_weeks: int = Field(
        4,
        ge=1,
        description="动量回看周数",
    )
    rebalance_freq: str = Field("W", description="调仓频率")
    recluster_interval_weeks: int = Field(
        26,
        ge=1,
        description="重聚类间隔周数",
    )
    correlation_lookback_weeks: int = Field(
        52,
        ge=1,
        description="相关性回看周数",
    )
    min_training_weeks: int = Field(
        52,
        ge=1,
        description="最小训练周数",
    )
    min_valid_weeks: int = Field(
        20,
        ge=1,
        description="最小有效周数",
    )
    min_pairwise_weeks: int = Field(
        20,
        ge=1,
        description="最小成对周数",
    )
    momentum_threshold: float = Field(0.0, description="动量阈值")

    @model_validator(mode="after")
    def _cross_field_constraints(self) -> "CorrelationAllMembersConfig":
        if self.top_n > self.k:
            raise ValueError(
                f"top_n must be in [1, k={self.k}], got {self.top_n}"
            )
        if self.momentum_window_weeks >= self.correlation_lookback_weeks:
            raise ValueError(
                f"momentum_window_weeks ({self.momentum_window_weeks}) "
                "must be < correlation_lookback_weeks "
                f"({self.correlation_lookback_weeks})"
            )
        if self.min_pairwise_weeks > self.correlation_lookback_weeks:
            raise ValueError(
                f"min_pairwise_weeks ({self.min_pairwise_weeks}) must be <= "
                f"correlation_lookback_weeks ({self.correlation_lookback_weeks})"
            )
        if self.min_valid_weeks > self.correlation_lookback_weeks:
            raise ValueError(
                f"min_valid_weeks ({self.min_valid_weeks}) must be <= "
                f"correlation_lookback_weeks ({self.correlation_lookback_weeks})"
            )
        return self

    @classmethod
    def from_legacy(cls, legacy) -> "CorrelationAllMembersConfig":
        """One-way adapter that copies only strategy-owned legacy fields."""
        return cls(
            **{
                field_name: getattr(legacy, field_name)
                for field_name in cls.model_fields
                if hasattr(legacy, field_name)
            }
        )
