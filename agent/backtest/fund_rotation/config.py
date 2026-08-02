"""Fund rotation configuration — §2 baseline parameters.

All parameters are frozen (immutable) after construction.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FundRotationConfig:
    """Complete parameter set for one fund rotation backtest run.

    Defaults follow §2 confirmed baseline parameters.
    """

    # ── Strategy parameters ──
    k: int = 8
    top_n: int = 3
    momentum_window_weeks: int = 4
    rebalance_freq: str = "W"
    recluster_interval_weeks: int = 26
    correlation_lookback_weeks: int = 52
    min_training_weeks: int = 52
    min_valid_weeks: int = 20
    min_pairwise_weeks: int = 20
    momentum_threshold: float = 0.0

    # ── Capital and fees ──
    initial_capital: float = 1_000_000.0
    commission_rate: float = 0.00025  # 2.5 bps
    commission_min: float = 5.0  # CNY
    other_fee_rate: float = 0.0

    # ── Capacity and slippage ──
    max_participation_rate: float = 0.05
    adv_lookback: int = 20
    adv_min_observations: int = 10
    base_slippage_bps: float = 5.0
    max_slippage_bps: float = 30.0
    lot_size: int = 100

    # ── Date range ──
    start_date: str = ""
    end_date: str = ""

    def __post_init__(self) -> None:
        """Validate parameter constraints."""
        if self.k < 1:
            raise ValueError(f"k must be >= 1, got {self.k}")
        if self.top_n < 1 or self.top_n > self.k:
            raise ValueError(f"top_n must be in [1, k={self.k}], got {self.top_n}")
        if self.initial_capital <= 0:
            raise ValueError(f"initial_capital must be > 0, got {self.initial_capital}")
        if self.momentum_window_weeks < 1:
            raise ValueError(f"momentum_window_weeks must be >= 1, got {self.momentum_window_weeks}")
        if self.min_pairwise_weeks > self.correlation_lookback_weeks:
            raise ValueError(
                f"min_pairwise_weeks ({self.min_pairwise_weeks}) must be <= "
                f"correlation_lookback_weeks ({self.correlation_lookback_weeks})"
            )
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError(
                f"start_date ({self.start_date}) must be <= end_date ({self.end_date})"
            )
