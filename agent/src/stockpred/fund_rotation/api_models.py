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


def _canonical_date(value: object) -> str:
    """Normalize CSV/Lance date values to the UI's YYYYMMDD key format."""
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    digits = "".join(character for character in text if character.isdigit())
    return digits[:8] if len(digits) >= 8 else text


def _normalize_date_fields(
    value: object,
    fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for row in value:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        for field in fields:
            field_value = item.get(field)
            if field_value is not None and str(field_value).strip():
                item[field] = _canonical_date(field_value)
        normalized.append(item)
    return normalized


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


class CandidatePoolRepresentative(BaseModel):
    cluster_id: int
    cluster_size: int = 0
    selected_code: str | None = None
    selected_name: str | None = None
    selected_fund_type: str | None = None
    lock_maintained: bool = False
    exclusion_reason: str = ""


class CandidatePoolRecluster(BaseModel):
    week: str
    num_etfs: int = 0
    overall: str | None = None
    max_cluster_share: float | None = None
    max_cluster_share_status: str | None = None
    effective_cluster_count: float | None = None
    effective_cluster_count_status: str | None = None
    representatives: list[CandidatePoolRepresentative] = Field(default_factory=list)


class CandidatePoolResponse(BaseModel):
    run_id: str
    reclusters: list[CandidatePoolRecluster] = Field(default_factory=list)


class HoldingInterval(BaseModel):
    ts_code: str
    start_date: str
    end_date: str
    actual_weight: float
    target_weight: float | None = None
    market_value: float | None = None
    opened_by_signal_date: str | None = None
    changed_by_signal_date: str | None = None


class HoldingsRebalanceMarker(BaseModel):
    signal_date: str
    effective_trade_date: str | None = None
    changed_positions: int = 0
    target_changed_positions: int = 0
    actual_changed_positions: int = 0
    turnover: float | None = None
    execution_turnover: float | None = None
    quality_status: str | None = None
    cash_target_weight: float | None = None
    decision_id: str


class HoldingsTimelineResponse(BaseModel):
    schema_version: str = "1"
    run_id: str
    start_date: str
    end_date: str
    instruments: list[dict[str, str | None]] = Field(default_factory=list)
    intervals: list[HoldingInterval] = Field(default_factory=list)
    rebalance_markers: list[HoldingsRebalanceMarker] = Field(default_factory=list)


class RebalanceIndexItem(BaseModel):
    signal_date: str
    sequence: int
    quality_status: str
    changed_positions: int = 0
    target_changed_positions: int = 0
    required_changed_positions: int = 0
    actual_changed_positions: int = 0
    executed_changed_positions: int = 0
    target_count: int = 0
    turnover: float | None = None
    target_turnover: float | None = None
    required_turnover: float | None = None
    execution_turnover: float | None = None
    cash_target_weight: float | None = None
    cluster_snapshot_date: str | None = None
    has_execution: bool = False


class RebalanceIndexResponse(BaseModel):
    schema_version: str = "1"
    run_id: str
    items: list[RebalanceIndexItem] = Field(default_factory=list)


class PortfolioSnapshot(BaseModel):
    as_of_date: str | None = None
    as_of_signal_date: str | None = None
    source: str = "UNKNOWN"
    weights: dict[str, float] = Field(default_factory=dict)
    cash_weight: float | None = None


class CandidateStages(BaseModel):
    universe_eligible: bool = False
    cluster_id: int | None = None
    cluster_representative: bool = False
    ranking_eligible: bool = False
    rank: int | None = None
    portfolio_selected: bool = False


class CandidateMetric(BaseModel):
    id: str
    label: str
    value: float | None = None


class CandidateDecisionRow(BaseModel):
    ts_code: str
    name: str | None = None
    stages: CandidateStages = Field(default_factory=CandidateStages)
    primary_metric: CandidateMetric | None = None
    score: dict[str, Any] | None = None
    secondary_metrics: dict[str, float | None] = Field(default_factory=dict)
    previous_weight: float = 0.0
    before_weight: float = 0.0
    target_weight: float = 0.0
    exclusion_stage: str | None = None
    exclusion_reason: str | None = None


class StrategyDecisionMetadata(BaseModel):
    strategy_id: str | None = None
    name: str | None = None
    universe: str | None = None
    dedup_method: str | None = None
    representative_method: str | None = None
    ranking_metric: str | None = None
    score_model: dict[str, Any] | None = None
    selection_rule: str | None = None
    top_n: int | None = None
    weighting_rule: str | None = None
    rebalance_frequency: str | None = None


class ClusterSnapshot(BaseModel):
    snapshot_date: str
    overall: str | None = None
    max_cluster_share: float | None = None
    max_cluster_share_warn_threshold: float | None = None
    max_cluster_share_reject_threshold: float | None = None
    effective_cluster_count: float | None = None
    effective_cluster_count_warn_threshold: float | None = None
    effective_cluster_count_reject_threshold: float | None = None


class RebalanceDecisionQuality(BaseModel):
    decision_status: str
    reasons: list[str] = Field(default_factory=list)


class RebalanceDecisionPayload(BaseModel):
    strategy: StrategyDecisionMetadata = Field(default_factory=StrategyDecisionMetadata)
    cluster_snapshot: ClusterSnapshot | None = None
    candidates: list[CandidateDecisionRow] = Field(default_factory=list)


class RebalanceExecutionSummary(BaseModel):
    filled: int = 0
    partial: int = 0
    blocked: int = 0
    commission: float = 0.0
    turnover: float | None = None
    target_turnover: float | None = None
    execution_turnover: float | None = None
    target_changed_positions: int = 0
    required_changed_positions: int = 0
    required_turnover: float | None = None
    executed_changed_positions: int = 0
    actual_changed_positions: int = 0


class RebalanceExecution(BaseModel):
    first_trade_date: str | None = None
    last_trade_date: str | None = None
    orders: list[dict[str, Any]] = Field(default_factory=list)
    fills: list[dict[str, Any]] = Field(default_factory=list)
    summary: RebalanceExecutionSummary = Field(default_factory=RebalanceExecutionSummary)


class RebalanceDecisionResponse(BaseModel):
    schema_version: str = "1"
    run_id: str
    signal_date: str
    sequence: int
    quality: RebalanceDecisionQuality
    before: PortfolioSnapshot
    after_target: PortfolioSnapshot
    decision: RebalanceDecisionPayload
    execution: RebalanceExecution


class StrategyEvidencePoint(BaseModel):
    date: str
    value: float


class StrategyScorePoint(StrategyEvidencePoint):
    eligible: bool = True
    rank: int | None = None
    selected: bool = False
    subject_id: str | None = None


class StrategyScoreEvidence(BaseModel):
    id: str
    label: str
    display_label: str | None = None
    model_label: str | None = None
    frequency: str = "WEEKLY"
    direction: str = "HIGHER_BETTER"
    scope: str = "UNKNOWN"
    subject_id: str | None = None
    model_id: str = "strategy_score"
    model_version: str = "1"
    points: list[StrategyScorePoint] = Field(default_factory=list)


class StrategyEvidenceSeries(BaseModel):
    id: str
    label: str
    formula_id: str
    window: int | None = None
    unit: str
    points: list[StrategyEvidencePoint] = Field(default_factory=list)


class StrategyEvidenceBenchmark(BaseModel):
    ts_code: str
    name: str | None = None
    normalized_price: list[StrategyEvidencePoint] = Field(default_factory=list)


class StrategyEvidence(BaseModel):
    schema_version: str = "1"
    benchmark: StrategyEvidenceBenchmark | None = None
    indicators: list[StrategyEvidenceSeries] = Field(default_factory=list)
    score: StrategyScoreEvidence | None = None
    score_components: dict[str, dict[str, Any]] = Field(default_factory=dict)
    evidence_version: str | None = None


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
    name: str | None = None
    fund_type: str | None = None
    signals: list[dict[str, Any]] = Field(default_factory=list)
    trades: list[dict[str, Any]] = Field(default_factory=list)
    ohlcv: list[dict[str, Any]] = Field(default_factory=list)
    positions: list[dict[str, Any]] = Field(default_factory=list)
    orders: list[dict[str, Any]] = Field(default_factory=list)
    ohlcv_source: dict[str, Any] = Field(default_factory=dict)
    strategy_evidence: StrategyEvidence | None = None
    mode: str = "RESEARCH_ONLY"

    @field_validator("signals", mode="before")
    @classmethod
    def normalize_signal_dates(cls, value: object) -> list[dict[str, Any]]:
        return _normalize_date_fields(
            value,
            ("date", "week_ending", "trade_date", "signal_date"),
        )

    @field_validator("trades", mode="before")
    @classmethod
    def retain_trade_markers(cls, value: object) -> list[dict[str, Any]]:
        """Expose only BUY/SELL rows with canonical date keys."""
        rows = _normalize_date_fields(
            value,
            ("trade_date", "signal_date"),
        )
        normalized: list[dict[str, Any]] = []
        for row in rows:
            action = str(row.get("action", "")).upper()
            if action not in {"BUY", "SELL"}:
                continue
            row["action"] = action
            normalized.append(row)
        return normalized

    @field_validator("ohlcv", mode="before")
    @classmethod
    def normalize_ohlcv_dates(cls, value: object) -> list[dict[str, Any]]:
        return _normalize_date_fields(value, ("trade_date",))

    @field_validator("positions", mode="before")
    @classmethod
    def normalize_position_dates(cls, value: object) -> list[dict[str, Any]]:
        return _normalize_date_fields(value, ("trade_date",))

    @field_validator("orders", mode="before")
    @classmethod
    def normalize_order_dates(cls, value: object) -> list[dict[str, Any]]:
        return _normalize_date_fields(
            value,
            ("trade_date", "signal_date", "created_date"),
        )
