from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from src.stockpred.fund_rotation.forward_validation import (
    EXPECTED_ARTIFACT_NAMES,
    ApprovalRecord,
    DecisionQualification,
    DriftThresholds,
    FrozenStrategyVersion,
    GateSpec,
    InMemoryForwardValidationStore,
    MarketDataForExecution,
    QualificationAssessment,
    QualificationEvidence,
    QualificationPolicy,
    ShadowAccountState,
    ShadowDecisionService,
    ShadowDecisionStatus,
    ShadowDeployment,
    ShadowDeploymentStatus,
    ShadowExecutionService,
    StrategyVersionLifecycle,
    assess_decision_eligibility,
    build_frozen_strategy_version,
    default_artifact_contracts,
    invalidate_strategy_version,
    monitor_drift,
)


UTC = timezone.utc


def at(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def frozen_version(**overrides) -> FrozenStrategyVersion:
    base = dict(
        strategy_id="fund-rotation",
        parent_research_experiment_id="exp-oos-1",
        implementation_payload={"module": "momentum", "rev": "abc"},
        framework_payload={"engine": "ledger-v2"},
        config_payload={"lookback": 20, "top_n": 2},
        data_contract_version="pit-data/v1",
        execution_contract_version="shadow-execution/v1",
        accounting_contract_version="daily-accounting-order/v1",
        qualification_policy_hash="policy-sha",
        frozen_at=at("2026-01-01T00:00:00"),
        effective_from=at("2026-01-05T00:00:00"),
    )
    base.update(overrides)
    return build_frozen_strategy_version(**base)


def valid_policy() -> QualificationPolicy:
    return QualificationPolicy(
        policy_id="policy-1",
        policy_hash="policy-sha",
        target_transition="CAN_GRANT_DECISION_ELIGIBILITY",
        hard_gates=(
            GateSpec(
                gate_id="min-observation",
                metric_name="forward_observation_weeks",
                formula="calendar_weeks(first_shadow_decision, assessment_time)",
                evaluation_scope="shadow_deployment",
                threshold=26,
                comparison_operator=">=",
                missing_data_policy="FAIL_CLOSED",
                evidence_artifact="shadow_metrics.json",
            ),
        ),
        warning_gates=(
            GateSpec(
                gate_id="regime-coverage",
                metric_name="regime_exposure_coverage",
                formula="count(pre_registered_regimes_observed)",
                evaluation_scope="shadow_deployment",
                threshold=3,
                comparison_operator=">=",
                missing_data_policy="WARN",
                evidence_artifact="shadow_metrics.json",
            ),
        ),
        frozen_at=at("2025-12-31T00:00:00"),
    )


def valid_evidence() -> QualificationEvidence:
    return QualificationEvidence(
        evidence_id="evidence-1",
        evidence_type="SHADOW_FORWARD_METRICS",
        subject_id="sv-1",
        artifact_ids=("shadow_metrics.json", "shadow_decisions.csv"),
        artifact_hashes=("metrics-hash", "decisions-hash"),
        quality_status="SEALED",
        generated_at=at("2026-07-01T00:00:00"),
    )


def seeded_store() -> tuple[InMemoryForwardValidationStore, FrozenStrategyVersion]:
    store = InMemoryForwardValidationStore()
    version = frozen_version()
    store.add_strategy_version(version)
    store.add_deployment(
        ShadowDeployment(
            deployment_id="shadow-1",
            strategy_version_id=version.strategy_version_id,
            status=ShadowDeploymentStatus.RUNNING,
            created_at=at("2026-01-01T00:00:00"),
        )
    )
    store.set_account_state(
        version.strategy_version_id,
        ShadowAccountState(
            strategy_version_id=version.strategy_version_id,
            as_of_time=at("2026-01-04T00:00:00"),
            cash=1_000.0,
            positions=(),
            target_weights=(("OLD", 1.0),),
            residual_orders=(("OLD", 10.0),),
            shadow_ideal_nav=1_000.0,
            shadow_executable_nav=1_000.0,
            accounting_contract_version=version.accounting_contract_version,
            completed_rebalance_cycles=0,
        ),
    )
    return store, version


def schedule_ready_signal(
    store: InMemoryForwardValidationStore,
    version: FrozenStrategyVersion,
) -> None:
    store.schedule_signal(
        strategy_version_id=version.strategy_version_id,
        signal_date="2026-01-05",
        data_available_at=at("2026-01-05T09:00:00"),
        snapshot_fingerprint="snapshot-1",
        raw_signal={"momentum": {"ETF_A": 0.8, "ETF_B": 0.2}},
        selected_clusters=("growth",),
        target_weights=(("ETF_A", 0.6), ("ETF_B", 0.4)),
        target_change_reasons=("weekly-rebalance",),
        expected_execution_date="2026-01-06",
    )


def test_frozen_strategy_version_is_immutable_and_hashes_contracts() -> None:
    version = frozen_version()
    changed_config = frozen_version(config_payload={"lookback": 21, "top_n": 2})

    assert version.lifecycle == StrategyVersionLifecycle.FROZEN
    assert version.data_contract_version == "pit-data/v1"
    assert version.execution_contract_version == "shadow-execution/v1"
    assert version.accounting_contract_version == "daily-accounting-order/v1"
    assert version.config_hash != changed_config.config_hash
    assert version.strategy_version_id != changed_config.strategy_version_id

    with pytest.raises(FrozenInstanceError):
        version.config_hash = "tampered"


def test_lifecycle_enums_are_separate_state_machines() -> None:
    assert [state.value for state in StrategyVersionLifecycle] == [
        "CANDIDATE",
        "FROZEN",
        "RETIRED",
        "INVALIDATED",
    ]
    assert [state.value for state in ShadowDeploymentStatus] == [
        "CREATED",
        "RUNNING",
        "COMPLETED",
        "SUSPENDED",
        "FAILED",
    ]
    assert [state.value for state in DecisionQualification] == [
        "INELIGIBLE",
        "ELIGIBLE",
        "REVOKED",
    ]


def test_qualification_objects_are_immutable_and_policy_requires_gate_contract() -> None:
    policy = valid_policy()
    evidence = valid_evidence()
    assessment = QualificationAssessment(
        assessment_id="assessment-1",
        target_transition=policy.target_transition,
        subject_id=evidence.subject_id,
        policy_hash=policy.policy_hash,
        evidence_ids=(evidence.evidence_id,),
        decision=DecisionQualification.INELIGIBLE,
        failed_hard_gates=("manual-approval",),
        warnings=("regime-coverage",),
        reason_codes=("APPROVAL_REQUIRED",),
        evaluated_at=at("2026-07-02T00:00:00"),
        evaluator_version="forward-validation-test",
    )

    with pytest.raises(FrozenInstanceError):
        policy.policy_hash = "changed"
    assert assessment.evidence_ids == ("evidence-1",)

    with pytest.raises(ValueError, match="missing gate contract"):
        QualificationPolicy(
            policy_id="bad",
            policy_hash="bad-hash",
            target_transition="CAN_START_SHADOW",
            hard_gates=(
                GateSpec(
                    gate_id="bad-gate",
                    metric_name="drawdown",
                    formula="",
                    evaluation_scope="shadow",
                    threshold=0.1,
                    comparison_operator="<=",
                    missing_data_policy="FAIL_CLOSED",
                    evidence_artifact="shadow_metrics.json",
                ),
            ),
            warning_gates=(),
            frozen_at=at("2025-12-31T00:00:00"),
        )


def test_decision_service_seals_data_not_ready_without_changing_targets() -> None:
    store, version = seeded_store()
    store.schedule_signal(
        strategy_version_id=version.strategy_version_id,
        signal_date="2026-01-05",
        data_available_at=at("2026-01-05T10:00:00"),
        snapshot_fingerprint="snapshot-late",
        raw_signal={"momentum": {"ETF_A": 1.0}},
        selected_clusters=("growth",),
        target_weights=(("ETF_A", 1.0),),
        target_change_reasons=("late-data",),
        expected_execution_date="2026-01-06",
    )

    result = ShadowDecisionService(store).seal_scheduled_decision(
        version.strategy_version_id,
        as_of_time=at("2026-01-05T09:30:00"),
    )

    assert result.decision.status == ShadowDecisionStatus.DATA_NOT_READY
    assert result.decision.previous_targets == (("OLD", 1.0),)
    assert result.decision.new_targets == (("OLD", 1.0),)
    assert result.decision.reason_codes == ("DATA_NOT_READY",)
    assert result.orders == ()
    assert result.decision.generated_before_execution_price is True


def test_decision_service_rejects_seal_after_execution_price_is_visible() -> None:
    store, version = seeded_store()
    schedule_ready_signal(store, version)
    store.add_market_data(
        MarketDataForExecution(
            execution_date="2026-01-06",
            available_at=at("2026-01-06T09:31:00"),
            prices=(("ETF_A", 10.0), ("ETF_B", 20.0)),
            ideal_nav=1_020.0,
            executable_nav=1_015.0,
        )
    )
    service = ShadowDecisionService(store)

    first = service.seal_scheduled_decision(
        version.strategy_version_id,
        as_of_time=at("2026-01-06T09:45:00"),
    )
    again = service.seal_scheduled_decision(
        version.strategy_version_id,
        as_of_time=at("2026-01-06T09:45:00"),
    )

    assert again.decision is first.decision
    assert first.decision.status == ShadowDecisionStatus.INVALID
    assert first.decision.reason_codes == ("EXECUTION_PRICE_ALREADY_VISIBLE",)
    assert first.decision.new_targets == (("OLD", 1.0),)
    assert first.decision.raw_signal == {}
    assert first.decision.generated_before_execution_price is False
    assert first.orders == ()
    assert len(store.decisions) == 1
    assert store.orders == []
    assert all(decision.status != ShadowDecisionStatus.SEALED for decision in store.decisions)


def test_decision_service_is_idempotent_and_corrections_append_only() -> None:
    store, version = seeded_store()
    schedule_ready_signal(store, version)
    service = ShadowDecisionService(store)

    first = service.seal_scheduled_decision(
        version.strategy_version_id,
        as_of_time=at("2026-01-05T09:30:00"),
    )
    again = service.seal_scheduled_decision(
        version.strategy_version_id,
        as_of_time=at("2026-01-05T09:30:00"),
    )

    assert again.decision is first.decision
    assert first.decision.status == ShadowDecisionStatus.SEALED
    assert len(store.decisions) == 1
    assert first.decision.decision_idempotency_key.startswith("decision:")
    assert first.decision.new_targets == (("ETF_A", 0.6), ("ETF_B", 0.4))

    correction = store.append_decision_correction(
        shadow_decision_id=first.decision.shadow_decision_id,
        correction_type="FUTURE_DATA_VENDOR_RESTATEMENT",
        reason="vendor revised input after seal",
        corrected_at=at("2026-01-07T00:00:00"),
    )

    assert len(store.decisions) == 1
    assert len(store.corrections) == 1
    assert correction.shadow_decision_id == first.decision.shadow_decision_id
    assert store.decisions[0].new_targets == (("ETF_A", 0.6), ("ETF_B", 0.4))


def test_execution_waits_for_market_data_then_uses_separate_idempotency_and_dual_nav() -> None:
    store, version = seeded_store()
    schedule_ready_signal(store, version)
    decision = ShadowDecisionService(store).seal_scheduled_decision(
        version.strategy_version_id,
        as_of_time=at("2026-01-05T09:30:00"),
    ).decision
    service = ShadowExecutionService(store)

    before_data = service.execute_due_orders(
        decision.shadow_decision_id,
        execution_as_of_time=at("2026-01-06T09:25:00"),
    )

    assert before_data.status == "DATA_NOT_READY"
    assert before_data.attempts == ()
    assert before_data.fills == ()
    assert len(store.execution_attempts) == 0

    store.add_market_data(
        MarketDataForExecution(
            execution_date="2026-01-06",
            available_at=at("2026-01-06T09:31:00"),
            prices=(("ETF_A", 10.0), ("ETF_B", 20.0)),
            ideal_nav=1_020.0,
            executable_nav=1_015.0,
            data_delay_seconds=60,
        )
    )
    executed = service.execute_due_orders(
        decision.shadow_decision_id,
        execution_as_of_time=at("2026-01-06T09:31:00"),
    )
    again = service.execute_due_orders(
        decision.shadow_decision_id,
        execution_as_of_time=at("2026-01-06T09:31:00"),
    )

    assert again is executed
    assert executed.status == "EXECUTED"
    assert executed.execution_idempotency_key.startswith("execution:")
    assert executed.execution_idempotency_key != decision.decision_idempotency_key
    assert len(executed.attempts) == 2
    assert executed.account_state.shadow_ideal_nav == 1_020.0
    assert executed.account_state.shadow_executable_nav == 1_015.0
    assert executed.account_state.completed_rebalance_cycles == 1
    assert executed.data_delay_seconds == 60


@pytest.mark.parametrize(
    ("seal_time", "expected_status"),
    (
        (at("2026-01-05T08:30:00"), ShadowDecisionStatus.DATA_NOT_READY),
        (at("2026-01-06T09:45:00"), ShadowDecisionStatus.INVALID),
    ),
)
def test_execution_rejects_non_sealed_decisions_without_account_lifecycle_side_effects(
    seal_time: datetime,
    expected_status: ShadowDecisionStatus,
) -> None:
    store, version = seeded_store()
    schedule_ready_signal(store, version)
    store.add_market_data(
        MarketDataForExecution(
            execution_date="2026-01-06",
            available_at=at("2026-01-06T09:31:00"),
            prices=(("ETF_A", 10.0), ("ETF_B", 20.0)),
            ideal_nav=1_020.0,
            executable_nav=1_015.0,
        )
    )
    previous_account_state = store.account_states[version.strategy_version_id]
    decision = ShadowDecisionService(store).seal_scheduled_decision(
        version.strategy_version_id,
        as_of_time=seal_time,
    ).decision

    result = ShadowExecutionService(store).execute_due_orders(
        decision.shadow_decision_id,
        execution_as_of_time=at("2026-01-06T09:31:00"),
    )

    assert decision.status == expected_status
    assert result.status == "DECISION_NOT_SEALED"
    assert result.account_state is None
    assert result.attempts == ()
    assert result.fills == ()
    assert store.execution_results == {}
    assert store.execution_attempts == []
    assert store.fills == []
    assert store.account_states[version.strategy_version_id] is previous_account_state
    assert previous_account_state.completed_rebalance_cycles == 0


def test_shadow_account_state_is_continuous_across_decision_cycles() -> None:
    store, version = seeded_store()
    schedule_ready_signal(store, version)
    first_decision = ShadowDecisionService(store).seal_scheduled_decision(
        version.strategy_version_id,
        as_of_time=at("2026-01-05T09:30:00"),
    ).decision
    store.add_market_data(
        MarketDataForExecution(
            execution_date="2026-01-06",
            available_at=at("2026-01-06T09:31:00"),
            prices=(("ETF_A", 10.0), ("ETF_B", 20.0)),
            ideal_nav=1_020.0,
            executable_nav=1_015.0,
        )
    )
    first_execution = ShadowExecutionService(store).execute_due_orders(
        first_decision.shadow_decision_id,
        execution_as_of_time=at("2026-01-06T09:31:00"),
    )
    store.schedule_signal(
        strategy_version_id=version.strategy_version_id,
        signal_date="2026-01-12",
        data_available_at=at("2026-01-12T09:00:00"),
        snapshot_fingerprint="snapshot-2",
        raw_signal={"momentum": {"ETF_B": 1.0}},
        selected_clusters=("defensive",),
        target_weights=(("ETF_B", 1.0),),
        target_change_reasons=("risk-off",),
        expected_execution_date="2026-01-13",
    )

    second_decision = ShadowDecisionService(store).seal_scheduled_decision(
        version.strategy_version_id,
        as_of_time=at("2026-01-12T09:30:00"),
    ).decision

    assert first_execution.account_state.positions == (("ETF_A", 60.9), ("ETF_B", 20.3))
    assert second_decision.previous_targets == (("ETF_A", 0.6), ("ETF_B", 0.4))
    assert second_decision.previous_cash == 0.0
    assert second_decision.accounting_contract_version == "daily-accounting-order/v1"


def test_decision_eligibility_requires_minimum_observation_cycles_and_manual_approval() -> None:
    policy = valid_policy()
    evidence = valid_evidence()

    too_early = assess_decision_eligibility(
        strategy_version_id="sv-1",
        policy=policy,
        evidence=(evidence,),
        forward_observation_weeks=25,
        completed_rebalance_cycles=6,
        regime_coverage_sufficient=False,
        approval=None,
        evaluated_at=at("2026-07-01T00:00:00"),
    )
    mature_without_approval = assess_decision_eligibility(
        strategy_version_id="sv-1",
        policy=policy,
        evidence=(evidence,),
        forward_observation_weeks=26,
        completed_rebalance_cycles=6,
        regime_coverage_sufficient=False,
        approval=None,
        evaluated_at=at("2026-07-08T00:00:00"),
    )
    approval = ApprovalRecord(
        approval_id="approval-1",
        strategy_version_id="sv-1",
        approver="risk-owner",
        approved_at=at("2026-07-09T00:00:00"),
        policy_hash=policy.policy_hash,
        assessment_id="pre-approval-assessment",
        evidence_version="evidence-v1",
        known_limitations=("short forward window",),
        allowed_use="research-and-investment-reference",
    )
    approved = assess_decision_eligibility(
        strategy_version_id="sv-1",
        policy=policy,
        evidence=(evidence,),
        forward_observation_weeks=26,
        completed_rebalance_cycles=6,
        regime_coverage_sufficient=False,
        approval=approval,
        evaluated_at=at("2026-07-10T00:00:00"),
    )

    assert too_early.decision == DecisionQualification.INELIGIBLE
    assert "MIN_FORWARD_OBSERVATION_WEEKS" in too_early.reason_codes
    assert mature_without_approval.decision == DecisionQualification.INELIGIBLE
    assert mature_without_approval.failed_hard_gates == ("manual-approval",)
    assert approved.decision == DecisionQualification.ELIGIBLE
    assert approved.failed_hard_gates == ()
    assert approved.warnings == ("REGIME_COVERAGE_INSUFFICIENT",)


def test_drift_suspends_deployment_without_invalidating_frozen_version() -> None:
    store, version = seeded_store()
    assessment = monitor_drift(
        store,
        deployment_id="shadow-1",
        metrics={"maximum_shadow_drawdown": 0.21, "ideal_execution_gap": 0.02},
        thresholds=DriftThresholds(
            maximum_shadow_drawdown=0.2,
            execution_cost_ratio=0.05,
            data_failure_count=3,
            consecutive_invalid_decisions=2,
            ideal_execution_gap=0.1,
        ),
        evaluated_at=at("2026-03-01T00:00:00"),
    )

    assert store.deployments["shadow-1"].status == ShadowDeploymentStatus.SUSPENDED
    assert store.strategy_lifecycles[version.strategy_version_id] == StrategyVersionLifecycle.FROZEN
    assert assessment.decision == DecisionQualification.INELIGIBLE
    assert assessment.reason_codes == ("SHADOW_DRIFT_SUSPENDED",)


def test_invalidating_strategy_revokes_decision_qualification_but_preserves_history() -> None:
    store, version = seeded_store()
    schedule_ready_signal(store, version)
    decision = ShadowDecisionService(store).seal_scheduled_decision(
        version.strategy_version_id,
        as_of_time=at("2026-01-05T09:30:00"),
    ).decision

    assessment = invalidate_strategy_version(
        store,
        strategy_version_id=version.strategy_version_id,
        reason_code="FROZEN_HASH_MISMATCH",
        evaluated_at=at("2026-02-01T00:00:00"),
    )

    assert store.strategy_lifecycles[version.strategy_version_id] == StrategyVersionLifecycle.INVALIDATED
    assert store.decision_qualifications[version.strategy_version_id] == DecisionQualification.REVOKED
    assert assessment.decision == DecisionQualification.REVOKED
    assert store.decisions[0].shadow_decision_id == decision.shadow_decision_id


def test_artifact_names_have_event_contracts_and_no_broker_artifact() -> None:
    contracts = default_artifact_contracts()

    assert set(EXPECTED_ARTIFACT_NAMES) == set(contracts)
    assert contracts["frozen_strategy_manifest.json"] == "immutable-json-event/v1"
    assert contracts["shadow_decisions.csv"] == "append-only-csv-event/v1"
    assert contracts["shadow_manifest.json"] == "run-manifest-json/v1"
    assert "broker_orders.csv" not in contracts
