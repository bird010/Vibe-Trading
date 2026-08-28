"""Lifecycle helpers for execution-layer adjustment-factor state."""

from __future__ import annotations

from enum import Enum
from typing import Mapping


class FactorBasisOwnershipError(ValueError):
    """Raised when persisted factor basis has no valid lifecycle owner."""


class PositionTransition(str, Enum):
    OPEN = "OPEN"
    CLOSE = "CLOSE"
    CONTINUE = "CONTINUE"
    NONE = "NONE"


def classify_position_transition(*, pre_size: int, post_size: int) -> PositionTransition:
    if pre_size < 0 or post_size < 0:
        raise ValueError("position size must be non-negative")
    if pre_size == 0 and post_size > 0:
        return PositionTransition.OPEN
    if pre_size > 0 and post_size == 0:
        return PositionTransition.CLOSE
    if pre_size > 0 and post_size > 0:
        return PositionTransition.CONTINUE
    return PositionTransition.NONE


def sync_position_factor_basis(
    basis: dict[str, float],
    *,
    code: str,
    pre_size: int,
    post_size: int,
    current_factor: float | None,
) -> PositionTransition:
    transition = classify_position_transition(pre_size=pre_size, post_size=post_size)
    if transition is PositionTransition.OPEN:
        if current_factor is None or current_factor <= 0:
            raise ValueError("adj_factor is required for a new position")
        basis[code] = float(current_factor)
    elif transition is PositionTransition.CLOSE:
        basis.pop(code, None)
    return transition


def cleanup_native_factor_basis(
    basis: dict[str, float],
    *,
    positions: Mapping[str, Mapping[str, object]],
    live_order_codes: set[str],
) -> None:
    positive_position_codes = {
        code
        for code, position in positions.items()
        if int(position.get("size", 0) or 0) > 0
    }
    valid_owner_codes = positive_position_codes | set(live_order_codes)
    for code in list(basis):
        if code not in valid_owner_codes:
            basis.pop(code, None)


def validate_factor_basis_ownership(
    basis: Mapping[str, float],
    *,
    positions: Mapping[str, Mapping[str, object]],
    live_order_codes: set[str],
    native: bool,
) -> None:
    positive_position_codes = {
        code
        for code, position in positions.items()
        if int(position.get("size", 0) or 0) > 0
    }
    valid_owner_codes = (
        positive_position_codes | set(live_order_codes)
        if native else positive_position_codes
    )
    orphan_codes = sorted(set(basis) - valid_owner_codes)
    if orphan_codes:
        raise FactorBasisOwnershipError(
            "orphan factor basis without valid owner: "
            + ", ".join(orphan_codes)
        )


def migrate_legacy_native_factor_basis(
    basis: Mapping[str, float],
    *,
    positions: Mapping[str, Mapping[str, object]],
    live_order_codes: set[str],
) -> tuple[dict[str, float], tuple[str, ...]]:
    """Migrate only legacy orphan basis; reject owned basis without evidence."""
    migrated = dict(basis)
    positive_position_codes = {
        code
        for code, position in positions.items()
        if int(position.get("size", 0) or 0) > 0
    }
    valid_owner_codes = positive_position_codes | set(live_order_codes)
    orphan_codes = sorted(set(migrated) - valid_owner_codes)
    owned_codes = sorted(set(migrated) & valid_owner_codes)
    if owned_codes:
        raise FactorBasisOwnershipError(
            "legacy factor basis with owned position/order cannot be verified: "
            + ", ".join(owned_codes)
        )
    for code in orphan_codes:
        migrated.pop(code, None)
    diagnostics = tuple(f"removed legacy orphan factor basis: {code}" for code in orphan_codes)
    return migrated, diagnostics
