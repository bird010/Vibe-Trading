"""Frozen v4.3 Economic Role strategy configuration."""

from __future__ import annotations

from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator


_DEFAULT_MANIFEST = {
    "CN_DEFENSIVE_EQUITY": ["515180.SH", "510880.SH", "515100.SH"],
    "CN_GROWTH_EQUITY": ["159949.SZ", "588000.SH", "159915.SZ"],
    "OVERSEAS_GROWTH_EQUITY": ["513100.SH", "513500.SH", "513300.SH"],
    "GOLD": ["518880.SH", "518800.SH", "159934.SZ"],
    "BOND": ["511010.SH", "511260.SH", "511090.SH"],
}


class EconomicRoleConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    top_n: int = Field(3, ge=1)
    history_quality_lookback_weeks: int = Field(52, ge=1)
    correlation_lookback_weeks: int = Field(52, ge=1)
    min_valid_weeks: int = Field(20, ge=1)
    momentum_window_weeks: int = Field(4, ge=1)
    refresh_interval_weeks: int = Field(26, ge=1)
    warmup_trade_days: int = Field(264, ge=1)
    representative_liquidity_window_days: int = Field(20, ge=1)
    representative_min_liquidity_observations: int = Field(15, ge=1)
    fixed_role_manifest: Mapping[str, tuple[str, ...]] = Field(
        default_factory=lambda: {
            key: tuple(value) for key, value in _DEFAULT_MANIFEST.items()
        },
        json_schema_extra={"readOnly": True},
    )

    @model_validator(mode="after")
    def _constraints(self) -> "EconomicRoleConfig":
        if self.history_quality_lookback_weeks != self.correlation_lookback_weeks:
            raise ValueError(
                "history_quality_lookback_weeks must equal "
                "correlation_lookback_weeks"
            )
        if self.min_valid_weeks > self.history_quality_lookback_weeks:
            raise ValueError("min_valid_weeks must be <= history_quality_lookback_weeks")
        if self.momentum_window_weeks >= self.history_quality_lookback_weeks:
            raise ValueError("momentum_window_weeks must be < history_quality_lookback_weeks")
        if self.representative_min_liquidity_observations > self.representative_liquidity_window_days:
            raise ValueError(
                "representative_min_liquidity_observations must be <= "
                "representative_liquidity_window_days"
            )
        missing = set(self.fixed_role_manifest) ^ {
            "CN_DEFENSIVE_EQUITY",
            "CN_GROWTH_EQUITY",
            "OVERSEAS_GROWTH_EQUITY",
            "GOLD",
            "BOND",
        }
        if missing:
            raise ValueError(f"fixed_role_manifest role keys mismatch: {sorted(missing)}")
        if any(not tuple(value) for value in self.fixed_role_manifest.values()):
            raise ValueError("fixed_role_manifest entries must not be empty")
        return self
