from __future__ import annotations

import json
from pathlib import Path

import pytest

from backtest.fund_rotation.champion_validation.contracts import (
    ValidationContract,
    canonical_hash,
    freeze_identity,
)
from backtest.fund_rotation.champion_validation.controller import (
    EXPECTED_STAGE_ORDER,
    ChampionValidationController,
    IdentityDriftError,
)


def _pass_handlers(calls: list[str], *, fail_stage: str | None = None):
    def handler(context):
        stage = context["stage"]
        calls.append(stage)
        return {
            "status": "INCONCLUSIVE" if stage == fail_stage else "PASS",
            "reason_codes": ["FIXTURE_RESULT"],
            "payload": {"stage": stage, "terminal": True},
        }

    return {stage: handler for stage in EXPECTED_STAGE_ORDER[:-1]}


def _real_identity(label: str = "fixture"):
    return freeze_identity(
        source={"fixture": label},
        strategy={"id": ValidationContract().subject_strategy, "fixture": label},
    )


def test_controller_runs_stages_in_order_and_writes_auditable_experiment(tmp_path: Path):
    calls: list[str] = []
    controller = ChampionValidationController(
        tmp_path / "experiment",
        contract=ValidationContract(),
        identity=_real_identity("pass"),
        stage_handlers=_pass_handlers(calls),
    )

    result = controller.run(idempotency_key="fixture-pass")

    assert calls == list(EXPECTED_STAGE_ORDER[:-1])
    assert result.stage_order == EXPECTED_STAGE_ORDER
    assert result.decision.action.value == "P2_RESEARCH_AUTHORIZED"
    assert (tmp_path / "experiment" / "validation_spec.json").exists()
    assert (tmp_path / "experiment" / "validation_ledger.jsonl").exists()
    assert (tmp_path / "experiment" / "report.md").read_text(encoding="utf-8").startswith("# R11")
    assert all((tmp_path / "experiment" / "stages" / f"{index:02d}_{stage}").is_dir() for index, stage in enumerate(EXPECTED_STAGE_ORDER[:-1]))

    ledger_lines = (tmp_path / "experiment" / "validation_ledger.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(ledger_lines) == len(EXPECTED_STAGE_ORDER)
    assert all(json.loads(line)["sequence"] == index for index, line in enumerate(ledger_lines, 1))


def test_controller_stops_after_universe_gap_and_does_not_interpret_later_stages(tmp_path: Path):
    calls: list[str] = []
    controller = ChampionValidationController(
        tmp_path / "experiment",
        stage_handlers=_pass_handlers(calls, fail_stage="universe"),
    )

    result = controller.run(idempotency_key="fixture-gap")

    assert calls == ["preflight", "universe"]
    assert result.stage_results["universe"].status.value == "INCONCLUSIVE"
    assert "benchmarks" not in result.stage_results
    assert result.decision.action.value == "STOP_CURRENT_ARCHITECTURE"
    assert "UNIVERSE_EVIDENCE_INSUFFICIENT" in result.decision.reason_codes


def test_resume_rechecks_frozen_identity_before_using_existing_results(tmp_path: Path):
    experiment_dir = tmp_path / "experiment"
    first = ChampionValidationController(
        experiment_dir,
        identity=_real_identity("one"),
        stage_handlers=_pass_handlers([]),
    )
    first.run(idempotency_key="same-key")

    resumed = ChampionValidationController(
        experiment_dir,
        identity=_real_identity("two"),
        stage_handlers=_pass_handlers([]),
    )
    with pytest.raises(IdentityDriftError, match="BLOCKED_IDENTITY_DRIFT"):
        resumed.run(resume=False, idempotency_key="same-key")

    assert json.loads((experiment_dir / "frozen_subject.json").read_text(encoding="utf-8")) == _real_identity("one").to_dict()


def test_idempotent_resume_does_not_duplicate_ledger_or_rerun_completed_stages(tmp_path: Path):
    calls: list[str] = []
    handlers = _pass_handlers(calls)
    controller = ChampionValidationController(tmp_path / "experiment", stage_handlers=handlers)
    controller.run(idempotency_key="same-key")
    first_ledger = (tmp_path / "experiment" / "validation_ledger.jsonl").read_text(encoding="utf-8")
    first_calls = list(calls)

    resumed = ChampionValidationController(tmp_path / "experiment", stage_handlers=handlers)
    resumed.run(idempotency_key="same-key")

    assert calls == first_calls
    assert (tmp_path / "experiment" / "validation_ledger.jsonl").read_text(encoding="utf-8") == first_ledger
    assert json.loads((tmp_path / "experiment" / "idempotency.json").read_text(encoding="utf-8"))["idempotency_key"] == "same-key"


def test_different_idempotency_key_is_blocked_without_appending_ledger(tmp_path: Path):
    experiment_dir = tmp_path / "experiment"
    controller = ChampionValidationController(experiment_dir, stage_handlers=_pass_handlers([]))
    controller.run(idempotency_key="first-key")
    ledger_before = (experiment_dir / "validation_ledger.jsonl").read_text(encoding="utf-8")

    with pytest.raises(RuntimeError, match="IDEMPOTENCY_KEY_CONFLICT"):
        ChampionValidationController(experiment_dir, stage_handlers=_pass_handlers([])).run(
            idempotency_key="second-key"
        )

    assert (experiment_dir / "validation_ledger.jsonl").read_text(encoding="utf-8") == ledger_before


def test_default_identity_binds_r11_subject_but_cannot_authorize_p2(tmp_path: Path):
    contract = ValidationContract()
    result = ChampionValidationController(
        tmp_path / "experiment",
        contract=contract,
        stage_handlers=_pass_handlers([]),
    ).run(idempotency_key="default-identity")

    stored_identity = json.loads((tmp_path / "experiment" / "frozen_subject.json").read_text(encoding="utf-8"))
    assert stored_identity["strategy_hash"] == canonical_hash(
        {"id": contract.subject_strategy, "status": contract.subject_status}
    )
    assert result.decision.action.value == "FORWARD_SHADOW_ONLY"
    assert "REAL_IDENTITY_REQUIRED" in result.decision.reason_codes


def test_resume_blocks_artifact_checksum_mismatch_without_overwriting_old_evidence(tmp_path: Path):
    experiment_dir = tmp_path / "experiment"
    ChampionValidationController(
        experiment_dir,
        identity=_real_identity("checksum"),
        stage_handlers=_pass_handlers([]),
    ).run(idempotency_key="checksum-key")
    result_path = experiment_dir / "stages" / "00_preflight" / "result.json"
    value = json.loads(result_path.read_text(encoding="utf-8"))
    value["data_hash"] = "0" * 64
    result_path.write_text(json.dumps(value), encoding="utf-8")
    old_evidence = result_path.read_text(encoding="utf-8")
    ledger_before = (experiment_dir / "validation_ledger.jsonl").read_text(encoding="utf-8")

    with pytest.raises(RuntimeError, match="BLOCKED_ARTIFACT_CHECKSUM_MISMATCH"):
        ChampionValidationController(
            experiment_dir,
            identity=_real_identity("checksum"),
            stage_handlers=_pass_handlers([]),
        ).run(resume=True, idempotency_key="checksum-key")

    assert result_path.read_text(encoding="utf-8") == old_evidence
    assert (experiment_dir / "validation_ledger.jsonl").read_text(encoding="utf-8") == ledger_before


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("experiment_id", "different-experiment"),
        ("schema_version", "different-schema"),
        ("stage", "universe"),
    ],
)
def test_resume_compares_artifact_identity_context_fields(tmp_path: Path, field_name: str, field_value: str):
    experiment_dir = tmp_path / "experiment"
    ChampionValidationController(
        experiment_dir,
        identity=_real_identity("context"),
        stage_handlers=_pass_handlers([]),
    ).run(idempotency_key="context-key")
    result_path = experiment_dir / "stages" / "00_preflight" / "result.json"
    value = json.loads(result_path.read_text(encoding="utf-8"))
    value[field_name] = field_value
    result_path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(RuntimeError, match="BLOCKED_ARTIFACT"):
        ChampionValidationController(
            experiment_dir,
            identity=_real_identity("context"),
            stage_handlers=_pass_handlers([]),
        ).run(resume=True, idempotency_key="context-key")


def test_resume_validates_ledger_chain_before_using_existing_results(tmp_path: Path):
    experiment_dir = tmp_path / "experiment"
    ChampionValidationController(
        experiment_dir,
        identity=_real_identity("ledger"),
        stage_handlers=_pass_handlers([]),
    ).run(idempotency_key="ledger-key")
    ledger_path = experiment_dir / "validation_ledger.jsonl"
    records = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
    records[1]["status"] = "FAIL"
    ledger_path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    ledger_before = ledger_path.read_text(encoding="utf-8")

    with pytest.raises(RuntimeError, match="BLOCKED_LEDGER_CHAIN"):
        ChampionValidationController(
            experiment_dir,
            identity=_real_identity("ledger"),
            stage_handlers=_pass_handlers([]),
        ).run(resume=True, idempotency_key="ledger-key")

    assert ledger_path.read_text(encoding="utf-8") == ledger_before


@pytest.mark.parametrize("fail_stage", ["universe", "stress"])
def test_final_artifact_preserves_actual_non_pass_status_action_and_reasons(tmp_path: Path, fail_stage: str):
    experiment_dir = tmp_path / fail_stage
    result = ChampionValidationController(
        experiment_dir,
        identity=_real_identity(fail_stage),
        stage_handlers=_pass_handlers([], fail_stage=fail_stage),
    ).run(idempotency_key=f"final-{fail_stage}")

    final = json.loads((experiment_dir / "stages" / "08_final" / "result.json").read_text(encoding="utf-8"))
    assert final["status"] == "INCONCLUSIVE"
    assert final["payload"]["action"] == result.decision.action.value
    assert "FIXTURE_RESULT" in final["reason_codes"]
    if fail_stage == "universe":
        assert result.decision.action.value == "STOP_CURRENT_ARCHITECTURE"
    else:
        assert result.decision.action.value == "FORWARD_SHADOW_ONLY"


@pytest.mark.parametrize("mutation", ["partial", "corrupt"])
def test_resume_does_not_treat_partial_or_corrupt_stage_as_completed(tmp_path: Path, mutation: str):
    calls: list[str] = []
    controller = ChampionValidationController(tmp_path / "experiment", stage_handlers=_pass_handlers(calls))
    controller.run(idempotency_key="repair-key")
    calls.clear()
    result_path = tmp_path / "experiment" / "stages" / "03_ablation" / "result.json"
    if mutation == "partial":
        value = json.loads(result_path.read_text(encoding="utf-8"))
        value["payload"]["partial"] = True
        result_path.write_text(json.dumps(value), encoding="utf-8")
    else:
        result_path.write_text("{not-json", encoding="utf-8")

    resumed = ChampionValidationController(tmp_path / "experiment", stage_handlers=_pass_handlers(calls))
    resumed.run(resume=True, idempotency_key="repair-key")

    assert calls == ["ablation", "stability", "stress", "attribution", "statistics"]


def test_report_and_artifacts_never_emit_selection_fields(tmp_path: Path):
    def handler(context):
        return {"status": "PASS", "payload": {"terminal": True, "winner": "forbidden"}}

    controller = ChampionValidationController(
        tmp_path / "experiment",
        stage_handlers={stage: handler for stage in EXPECTED_STAGE_ORDER[:-1]},
    )
    with pytest.raises(ValueError, match="winner"):
        controller.run(idempotency_key="forbidden")
