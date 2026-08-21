"""三态阶段状态机与最终三动作决策。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .contracts import StageResult, StageStatus


class FinalAction(str, Enum):
    STOP_CURRENT_ARCHITECTURE = "STOP_CURRENT_ARCHITECTURE"
    FORWARD_SHADOW_ONLY = "FORWARD_SHADOW_ONLY"
    P2_RESEARCH_AUTHORIZED = "P2_RESEARCH_AUTHORIZED"


class ValidationState(str, Enum):
    CREATED = "CREATED"
    PREFLIGHT_PASSED = "PREFLIGHT_PASSED"
    UNIVERSE_VERIFIED = "UNIVERSE_VERIFIED"
    ECONOMIC_VALUE_PASSED = "ECONOMIC_VALUE_PASSED"
    MECHANISM_IDENTIFIED = "MECHANISM_IDENTIFIED"
    ROBUSTNESS_PASSED = "ROBUSTNESS_PASSED"
    STATISTICAL_CREDIBILITY_PASSED = "STATISTICAL_CREDIBILITY_PASSED"
    P2_RESEARCH_AUTHORIZED = "P2_RESEARCH_AUTHORIZED"
    BLOCKED_IDENTITY_DRIFT = "BLOCKED_IDENTITY_DRIFT"
    DATA_GAP = "DATA_GAP"
    INCONCLUSIVE_UNIVERSE = "INCONCLUSIVE_UNIVERSE"
    ECONOMIC_VALUE_FAILED = "ECONOMIC_VALUE_FAILED"
    MECHANISM_FAILED = "MECHANISM_FAILED"
    ROBUSTNESS_FAILED = "ROBUSTNESS_FAILED"
    ROBUSTNESS_INCONCLUSIVE = "ROBUSTNESS_INCONCLUSIVE"
    STATISTICAL_FAILED = "STATISTICAL_FAILED"
    STATISTICAL_INCONCLUSIVE = "STATISTICAL_INCONCLUSIVE"


_NEXT_STATE = {
    ValidationState.CREATED: ("preflight", ValidationState.PREFLIGHT_PASSED),
    ValidationState.PREFLIGHT_PASSED: ("universe", ValidationState.UNIVERSE_VERIFIED),
    ValidationState.UNIVERSE_VERIFIED: ("economic", ValidationState.ECONOMIC_VALUE_PASSED),
    ValidationState.ECONOMIC_VALUE_PASSED: ("mechanism", ValidationState.MECHANISM_IDENTIFIED),
    ValidationState.MECHANISM_IDENTIFIED: ("robustness", ValidationState.ROBUSTNESS_PASSED),
    ValidationState.ROBUSTNESS_PASSED: ("statistics", ValidationState.STATISTICAL_CREDIBILITY_PASSED),
    ValidationState.STATISTICAL_CREDIBILITY_PASSED: ("final", ValidationState.P2_RESEARCH_AUTHORIZED),
}


def _stage_name(stage: str) -> str:
    return stage.strip().lower().replace("_", "-")


def _failure_state(stage: str, status: StageStatus) -> ValidationState:
    name = _stage_name(stage)
    if "identity" in name:
        return ValidationState.BLOCKED_IDENTITY_DRIFT
    if name == "universe":
        return ValidationState.INCONCLUSIVE_UNIVERSE if status is StageStatus.INCONCLUSIVE else ValidationState.DATA_GAP
    if name == "economic":
        return ValidationState.ECONOMIC_VALUE_FAILED
    if name == "mechanism":
        return ValidationState.MECHANISM_FAILED
    if name == "robustness":
        return ValidationState.ROBUSTNESS_INCONCLUSIVE if status is StageStatus.INCONCLUSIVE else ValidationState.ROBUSTNESS_FAILED
    if name == "statistics":
        return ValidationState.STATISTICAL_INCONCLUSIVE if status is StageStatus.INCONCLUSIVE else ValidationState.STATISTICAL_FAILED
    return ValidationState.DATA_GAP


@dataclass(frozen=True)
class ValidationStateMachine:
    state: ValidationState = ValidationState.CREATED
    history: tuple[StageResult, ...] = ()

    def advance(self, result: StageResult) -> ValidationStateMachine:
        if self.state not in _NEXT_STATE:
            raise ValueError(f"cannot advance terminal validation state {self.state.value}")
        expected_stage, next_state = _NEXT_STATE[self.state]
        actual_stage = _stage_name(result.stage)
        if actual_stage != expected_stage:
            raise ValueError(f"expected {expected_stage} stage before {self.state.value}, got {result.stage}")
        if result.status is StageStatus.PASS:
            return ValidationStateMachine(next_state, self.history + (result,))
        return ValidationStateMachine(_failure_state(actual_stage, result.status), self.history + (result,))


@dataclass(frozen=True)
class ValidationDecision:
    action: FinalAction
    state: ValidationState
    stage_statuses: Mapping[str, StageStatus] = field(default_factory=dict)
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", FinalAction(self.action))
        object.__setattr__(self, "state", ValidationState(self.state))
        object.__setattr__(self, "stage_statuses", dict(self.stage_statuses))
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "state": self.state.value,
            "stage_statuses": {key: value.value for key, value in self.stage_statuses.items()},
            "reason_codes": list(self.reason_codes),
        }


_STAGE_ALIASES = {
    "preflight": "preflight",
    "universe": "universe",
    "economic": "economic",
    "economic-value": "economic",
    "mechanism": "mechanism",
    "robustness": "robustness",
    "statistics": "statistics",
    "statistical": "statistics",
}


def _normalise_results(
    results: Mapping[str, Any] | Iterable[StageResult],
) -> tuple[dict[str, StageStatus], dict[str, tuple[str, ...] | None]]:
    normalized: dict[str, StageStatus] = {}
    identities: dict[str, tuple[str, ...] | None] = {}
    source = results.items() if isinstance(results, Mapping) else ((item.stage, item) for item in results)
    for stage, value in source:
        canonical_stage = _STAGE_ALIASES.get(_stage_name(str(stage)), _stage_name(str(stage)))
        status = value.status if isinstance(value, StageResult) else value
        status = StageStatus(status)
        prior = normalized.get(canonical_stage)
        if prior is None or _status_severity(status) > _status_severity(prior):
            normalized[canonical_stage] = status
        if isinstance(value, StageResult):
            identity = value.identity_context if value.identity_complete else None
            if canonical_stage in identities:
                identities[canonical_stage] = (
                    identities[canonical_stage]
                    if identities[canonical_stage] == identity
                    else None
                )
            else:
                identities[canonical_stage] = identity
        elif canonical_stage not in identities:
            identities[canonical_stage] = None
    return normalized, identities


def _status_severity(status: StageStatus) -> int:
    return {StageStatus.PASS: 0, StageStatus.INCONCLUSIVE: 1, StageStatus.FAIL: 2}[status]


def evaluate_final_decision(
    stage_results: Mapping[str, Any] | Iterable[StageResult],
) -> ValidationDecision:
    """Apply the design's conservative final-action rules.

    Missing or inconclusive evidence never upgrades to research authorization.
    An explicit failure stops the current architecture; residual uncertainty is
    limited to forward shadow.
    """
    statuses, identities = _normalise_results(stage_results)
    required = ("preflight", "universe", "economic", "mechanism", "robustness", "statistics")
    failed = [stage for stage in required if statuses.get(stage) is StageStatus.FAIL]
    if failed:
        return ValidationDecision(
            FinalAction.STOP_CURRENT_ARCHITECTURE,
            ValidationState.DATA_GAP if "universe" in failed else ValidationState.ECONOMIC_VALUE_FAILED,
            statuses,
            tuple(f"{stage.upper()}_FAIL" for stage in failed),
        )

    if statuses.get("universe") is not StageStatus.PASS:
        return ValidationDecision(
            FinalAction.STOP_CURRENT_ARCHITECTURE,
            ValidationState.INCONCLUSIVE_UNIVERSE,
            statuses,
            ("UNIVERSE_EVIDENCE_INSUFFICIENT",),
        )

    if all(statuses.get(stage) is StageStatus.PASS for stage in required):
        if any(identities.get(stage) is None for stage in required):
            return ValidationDecision(
                FinalAction.STOP_CURRENT_ARCHITECTURE,
                ValidationState.BLOCKED_IDENTITY_DRIFT,
                statuses,
                ("INCOMPLETE_STAGE_IDENTITY",),
            )
        identity_values = {identities[stage] for stage in required}
        if len(identity_values) != 1:
            return ValidationDecision(
                FinalAction.STOP_CURRENT_ARCHITECTURE,
                ValidationState.BLOCKED_IDENTITY_DRIFT,
                statuses,
                ("STAGE_IDENTITY_MISMATCH",),
            )
        return ValidationDecision(
            FinalAction.P2_RESEARCH_AUTHORIZED,
            ValidationState.P2_RESEARCH_AUTHORIZED,
            statuses,
            ("ALL_HISTORICAL_VALIDATION_LAYERS_PASS",),
        )

    return ValidationDecision(
        FinalAction.FORWARD_SHADOW_ONLY,
        ValidationState.STATISTICAL_INCONCLUSIVE if statuses.get("statistics") is StageStatus.INCONCLUSIVE else ValidationState.ROBUSTNESS_INCONCLUSIVE,
        statuses,
        ("HISTORICAL_EVIDENCE_INCONCLUSIVE",),
    )
