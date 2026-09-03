"""R86: repaired R81 with a 50% weekly transition cap."""

from .strategy import (
    AiRotationR86R81TransitionCap50Strategy,
    EconomicRoleR81TransitionCap50Session,
    apply_transition_cap,
)

__all__ = [
    "AiRotationR86R81TransitionCap50Strategy",
    "EconomicRoleR81TransitionCap50Session",
    "apply_transition_cap",
]
