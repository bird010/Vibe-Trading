"""Lifecycle helpers for execution-layer adjustment-factor state."""

from __future__ import annotations

from enum import Enum
from typing import Mapping


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
