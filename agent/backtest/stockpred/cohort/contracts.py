"""Domain contracts for the StockPred signal cohort evaluation engine.

Defines immutable domain models and deterministic identity functions per
design document §9 and §27.14.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Identity functions
# ---------------------------------------------------------------------------


def compute_cohort_id(
    *,
    evaluation_protocol_key: str,
    strategy_id: str,
    strategy_version: str,
    evaluation_date: str,
) -> str:
    """Deterministic cohort identity per §27.14.

    cohort_id = "cohort_" + sha256(
        "signal_cohort_v1"
        + evaluation_protocol_key
        + strategy_id
        + strategy_version
        + evaluation_date
    )[0:24]
    """
    payload = (
        "signal_cohort_v1"
        + evaluation_protocol_key
        + strategy_id
        + strategy_version
        + evaluation_date
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"cohort_{digest}"


def compute_evaluation_protocol_key(config: dict[str, Any]) -> str:
    """Compute a canonical SHA-256 key from evaluation protocol parameters.

    The key is order-independent (sorted JSON) and covers all parameters
    that must be identical for strategies to enter the same strict leaderboard.
    """
    canonical = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class CohortStatus(str, Enum):
    """Cohort lifecycle states per §9.5."""

    PLANNED = "PLANNED"
    ENTERING = "ENTERING"
    HOLDING = "HOLDING"
    EXITING = "EXITING"
    LIQUIDATED = "LIQUIDATED"
    UNLIQUIDATED = "UNLIQUIDATED"
    FAILED_DATA = "FAILED_DATA"
    FAILED_EXECUTION = "FAILED_EXECUTION"


# ---------------------------------------------------------------------------
# Domain models (§9)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SignalSnapshot:
    """Immutable signal snapshot per §9.1.

    Contains the complete signal cross-section for one evaluation date.
    Does not contain orders or future returns.
    """

    evaluation_date: str
    strategy_id: str
    strategy_version: str
    data_snapshot_id: str
    signals: list[dict[str, Any]] = field(default_factory=list)
    eligible_universe: list[str] = field(default_factory=list)
    data_quality: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TargetSnapshot:
    """Frozen target portfolio per §9.2.

    Targets are frozen before execution; subsequent rejections do not
    alter the target set.
    """

    cohort_id: str
    evaluation_date: str
    committed_capital: float
    selected_codes: tuple[str, ...] = ()
    target_weights: dict[str, float] = field(default_factory=dict)
    target_values: dict[str, float] = field(default_factory=dict)
    selection_reason: str = ""


@dataclass(frozen=True)
class OrderIntent:
    """Order intent per §9.3.

    Future Portfolio engine reuses the same execution contract starting
    from OrderIntent.
    """

    order_id: str
    cohort_id: str
    signal_date: str
    eligible_from: str
    code: str
    side: Literal["BUY", "SELL"]
    requested_quantity: int
    requested_value: float
    execution_policy_version: str = "exec_v1"


@dataclass(frozen=True)
class ExecutionEvent:
    """Execution event per §9.4.

    Fee components are absolute amounts:
    commission, stamp_duty, transfer_fee, slippage, market_impact.
    """

    order_id: str
    cohort_id: str
    trade_date: str
    code: str
    side: Literal["BUY", "SELL"]
    requested_quantity: int
    executed_quantity: int
    executed_value: float
    price: float
    requested_value: float = 0.0
    requested_quantity_known: bool = True
    fee_components: dict[str, float] = field(default_factory=dict)
    status: str = "FILLED"
    reason_code: str | None = None
    remaining_quantity: int = 0
    market_data_as_of: str = ""

    @property
    def total_fees(self) -> float:
        return sum(self.fee_components.values())


@dataclass
class CohortState:
    """Mutable cohort state per §9.5.

    Maintains committed capital, cash, positions, orders and exit status
    for a single cohort. Cash must never go negative.
    """

    cohort_id: str
    committed_capital: float
    available_cash: float
    evaluation_date: str
    positions: dict[str, int] = field(default_factory=dict)
    pending_orders: list[str] = field(default_factory=list)
    target_exit_date: str = ""
    last_valuation_date: str = ""
    status: CohortStatus = CohortStatus.PLANNED
    total_fees_paid: float = 0.0
    total_exit_proceeds: float = 0.0

    @classmethod
    def create(
        cls,
        *,
        cohort_id: str,
        committed_capital: float,
        evaluation_date: str,
    ) -> "CohortState":
        """Initialize a new cohort with all capital available."""
        return cls(
            cohort_id=cohort_id,
            committed_capital=committed_capital,
            available_cash=committed_capital,
            evaluation_date=evaluation_date,
        )

    @property
    def invested_value(self) -> float:
        """Total capital deployed into positions (at cost)."""
        return self.committed_capital - self.available_cash - self.total_fees_paid

    @property
    def idle_cash_ratio(self) -> float:
        if self.committed_capital <= 0:
            return 0.0
        return self.available_cash / self.committed_capital


@dataclass(frozen=True)
class CohortResult:
    """Per-cohort result metrics per §9.6 and §12."""

    cohort_id: str
    committed_capital_return: float | None
    executed_capital_return: float | None
    raw_signal_return: float | None
    horizon_mark_return: float | None
    liquidation_return: float | None
    benchmark_return: float | None
    target_horizon_excess_return: float | None
    liquidation_policy_excess_return: float | None
    fill_rate: float
    idle_cash_ratio: float
    cost_ratio: float
    exit_delay_days: int
    unliquidated_ratio: float
    status: CohortStatus
    data_quality: dict[str, Any] = field(default_factory=dict)
    evaluation_date: str = ""
    raw_label_coverage: float = 0.0
    raw_label_status: str = "insufficient_data"
    uses_stale_valuation: bool = False
    max_stale_days: int = 0
