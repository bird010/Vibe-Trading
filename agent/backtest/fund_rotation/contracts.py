"""Complete fund-rotation strategy contracts and immutable value objects."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import AbstractSet, Protocol, Sequence, runtime_checkable

import pandas as pd
from pydantic import BaseModel


class DecisionKind(str, Enum):
    SET_TARGETS = "SET_TARGETS"
    HOLD_TARGETS = "HOLD_TARGETS"
    INVALID = "INVALID"


class QualityStatus(str, Enum):
    VALID = "VALID"
    DEGRADED = "DEGRADED"
    INVALID = "INVALID"
    FAILED = "FAILED"


@dataclass(frozen=True)
class FundRotationStrategyDescriptor:
    id: str
    name: str
    description: str
    interface_version: str
    supported_universe: tuple[str, ...]
    deterministic: bool


@dataclass(frozen=True)
class StrategyDataRequirements:
    required_datasets: tuple[str, ...]
    required_fields: tuple[str, ...]
    warmup_trade_days: int
    frequency: str
    needs_benchmark: bool


@dataclass(frozen=True)
class StrategyInitializationContext:
    run_id: str
    evaluation_calendar: tuple[str, ...]


@dataclass(frozen=True)
class StrategyArtifact:
    role: str
    media_type: str
    payload: object


@dataclass(frozen=True)
class StrategyDiagnostics:
    artifacts: tuple[StrategyArtifact, ...] = ()
    decision_trace: tuple[dict[str, object], ...] = ()


@runtime_checkable
class CausalDataView(Protocol):
    @property
    def signal_date(self) -> pd.Timestamp: ...


@dataclass(frozen=True)
class StrategyDecisionContext:
    signal_date: str
    data_view: CausalDataView
    previous_target_weights: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class TargetWeightDecision:
    decision_id: str
    signal_date: str
    action: DecisionKind
    target_weights: Mapping[str, float] = field(default_factory=dict)
    cash_weight: float = 1.0
    reason_code: str = ""
    quality_status: QualityStatus = QualityStatus.VALID
    diagnostics: Mapping[str, object] = field(default_factory=dict)


@runtime_checkable
class FundRotationStrategySession(Protocol):
    def scheduled_dates(
        self,
        calendar: tuple[str, ...],
        decision_start_date: str,
        evaluation_end_date: str,
    ) -> tuple[str, ...]: ...

    def evaluate(
        self,
        context: StrategyDecisionContext,
    ) -> TargetWeightDecision: ...

    def finalize(self) -> StrategyDiagnostics: ...


@runtime_checkable
class FundRotationStrategy(Protocol):
    descriptor: FundRotationStrategyDescriptor
    config_model: type[BaseModel]

    def resolve_requirements(
        self,
        config: BaseModel,
    ) -> StrategyDataRequirements: ...

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> FundRotationStrategySession: ...


class StrategyContractViolation(Exception):
    code = "STRATEGY_CONTRACT_VIOLATION"


def _validate_diagnostic_value(
    value: object,
    *,
    path: str,
) -> None:
    """Validate the strict JSON subset allowed in decision diagnostics."""
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise StrategyContractViolation(
                f"{path} contains a non-finite float"
            )
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise StrategyContractViolation(
                    f"{path} contains non-string mapping key {key!r}"
                )
            _validate_diagnostic_value(item, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_diagnostic_value(
                item,
                path=f"{path}[{index}]",
            )
        return
    raise StrategyContractViolation(
        f"{path} contains unsupported type {type(value).__name__}"
    )


def validate_diagnostics(
    diagnostics: Mapping[str, object],
) -> None:
    """Fail at the decision boundary when diagnostics cannot be strict JSON."""
    _validate_diagnostic_value(diagnostics, path="$.diagnostics")


def validate_target_decision(
    decision: TargetWeightDecision,
    eligible_codes: AbstractSet[str],
    seen_decision_ids: AbstractSet[str],
) -> None:
    if not isinstance(decision, TargetWeightDecision):
        raise StrategyContractViolation(
            "strategy must return a TargetWeightDecision instance"
        )
    if not isinstance(decision.action, DecisionKind):
        raise StrategyContractViolation(
            "decision action must be one of SET_TARGETS, HOLD_TARGETS, INVALID"
        )

    # Diagnostics are part of every decision action, including HOLD_TARGETS
    # and INVALID. Validate before either action's early return so malformed
    # research evidence fails at the signal date rather than during publication.
    validate_diagnostics(decision.diagnostics)

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

    if not math.isfinite(decision.cash_weight) or decision.cash_weight < 0:
        raise StrategyContractViolation(
            "cash_weight must be finite and non-negative, "
            f"got {decision.cash_weight}"
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


def merge_requirements(
    requirements: Sequence[StrategyDataRequirements],
) -> StrategyDataRequirements:
    """Merge common data needs without constraining strategy cadence.

    A batch may contain daily, weekly and monthly strategies. Each session owns
    its own schedule; the merged object exists only to size the common data
    snapshot. ``frequency`` is therefore ``MIXED`` when cadences differ.
    """
    if not requirements:
        raise ValueError("no requirements to merge")
    datasets: set[str] = set()
    fields: set[str] = set()
    warmup = 0
    needs_benchmark = False
    frequencies: set[str] = set()
    for requirement in requirements:
        datasets.update(requirement.required_datasets)
        fields.update(requirement.required_fields)
        warmup = max(warmup, requirement.warmup_trade_days)
        needs_benchmark = needs_benchmark or requirement.needs_benchmark
        frequencies.add(str(requirement.frequency))
    frequency = (
        next(iter(frequencies))
        if len(frequencies) == 1
        else "MIXED"
    )
    return StrategyDataRequirements(
        required_datasets=tuple(sorted(datasets)),
        required_fields=tuple(sorted(fields)),
        warmup_trade_days=warmup,
        frequency=frequency,
        needs_benchmark=needs_benchmark,
    )
