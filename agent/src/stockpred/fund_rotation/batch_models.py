"""Strategy batch request models and normalization — Phase 4 Task 2 (§21/§21.1).

Single submission shape for one or many variants. The idempotency hash is
computed over ``schema_version`` + the canonicalized CLIENT payload only:
JSON typing, object-key sorting and declared aliases — never re-resolved
strategy defaults, never server-generated fields (batch_id, timestamps,
snapshots). The resolved strategy/config/implementation hashes are stored
separately as ``resolved_batch_identity`` (§21.1).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RESEARCH_ONLY = "RESEARCH_ONLY"

# Declared request aliases normalized before hashing (§21.1). None today; the
# hook stays explicit so future aliases are a visible, tested change.
_DECLARED_ALIASES: dict[str, str] = {}


class BatchVariantRequest(BaseModel):
    """One strategy variant: identity params only — ``label`` is display-only
    and never enters the run identity (§21)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy_id: str = Field(min_length=1)
    label: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class StrategyBatchRequest(BaseModel):
    """Unified batch submission (§21): single- and multi-strategy share it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    mode: str = Field(min_length=1)
    evaluation_start_date: str = Field(min_length=8, max_length=8)
    evaluation_end_date: str = Field(min_length=8, max_length=8)
    execution: dict[str, Any] = Field(default_factory=dict)
    variants: list[BatchVariantRequest] = Field(min_length=1)

    @field_validator("mode")
    @classmethod
    def _mode_research_only(cls, value: str) -> str:
        return validate_research_mode(value)

    @model_validator(mode="after")
    def _date_order(self) -> "StrategyBatchRequest":
        if self.evaluation_start_date > self.evaluation_end_date:
            raise ValueError(
                "evaluation_start_date must be <= evaluation_end_date"
            )
        return self


def validate_research_mode(value: str) -> str:
    """RESEARCH_ONLY is enforced exactly — no case variants, no live modes."""
    if value != RESEARCH_ONLY:
        raise ValueError(
            f"mode must be exactly {RESEARCH_ONLY!r}, got {value!r}"
        )
    return value


def canonical_payload_hash(request: StrategyBatchRequest) -> str:
    """§21.1 — stable SHA-256 over schema_version + canonical client payload.

    Normalization is limited to JSON typing, object-key sorting and declared
    aliases; strategy defaults are NOT re-resolved here, so replaying an old
    request after a service upgrade still binds to the original batch.
    """
    payload = request.model_dump(mode="json")
    for old_name, new_name in _DECLARED_ALIASES.items():
        if old_name in payload:
            payload.setdefault(new_name, payload.pop(old_name))
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
