"""R78 fail-closed survivor composition adapter."""

from .strategy import (
    AiRotationR78SurvivorComboSession,
    AiRotationR78SurvivorComboStrategy,
    DESCRIPTOR,
    MechanismProvenance,
    compose_survivor_layers,
    select_survivor_layers,
)

__all__ = [
    "AiRotationR78SurvivorComboSession",
    "AiRotationR78SurvivorComboStrategy",
    "DESCRIPTOR",
    "MechanismProvenance",
    "compose_survivor_layers",
    "select_survivor_layers",
]
