from pydantic import BaseModel, ConfigDict, Field

class DirectCorrelationDiversificationConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    top_n: int = Field(3, ge=1)
    correlation_lookback_weeks: int = Field(52, ge=1)
    min_pairwise_weeks: int = Field(20, ge=1)
