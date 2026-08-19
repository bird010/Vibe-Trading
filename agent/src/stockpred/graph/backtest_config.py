"""Validated configuration for StockPred Graph historical backtests."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class GraphBacktestConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: str
    end: str
    mode: Literal["parity", "research"] = "parity"
    lookback_days: int = Field(default=120, gt=0)
    data_lookback_days: int = Field(default=180, gt=0)
    forward_days: int = Field(default=5, gt=0)
    top_n: int = Field(default=50, gt=0)
    eval_step: int = Field(default=5, gt=0)
    benchmark_code: str = "000300.SH"
    min_listed_trade_days: int = Field(default=60, ge=0)
    min_adj_coverage: float = Field(default=0.98, ge=0.0, le=1.0)
    min_valid_eval_ratio: float = Field(default=0.90, ge=0.0, le=1.0)
    buffer_retain_rank: int = Field(default=15, ge=0)
    portfolio_capital: float = Field(default=10_000_000.0, gt=0.0)
    max_participation: float = Field(default=0.05, gt=0.0, le=1.0)
    exclude_st: bool = True
    require_pit_industry: bool = True
    allowed_exchanges: tuple[str, ...] = ("SSE", "SZSE")
    parity_reference: str | None = None

    @field_validator("start", "end", mode="before")
    @classmethod
    def normalize_date(cls, value: object) -> str:
        normalized = str(value).replace("-", "")
        datetime.strptime(normalized, "%Y%m%d")
        return normalized

    @model_validator(mode="after")
    def validate_boundaries(self) -> "GraphBacktestConfig":
        if self.start > self.end:
            raise ValueError("start must not be after end")
        locked = {
            "top_n": 50,
            "eval_step": 5,
            "forward_days": 5,
            "benchmark_code": "000300.SH",
            "portfolio_capital": 10_000_000.0,
            "max_participation": 0.05,
        }
        if self.mode == "parity" and any(
            getattr(self, name) != expected for name, expected in locked.items()
        ):
            raise ValueError("parity mode parameters are locked")
        return self
