"""Fund-rotation API response models — Phase 4 Task 1 (design §16/§18).

Thin Pydantic shapes for the catalog read endpoints. Every value is derived
from the Catalog at request time; nothing is duplicated or cached in the
route layer.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

RESEARCH_MODE_WARNING = (
    "RESEARCH_ONLY：基金轮动回测仅用于研究与策略比较，不构成投资建议，"
    "不接入下单或实盘执行。"
)


class StrategySummary(BaseModel):
    """One catalog entry as shown in the strategy list."""

    model_config = ConfigDict(frozen=True)

    strategy_id: str
    name: str
    description: str
    interface_version: str
    implementation_hash: str
    supported_universe: tuple[str, ...]
    # Requirements resolved from the default config.
    warmup_trade_days: int
    required_datasets: tuple[str, ...]
    required_fields: tuple[str, ...]
    frequency: str


class StrategyListResponse(BaseModel):
    mode: str = "RESEARCH_ONLY"
    catalog_version: str
    strategies: list[StrategySummary] = Field(default_factory=list)


class StrategyDetail(StrategySummary):
    """Full detail for the dynamic configuration form (§18)."""

    mode: str = "RESEARCH_ONLY"
    config_schema: dict
    config_schema_version: str
    config_schema_hash: str
    default_config: dict
    parameter_descriptions: dict[str, str]
    artifact_roles: list[str]
    research_mode_warning: str = RESEARCH_MODE_WARNING
