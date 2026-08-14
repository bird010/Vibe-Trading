"""Deterministic in-memory forward validation slice for fund rotation.

This module intentionally does not connect to a broker.  It models the
contracts needed to prove that shadow decisions were sealed before future
execution data arrived, then executed later with a separate idempotency
boundary.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Protocol

from backtest.fund_rotation.accounting_contract import (
    ACCOUNTING_CONTRACT_VERSION,
    DAILY_ACCOUNTING_EVENT_ORDER,
)


class StrategyVersionLifecycle(str, Enum):
    CANDIDATE = "CANDIDATE"
    FROZEN = "FROZEN"
    RETIRED = "RETIRED"
    INVALIDATED = "INVALIDATED"


class ShadowDeploymentStatus(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    SUSPENDED = "SUSPENDED"
    FAILED = "FAILED"


class DecisionQualification(str, Enum):
    INELIGIBLE = "INELIGIBLE"
    ELIGIBLE = "ELIGIBLE"
    REVOKED = "REVOKED"


class ShadowDecisionStatus(str, Enum):
    SEALED = "SEALED"
    DATA_NOT_READY = "DATA_NOT_READY"
    INVALID = "INVALID"


EXPECTED_ARTIFACT_NAMES: tuple[str, ...] = (
    "frozen_strategy_manifest.json",
    "qualification_evidence.json",
    "qualification_policy.json",
    "qualification_assessments.json",
    "shadow_account_state.json",
    "shadow_decisions.csv",
    "shadow_targets.csv",
    "shadow_orders.csv",
    "shadow_attempts.csv",
    "shadow_trades.csv",
    "shadow_positions.csv",
    "shadow_equity.csv",
    "shadow_metrics.json",
    "shadow_drift_report.json",
    "shadow_incidents.json",
    "shadow_manifest.json",
)


def default_artifact_contracts() -> dict[str, str]:
    contracts = {
        "frozen_strategy_manifest.json": "immutable-json-event/v1",
        "qualification_evidence.json": "append-only-json-event/v1",
        "qualification_policy.json": "immutable-json-event/v1",
        "qualification_assessments.json": "append-only-json-event/v1",
        "shadow_account_state.json": "append-only-json-event/v1",
        "shadow_metrics.json": "append-only-json-event/v1",
        "shadow_drift_report.json": "append-only-json-event/v1",
        "shadow_incidents.json": "append-only-json-event/v1",
        "shadow_manifest.json": "run-manifest-json/v1",
    }
    for name in (
        "shadow_decisions.csv",
        "shadow_targets.csv",
        "shadow_orders.csv",
        "shadow_attempts.csv",
        "shadow_trades.csv",
        "shadow_positions.csv",
        "shadow_equity.csv",
    ):
        contracts[name] = "append-only-csv-event/v1"
    return contracts


def _canonical_payload(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_payload(value).encode("utf-8")).hexdigest()


def _stable_id(prefix: str, *parts: Any) -> str:
    return f"{prefix}-{_sha256(parts)[:16]}"


def _require_daily_accounting_contract(accounting_contract_version: str) -> None:
    if accounting_contract_version != ACCOUNTING_CONTRACT_VERSION:
        raise ValueError(f"accounting contract must be {ACCOUNTING_CONTRACT_VERSION}")


@dataclass(frozen=True)
class FrozenStrategyVersion:
    strategy_version_id: str
    strategy_id: str
    parent_research_experiment_id: str
    implementation_hash: str
    framework_hash: str
    config_hash: str
    data_contract_version: str
    execution_contract_version: str
    accounting_contract_version: str
    qualification_policy_hash: str
    frozen_at: datetime
    effective_from: datetime
    lifecycle: StrategyVersionLifecycle = StrategyVersionLifecycle.FROZEN

    def __post_init__(self) -> None:
        _require_daily_accounting_contract(self.accounting_contract_version)


def build_frozen_strategy_version(
    *,
    strategy_id: str,
    parent_research_experiment_id: str,
    implementation_payload: Any,
    framework_payload: Any,
    config_payload: Any,
    data_contract_version: str,
    execution_contract_version: str,
    accounting_contract_version: str,
    qualification_policy: QualificationPolicy,
    qualification_policy_hash: str | None = None,
    frozen_at: datetime,
    effective_from: datetime,
) -> FrozenStrategyVersion:
    if not isinstance(qualification_policy, QualificationPolicy):
        raise TypeError("a formal QualificationPolicy is required")
    if (
        qualification_policy_hash is not None
        and qualification_policy_hash != qualification_policy.policy_hash
    ):
        raise ValueError("qualification policy hash does not match formal policy")
    effective_qualification_policy_hash = qualification_policy.policy_hash
    implementation_hash = _sha256(implementation_payload)
    framework_hash = _sha256(framework_payload)
    config_hash = _sha256(config_payload)
    strategy_version_id = _stable_id(
        "sv",
        strategy_id,
        parent_research_experiment_id,
        implementation_hash,
        framework_hash,
        config_hash,
        data_contract_version,
        execution_contract_version,
        accounting_contract_version,
        effective_qualification_policy_hash,
        effective_from.isoformat(),
    )
    return FrozenStrategyVersion(
        strategy_version_id=strategy_version_id,
        strategy_id=strategy_id,
        parent_research_experiment_id=parent_research_experiment_id,
        implementation_hash=implementation_hash,
        framework_hash=framework_hash,
        config_hash=config_hash,
        data_contract_version=data_contract_version,
        execution_contract_version=execution_contract_version,
        accounting_contract_version=accounting_contract_version,
        qualification_policy_hash=effective_qualification_policy_hash,
        frozen_at=frozen_at,
        effective_from=effective_from,
    )


@dataclass(frozen=True)
class GateSpec:
    gate_id: str
    metric_name: str
    formula: str
    evaluation_scope: str
    threshold: Any
    comparison_operator: str
    missing_data_policy: str
    evidence_artifact: str

    def __post_init__(self) -> None:
        required = {
            "gate_id": self.gate_id,
            "metric_name": self.metric_name,
            "formula": self.formula,
            "evaluation_scope": self.evaluation_scope,
            "comparison_operator": self.comparison_operator,
            "missing_data_policy": self.missing_data_policy,
            "evidence_artifact": self.evidence_artifact,
        }
        if self.threshold is None or any(not value for value in required.values()):
            raise ValueError("missing gate contract")


def _gate_semantics(gate: GateSpec) -> dict[str, Any]:
    return {
        "gate_id": gate.gate_id,
        "metric_name": gate.metric_name,
        "formula": gate.formula,
        "evaluation_scope": gate.evaluation_scope,
        "threshold": gate.threshold,
        "comparison_operator": gate.comparison_operator,
        "missing_data_policy": gate.missing_data_policy,
        "evidence_artifact": gate.evidence_artifact,
    }


def _policy_hash(
    *,
    policy_id: str,
    target_transition: str,
    hard_gates: tuple[GateSpec, ...],
    warning_gates: tuple[GateSpec, ...],
) -> str:
    return _sha256(
        {
            "policy_id": policy_id,
            "target_transition": target_transition,
            "hard_gates": [_gate_semantics(gate) for gate in hard_gates],
            "warning_gates": [_gate_semantics(gate) for gate in warning_gates],
        }
    )


@dataclass(frozen=True)
class QualificationEvidence:
    evidence_id: str
    evidence_type: str
    subject_id: str
    artifact_ids: tuple[str, ...]
    artifact_hashes: tuple[str, ...]
    quality_status: str
    generated_at: datetime
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QualificationPolicy:
    policy_id: str
    policy_hash: str
    target_transition: str
    hard_gates: tuple[GateSpec, ...]
    warning_gates: tuple[GateSpec, ...]
    frozen_at: datetime

    def __post_init__(self) -> None:
        canonical_hash = _policy_hash(
            policy_id=self.policy_id,
            target_transition=self.target_transition,
            hard_gates=self.hard_gates,
            warning_gates=self.warning_gates,
        )
        if not self.policy_hash:
            object.__setattr__(self, "policy_hash", canonical_hash)
        elif self.policy_hash != canonical_hash:
            raise ValueError("policy hash does not match canonical policy semantics")


@dataclass(frozen=True)
class QualificationAssessment:
    assessment_id: str
    target_transition: str
    subject_id: str
    policy_hash: str
    evidence_ids: tuple[str, ...]
    decision: DecisionQualification
    failed_hard_gates: tuple[str, ...]
    warnings: tuple[str, ...]
    reason_codes: tuple[str, ...]
    evaluated_at: datetime
    evaluator_version: str


@dataclass(frozen=True)
class ApprovalRecord:
    approval_id: str
    strategy_version_id: str
    approver: str
    approved_at: datetime
    policy_hash: str
    assessment_id: str
    evidence_version: str
    known_limitations: tuple[str, ...]
    allowed_use: str


@dataclass(frozen=True)
class ShadowAccountState:
    strategy_version_id: str
    as_of_time: datetime
    cash: float
    positions: tuple[tuple[str, float], ...]
    target_weights: tuple[tuple[str, float], ...]
    residual_orders: tuple[tuple[str, float], ...]
    shadow_ideal_nav: float
    shadow_executable_nav: float
    accounting_contract_version: str
    completed_rebalance_cycles: int
    cash_weight: float = 0.0
    daily_accounting_event_order: tuple[str, ...] = DAILY_ACCOUNTING_EVENT_ORDER
    valuation_prices: tuple[tuple[str, float], ...] = ()
    execution_state: object | None = None
    execution_state_snapshot: Mapping[str, object] | None = None


@dataclass
class ShadowDeployment:
    deployment_id: str
    strategy_version_id: str
    status: ShadowDeploymentStatus
    created_at: datetime


@dataclass(frozen=True)
class ScheduledSignal:
    strategy_version_id: str
    signal_date: str
    data_available_at: datetime
    snapshot_fingerprint: str
    raw_signal: dict[str, Any]
    selected_clusters: tuple[str, ...]
    target_weights: tuple[tuple[str, float], ...]
    target_change_reasons: tuple[str, ...]
    expected_execution_date: str
    cash_weight: float = 0.0

    def to_snapshot(self) -> dict[str, Any]:
        """Return a JSON-safe scheduled signal for process restart."""
        return json.loads(json.dumps({
            "strategy_version_id": self.strategy_version_id,
            "signal_date": self.signal_date,
            "data_available_at": self.data_available_at.isoformat(),
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "raw_signal": self.raw_signal,
            "selected_clusters": self.selected_clusters,
            "target_weights": self.target_weights,
            "target_change_reasons": self.target_change_reasons,
            "expected_execution_date": self.expected_execution_date,
            "cash_weight": self.cash_weight,
        }, sort_keys=True, default=str))

    @classmethod
    def from_snapshot(cls, snapshot: Mapping[str, Any]) -> "ScheduledSignal":
        if not isinstance(snapshot, Mapping):
            raise TypeError("scheduled signal snapshot must be a mapping")
        return cls(
            strategy_version_id=str(snapshot["strategy_version_id"]),
            signal_date=str(snapshot["signal_date"]),
            data_available_at=datetime.fromisoformat(str(snapshot["data_available_at"])),
            snapshot_fingerprint=str(snapshot["snapshot_fingerprint"]),
            raw_signal=dict(snapshot.get("raw_signal", {})),
            selected_clusters=tuple(str(value) for value in snapshot.get("selected_clusters", ())),
            target_weights=tuple(
                (str(symbol), float(weight))
                for symbol, weight in snapshot.get("target_weights", ())
            ),
            target_change_reasons=tuple(
                str(value) for value in snapshot.get("target_change_reasons", ())
            ),
            expected_execution_date=str(snapshot["expected_execution_date"]),
            cash_weight=float(snapshot.get("cash_weight", 0.0)),
        )


@dataclass(frozen=True)
class ShadowOrder:
    shadow_order_id: str
    shadow_decision_id: str
    symbol: str
    target_weight: float
    expected_execution_date: str


@dataclass(frozen=True)
class ShadowDecision:
    shadow_decision_id: str
    strategy_version_id: str
    generated_at: datetime
    signal_date: str
    as_of_time: datetime
    snapshot_fingerprint: str
    previous_targets: tuple[tuple[str, float], ...]
    new_targets: tuple[tuple[str, float], ...]
    previous_cash: float
    previous_nav: float
    raw_signal: dict[str, Any]
    selected_clusters: tuple[str, ...]
    target_change_reasons: tuple[str, ...]
    expected_execution_date: str
    status: ShadowDecisionStatus
    reason_codes: tuple[str, ...]
    decision_idempotency_key: str
    accounting_contract_version: str
    generated_before_execution_price: bool = True
    new_cash_weight: float = 0.0


@dataclass(frozen=True)
class ShadowDecisionResult:
    decision: ShadowDecision
    orders: tuple[ShadowOrder, ...]


@dataclass(frozen=True)
class MarketDataForExecution:
    execution_date: str
    available_at: datetime
    prices: tuple[tuple[str, float], ...]
    ideal_nav: float
    executable_nav: float
    data_delay_seconds: int = 0
    prior_close_prices: tuple[tuple[str, float], ...] = ()
    open_prices: tuple[tuple[str, float], ...] = ()
    corporate_actions: tuple[Mapping[str, object], ...] = ()


@dataclass(frozen=True)
class ShadowExecutionAttempt:
    attempt_id: str
    shadow_decision_id: str
    symbol: str
    target_weight: float
    execution_as_of_time: datetime


@dataclass(frozen=True)
class ShadowFill:
    fill_id: str
    attempt_id: str
    symbol: str
    quantity: float
    price: float
    explicit_cost: float


@dataclass(frozen=True)
class ShadowExecutionFacts:
    """Formal execution facts plus the native state for the next cycle."""

    attempts: tuple[ShadowExecutionAttempt, ...]
    fills: tuple[ShadowFill, ...]
    execution_state: object | None = None


@dataclass(frozen=True)
class ShadowExecutionResult:
    shadow_decision_id: str
    status: str
    execution_idempotency_key: str
    attempts: tuple[ShadowExecutionAttempt, ...]
    fills: tuple[ShadowFill, ...]
    account_state: ShadowAccountState | None
    data_delay_seconds: int = 0
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class DecisionCorrection:
    correction_id: str
    shadow_decision_id: str
    correction_type: str
    reason: str
    corrected_at: datetime


@dataclass(frozen=True)
class DriftThresholds:
    maximum_shadow_drawdown: float
    execution_cost_ratio: float
    data_failure_count: int
    consecutive_invalid_decisions: int
    ideal_execution_gap: float


@dataclass
class InMemoryForwardValidationStore:
    strategy_versions: dict[str, FrozenStrategyVersion] = field(default_factory=dict)
    strategy_lifecycles: dict[str, StrategyVersionLifecycle] = field(default_factory=dict)
    decision_qualifications: dict[str, DecisionQualification] = field(default_factory=dict)
    deployments: dict[str, ShadowDeployment] = field(default_factory=dict)
    account_states: dict[str, ShadowAccountState] = field(default_factory=dict)
    scheduled_signals: list[ScheduledSignal] = field(default_factory=list)
    decisions: list[ShadowDecision] = field(default_factory=list)
    orders: list[ShadowOrder] = field(default_factory=list)
    corrections: list[DecisionCorrection] = field(default_factory=list)
    market_data: dict[str, MarketDataForExecution] = field(default_factory=dict)
    execution_attempts: list[ShadowExecutionAttempt] = field(default_factory=list)
    fills: list[ShadowFill] = field(default_factory=list)
    execution_results: dict[str, ShadowExecutionResult] = field(default_factory=dict)
    assessments: list[QualificationAssessment] = field(default_factory=list)
    strategy_session_snapshots: dict[str, Mapping[str, object]] = field(default_factory=dict)

    def add_strategy_version(self, version: FrozenStrategyVersion) -> None:
        self.strategy_versions[version.strategy_version_id] = version
        self.strategy_lifecycles[version.strategy_version_id] = version.lifecycle
        self.decision_qualifications.setdefault(
            version.strategy_version_id,
            DecisionQualification.INELIGIBLE,
        )

    def add_deployment(self, deployment: ShadowDeployment) -> None:
        self.deployments[deployment.deployment_id] = deployment

    def set_account_state(self, strategy_version_id: str, state: ShadowAccountState) -> None:
        self.account_states[strategy_version_id] = state

    def save_scheduled_signal(self, signal: ScheduledSignal) -> None:
        if not any(
            existing.strategy_version_id == signal.strategy_version_id
            and existing.signal_date == signal.signal_date
            for existing in self.scheduled_signals
        ):
            self.scheduled_signals.append(signal)

    def save_strategy_session_snapshot(
        self,
        strategy_version_id: str,
        snapshot: Mapping[str, object],
    ) -> None:
        self.strategy_session_snapshots[strategy_version_id] = dict(snapshot)

    def export_strategy_runtime_state(self, strategy_version_id: str) -> dict[str, Any]:
        """Export the restart boundary in a process-independent JSON shape."""
        snapshot = self.strategy_session_snapshots.get(strategy_version_id)
        return {
            "strategy_session_snapshot": (
                json.loads(json.dumps(snapshot, sort_keys=True, default=str))
                if snapshot is not None
                else None
            ),
            "scheduled_signals": [
                signal.to_snapshot()
                for signal in self.scheduled_signals
                if signal.strategy_version_id == strategy_version_id
            ],
        }

    def import_strategy_runtime_state(
        self,
        strategy_version_id: str,
        payload: Mapping[str, Any],
    ) -> None:
        if not isinstance(payload, Mapping):
            raise TypeError("strategy runtime state must be a mapping")
        snapshot = payload.get("strategy_session_snapshot")
        if snapshot is not None:
            self.save_strategy_session_snapshot(strategy_version_id, dict(snapshot))
        for raw_signal in payload.get("scheduled_signals", ()):
            signal = ScheduledSignal.from_snapshot(raw_signal)
            if signal.strategy_version_id != strategy_version_id:
                raise ValueError("scheduled signal strategy version does not match runtime state")
            self.save_scheduled_signal(signal)

    def schedule_signal(
        self,
        *,
        strategy_version_id: str,
        signal_date: str,
        data_available_at: datetime,
        snapshot_fingerprint: str,
        raw_signal: dict[str, Any],
        selected_clusters: tuple[str, ...],
        target_weights: tuple[tuple[str, float], ...],
        target_change_reasons: tuple[str, ...],
        expected_execution_date: str,
        cash_weight: float = 0.0,
    ) -> None:
        self.scheduled_signals.append(
            ScheduledSignal(
                strategy_version_id=strategy_version_id,
                signal_date=signal_date,
                data_available_at=data_available_at,
                snapshot_fingerprint=snapshot_fingerprint,
                raw_signal=raw_signal,
                selected_clusters=selected_clusters,
                target_weights=target_weights,
                target_change_reasons=target_change_reasons,
                expected_execution_date=expected_execution_date,
                cash_weight=cash_weight,
            )
        )

    def next_signal(self, strategy_version_id: str, as_of_time: datetime) -> ScheduledSignal | None:
        candidates = [
            signal
            for signal in self.scheduled_signals
            if signal.strategy_version_id == strategy_version_id
            and signal.signal_date <= as_of_time.date().isoformat()
        ]
        return candidates[-1] if candidates else None

    def add_market_data(self, data: MarketDataForExecution) -> None:
        self.market_data[data.execution_date] = data

    def append_decision_correction(
        self,
        *,
        shadow_decision_id: str,
        correction_type: str,
        reason: str,
        corrected_at: datetime,
    ) -> DecisionCorrection:
        correction = DecisionCorrection(
            correction_id=_stable_id("correction", shadow_decision_id, correction_type, corrected_at.isoformat()),
            shadow_decision_id=shadow_decision_id,
            correction_type=correction_type,
            reason=reason,
            corrected_at=corrected_at,
        )
        self.corrections.append(correction)
        return correction


class FrozenStrategyDecisionProvider(Protocol):
    def next_signal(
        self,
        *,
        store: InMemoryForwardValidationStore,
        strategy_version_id: str,
        as_of_time: datetime,
    ) -> ScheduledSignal | None:
        """Return the frozen strategy signal due at the scheduler cutoff."""


class StoreScheduledSignalProvider:
    def next_signal(
        self,
        *,
        store: InMemoryForwardValidationStore,
        strategy_version_id: str,
        as_of_time: datetime,
    ) -> ScheduledSignal | None:
        return store.next_signal(strategy_version_id, as_of_time)


class ShadowTargetValidator(Protocol):
    def validate(self, signal: ScheduledSignal) -> tuple[bool, tuple[str, ...]]:
        """Validate frozen strategy targets before a shadow decision is sealed."""


class DefaultShadowTargetValidator:
    def validate(self, signal: ScheduledSignal) -> tuple[bool, tuple[str, ...]]:
        if not signal.target_weights:
            if signal.cash_weight < 1.0 - 1e-9:
                return False, ("TARGETS_EMPTY",)
        if not math.isfinite(signal.cash_weight) or signal.cash_weight < 0:
            return False, ("CASH_WEIGHT_NEGATIVE",)
        if any(not math.isfinite(weight) or weight < 0 for _, weight in signal.target_weights):
            return False, ("TARGET_WEIGHT_NEGATIVE",)
        total_weight = sum(weight for _, weight in signal.target_weights)
        if abs(total_weight + signal.cash_weight - 1.0) > 1e-9:
            return False, ("TARGET_WEIGHTS_NOT_NORMALIZED",)
        return True, ()


class ShadowDecisionService:
    def __init__(
        self,
        store: InMemoryForwardValidationStore,
        *,
        decision_provider: FrozenStrategyDecisionProvider | None = None,
        target_validator: ShadowTargetValidator | None = None,
    ) -> None:
        self.store = store
        self.decision_provider = decision_provider or StoreScheduledSignalProvider()
        self.target_validator = target_validator or DefaultShadowTargetValidator()

    def seal_scheduled_decision(
        self,
        strategy_version_id: str,
        as_of_time: datetime,
    ) -> ShadowDecisionResult:
        signal = self.decision_provider.next_signal(
            store=self.store,
            strategy_version_id=strategy_version_id,
            as_of_time=as_of_time,
        )
        if signal is None:
            raise ValueError(f"no scheduled signal due for {strategy_version_id} at {as_of_time.isoformat()}")
        key = f"decision:{strategy_version_id}:{signal.signal_date}"
        existing = next((decision for decision in self.store.decisions if decision.decision_idempotency_key == key), None)
        if existing is not None:
            if existing.as_of_time != as_of_time and not any(
                correction.shadow_decision_id == existing.shadow_decision_id
                and correction.correction_type == "CUTOFF_REVISION"
                and correction.corrected_at == as_of_time
                for correction in self.store.corrections
            ):
                self.store.append_decision_correction(
                    shadow_decision_id=existing.shadow_decision_id,
                    correction_type="CUTOFF_REVISION",
                    reason=f"ignored scheduler cutoff {as_of_time.isoformat()} for stable key {key}",
                    corrected_at=as_of_time,
                )
            existing_orders = tuple(order for order in self.store.orders if order.shadow_decision_id == existing.shadow_decision_id)
            return ShadowDecisionResult(decision=existing, orders=existing_orders)

        version = self.store.strategy_versions[strategy_version_id]
        previous_state = self.store.account_states[strategy_version_id]
        signal_ready = signal.data_available_at <= as_of_time
        execution_market_data = self.store.market_data.get(signal.expected_execution_date)
        execution_price_visible = execution_market_data is not None and execution_market_data.available_at <= as_of_time
        targets_valid, target_reason_codes = self.target_validator.validate(signal) if signal_ready else (True, ())
        can_seal = signal_ready and not execution_price_visible and targets_valid
        if execution_price_visible:
            status = ShadowDecisionStatus.INVALID
            reason_codes = ("EXECUTION_PRICE_ALREADY_VISIBLE",)
        elif signal_ready and not targets_valid:
            status = ShadowDecisionStatus.INVALID
            reason_codes = target_reason_codes
        elif signal_ready:
            status = ShadowDecisionStatus.SEALED
            reason_codes = ("SEALED",)
        else:
            status = ShadowDecisionStatus.DATA_NOT_READY
            reason_codes = ("DATA_NOT_READY",)
        new_targets = signal.target_weights if can_seal else previous_state.target_weights
        new_cash_weight = signal.cash_weight if can_seal else previous_state.cash_weight
        snapshot_fingerprint = signal.snapshot_fingerprint if can_seal else f"pending:{signal.snapshot_fingerprint}"
        decision_id = _stable_id("sd", key)
        decision = ShadowDecision(
            shadow_decision_id=decision_id,
            strategy_version_id=strategy_version_id,
            generated_at=as_of_time,
            signal_date=signal.signal_date,
            as_of_time=as_of_time,
            snapshot_fingerprint=snapshot_fingerprint,
            previous_targets=previous_state.target_weights,
            new_targets=new_targets,
            previous_cash=previous_state.cash,
            previous_nav=previous_state.shadow_executable_nav,
            raw_signal=signal.raw_signal if can_seal else {},
            selected_clusters=signal.selected_clusters if can_seal else (),
            target_change_reasons=signal.target_change_reasons if can_seal else (),
            expected_execution_date=signal.expected_execution_date,
            status=status,
            reason_codes=reason_codes,
            decision_idempotency_key=key,
            accounting_contract_version=version.accounting_contract_version,
            generated_before_execution_price=not execution_price_visible,
            new_cash_weight=new_cash_weight,
        )
        self.store.decisions.append(decision)
        orders = tuple(
            ShadowOrder(
                shadow_order_id=_stable_id("so", decision_id, symbol),
                shadow_decision_id=decision_id,
                symbol=symbol,
                target_weight=weight,
                expected_execution_date=signal.expected_execution_date,
            )
            for symbol, weight in new_targets
            if can_seal
        )
        self.store.orders.extend(orders)
        return ShadowDecisionResult(decision=decision, orders=orders)


class ShadowExecutionAdapter(Protocol):
    def execute(
        self,
        *,
        decision: ShadowDecision,
        orders: tuple[ShadowOrder, ...],
        market_data: MarketDataForExecution,
        execution_as_of_time: datetime,
    ) -> tuple[tuple[ShadowExecutionAttempt, ...], tuple[ShadowFill, ...]]:
        """Turn sealed shadow orders into execution attempts and fills."""


class ShadowAccountingAdapter(Protocol):
    def apply(
        self,
        *,
        decision: ShadowDecision,
        previous_state: ShadowAccountState,
        fills: tuple[ShadowFill, ...],
        market_data: MarketDataForExecution,
        execution_as_of_time: datetime,
    ) -> ShadowAccountState:
        """Apply fills to the shadow account ledger."""


class ShadowExecutionService:
    def __init__(
        self,
        store: InMemoryForwardValidationStore,
        *,
        decision_provider: FrozenStrategyDecisionProvider | None = None,
        execution_adapter: ShadowExecutionAdapter | None = None,
        accounting_adapter: ShadowAccountingAdapter | None = None,
    ) -> None:
        self.store = store
        self.decision_provider = decision_provider
        self.execution_adapter = execution_adapter
        self.accounting_adapter = accounting_adapter

    def seal_scheduled_decision(
        self,
        strategy_version_id: str,
        as_of_time: datetime,
    ) -> ShadowDecisionResult:
        """Seal through the explicitly wired provider used by production Shadow."""
        if self.decision_provider is None:
            raise ValueError("NOT_CONFIGURED: formal strategy decision provider is required")
        return ShadowDecisionService(
            self.store,
            decision_provider=self.decision_provider,
        ).seal_scheduled_decision(strategy_version_id, as_of_time)

    def execute_due_orders(
        self,
        shadow_decision_id: str,
        execution_as_of_time: datetime,
    ) -> ShadowExecutionResult:
        decision = self._decision(shadow_decision_id)
        key = f"execution:{shadow_decision_id}:{decision.expected_execution_date}"
        if decision.status != ShadowDecisionStatus.SEALED:
            return ShadowExecutionResult(
                shadow_decision_id=shadow_decision_id,
                status="DECISION_NOT_SEALED",
                execution_idempotency_key=key,
                attempts=(),
                fills=(),
                account_state=None,
            )
        if key in self.store.execution_results:
            return self.store.execution_results[key]

        market_data = self.store.market_data.get(decision.expected_execution_date)
        if market_data is None or market_data.available_at > execution_as_of_time:
            return ShadowExecutionResult(
                shadow_decision_id=shadow_decision_id,
                status="DATA_NOT_READY",
                execution_idempotency_key=key,
                attempts=(),
                fills=(),
                account_state=None,
            )

        missing_adapters = []
        if self.execution_adapter is None:
            missing_adapters.append("EXECUTION_ADAPTER_NOT_CONFIGURED")
        if self.accounting_adapter is None:
            missing_adapters.append("ACCOUNTING_ADAPTER_NOT_CONFIGURED")
        if missing_adapters:
            return ShadowExecutionResult(
                shadow_decision_id=shadow_decision_id,
                status="NOT_CONFIGURED",
                execution_idempotency_key=key,
                attempts=(),
                fills=(),
                account_state=None,
                data_delay_seconds=market_data.data_delay_seconds,
                reason_codes=tuple(missing_adapters),
            )

        orders = tuple(order for order in self.store.orders if order.shadow_decision_id == shadow_decision_id)
        previous_state = self.store.account_states[decision.strategy_version_id]
        start_violations = self._validate_starting_account_state(
            decision=decision,
            previous_state=previous_state,
        )
        if start_violations:
            return self._contract_violation_result(
                shadow_decision_id=shadow_decision_id,
                execution_idempotency_key=key,
                market_data=market_data,
                reason_codes=start_violations,
            )

        formal_execute = getattr(self.execution_adapter, "execute_formal", None)
        if callable(formal_execute):
            execution_facts = formal_execute(
                decision=decision,
                orders=orders,
                previous_state=previous_state,
                market_data=market_data,
                execution_as_of_time=execution_as_of_time,
            )
            attempts = tuple(execution_facts.attempts)
            fills = tuple(execution_facts.fills)
            execution_state = execution_facts.execution_state
        else:
            attempts, fills = self.execution_adapter.execute(
                decision=decision,
                orders=orders,
                market_data=market_data,
                execution_as_of_time=execution_as_of_time,
            )
            execution_state = None
        formal_apply = getattr(self.accounting_adapter, "apply_formal", None)
        if callable(formal_apply):
            account_state = formal_apply(
                decision=decision,
                previous_state=previous_state,
                fills=fills,
                execution_state=execution_state,
                market_data=market_data,
                execution_as_of_time=execution_as_of_time,
            )
        else:
            account_state = self.accounting_adapter.apply(
                decision=decision,
                previous_state=previous_state,
                fills=fills,
                market_data=market_data,
                execution_as_of_time=execution_as_of_time,
            )
        output_violations = self._validate_adapter_output(
            decision=decision,
            orders=orders,
            previous_state=previous_state,
            attempts=attempts,
            fills=fills,
            account_state=account_state,
            execution_as_of_time=execution_as_of_time,
        )
        if output_violations:
            return self._contract_violation_result(
                shadow_decision_id=shadow_decision_id,
                execution_idempotency_key=key,
                market_data=market_data,
                reason_codes=output_violations,
            )

        result = ShadowExecutionResult(
            shadow_decision_id=shadow_decision_id,
            status="EXECUTED",
            execution_idempotency_key=key,
            attempts=attempts,
            fills=fills,
            account_state=account_state,
            data_delay_seconds=market_data.data_delay_seconds,
        )
        self.store.execution_attempts.extend(attempts)
        self.store.fills.extend(fills)
        self.store.set_account_state(decision.strategy_version_id, account_state)
        self.store.execution_results[key] = result
        return result

    def _contract_violation_result(
        self,
        *,
        shadow_decision_id: str,
        execution_idempotency_key: str,
        market_data: MarketDataForExecution,
        reason_codes: tuple[str, ...],
    ) -> ShadowExecutionResult:
        return ShadowExecutionResult(
            shadow_decision_id=shadow_decision_id,
            status="CONTRACT_VIOLATION",
            execution_idempotency_key=execution_idempotency_key,
            attempts=(),
            fills=(),
            account_state=None,
            data_delay_seconds=market_data.data_delay_seconds,
            reason_codes=reason_codes,
        )

    def _validate_starting_account_state(
        self,
        *,
        decision: ShadowDecision,
        previous_state: ShadowAccountState,
    ) -> tuple[str, ...]:
        violations: list[str] = []
        if decision.accounting_contract_version != ACCOUNTING_CONTRACT_VERSION:
            violations.append("ACCOUNTING_CONTRACT_MISMATCH")
        if previous_state.accounting_contract_version != decision.accounting_contract_version:
            violations.append("STARTING_ACCOUNTING_CONTRACT_MISMATCH")
        if previous_state.daily_accounting_event_order != DAILY_ACCOUNTING_EVENT_ORDER:
            violations.append("STARTING_ACCOUNTING_EVENT_ORDER_MISMATCH")
        if previous_state.strategy_version_id != decision.strategy_version_id:
            violations.append("STARTING_ACCOUNT_STRATEGY_MISMATCH")
        if (
            previous_state.cash != decision.previous_cash
            or previous_state.shadow_executable_nav != decision.previous_nav
            or previous_state.target_weights != decision.previous_targets
        ):
            violations.append("STARTING_ACCOUNT_STATE_MISMATCH")
        if previous_state.cash_weight < 0 or previous_state.cash_weight > 1:
            violations.append("STARTING_CASH_WEIGHT_INVALID")
        if not math.isfinite(previous_state.shadow_executable_nav) or previous_state.shadow_executable_nav <= 0:
            violations.append("STARTING_ACCOUNT_NAV_INVALID")
        elif not math.isclose(
            previous_state.cash / previous_state.shadow_executable_nav,
            previous_state.cash_weight,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            violations.append("STARTING_CASH_WEIGHT_NOT_BACKED_BY_CASH")
        return tuple(violations)

    def _validate_adapter_output(
        self,
        *,
        decision: ShadowDecision,
        orders: tuple[ShadowOrder, ...],
        previous_state: ShadowAccountState,
        attempts: tuple[ShadowExecutionAttempt, ...],
        fills: tuple[ShadowFill, ...],
        account_state: ShadowAccountState,
        execution_as_of_time: datetime,
    ) -> tuple[str, ...]:
        violations: list[str] = []
        expected_orders = tuple((symbol, weight) for symbol, weight in decision.new_targets)
        actual_orders = tuple((order.symbol, order.target_weight) for order in orders)
        if actual_orders != expected_orders:
            violations.append("ORDER_TARGET_MISMATCH")
        if any(order.shadow_decision_id != decision.shadow_decision_id for order in orders):
            violations.append("ORDER_DECISION_MISMATCH")
        if any(order.expected_execution_date != decision.expected_execution_date for order in orders):
            violations.append("ORDER_EXECUTION_DATE_MISMATCH")

        attempt_ids = {attempt.attempt_id for attempt in attempts}
        order_symbols = {order.symbol for order in orders}
        if any(attempt.shadow_decision_id != decision.shadow_decision_id for attempt in attempts):
            violations.append("ATTEMPT_DECISION_MISMATCH")
        if any(attempt.symbol not in order_symbols for attempt in attempts):
            violations.append("ATTEMPT_ORDER_MISMATCH")
        if len(attempt_ids) != len(attempts):
            violations.append("ATTEMPT_ID_DUPLICATE")

        if any(fill.attempt_id not in attempt_ids for fill in fills):
            violations.append("FILL_ATTEMPT_MISMATCH")
        attempts_by_id = {attempt.attempt_id: attempt for attempt in attempts}
        if any(fill.attempt_id in attempts_by_id and fill.symbol != attempts_by_id[fill.attempt_id].symbol for fill in fills):
            violations.append("FILL_SYMBOL_MISMATCH")

        if account_state.strategy_version_id != decision.strategy_version_id:
            violations.append("ACCOUNT_STRATEGY_MISMATCH")
        if account_state.accounting_contract_version != ACCOUNTING_CONTRACT_VERSION:
            violations.append("ACCOUNTING_CONTRACT_MISMATCH")
        if account_state.accounting_contract_version != decision.accounting_contract_version:
            violations.append("ACCOUNTING_CONTRACT_MISMATCH")
        if account_state.daily_accounting_event_order != DAILY_ACCOUNTING_EVENT_ORDER:
            violations.append("ACCOUNTING_EVENT_ORDER_MISMATCH")
        if account_state.as_of_time != execution_as_of_time:
            violations.append("ACCOUNT_AS_OF_TIME_MISMATCH")
        if account_state.target_weights != decision.new_targets:
            violations.append("ACCOUNT_TARGET_MISMATCH")
        if not math.isclose(account_state.cash_weight, decision.new_cash_weight, abs_tol=1e-9):
            violations.append("ACCOUNT_CASH_WEIGHT_MISMATCH")
        if not math.isfinite(account_state.shadow_executable_nav) or account_state.shadow_executable_nav <= 0:
            violations.append("ACCOUNT_NAV_INVALID")
        elif not math.isclose(
            account_state.cash / account_state.shadow_executable_nav,
            account_state.cash_weight,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            violations.append("ACCOUNT_CASH_WEIGHT_NOT_BACKED_BY_CASH")
        if account_state.completed_rebalance_cycles != previous_state.completed_rebalance_cycles + 1:
            violations.append("ACCOUNT_CYCLE_NOT_CONTINUOUS")

        return tuple(dict.fromkeys(violations))

    def _decision(self, shadow_decision_id: str) -> ShadowDecision:
        for decision in self.store.decisions:
            if decision.shadow_decision_id == shadow_decision_id:
                return decision
        raise ValueError(f"unknown shadow decision: {shadow_decision_id}")


def _metrics_from_evidence(
    *,
    evidence: tuple[QualificationEvidence, ...],
    forward_observation_weeks: int,
    completed_rebalance_cycles: int,
    regime_coverage_sufficient: bool,
) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "forward_observation_weeks": forward_observation_weeks,
        "completed_rebalance_cycles": completed_rebalance_cycles,
        "regime_exposure_coverage": 3 if regime_coverage_sufficient else 0,
    }
    for item in evidence:
        metrics.update(item.metrics)
    return metrics


def _compare_gate_metric(value: Any, operator: str, threshold: Any) -> bool:
    if operator == ">=":
        return value >= threshold
    if operator == ">":
        return value > threshold
    if operator == "<=":
        return value <= threshold
    if operator == "<":
        return value < threshold
    if operator == "==":
        return value == threshold
    if operator == "!=":
        return value != threshold
    raise ValueError(f"unsupported gate comparison operator: {operator}")


def _gate_reason_code(prefix: str, gate: GateSpec) -> str:
    legacy_reason_codes = {
        "min-observation": "MIN_FORWARD_OBSERVATION_WEEKS",
        "min-forward-observation-weeks": "MIN_FORWARD_OBSERVATION_WEEKS",
        "min-completed-rebalance-cycles": "MIN_COMPLETED_REBALANCE_CYCLES",
    }
    if prefix == "GATE_FAILED" and gate.gate_id in legacy_reason_codes:
        return legacy_reason_codes[gate.gate_id]
    return f"{prefix}:{gate.gate_id}"


def _gate_fails(gate: GateSpec, metrics: dict[str, Any]) -> bool:
    if gate.metric_name not in metrics:
        return gate.missing_data_policy in {"FAIL_CLOSED", "WARN"}
    return not _compare_gate_metric(metrics[gate.metric_name], gate.comparison_operator, gate.threshold)


def assess_decision_eligibility(
    *,
    strategy_version_id: str,
    policy: QualificationPolicy,
    evidence: tuple[QualificationEvidence, ...],
    forward_observation_weeks: int,
    completed_rebalance_cycles: int,
    regime_coverage_sufficient: bool,
    approval: ApprovalRecord | None,
    evaluated_at: datetime,
) -> QualificationAssessment:
    failed: list[str] = []
    reasons: list[str] = []
    warnings: list[str] = []
    metrics = _metrics_from_evidence(
        evidence=evidence,
        forward_observation_weeks=forward_observation_weeks,
        completed_rebalance_cycles=completed_rebalance_cycles,
        regime_coverage_sufficient=regime_coverage_sufficient,
    )
    for gate in policy.hard_gates:
        if _gate_fails(gate, metrics):
            failed.append(gate.gate_id)
            reasons.append(_gate_reason_code("GATE_FAILED", gate))
    if approval is None:
        failed.append("manual-approval")
        reasons.append("APPROVAL_REQUIRED")
    elif approval.policy_hash != policy.policy_hash or approval.strategy_version_id != strategy_version_id:
        failed.append("manual-approval")
        reasons.append("APPROVAL_CONTRACT_MISMATCH")
    for gate in policy.warning_gates:
        if _gate_fails(gate, metrics):
            warnings.append(gate.gate_id)
            reasons.append(_gate_reason_code("GATE_WARNING", gate))

    decision = DecisionQualification.INELIGIBLE if failed else DecisionQualification.ELIGIBLE
    assessment_id = _stable_id(
        "qa",
        strategy_version_id,
        policy.policy_hash,
        tuple(item.evidence_id for item in evidence),
        metrics,
        approval.approval_id if approval else None,
        evaluated_at.isoformat(),
    )
    return QualificationAssessment(
        assessment_id=assessment_id,
        target_transition=policy.target_transition,
        subject_id=strategy_version_id,
        policy_hash=policy.policy_hash,
        evidence_ids=tuple(item.evidence_id for item in evidence),
        decision=decision,
        failed_hard_gates=tuple(failed),
        warnings=tuple(warnings),
        reason_codes=tuple(reasons),
        evaluated_at=evaluated_at,
        evaluator_version="forward-validation/v1",
    )


def monitor_drift(
    store: InMemoryForwardValidationStore,
    *,
    deployment_id: str,
    metrics: dict[str, float],
    thresholds: DriftThresholds,
    evaluated_at: datetime,
) -> QualificationAssessment:
    deployment = store.deployments[deployment_id]
    breaches = []
    if metrics.get("maximum_shadow_drawdown", 0.0) > thresholds.maximum_shadow_drawdown:
        breaches.append("maximum-shadow-drawdown")
    if metrics.get("execution_cost_ratio", 0.0) > thresholds.execution_cost_ratio:
        breaches.append("execution-cost-ratio")
    if metrics.get("data_failure_count", 0) > thresholds.data_failure_count:
        breaches.append("data-failure-count")
    if metrics.get("consecutive_invalid_decisions", 0) > thresholds.consecutive_invalid_decisions:
        breaches.append("consecutive-invalid-decisions")
    if metrics.get("ideal_execution_gap", 0.0) > thresholds.ideal_execution_gap:
        breaches.append("ideal-execution-gap")

    if breaches:
        deployment.status = ShadowDeploymentStatus.SUSPENDED

    assessment = QualificationAssessment(
        assessment_id=_stable_id("qa", deployment_id, tuple(breaches), evaluated_at.isoformat()),
        target_transition="SHADOW_DRIFT_MONITORING",
        subject_id=deployment.strategy_version_id,
        policy_hash="drift-thresholds",
        evidence_ids=("shadow_drift_report.json",),
        decision=DecisionQualification.INELIGIBLE if breaches else DecisionQualification.ELIGIBLE,
        failed_hard_gates=tuple(breaches),
        warnings=(),
        reason_codes=("SHADOW_DRIFT_SUSPENDED",) if breaches else ("SHADOW_DRIFT_OK",),
        evaluated_at=evaluated_at,
        evaluator_version="forward-validation/v1",
    )
    store.assessments.append(assessment)
    return assessment


def invalidate_strategy_version(
    store: InMemoryForwardValidationStore,
    *,
    strategy_version_id: str,
    reason_code: str,
    evaluated_at: datetime,
) -> QualificationAssessment:
    store.strategy_lifecycles[strategy_version_id] = StrategyVersionLifecycle.INVALIDATED
    store.decision_qualifications[strategy_version_id] = DecisionQualification.REVOKED
    assessment = QualificationAssessment(
        assessment_id=_stable_id("qa", strategy_version_id, reason_code, evaluated_at.isoformat()),
        target_transition="INVALIDATE_AND_REVOKE",
        subject_id=strategy_version_id,
        policy_hash="integrity-policy",
        evidence_ids=("shadow_incidents.json",),
        decision=DecisionQualification.REVOKED,
        failed_hard_gates=("integrity",),
        warnings=(),
        reason_codes=(reason_code,),
        evaluated_at=evaluated_at,
        evaluator_version="forward-validation/v1",
    )
    store.assessments.append(assessment)
    return assessment


class ShadowRunScheduler:
    def __init__(self, store: InMemoryForwardValidationStore) -> None:
        self.store = store

    def due_versions(self, now: datetime) -> tuple[str, ...]:
        due = []
        for strategy_version_id in self.store.strategy_versions:
            signal = self.store.next_signal(strategy_version_id, now)
            if signal is not None:
                due.append(strategy_version_id)
        return tuple(due)
