"""correlation_all_members strategy config (design §4/§17/§32.2).

Frozen, extra-forbid Pydantic model mirroring the legacy ``FundRotationConfig``
fields so the baseline strategy is driven by a schema-validated config that also
produces a JSON Schema for the frontend. A one-way ``from_legacy`` adapter
converts the old dataclass; new code never depends on the old type.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CorrelationAllMembersConfig(BaseModel):
    """Complete config for the correlation-clustering all-members baseline."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # ── Strategy parameters ──
    k: int = Field(8, ge=1, description="聚类簇数量 K")
    top_n: int = Field(3, ge=1, description="动量 Top-N 入选簇数")
    momentum_window_weeks: int = Field(4, ge=1, description="动量回看周数")
    rebalance_freq: str = Field("W", description="调仓频率")
    recluster_interval_weeks: int = Field(26, ge=1, description="重聚类间隔周数")
    correlation_lookback_weeks: int = Field(52, ge=1, description="相关性回看周数")
    min_training_weeks: int = Field(52, ge=1, description="最小训练周数")
    min_valid_weeks: int = Field(20, ge=1, description="最小有效周数")
    min_pairwise_weeks: int = Field(20, ge=1, description="最小成对周数")
    momentum_threshold: float = Field(0.0, description="动量阈值")

    # ── Capital and fees ──
    initial_capital: float = Field(1_000_000.0, gt=0, description="初始资金")
    commission_rate: float = Field(0.00025, ge=0, description="佣金费率")
    commission_min: float = Field(5.0, ge=0, description="最低佣金(CNY)")
    other_fee_rate: float = Field(0.0, ge=0, description="其他费率")

    # ── Capacity and slippage ──
    max_participation_rate: float = Field(0.05, gt=0, description="最大参与率")
    adv_lookback: int = Field(20, ge=1, description="ADV 回看天数")
    adv_min_observations: int = Field(10, ge=1, description="ADV 最小观测数")
    base_slippage_bps: float = Field(5.0, ge=0, description="基础滑点(bps)")
    max_slippage_bps: float = Field(30.0, ge=0, description="最大滑点(bps)")
    lot_size: int = Field(100, ge=1, description="交易单位(份)")

    # ── Date range ──
    start_date: str = Field("", description="评价开始日(YYYYMMDD)")
    end_date: str = Field("", description="评价结束日(YYYYMMDD)")

    @model_validator(mode="after")
    def _cross_field_constraints(self) -> "CorrelationAllMembersConfig":
        if self.top_n > self.k:
            raise ValueError(f"top_n must be in [1, k={self.k}], got {self.top_n}")
        if self.min_pairwise_weeks > self.correlation_lookback_weeks:
            raise ValueError(
                f"min_pairwise_weeks ({self.min_pairwise_weeks}) must be <= "
                f"correlation_lookback_weeks ({self.correlation_lookback_weeks})"
            )
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError(
                f"start_date ({self.start_date}) must be <= end_date ({self.end_date})"
            )
        return self

    @classmethod
    def from_legacy(cls, legacy) -> "CorrelationAllMembersConfig":
        """One-way adapter from the legacy ``FundRotationConfig`` dataclass.

        Only old -> new conversion is permitted; new code must not depend on the
        legacy type (design §32.2).
        """
        fields = {f for f in cls.model_fields}
        return cls(**{f: getattr(legacy, f) for f in fields if hasattr(legacy, f)})
