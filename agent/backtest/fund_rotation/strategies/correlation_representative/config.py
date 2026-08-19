"""correlation_representative strategy config (design §4/§17, Phase 3 Task 1).

Frozen, extra-forbid Pydantic model with the exact design §4 field set (no
legacy aliases). All clustering-gate thresholds belong to THIS strategy —
never to the common Runner or ExecutionConfig (§4). Conflicting gate
combinations are rejected at construction time ("before launch").
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CorrelationRepresentativeConfig(BaseModel):
    """Complete config for the correlation-clustering representative-ETF
    strategy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # ── Clustering and momentum ──
    k: int = Field(8, ge=1, description="聚类簇数量 K")
    top_n: int = Field(3, ge=1, description="动量 Top-N 入选簇数")
    correlation_lookback_weeks: int = Field(52, ge=1, description="相关性回看周数")
    momentum_window_weeks: int = Field(4, ge=1, description="动量回看周数")
    recluster_interval_weeks: int = Field(26, ge=1, description="重聚类间隔周数")
    min_valid_weeks: int = Field(20, ge=1, description="最小有效周数")
    min_pairwise_weeks: int = Field(20, ge=1, description="最小成对周数")

    # ── Representative ETF selection (§8.1/§8.2) ──
    representative_candidate_count: int = Field(
        5, ge=1, description="medoid 邻域候选数量 M",
    )
    representative_min_cluster_corr: float = Field(
        0.85, gt=0, le=1,
        description="代表 ETF 与留一簇指数的最小相关系数",
    )
    representative_liquidity_window_days: int = Field(
        20, ge=1, description="ADV 流动性窗口（交易日）",
    )
    representative_min_liquidity_observations: int = Field(
        15, ge=1, description="ADV 最小观测数",
    )

    # ── Cluster quality gates (§9) — strategy-owned, warn/reject pairs ──
    max_cluster_share_warn: float = Field(
        0.50, gt=0, le=1, description="最大簇占比 warning 阈值",
    )
    max_cluster_share_reject: float = Field(
        0.80, gt=0, le=1, description="最大簇占比 reject 阈值",
    )
    min_effective_cluster_count_warn: float = Field(
        4.0, gt=0, description="有效簇数量 warning 阈值",
    )
    min_effective_cluster_count_reject: float = Field(
        2.5, gt=0, description="有效簇数量 reject 阈值",
    )

    @model_validator(mode="after")
    def _cross_field_constraints(self) -> "CorrelationRepresentativeConfig":
        if self.top_n > self.k:
            raise ValueError(f"top_n must be in [1, k={self.k}], got {self.top_n}")
        if self.momentum_window_weeks >= self.correlation_lookback_weeks:
            # The momentum window must stay a strict subset of the correlation
            # window (Phase 2 parity domain).
            raise ValueError(
                f"momentum_window_weeks ({self.momentum_window_weeks}) must be < "
                f"correlation_lookback_weeks ({self.correlation_lookback_weeks})"
            )
        if self.min_pairwise_weeks > self.correlation_lookback_weeks:
            raise ValueError(
                f"min_pairwise_weeks ({self.min_pairwise_weeks}) must be <= "
                f"correlation_lookback_weeks ({self.correlation_lookback_weeks})"
            )
        if self.min_valid_weeks > self.correlation_lookback_weeks:
            # An unsatisfiable gate (valid weeks can never exceed the window).
            raise ValueError(
                f"min_valid_weeks ({self.min_valid_weeks}) must be <= "
                f"correlation_lookback_weeks ({self.correlation_lookback_weeks})"
            )
        if (
            self.representative_min_liquidity_observations
            > self.representative_liquidity_window_days
        ):
            raise ValueError(
                "representative_min_liquidity_observations "
                f"({self.representative_min_liquidity_observations}) must be <= "
                f"representative_liquidity_window_days "
                f"({self.representative_liquidity_window_days})"
            )
        # Gate threshold conflicts are rejected before launch (§4).
        if self.max_cluster_share_warn >= self.max_cluster_share_reject:
            raise ValueError(
                "max_cluster_share_warn "
                f"({self.max_cluster_share_warn}) must be strictly < "
                f"max_cluster_share_reject ({self.max_cluster_share_reject})"
            )
        if (
            self.min_effective_cluster_count_warn
            <= self.min_effective_cluster_count_reject
        ):
            raise ValueError(
                "min_effective_cluster_count_warn "
                f"({self.min_effective_cluster_count_warn}) must be strictly > "
                "min_effective_cluster_count_reject "
                f"({self.min_effective_cluster_count_reject})"
            )
        return self
