"""Economic Role strategy package for the v4.3 research experiment."""

from .config import EconomicRoleConfig
from .roles import (
    AMBIGUOUS,
    BOND,
    CN_DEFENSIVE_EQUITY,
    CN_GROWTH_EQUITY,
    EMPTY_NAME,
    GOLD,
    MATCHED,
    OVERSEAS_GROWTH_EQUITY,
    UNCLASSIFIED,
)

__all__ = [
    "EconomicRoleConfig",
    "AMBIGUOUS",
    "BOND",
    "CN_DEFENSIVE_EQUITY",
    "CN_GROWTH_EQUITY",
    "EMPTY_NAME",
    "GOLD",
    "MATCHED",
    "OVERSEAS_GROWTH_EQUITY",
    "UNCLASSIFIED",
]
