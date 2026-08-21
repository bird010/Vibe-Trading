from datetime import date
import json

import pytest

import backtest.fund_rotation.champion_validation.contracts as contracts_module
from backtest.fund_rotation.champion_validation.contracts import (
    ABLATION_VARIANTS,
    CONSUMED_CONFIRMATION_INTERVAL,
    EXPERIMENT_ID,
    SUBJECT_STRATEGY,
    TRIAL_COUNT,
    ConfirmationIntervalViolation,
    ValidationContract,
    ValidationLedger,
    append_ledger_entry,
    build_structured_artifact,
    canonical_hash,
    freeze_identity,
    validate_confirmation_interval,
)


def test_contract_is_frozen_and_exposes_non_promotion_experiment_identity():
    contract = ValidationContract()

    assert contract.experiment_id == EXPERIMENT_ID
    assert contract.subject_strategy == SUBJECT_STRATEGY
    assert contract.candidate_selection_enabled is False
    assert contract.promotion_enabled is False
    assert contract.trial_count == TRIAL_COUNT == 30
    assert contract.ablation_variants == ABLATION_VARIANTS == ("A", "B", "C", "D", "E")

    with pytest.raises((AttributeError, TypeError)):
        contract.trial_count = 31


def test_contract_rejects_changes_to_preregistered_grid_stress_and_thresholds():
    contract = ValidationContract()

    with pytest.raises(ValueError, match="momentum_windows"):
        ValidationContract(momentum_windows=(3, 4, 6, 8, 13))
    with pytest.raises(ValueError, match="top_n_values"):
        ValidationContract(top_n_values=(2, 3, 5))
    with pytest.raises(ValueError, match="recluster_weeks"):
        ValidationContract(recluster_weeks=(13, 26, 104))
    with pytest.raises(ValueError, match="stress_scenarios"):
        ValidationContract(stress_scenarios=contract.stress_scenarios[:-1])

    altered_thresholds = dict(contract.thresholds)
    altered_thresholds["stress"] = {"slippage_bps": 99}
    with pytest.raises(ValueError, match="thresholds"):
        ValidationContract(thresholds=altered_thresholds)


def test_contract_thresholds_are_deeply_immutable():
    thresholds = ValidationContract().thresholds

    with pytest.raises(TypeError):
        thresholds["economic_value"]["annualized_excess_return_gt"] = 1.0


def test_canonical_hash_is_independent_of_mapping_insertion_order():
    left = {"outer": {"b": 2, "a": ["x", 1]}, "value": True}
    right = {"value": True, "outer": {"a": ["x", 1], "b": 2}}

    assert canonical_hash(left) == canonical_hash(right)
    assert canonical_hash(left) != canonical_hash({**left, "value": False})


def test_freeze_identity_produces_component_hashes_and_stable_identity_hash():
    first = freeze_identity(
        data_snapshot={"date": "20220729", "rows": 10},
        framework={"python": "3.11"},
        strategy={"id": SUBJECT_STRATEGY, "source": "r11.py"},
        execution={"slippage_bps": 10},
        spec={"trial_count": 30},
    )
    second = freeze_identity(
        spec={"trial_count": 30},
        execution={"slippage_bps": 10},
        strategy={"source": "r11.py", "id": SUBJECT_STRATEGY},
        framework={"python": "3.11"},
        data_snapshot={"rows": 10, "date": "20220729"},
    )

    assert first == second
    assert first.strategy_hash == canonical_hash({"id": SUBJECT_STRATEGY, "source": "r11.py"})
    assert first.identity_hash
    assert len(first.identity_hash) == 64


def test_confirmation_interval_rejects_any_overlap_with_consumed_interval():
    consumed = CONSUMED_CONFIRMATION_INTERVAL
    assert validate_confirmation_interval("2017-07-07", "2022-07-29") is True
    assert validate_confirmation_interval(date(2026, 8, 2), date(2026, 8, 21)) is True

    with pytest.raises(ConfirmationIntervalViolation):
        validate_confirmation_interval(consumed.start, consumed.start)

    with pytest.raises(ConfirmationIntervalViolation):
        validate_confirmation_interval("2022-07-31", "2022-08-02")


def test_structured_artifact_contains_audit_identity_fields_and_rejects_selection_fields():
    artifact = build_structured_artifact(
        ValidationContract(),
        status="PASS",
        reason_codes=("CHECKED",),
        payload={"stage": "preflight", "metrics": {"count": 1}},
        timestamp="2026-08-21T00:00:00Z",
    )

    assert {
        "schema_version",
        "experiment_id",
        "timestamp",
        "input_checksum",
        "data_hash",
        "framework_hash",
        "strategy_hash",
        "execution_hash",
        "spec_hash",
        "status",
        "reason_codes",
        "payload",
    } <= set(artifact)
    assert artifact["status"] == "PASS"
    assert artifact["reason_codes"] == ["CHECKED"]

    with pytest.raises(ValueError, match="winner"):
        build_structured_artifact(ValidationContract(), status="PASS", payload={"winner": "A"})


def test_validation_ledger_appends_entries_without_replacing_previous_entries(tmp_path):
    path = tmp_path / "validation_ledger.jsonl"
    first = build_structured_artifact(ValidationContract(), status="PASS", payload={"stage": "preflight"})
    second = build_structured_artifact(ValidationContract(), status="INCONCLUSIVE", payload={"stage": "universe"})

    append_ledger_entry(path, first)
    append_ledger_entry(path, second)
    lines = path.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 2
    first_record = json.loads(lines[0])
    second_record = json.loads(lines[1])
    assert first_record["payload"]["stage"] == "preflight"
    assert second_record["payload"]["stage"] == "universe"
    assert first_record["stage"] == "preflight"
    assert second_record["stage"] == "universe"
    assert second_record["previous_entry_hash"] == first_record["entry_hash"]
    verifier = getattr(contracts_module, "verify_ledger_chain", None)
    assert verifier is not None
    assert verifier(path) is True


def test_validation_ledger_chain_rejects_tampering(tmp_path):
    path = tmp_path / "validation_ledger.jsonl"
    append_ledger_entry(path, {"status": "PASS", "payload": {"stage": "preflight"}})
    append_ledger_entry(path, {"status": "PASS", "payload": {"stage": "universe"}})

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    records[0]["status"] = "FAIL"
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")

    verifier = getattr(contracts_module, "verify_ledger_chain", None)
    assert verifier is not None
    assert verifier(path) is False


@pytest.mark.parametrize(
    "field_name",
    [" Winner ", "WINNER_ID", "selected_winner", "winnerName", " recommended_parameter ", "recommendedParameter"],
)
def test_selection_field_detection_rejects_trimmed_and_semantic_variants(field_name):
    with pytest.raises(ValueError, match="selection field"):
        build_structured_artifact(ValidationContract(), status="PASS", payload={field_name: "A"})


def test_in_memory_ledger_returns_a_new_ledger_and_preserves_existing_entries():
    ledger = ValidationLedger()
    first = build_structured_artifact(ValidationContract(), status="PASS", payload={"stage": "preflight"})
    updated = append_ledger_entry(ledger, first)

    assert ledger.entries == ()
    assert len(updated.entries) == 1
    assert updated.entries[0]["status"] == "PASS"
