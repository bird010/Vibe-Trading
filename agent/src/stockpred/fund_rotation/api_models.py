"""Fund-rotation API response models.

The catalog models describe strategy discovery. Run-detail models describe the
checksum-gated child-run read APIs used by the research UI.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    warmup_trade_days: int
    required_datasets: tuple[str, ...]
    required_fields: tuple[str, ...]
    frequency: str


class StrategyListResponse(BaseModel):
    mode: str = "RESEARCH_ONLY"
    catalog_version: str
    strategies: list[StrategySummary] = Field(default_factory=list)


class StrategyDetail(StrategySummary):
    """Full detail for the dynamic configuration form."""

    mode: str = "RESEARCH_ONLY"
    config_schema: dict
    config_schema_version: str
    config_schema_hash: str
    default_config: dict
    parameter_descriptions: dict[str, str]
    artifact_roles: list[str]
    research_mode_warning: str = RESEARCH_MODE_WARNING


class BacktestPeriod(BaseModel):
    data_start: str | None = None
    decision_start_date: str | None = None
    anchor_decision_date: str | None = None
    evaluation_start_date: str | None = None
    evaluation_end_date: str | None = None


class BacktestIdentity(BaseModel):
    implementation_hash: str | None = None
    framework_implementation_hash: str | None = None
    resolved_config_hash: str | None = None
    resolved_requirements_hash: str | None = None
    snapshot_fingerprint: str | None = None
    run_identity_hash: str | None = None


class BacktestArtifact(BaseModel):
    role: str
    file: str
    media_type: str
    producer: str
    checksum: str | None = None
    rows: int | None = None
    columns: list[str] = Field(default_factory=list)


class BacktestInstrument(BaseModel):
    ts_code: str
    has_signal: bool = False
    has_order: bool = False
    has_trade: bool = False
    has_position: bool = False


class BacktestDetailResponse(BaseModel):
    schema_version: str
    run_id: str
    batch_id: str | None = None
    variant_key: str | None = None
    strategy_id: str | None = None
    label: str | None = None
    status: str
    quality_status: str | None = None
    mode: str = "RESEARCH_ONLY"
    message: str | None = None
    error: str | None = None
    result_published: bool = False
    partial: bool = False
    publishable_for_comparison: bool = False
    period: BacktestPeriod = Field(default_factory=BacktestPeriod)
    identity: BacktestIdentity = Field(default_factory=BacktestIdentity)
    resolved_config: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, float] = Field(default_factory=dict)
    instruments: list[BacktestInstrument] = Field(default_factory=list)
    artifacts: list[BacktestArtifact] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)


class InstrumentChartResponse(BaseModel):
    ts_code: str
    run_id: str
    signals: list[dict[str, Any]] = Field(default_factory=list)
    trades: list[dict[str, Any]] = Field(default_factory=list)
    ohlcv: list[dict[str, Any]] = Field(default_factory=list)
    positions: list[dict[str, Any]] = Field(default_factory=list)
    orders: list[dict[str, Any]] = Field(default_factory=list)
    ohlcv_source: dict[str, Any] = Field(default_factory=dict)
    mode: str = "RESEARCH_ONLY"

    @field_validator("trades", mode="before")
    @classmethod
    def retain_trade_markers(cls, value: object) -> list[dict[str, Any]]:
        """Expose only BUY/SELL rows to the candlestick marker component."""
        if not isinstance(value, list):
            return []
        return [
            dict(row)
            for row in value
            if isinstance(row, dict) and row.get("action") in {"BUY", "SELL"}
        ]
