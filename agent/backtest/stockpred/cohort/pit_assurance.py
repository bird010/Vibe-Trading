"""Point-in-time assurance classifier.

Implements design §27.7: classifies runs as strict or snapshot_only
based on which data tables the strategy depends on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# Non-revisable tables with clear event dates (can achieve strict with version freeze)
STRICT_TABLES: set[str] = {
    "stock",
    "fact_adj_factor",
    "fact_stock_limit",
    "dim_trade_cal",
    "fact_index_daily",
}

# Revisable tables requiring bi-temporal history for strict assurance
REVISABLE_TABLES: set[str] = {
    "fact_fina_indicator",
    "dim_stock_name_history",
    "bridge_stock_industry",
    "fact_stock_daily_basic",
    "fact_moneyflow",
}


@dataclass(frozen=True)
class PitAssuranceResult:
    """Result of PIT assurance classification."""

    level: Literal["strict", "snapshot_only"]
    strict_tables: list[str] = field(default_factory=list)
    snapshot_only_tables: list[str] = field(default_factory=list)
    unknown_tables: list[str] = field(default_factory=list)
    warning: str = ""


def classify_pit_assurance(strategy_dependencies: list[str]) -> PitAssuranceResult:
    """Classify PIT assurance level based on strategy table dependencies.

    Rules (§27.7):
    - If strategy depends on ANY revisable table -> snapshot_only
    - Unknown tables are not proven PIT-safe and therefore trigger downgrade
    - Otherwise -> strict
    """
    strict_used = sorted(t for t in strategy_dependencies if t in STRICT_TABLES)
    revisable_used = sorted(t for t in strategy_dependencies if t in REVISABLE_TABLES)
    unknown_used = sorted(t for t in strategy_dependencies if t not in STRICT_TABLES and t not in REVISABLE_TABLES)

    snapshot_only = sorted(set(revisable_used + unknown_used))
    if snapshot_only:
        warning = (
            f"Strategy depends on PIT-unproven tables {snapshot_only} without bi-temporal "
            f"proof. Run classified as snapshot_only; ranking_eligible=false."
        )
        return PitAssuranceResult(
            level="snapshot_only",
            strict_tables=strict_used,
            snapshot_only_tables=snapshot_only,
            unknown_tables=unknown_used,
            warning=warning,
        )

    return PitAssuranceResult(
        level="strict",
        strict_tables=strict_used,
        snapshot_only_tables=[],
        unknown_tables=unknown_used,
        warning="",
    )
