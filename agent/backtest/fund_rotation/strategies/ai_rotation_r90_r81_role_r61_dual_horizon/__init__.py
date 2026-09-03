"""R90 role-level dual-horizon strategy."""

from .strategy import (
    AiRotationR90R81RoleR61DualHorizonStrategy,
    DESCRIPTOR,
    EconomicRoleR81RoleR61DualHorizonSession,
    fuse_dual_horizon_role_scores,
)

__all__ = [
    "AiRotationR90R81RoleR61DualHorizonStrategy",
    "DESCRIPTOR",
    "EconomicRoleR81RoleR61DualHorizonSession",
    "fuse_dual_horizon_role_scores",
]
