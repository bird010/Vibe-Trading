"""Deterministic in-memory forward validation slice for fund rotation.

This module intentionally does not connect to a broker.  It models the
contracts needed to prove that shadow decisions were sealed before future
execution data arrived, then executed later with a separate idempotency
boundary.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


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
    qualification_policy_hash: str,
    frozen_at: datetime,
    effective_from: datetime,
) -> FrozenStrategyVersion:
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
        qualification_policy_hash,
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
        qualification_policy_hash=qualification_policy_hash,
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


@dataclass(frozen=True)
class QualificationEvidence:
    evidence_id: str
    evidence_type: str
    subject_id: str
    artifact_ids: tuple[str, ...]
    artifact_hashes: tuple[str, ...]
    quality_status: str
    generated_at: datetime


@dataclass(frozen=True)
class QualificationPolicy:
    policy_id: str
    policy_hash: str
    target_transition: str
    hard_gates: tuple[GateSpec, ...]
    warning_gates: tuple[GateSpec, ...]
    frozen_at: datetime


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
class ShadowExecutionResult:
    shadow_decision_id: str
    status: str
    execution_idempotency_key: str
    attempts: tuple[ShadowExecutionAttempt, ...]
    fills: tuple[ShadowFill, ...]
    account_state: ShadowAccountState | None
    data_delay_seconds: int = 0


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


class ShadowDecisionService:
    def __init__(self, store: InMemoryForwardValidationStore) -> None:
        self.store = store

    def seal_scheduled_decision(
        self,
        strategy_version_id: str,
        as_of_time: datetime,
    ) -> ShadowDecisionResult:
        signal = self.store.next_signal(strategy_version_id, as_of_time)
        if signal is None:
            raise ValueError(f"no scheduled signal due for {strategy_version_id} at {as_of_time.isoformat()}")
        key = f"decision:{strategy_version_id}:{signal.signal_date}:{as_of_time.isoformat()}"
        existing = next((decision for decision in self.store.decisions if decision.decision_idempotency_key == key), None)
        if existing is not None:
            existing_orders = tuple(order for order in self.store.orders if order.shadow_decision_id == existing.shadow_decision_id)
            return ShadowDecisionResult(decision=existing, orders=existing_orders)

        version = self.store.strategy_versions[strategy_version_id]
        previous_state = self.store.account_states[strategy_version_id]
        signal_ready = signal.data_available_at <= as_of_time
        execution_market_data = self.store.market_data.get(signal.expected_execution_date)
        execution_price_visible = execution_market_data is not None and execution_market_data.available_at <= as_of_time
        can_seal = signal_ready and not execution_price_visible
        if execution_price_visible:
            status = ShadowDecisionStatus.INVALID
            reason_codes = ("EXECUTION_PRICE_ALREADY_VISIBLE",)
        elif signal_ready:
            status = ShadowDecisionStatus.SEALED
            reason_codes = ("SEALED",)
        else:
            status = ShadowDecisionStatus.DATA_NOT_READY
            reason_codes = ("DATA_NOT_READY",)
        new_targets = signal.target_weights if can_seal else previous_state.target_weights
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


class ShadowExecutionService:
    def __init__(self, store: InMemoryForwardValidationStore) -> None:
        self.store = store

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

        orders = tuple(order for order in self.store.orders if order.shadow_decision_id == shadow_decision_id)
        attempts = tuple(
            ShadowExecutionAttempt(
                attempt_id=_stable_id("attempt", key, order.symbol),
                shadow_decision_id=shadow_decision_id,
                symbol=order.symbol,
                target_weight=order.target_weight,
                execution_as_of_time=execution_as_of_time,
            )
            for order in orders
        )
        prices = dict(market_data.prices)
        fills = tuple(
            ShadowFill(
                fill_id=_stable_id("fill", attempt.attempt_id),
                attempt_id=attempt.attempt_id,
                symbol=attempt.symbol,
                quantity=round((market_data.executable_nav * attempt.target_weight) / prices[attempt.symbol], 6),
                price=prices[attempt.symbol],
                explicit_cost=0.0,
            )
            for attempt in attempts
        )
        previous_state = self.store.account_states[decision.strategy_version_id]
        account_state = ShadowAccountState(
            strategy_version_id=decision.strategy_version_id,
            as_of_time=execution_as_of_time,
            cash=0.0,
            positions=tuple((fill.symbol, fill.quantity) for fill in fills),
            target_weights=decision.new_targets,
            residual_orders=previous_state.residual_orders,
            shadow_ideal_nav=market_data.ideal_nav,
            shadow_executable_nav=market_data.executable_nav,
            accounting_contract_version=decision.accounting_contract_version,
            completed_rebalance_cycles=previous_state.completed_rebalance_cycles + 1,
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

    def _decision(self, shadow_decision_id: str) -> ShadowDecision:
        for decision in self.store.decisions:
            if decision.shadow_decision_id == shadow_decision_id:
                return decision
        raise ValueError(f"unknown shadow decision: {shadow_decision_id}")


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
    if forward_observation_weeks < 26:
        failed.append("min-forward-observation-weeks")
        reasons.append("MIN_FORWARD_OBSERVATION_WEEKS")
    if completed_rebalance_cycles < 6:
        failed.append("min-completed-rebalance-cycles")
        reasons.append("MIN_COMPLETED_REBALANCE_CYCLES")
    if approval is None:
        failed.append("manual-approval")
        reasons.append("APPROVAL_REQUIRED")
    elif approval.policy_hash != policy.policy_hash or approval.strategy_version_id != strategy_version_id:
        failed.append("manual-approval")
        reasons.append("APPROVAL_CONTRACT_MISMATCH")
    if not regime_coverage_sufficient:
        warnings.append("REGIME_COVERAGE_INSUFFICIENT")

    decision = DecisionQualification.INELIGIBLE if failed else DecisionQualification.ELIGIBLE
    assessment_id = _stable_id(
        "qa",
        strategy_version_id,
        policy.policy_hash,
        tuple(item.evidence_id for item in evidence),
        forward_observation_weeks,
        completed_rebalance_cycles,
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
