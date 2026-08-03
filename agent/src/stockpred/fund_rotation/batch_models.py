"""Strategy batch request models and canonical client-payload identity."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

RESEARCH_ONLY = "RESEARCH_ONLY"
SUPPORTED_SCHEMA_VERSION = "1"
MAX_VARIANTS_PER_BATCH = 50
_DECLARED_ALIASES: dict[str, str] = {}


class BatchVariantRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy_id: str = Field(min_length=1, max_length=128)
    label: str | None = Field(default=None, max_length=128)
    params: dict[str, Any] = Field(default_factory=dict)


class BatchExecutionRequest(BaseModel):
    """Resolved public execution inputs; defaults are identity-bearing."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    initial_capital: float = Field(default=1_000_000.0, gt=0)
    commission_rate: float = Field(default=0.00025, ge=0)
    commission_min: float = Field(default=5.0, ge=0)
    other_fee_rate: float = Field(default=0.0, ge=0)
    max_participation_rate: float = Field(default=0.05, gt=0, le=1)
    adv_lookback: int = Field(default=20, ge=1)
    adv_min_observations: int = Field(default=10, ge=1)
    base_slippage_bps: float = Field(default=5.0, ge=0)
    max_slippage_bps: float = Field(default=30.0, ge=0)
    lot_size: int = Field(default=100, ge=1)

    @model_validator(mode="after")
    def _cross_field_constraints(self) -> "BatchExecutionRequest":
        if self.adv_min_observations > self.adv_lookback:
            raise ValueError(
                "adv_min_observations must be <= adv_lookback"
            )
        if self.max_slippage_bps < self.base_slippage_bps:
            raise ValueError(
                "max_slippage_bps must be >= base_slippage_bps"
            )
        return self


class StrategyBatchRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str
    idempotency_key: str = Field(min_length=1, max_length=256)
    mode: str
    evaluation_start_date: str = Field(min_length=8, max_length=8)
    evaluation_end_date: str = Field(min_length=8, max_length=8)
    execution: BatchExecutionRequest = Field(
        default_factory=BatchExecutionRequest
    )
    variants: list[BatchVariantRequest] = Field(
        min_length=1,
        max_length=MAX_VARIANTS_PER_BATCH,
    )

    @field_validator("schema_version")
    @classmethod
    def _supported_schema(cls, value: str) -> str:
        if value != SUPPORTED_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {SUPPORTED_SCHEMA_VERSION!r}, "
                f"got {value!r}"
            )
        return value

    @field_validator("mode")
    @classmethod
    def _mode_research_only(cls, value: str) -> str:
        return validate_research_mode(value)

    @field_validator("evaluation_start_date", "evaluation_end_date")
    @classmethod
    def _strict_date(cls, value: str) -> str:
        try:
            parsed = datetime.strptime(value, "%Y%m%d")
        except ValueError as exc:
            raise ValueError(
                f"invalid YYYYMMDD date: {value!r}"
            ) from exc
        normalized = parsed.strftime("%Y%m%d")
        if normalized != value:
            raise ValueError(f"invalid YYYYMMDD date: {value!r}")
        return value

    @model_validator(mode="after")
    def _date_order(self) -> "StrategyBatchRequest":
        if self.evaluation_start_date > self.evaluation_end_date:
            raise ValueError(
                "evaluation_start_date must be <= evaluation_end_date"
            )
        return self


def validate_research_mode(value: str) -> str:
    if value != RESEARCH_ONLY:
        raise ValueError(
            f"mode must be exactly {RESEARCH_ONLY!r}, got {value!r}"
        )
    return value


def canonical_payload_hash(request: StrategyBatchRequest) -> str:
    payload = request.model_dump(mode="json")
    for old_name, new_name in _DECLARED_ALIASES.items():
        if old_name in payload:
            payload.setdefault(new_name, payload.pop(old_name))
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
