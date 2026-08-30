"""R75 fixed-target-volatility risk layer."""

from .strategy import (
    AiRotationR75R39VolTargetSession,
    AiRotationR75R39VolTargetStrategy,
    DESCRIPTOR,
    TARGET_VOLATILITY,
)

__all__ = [
    "AiRotationR75R39VolTargetSession",
    "AiRotationR75R39VolTargetStrategy",
    "DESCRIPTOR",
    "TARGET_VOLATILITY",
]
