"""Complete fund-rotation strategy contracts and value objects — Phase 1 Task 1.

The complete ``FundRotationStrategy`` is the only public plug-in unit (design
§15.1). Clustering, quality gates and representative selectors are internal to a
strategy and never appear here. A strategy declares its data needs, produces a
per-run session, and emits ``TargetWeightDecision`` s that the common Runner
validates and executes.

Design references: §5 (strategy/session contract), §7 (decision semantics),
§9/§27 (research quality status), §12 (strategy-declared artifacts).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import AbstractSet, Mapping, Protocol, runtime_checkable

import pandas as pd
from pydantic import BaseModel


# ── Enumerations ──

class DecisionKind(str, Enum):
    """§7 — the three target-weight decision actions."""

    SET_TARGETS = "SET_TARGETS"
    HOLD_TARGETS = "HOLD_TARGETS"
    INVALID = "INVALID"


class QualityStatus(str, Enum):
    """§9/§27 — research quality of a decision/run (distinct from task state)."""

    VALID = "VALID"
    DEGRADED = "DEGRADED"
    INVALID = "INVALID"
    FAILED = "FAILED"


# ── Immutable value objects ──

@dataclass(frozen=True)
class FundRotationStrategyDescriptor:
    """§5 — static, parameter-independent capability description of a strategy."""

    id: str
    name: str
    description: str
    interface_version: str
    supported_universe: tuple[str, ...]
    deterministic: bool


@dataclass(frozen=True)
class StrategyDataRequirements:
    """§5/§23 — config-derived data needs (pure function of validated config)."""

    required_datasets: tuple[str, ...]
    required_fields: tuple[str, ...]
    warmup_trade_days: int
    frequency: str
    needs_benchmark: bool


@dataclass(frozen=True)
class StrategyInitializationContext:
    """Context handed to ``create_session`` to start one isolated sub-run."""

    run_id: str
    evaluation_calendar: tuple[str, ...]


@dataclass(frozen=True)
class StrategyArtifact:
    """§12 — a strategy-declared logical diagnostic artifact."""

    role: str
    media_type: str
    payload: object


@dataclass(frozen=True)
class StrategyDiagnostics:
    """Output of ``finalize`` — strategy-declared diagnostic artifacts."""

    artifacts: tuple[StrategyArtifact, ...] = ()


@runtime_checkable
class CausalDataView(Protocol):
    """§6 — controlled causal data access (full implementation in Phase 2).

    The concrete query surface (daily bars, returns, causal ADV, eligible
    universe, trading calendar) is provided by ``causal_data.py`` in Phase 2;
    this minimal contract lets the decision context reference it without a
    circular import.
    """

    @property
    def signal_date(self) -> pd.Timestamp: ...


@dataclass(frozen=True)
class StrategyDecisionContext:
    """§6 — everything a strategy sees at one decision date.

    Provides the signal date, a controlled ``CausalDataView`` and the read-only
    previous target weights. It deliberately exposes NO actual fills, cash,
    outstanding orders or slippage, so market signals do not feed back on
    execution.
    """

    signal_date: str
    data_view: CausalDataView
    previous_target_weights: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class TargetWeightDecision:
    """§7 — one target-weight decision emitted by a strategy session."""

    decision_id: str
    signal_date: str
    action: DecisionKind
    target_weights: Mapping[str, float] = field(default_factory=dict)
    cash_weight: float = 1.0
    reason_code: str = ""
    quality_status: QualityStatus = QualityStatus.VALID
    diagnostics: Mapping[str, object] = field(default_factory=dict)


# ── Strategy / session protocols ──

@runtime_checkable
class FundRotationStrategySession(Protocol):
    """§5 — one isolated sub-run session (holds all per-run state)."""

    def scheduled_dates(
        self,
        calendar: tuple[str, ...],
        simulation_start_date: str,
        evaluation_end_date: str,
    ) -> tuple[str, ...]: ...

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision: ...

    def finalize(self) -> StrategyDiagnostics: ...


@runtime_checkable
class FundRotationStrategy(Protocol):
    """§5 — the complete strategy plug-in unit."""

    descriptor: FundRotationStrategyDescriptor
    config_model: type[BaseModel]

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements: ...

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> FundRotationStrategySession: ...


# ── Runner contract validation ──

class StrategyContractViolation(Exception):
    """§7 — raised when a decision violates the Runner contract; terminates the
    sub-run with code ``STRATEGY_CONTRACT_VIOLATION``."""

    code = "STRATEGY_CONTRACT_VIOLATION"


def validate_target_decision(
    decision: TargetWeightDecision,
    eligible_codes: AbstractSet[str],
    seen_decision_ids: AbstractSet[str],
) -> None:
    """§7 — validate one decision against the Runner contract.

    Checks: decision_id uniqueness; HOLD carries no weights; INVALID carries a
    stable reason code; SET_TARGETS weights are finite, non-negative, sum
    (with cash_weight) to 1.0, and reference only eligible codes. Validation is
    independent of the iteration order of ``target_weights``.

    Raises:
        StrategyContractViolation: on any violation.
    """
    if decision.decision_id in seen_decision_ids:
        raise StrategyContractViolation(
            f"duplicate decision_id within sub-run: {decision.decision_id}"
        )

    if decision.action is DecisionKind.HOLD_TARGETS:
        if decision.target_weights:
            raise StrategyContractViolation(
                "HOLD_TARGETS must not carry new target_weights"
            )
        return

    if decision.action is DecisionKind.INVALID:
        if not decision.reason_code:
            raise StrategyContractViolation(
                "INVALID decision must carry a stable reason_code"
            )
        return

    # SET_TARGETS
    if not math.isfinite(decision.cash_weight) or decision.cash_weight < 0:
        raise StrategyContractViolation(
            f"cash_weight must be finite and non-negative, got {decision.cash_weight}"
        )
    total = decision.cash_weight
    for code, weight in decision.target_weights.items():
        if not math.isfinite(weight) or weight < 0:
            raise StrategyContractViolation(
                f"weight for {code} must be finite and non-negative, got {weight}"
            )
        if code not in eligible_codes:
            raise StrategyContractViolation(
                f"code {code} is not in the eligible pool for this signal date"
            )
        total += weight
    if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise StrategyContractViolation(
            f"target_weights + cash_weight must sum to 1.0, got {total}"
        )
