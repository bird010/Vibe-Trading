"""Immutable contracts shared by StockPred strategy backtests."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Mapping

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


StrategyKind = Literal["graph", "alpha_zoo"]
MetricSortField = Literal[
    "sharpe", "annual_return", "max_drawdown", "win_rate", "turnover", "strategy_name", "status"
]


class StrategyDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: StrategyKind
    zoo: str | None = None
    columns_required: tuple[str, ...] = ()
    min_warmup_bars: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class StrategySourceFile(BaseModel):
    model_config = ConfigDict(frozen=True)
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content: str


class StrategySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)
    descriptor: StrategyDescriptor
    source_files: tuple[StrategySourceFile, ...]
    strategy_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    git_commit: str | None = None
    git_dirty: bool = False
    python_version: str
    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class StrategyScore:
    scores: pd.DataFrame
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


class StrategyBacktestConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    strategy_snapshot: StrategySnapshot
    start: str
    end: str
    batch_id: str
    comparison_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    mode: Literal["parity", "research"] = "parity"
    lookback_days: int = Field(default=120, gt=0)
    data_lookback_days: int = Field(default=180, gt=0)
    forward_days: int = Field(default=5, gt=0)
    top_n: int = Field(default=50, gt=0)
    eval_step: int = Field(default=5, gt=0)
    benchmark_code: str = "H00300.CSI"
    min_listed_trade_days: int = Field(default=60, ge=0)
    min_adj_coverage: float = Field(default=0.98, ge=0.0, le=1.0)
    min_valid_eval_ratio: float = Field(default=0.90, ge=0.0, le=1.0)
    buffer_retain_rank: int = Field(default=15, ge=0)
    portfolio_capital: float = Field(default=10_000_000.0, gt=0.0)
    max_participation: float = Field(default=0.05, gt=0.0, le=1.0)
    exclude_st: bool = True
    require_pit_industry: bool = True
    allowed_exchanges: tuple[str, ...] = ("SSE", "SZSE")

    @field_validator("start", "end", mode="before")
    @classmethod
    def normalize_date(cls, value: object) -> str:
        normalized = str(value).replace("-", "")
        datetime.strptime(normalized, "%Y%m%d")
        return normalized

    @model_validator(mode="after")
    def validate_dates(self) -> "StrategyBacktestConfig":
        if self.start > self.end:
            raise ValueError("start must not be after end")
        return self


class StrategyBatchRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    start: str
    end: str
    strategy_ids: tuple[str, ...] = ()
    select_all: bool = False
    mode: Literal["parity", "research"] = "parity"
    evaluation_engine: Literal["cohort", "portfolio"] = "cohort"
    top_n: int = Field(default=50, ge=1, le=500)
    eval_step: int = Field(default=5, ge=1, le=60)
    forward_days: int = Field(default=5, ge=1, le=60)
    portfolio_capital: float = Field(default=10_000_000.0, gt=0.0)
    max_participation: float = Field(default=0.05, gt=0.0, le=1.0)

    @model_validator(mode="before")
    @classmethod
    def normalize_strategy_ids(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        values = dict(value)
        raw_ids = values.get("strategy_ids", ()) or ()
        values["strategy_ids"] = tuple(dict.fromkeys(str(item) for item in raw_ids))
        return values

    @field_validator("start", "end", mode="before")
    @classmethod
    def normalize_date(cls, value: object) -> str:
        normalized = str(value).replace("-", "")
        datetime.strptime(normalized, "%Y%m%d")
        return normalized

    @model_validator(mode="after")
    def validate_request(self) -> "StrategyBatchRequest":
        if self.start > self.end:
            raise ValueError("start must not be after end")
        if self.select_all == bool(self.strategy_ids):
            raise ValueError("select exactly one of strategy_ids or select_all")
        if self.mode == "parity":
            locked = {
                "top_n": 50,
                "eval_step": 5,
                "forward_days": 5,
                "portfolio_capital": 10_000_000.0,
                "max_participation": 0.05,
            }
            if any(getattr(self, name) != expected for name, expected in locked.items()):
                raise ValueError("parity mode parameters are locked")
        return self


def metric_sort_value(row: Mapping[str, object], field_name: MetricSortField) -> float | str | None:
    """Return a sortable value without treating invalid metrics as zero."""
    value = row.get(field_name)
    if field_name in {"strategy_name", "status"}:
        return str(value) if value is not None else None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
