"""Explicit fund-rotation strategy whitelist — §16.1 (Phase 3 Task 6).

The composition root for strategy registration: an explicit, startup-fixed
whitelist of complete strategies (no directory scanning, no dynamic import
from request strings). Kept separate from the catalog machinery so the
catalog itself stays strategy-agnostic.
"""

from __future__ import annotations

from backtest.fund_rotation.strategies.correlation_all_members.strategy import (
    CorrelationAllMembersStrategy,
)
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    CorrelationRepresentativeStrategy,
)


def default_fund_rotation_strategies() -> tuple[type, ...]:
    """§16.1 — the explicit strategy whitelist."""
    return (CorrelationAllMembersStrategy, CorrelationRepresentativeStrategy)
