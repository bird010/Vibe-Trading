"""R72: R39 plus a preregistered absolute 126-day momentum gate."""

from .strategy import (
    AiRotationR72R39AbsoluteMomentumSession,
    AiRotationR72R39AbsoluteMomentumStrategy,
    DESCRIPTOR,
    apply_absolute_momentum_gate,
    compute_absolute_momentum_returns,
)

__all__ = [
    "AiRotationR72R39AbsoluteMomentumSession",
    "AiRotationR72R39AbsoluteMomentumStrategy",
    "DESCRIPTOR",
    "apply_absolute_momentum_gate",
    "compute_absolute_momentum_returns",
]
