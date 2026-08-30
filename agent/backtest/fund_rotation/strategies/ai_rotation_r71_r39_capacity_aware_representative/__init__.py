"""R71: R39 with an auditable capacity-aware representative overlay."""

from .strategy import (
    AiRotationR71R39CapacityAwareRepresentativeStrategy,
    apply_capacity_overlay,
)

__all__ = [
    "AiRotationR71R39CapacityAwareRepresentativeStrategy",
    "apply_capacity_overlay",
]
