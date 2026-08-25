from typing import Literal
from pydantic import BaseModel, ConfigDict

class DirectCorrelationDiversificationConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    top_n: Literal[3] = 3
    correlation_lookback_weeks: Literal[52] = 52
    min_pairwise_weeks: Literal[20] = 20
