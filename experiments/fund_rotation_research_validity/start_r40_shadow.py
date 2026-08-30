"""Freeze R40 and start the append-only Shadow A evidence boundary.

The entry point deliberately does not fabricate a decision or a fill when no
live U1/control identity or market-data service is available.  It still
creates the frozen version, running deployment, qualification policy and the
empty first-cycle artifacts so that a later real cycle has a stable boundary.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

AGENT_ROOT = Path(__file__).resolve().parents[2] / "agent"
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from backtest.fund_rotation.accounting_contract import ACCOUNTING_CONTRACT_VERSION
from src.stockpred.fund_rotation.forward_validation import (
    EXPECTED_ARTIFACT_NAMES,
    GateSpec,
    InMemoryForwardValidationStore,
    QualificationEvidence,
    QualificationPolicy,
    ShadowAccountState,
    ShadowDecisionService,
    ShadowDeployment,
    ShadowDeploymentStatus,
    ShadowExecutionService,
    ShadowRunScheduler,
    StrategyVersionLifecycle,
    assess_decision_eligibility,
    build_frozen_strategy_version,
    default_artifact_contracts,
)


EXPERIMENT_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = EXPERIMENT_ROOT / "shadow_a_cycles6_v2"
DEFAULT_REPORT_PATH = EXPERIMENT_ROOT / "shadow_a_cycles6_v2_report.md"
R40_STRATEGY_ID = "ai_rotation_r40_single_name_ceiling"
R40_CEILING = 0.5
R40_PARENT_EXPERIMENT_ID = "shadow_a_r40_frozen_from_u1_and_r39_control"
R39_CONTROL_STRATEGY_ID = "ai_rotation_r39_incumbent_carry"
R39_SOURCE_PATH = (
    Path(__file__).resolve().parents[2]
    / "agent/backtest/fund_rotation/strategies/ai_rotation_r39_incumbent_carry/strategy.py"
)
R40_SOURCE_PATH = (
    Path(__file__).resolve().parents[2]
    / "agent/backtest/fund_rotation/strategies/ai_rotation_r40_single_name_ceiling/strategy.py"
)
SHADOW_STATUS = "STARTED"
QUALIFICATION_STATUS = "INSUFFICIENT_FORWARD_EVIDENCE"
INTEGRITY_ARTIFACT_NAME = "shadow_integrity.json"
DEFAULT_SHADOW_MANIFEST_SHA256 = (
    "d3a68fb1556a07607847c15b65435d482d7b6c16bef5abe152df72da10072f1f"
)
DEFAULT_SHADOW_REPORT_SHA256 = (
    "cbf6c235b1c0acd299a8d1c99532599e8da54f512a039d5fea3e34533f0ae65d"
)
DEFAULT_SHADOW_INTEGRITY_SHA256 = (
    "629d712c3f33237c8931637ab3ea9e7d04b3b7efe74d9216a45cfd4a25316d53"
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value.lower()
    )


def _pit_stable_hash(value: object) -> str:
    return _sha256_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    )


def _valid_pit_snapshot(snapshot: object, *, expected_layer: str) -> bool:
    if not isinstance(snapshot, dict):
        return False
    required = {
        "layer",
        "signal_date",
        "knowledge_cutoff",
        "source_snapshot_version",
        "eligible_codes",
        "membership",
        "identity_mapping",
        "identity_hash",
        "snapshot_fingerprint",
        "coverage_diagnostics",
        "quality_status",
    }
    if not required.issubset(snapshot):
        return False
    eligible_codes = snapshot["eligible_codes"]
    membership = snapshot["membership"]
    identity_mapping = snapshot["identity_mapping"]
    if not isinstance(snapshot["signal_date"], str) or not isinstance(
        snapshot["knowledge_cutoff"], str
    ):
        return False
    try:
        date.fromisoformat(snapshot["signal_date"])
        datetime.fromisoformat(snapshot["knowledge_cutoff"].replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if (
        snapshot["layer"] != expected_layer
        or not isinstance(snapshot["signal_date"], str)
        or len(snapshot["signal_date"]) != 10
        or snapshot["signal_date"][4] != "-"
        or snapshot["signal_date"][7] != "-"
        or not isinstance(snapshot["knowledge_cutoff"], str)
        or not isinstance(snapshot["source_snapshot_version"], int)
        or isinstance(snapshot["source_snapshot_version"], bool)
        or not isinstance(eligible_codes, list)
        or not all(isinstance(code, str) and bool(code.strip()) for code in eligible_codes)
        or len(set(eligible_codes)) != len(eligible_codes)
        or not isinstance(membership, list)
        or not isinstance(identity_mapping, dict)
        or not all(
            isinstance(key, str) and (value is None or isinstance(value, str))
            for key, value in identity_mapping.items()
        )
        or not _is_sha256(snapshot["identity_hash"])
        or not _is_sha256(snapshot["snapshot_fingerprint"])
        or not isinstance(snapshot["coverage_diagnostics"], dict)
        or snapshot["quality_status"]
        not in {
            "VERIFIED",
            "KNOWLEDGE_TIME_UNVERIFIED",
            "PIT_UNVERIFIED",
            "RESEARCH_ONLY_UNVERIFIED_UNIVERSE",
        }
    ):
        return False
    member_codes: set[str] = set()
    for item in membership:
        if not isinstance(item, dict) or not {
            "ts_code",
            "included",
            "reason_code",
            "identity_key",
            "layer",
        }.issubset(item):
            return False
        if (
            not isinstance(item["ts_code"], str)
            or not item["ts_code"].strip()
            or not isinstance(item["included"], bool)
            or not isinstance(item["reason_code"], str)
            or not item["reason_code"].strip()
            or (item["identity_key"] is not None and not isinstance(item["identity_key"], str))
            or item["layer"] != expected_layer
        ):
            return False
        member_codes.add(item["ts_code"])
    if expected_layer == "U1" and any(
        not isinstance(value, str) or not value.strip()
        for code in eligible_codes
        for value in (identity_mapping.get(code),)
    ):
        return False
    if (
        len(member_codes) != len(membership)
        or not set(eligible_codes).issubset(member_codes)
        or member_codes != set(identity_mapping)
        or any(
            item["included"] != (item["ts_code"] in set(eligible_codes))
            or item["identity_key"] != identity_mapping.get(item["ts_code"])
            for item in membership
        )
    ):
        return False
    expected_identity_payload: dict[str, object] = {
        "layer": expected_layer,
        "identity_mapping": sorted(identity_mapping.items()),
    }
    representatives: dict[str, str] = {}
    if expected_layer == "U1":
        representatives = {
            identity: min(
                code for code in eligible_codes if identity_mapping.get(code) == identity
            )
            for identity in {identity_mapping.get(code) for code in eligible_codes}
            if identity is not None
        }
        expected_identity_payload["representatives"] = sorted(representatives.items())
    expected_identity_hash = _pit_stable_hash(expected_identity_payload)
    expected_fingerprint = _pit_stable_hash(
        {
            "layer": expected_layer,
            "signal_date": snapshot["signal_date"],
            "knowledge_cutoff": snapshot["knowledge_cutoff"],
            "source_snapshot_version": snapshot["source_snapshot_version"],
            "eligible_codes": sorted(eligible_codes),
            "membership": membership,
            "identity_hash": expected_identity_hash,
            "quality_status": snapshot["quality_status"],
            "coverage_diagnostics": dict(sorted(snapshot["coverage_diagnostics"].items())),
        }
    )
    if expected_layer == "U1" and any(
        item["included"] and item["ts_code"] != representatives[item["identity_key"]]
        for item in membership
    ):
        return False
    if (
        snapshot["identity_hash"] != expected_identity_hash
        or snapshot["snapshot_fingerprint"] != expected_fingerprint
    ):
        return False
    return True


def _valid_u1_snapshot(snapshot: object) -> bool:
    return _valid_pit_snapshot(snapshot, expected_layer="U1")


def _valid_u1_envelope(snapshot: object) -> bool:
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("signal_date"), str):
        return False
    try:
        date.fromisoformat(snapshot["signal_date"])
    except ValueError:
        return False
    u1 = snapshot.get("u1")
    u0 = snapshot.get("u0")
    if not (
        isinstance(u0, dict)
        and isinstance(u1, dict)
        and _valid_pit_snapshot(u0, expected_layer="U0")
        and _valid_u1_snapshot(u1)
        and u0.get("signal_date") == snapshot["signal_date"]
        and u1.get("signal_date") == snapshot["signal_date"]
        and u0.get("knowledge_cutoff") == u1.get("knowledge_cutoff")
        and u0.get("source_snapshot_version") == u1.get("source_snapshot_version")
        and u0.get("quality_status") == u1.get("quality_status")
        and u0.get("identity_mapping") == u1.get("identity_mapping")
    ):
        return False
    representatives = {
        identity: min(
            code
            for code in u0["eligible_codes"]
            if u0["identity_mapping"].get(code) == identity
        )
        for identity in set(u0["identity_mapping"].values())
        if identity is not None
    }
    if set(u1["eligible_codes"]) != set(representatives.values()):
        return False
    expected_membership = []
    u0_eligible = set(u0["eligible_codes"])
    for item in u0["membership"]:
        code = item["ts_code"]
        identity = item["identity_key"]
        if code not in u0_eligible:
            expected = (False, item["reason_code"], identity)
        elif representatives[identity] == code:
            expected = (True, "U1_REPRESENTATIVE", identity)
        else:
            expected = (False, "DUPLICATE_IDENTITY", identity)
        expected_membership.append(
            {
                "ts_code": code,
                "included": expected[0],
                "reason_code": expected[1],
                "identity_key": expected[2],
                "layer": "U1",
            }
        )
    return u1["membership"] == expected_membership


def _write_immutable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(content.encode("utf-8"))
    except FileExistsError:
        if path.read_text(encoding="utf-8") != content:
            raise FileExistsError(f"refusing to overwrite immutable artifact: {path}") from None


def _input_record(path: Path | None, *, kind: str) -> dict[str, Any]:
    if path is None:
        return {"path": None, "status": "missing"}
    if not path.is_file():
        return {"path": str(path), "status": "missing"}
    record: dict[str, Any] = {
        "path": str(path),
        "status": "present",
        "sha256": _sha256_file(path),
    }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        record.update({"valid": False, "reason": "INVALID_JSON"})
        return record
    if not isinstance(payload, dict):
        record.update({"valid": False, "reason": "EXPECTED_JSON_OBJECT"})
        return record
    if kind == "frozen_u1":
        snapshot_status = payload.get("snapshot_status")
        snapshots = payload.get("snapshots")
        snapshots_valid = (
            isinstance(snapshots, list)
            and bool(snapshots)
            and all(_valid_u1_envelope(snapshot) for snapshot in snapshots)
        )
        valid = (
            payload.get("schema_version") == "fund_rotation_pit_identity_v1"
            and isinstance(snapshot_status, dict)
            and snapshot_status.get("status") == "available"
            and isinstance(snapshots, list)
            and isinstance(snapshot_status.get("date_count"), int)
            and not isinstance(snapshot_status.get("date_count"), bool)
            and snapshot_status.get("date_count") == len(snapshots)
            and snapshot_status.get("invalid_dates") == []
            and snapshots_valid
        )
        reason = "INVALID_U1_IDENTITY_MANIFEST" if not valid else ""
    else:
        strategy = payload.get("strategy")
        code_identity = payload.get("code_identity")
        run_identity = payload.get("run_identity")
        strategy_id = payload.get("strategy_id") or payload.get("subject_strategy")
        if not isinstance(strategy_id, str) and isinstance(strategy, dict):
            strategy_id = strategy.get("strategy_id")
        identity_hash = payload.get("implementation_hash")
        if not isinstance(identity_hash, str) and isinstance(strategy, dict):
            identity_hash = strategy.get("implementation_hash")
        if not isinstance(identity_hash, str) and isinstance(code_identity, dict):
            identity_hash = code_identity.get("strategy_implementation_hash")
        if not isinstance(identity_hash, str) and isinstance(run_identity, dict):
            identity_hash = run_identity.get("run_identity_hash")
        if not isinstance(identity_hash, str):
            identity_hash = payload.get("run_identity_hash")
        valid = (
            strategy_id == R39_CONTROL_STRATEGY_ID
            and identity_hash == _sha256_file(R39_SOURCE_PATH)
        )
        reason = "INVALID_R39_CONTROL_IDENTITY" if not valid else ""
        if valid:
            record.update({"strategy_id": strategy_id, "identity_hash": identity_hash})
    record.update({"valid": valid, "reason": reason})
    return record


def _require_valid_inputs(inputs: dict[str, Any]) -> None:
    invalid = [
        name
        for name, record in inputs.items()
        if record.get("status") == "present" and record.get("valid") is not True
    ]
    if invalid:
        raise ValueError("fail-closed: invalid identity inputs: " + ", ".join(invalid))


def _iso(value: datetime) -> str:
    return value.isoformat()


def _policy(frozen_at: datetime) -> QualificationPolicy:
    return QualificationPolicy(
        policy_id="r40-shadow-a-forward-qualification-v1",
        policy_hash="",
        target_transition="CAN_GRANT_DECISION_ELIGIBILITY",
        hard_gates=(
            GateSpec(
                gate_id="min-forward-observation-weeks",
                metric_name="forward_observation_weeks",
                formula="calendar_weeks(first_shadow_decision, assessment_time)",
                evaluation_scope="shadow_deployment",
                threshold=26,
                comparison_operator=">=",
                missing_data_policy="FAIL_CLOSED",
                evidence_artifact="shadow_metrics.json",
            ),
            GateSpec(
                gate_id="min-completed-rebalance-cycles",
                metric_name="completed_rebalance_cycles",
                formula="count(sealed_and_executed_shadow_rebalance_cycles)",
                evaluation_scope="shadow_deployment",
                threshold=6,
                comparison_operator=">=",
                missing_data_policy="FAIL_CLOSED",
                evidence_artifact="shadow_account_state.json",
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
        frozen_at=frozen_at,
    )


def _version_payload(version: object, *, config: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "frozen-strategy-manifest/v1",
        "strategy_version_id": version.strategy_version_id,
        "strategy_id": version.strategy_id,
        "parent_research_experiment_id": version.parent_research_experiment_id,
        "implementation_hash": version.implementation_hash,
        "framework_hash": version.framework_hash,
        "config_hash": version.config_hash,
        "data_contract_version": version.data_contract_version,
        "execution_contract_version": version.execution_contract_version,
        "accounting_contract_version": version.accounting_contract_version,
        "qualification_policy_hash": version.qualification_policy_hash,
        "frozen_at": _iso(version.frozen_at),
        "effective_from": _iso(version.effective_from),
        "lifecycle": version.lifecycle.value,
        "config": config,
        "identity_inputs": inputs,
    }


def _policy_payload(policy: QualificationPolicy) -> dict[str, Any]:
    return {
        "schema_version": "qualification-policy/v1",
        "policy_id": policy.policy_id,
        "policy_hash": policy.policy_hash,
        "target_transition": policy.target_transition,
        "hard_gates": [gate.__dict__ for gate in policy.hard_gates],
        "warning_gates": [gate.__dict__ for gate in policy.warning_gates],
        "frozen_at": _iso(policy.frozen_at),
    }


def _assessment_payload(assessment: object) -> dict[str, Any]:
    return {
        "schema_version": "qualification-assessment/v1",
        "assessment_id": assessment.assessment_id,
        "target_transition": assessment.target_transition,
        "subject_id": assessment.subject_id,
        "policy_hash": assessment.policy_hash,
        "evidence_ids": assessment.evidence_ids,
        "decision": assessment.decision.value,
        "failed_hard_gates": assessment.failed_hard_gates,
        "warnings": assessment.warnings,
        "reason_codes": assessment.reason_codes,
        "evaluated_at": _iso(assessment.evaluated_at),
        "evaluator_version": assessment.evaluator_version,
        "status": QUALIFICATION_STATUS,
    }


def _initial_account_payload(version_id: str, started_at: datetime) -> dict[str, Any]:
    state = ShadowAccountState(
        strategy_version_id=version_id,
        as_of_time=started_at,
        cash=1000.0,
        positions=(),
        target_weights=(),
        residual_orders=(),
        shadow_ideal_nav=1000.0,
        shadow_executable_nav=1000.0,
        accounting_contract_version=ACCOUNTING_CONTRACT_VERSION,
        completed_rebalance_cycles=0,
        cash_weight=1.0,
    )
    return {
        "schema_version": "shadow-account-state/v1",
        "status": "STARTED",
        "reason_code": "NO_REAL_SHADOW_INPUTS",
        "strategy_version_id": state.strategy_version_id,
        "as_of_time": _iso(state.as_of_time),
        "cash": state.cash,
        "positions": state.positions,
        "target_weights": state.target_weights,
        "residual_orders": state.residual_orders,
        "shadow_ideal_nav": state.shadow_ideal_nav,
        "shadow_executable_nav": state.shadow_executable_nav,
        "accounting_contract_version": state.accounting_contract_version,
        "completed_rebalance_cycles": state.completed_rebalance_cycles,
        "cash_weight": state.cash_weight,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _write_immutable(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        + "\n",
    )


def _write_csv(path: Path, fieldnames: tuple[str, ...]) -> None:
    rows: list[dict[str, str]] = []
    with_path = path.parent
    with_path.mkdir(parents=True, exist_ok=True)
    content_path = path
    try:
        with content_path.open("x", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    except FileExistsError:
        expected = "".join(
            [
                ",".join(fieldnames),
                "\n",
            ]
        )
        if path.read_text(encoding="utf-8") != expected:
            raise FileExistsError(f"refusing to overwrite immutable artifact: {path}") from None


def _existing_result(
    output_dir: Path,
    report_path: Path,
    *,
    source_hash: str,
    r39_source_hash: str,
    inputs: dict[str, Any],
) -> dict[str, Any] | None:
    manifest_path = output_dir / "shadow_manifest.json"
    if not manifest_path.is_file():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("strategy_id") != R40_STRATEGY_ID
        or manifest.get("ceiling") != R40_CEILING
    ):
        raise ValueError("existing Shadow A manifest does not match frozen R40 configuration")
    if manifest.get("r40_source_sha256") != source_hash:
        raise ValueError("existing Shadow A manifest does not match current R40 source")
    if manifest.get("inputs") != inputs:
        raise ValueError("existing Shadow A manifest does not match requested identity inputs")
    frozen = json.loads(
        (output_dir / "frozen_strategy_manifest.json").read_text(encoding="utf-8")
    ) if (output_dir / "frozen_strategy_manifest.json").is_file() else {}
    frozen_config = frozen.get("config")
    if (
        frozen.get("strategy_id") != R40_STRATEGY_ID
        or not isinstance(frozen_config, dict)
        or frozen_config.get("single_name_ceiling") != R40_CEILING
        or frozen_config.get("control_strategy_id") != R39_CONTROL_STRATEGY_ID
        or frozen_config.get("r39_control_source_sha256") != r39_source_hash
        or frozen_config.get("r40_source_sha256") != source_hash
        or frozen.get("identity_inputs") != inputs
    ):
        raise ValueError("existing Shadow A frozen manifest does not match current configuration")
    declared_hashes = manifest.get("artifact_hashes")
    if not isinstance(declared_hashes, dict):
        raise ValueError("existing Shadow A manifest has no artifact hashes")
    expected_manifest_hash_keys = set(EXPECTED_ARTIFACT_NAMES) - {"shadow_manifest.json"}
    if set(declared_hashes) != expected_manifest_hash_keys:
        raise ValueError("existing Shadow A manifest has an incomplete artifact hash set")
    if manifest.get("artifact_contracts") != default_artifact_contracts():
        raise ValueError("existing Shadow A manifest has unexpected artifact contracts")
    if (
        manifest.get("status") != SHADOW_STATUS
        or manifest.get("deployment_status") != "RUNNING"
        or manifest.get("qualification_status") != QUALIFICATION_STATUS
        or manifest.get("promotion_allowed") is not False
        or manifest.get("qualified") is not False
    ):
        raise ValueError("existing Shadow A manifest has unexpected lifecycle state")
    for name in EXPECTED_ARTIFACT_NAMES:
        artifact_path = output_dir / name
        if not artifact_path.is_file():
            raise ValueError(f"existing Shadow A artifact is missing: {name}")
        actual_hash = _sha256_file(artifact_path)
        if name != "shadow_manifest.json" and declared_hashes.get(name) != actual_hash:
            raise ValueError(f"existing Shadow A artifact hash mismatch: {name}")
    if not report_path.is_file():
        raise ValueError(f"existing Shadow A report is missing: {report_path}")
    report_text = report_path.read_text(encoding="utf-8")
    manifest_hash = _sha256_file(manifest_path)
    if output_dir.resolve() == DEFAULT_OUTPUT_DIR.resolve() and manifest_hash != DEFAULT_SHADOW_MANIFEST_SHA256:
        raise ValueError("default Shadow A manifest does not match committed integrity anchor")
    report_hash = _sha256_file(report_path)
    if output_dir.resolve() == DEFAULT_OUTPUT_DIR.resolve() and report_hash != DEFAULT_SHADOW_REPORT_SHA256:
        raise ValueError("default Shadow A report does not match committed integrity anchor")
    if f"shadow_manifest SHA-256：`{manifest_hash}`" not in report_text:
        raise ValueError("existing Shadow A report does not bind shadow manifest hash")
    integrity_path = output_dir / INTEGRITY_ARTIFACT_NAME
    if not integrity_path.is_file():
        raise ValueError("existing Shadow A integrity sidecar is missing")
    integrity_file_hash = _sha256_file(integrity_path)
    if output_dir.resolve() == DEFAULT_OUTPUT_DIR.resolve() and integrity_file_hash != DEFAULT_SHADOW_INTEGRITY_SHA256:
        raise ValueError("default Shadow A integrity sidecar does not match committed anchor")
    integrity = json.loads(integrity_path.read_text(encoding="utf-8"))
    integrity_hashes = integrity.get("artifact_hashes")
    expected_integrity_hash_keys = set(EXPECTED_ARTIFACT_NAMES) | {report_path.name}
    if (
        integrity.get("schema_version") != "shadow-integrity/v1"
        or integrity.get("manifest_sha256") != manifest_hash
        or not isinstance(integrity_hashes, dict)
        or set(integrity_hashes) != expected_integrity_hash_keys
    ):
        raise ValueError("existing Shadow A integrity sidecar is invalid")
    for name in EXPECTED_ARTIFACT_NAMES:
        actual_hash = _sha256_file(output_dir / name)
        if integrity_hashes.get(name) != actual_hash:
            raise ValueError(f"existing Shadow A integrity hash mismatch: {name}")
    if integrity_hashes.get(report_path.name) != report_hash:
        raise ValueError("existing Shadow A integrity hash mismatch: report")
    return {
        "strategy_id": manifest["strategy_id"],
        "ceiling": manifest["ceiling"],
        "shadow_status": manifest["status"],
        "qualification_status": manifest["qualification_status"],
        "promotion_allowed": manifest["promotion_allowed"],
        "qualified": False,
        "strategy_version_id": manifest["strategy_version_id"],
        "artifact_hashes": {
            **{name: _sha256_file(output_dir / name) for name in EXPECTED_ARTIFACT_NAMES},
            report_path.name: _sha256_file(report_path),
            INTEGRITY_ARTIFACT_NAME: integrity_file_hash,
        },
    }


def start_shadow_a(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    frozen_u1_manifest: Path | None = None,
    r39_control_manifest: Path | None = None,
    started_at: datetime | None = None,
    report_path: Path = DEFAULT_REPORT_PATH,
) -> dict[str, Any]:
    """Freeze R40 and create the first Shadow A evidence boundary."""
    inputs = {
        "frozen_u1_manifest": _input_record(frozen_u1_manifest, kind="frozen_u1"),
        "r39_control_manifest": _input_record(r39_control_manifest, kind="r39_control"),
    }
    _require_valid_inputs(inputs)
    source_hash = _sha256_file(R40_SOURCE_PATH)
    r39_source_hash = _sha256_file(R39_SOURCE_PATH)
    existing = _existing_result(
        output_dir,
        report_path,
        source_hash=source_hash,
        r39_source_hash=r39_source_hash,
        inputs=inputs,
    )
    if existing is not None:
        return existing

    started_at = started_at or datetime.now(timezone.utc)
    if started_at.tzinfo is None:
        raise ValueError("started_at must be timezone-aware")
    policy = _policy(started_at)
    config = {
        "single_name_ceiling": R40_CEILING,
        "threshold_policy": "FROZEN_NO_RETUNING",
        "control_strategy_id": R39_CONTROL_STRATEGY_ID,
        "r39_control_source_sha256": r39_source_hash,
        "r40_source_sha256": source_hash,
    }
    version = build_frozen_strategy_version(
        strategy_id=R40_STRATEGY_ID,
        parent_research_experiment_id=R40_PARENT_EXPERIMENT_ID,
        implementation_payload={
            "strategy_id": R40_STRATEGY_ID,
            "frozen_logic": "existing_r40",
            "source_path": str(R40_SOURCE_PATH),
            "source_sha256": source_hash,
        },
        framework_payload={"shadow_services": [
            "FrozenStrategyVersion",
            "ShadowDecisionService",
            "ShadowExecutionService",
            "ShadowRunScheduler",
        ]},
        config_payload={**config, "identity_inputs": inputs},
        data_contract_version="pit-data/v1",
        execution_contract_version="shadow-execution/v1",
        accounting_contract_version=ACCOUNTING_CONTRACT_VERSION,
        qualification_policy=policy,
        frozen_at=started_at,
        effective_from=started_at,
    )

    store = InMemoryForwardValidationStore()
    store.add_strategy_version(version)
    store.set_account_state(
        version.strategy_version_id,
        ShadowAccountState(
            strategy_version_id=version.strategy_version_id,
            as_of_time=started_at,
            cash=1000.0,
            positions=(),
            target_weights=(),
            residual_orders=(),
            shadow_ideal_nav=1000.0,
            shadow_executable_nav=1000.0,
            accounting_contract_version=ACCOUNTING_CONTRACT_VERSION,
            completed_rebalance_cycles=0,
            cash_weight=1.0,
        ),
    )
    deployment = ShadowDeployment(
        deployment_id=f"shadow-a:{version.strategy_version_id}",
        strategy_version_id=version.strategy_version_id,
        status=ShadowDeploymentStatus.RUNNING,
        created_at=started_at,
    )
    store.add_deployment(deployment)
    decision_service = ShadowDecisionService(store)
    execution_service = ShadowExecutionService(store)
    scheduler = ShadowRunScheduler(store)
    due_versions = scheduler.due_versions(started_at)
    service_binding = {
        "decision_service": type(decision_service).__name__,
        "decision_provider": type(decision_service.decision_provider).__name__,
        "execution_service": type(execution_service).__name__,
        "execution_configured": execution_service.execution_adapter is not None
        and execution_service.accounting_adapter is not None,
        "scheduler": type(scheduler).__name__,
        "runtime_binding_status": "BOOTSTRAP_ONLY",
        "cycle_status": "unavailable_no_real_signal_or_market_data",
    }

    evidence = QualificationEvidence(
        evidence_id=f"evidence:{version.strategy_version_id}:initial",
        evidence_type="SHADOW_FORWARD_METRICS",
        subject_id=version.strategy_version_id,
        artifact_ids=("shadow_metrics.json", "shadow_decisions.csv"),
        artifact_hashes=(),
        quality_status="UNAVAILABLE",
        generated_at=started_at,
        metrics={},
    )
    assessment = assess_decision_eligibility(
        strategy_version_id=version.strategy_version_id,
        policy=policy,
        evidence=(evidence,),
        forward_observation_weeks=0,
        completed_rebalance_cycles=0,
        regime_coverage_sufficient=False,
        approval=None,
        evaluated_at=started_at,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        output_dir / "frozen_strategy_manifest.json",
        _version_payload(version, config=config, inputs=inputs),
    )
    _write_json(output_dir / "qualification_policy.json", _policy_payload(policy))
    _write_json(output_dir / "qualification_assessments.json", _assessment_payload(assessment))
    _write_json(output_dir / "shadow_account_state.json", _initial_account_payload(version.strategy_version_id, started_at))
    _write_json(
        output_dir / "shadow_metrics.json",
        {
            "schema_version": "shadow-metrics/v1",
            "status": "unavailable",
            "reason_code": "NO_REAL_SHADOW_INPUTS",
            "forward_observation_weeks": "unavailable",
            "completed_rebalance_cycles": "unavailable",
            "regime_exposure_coverage": "unavailable",
        },
    )
    _write_json(
        output_dir / "shadow_drift_report.json",
        {"schema_version": "shadow-drift/v1", "status": "unavailable", "reason_code": "NO_REAL_SHADOW_INPUTS"},
    )
    _write_json(
        output_dir / "shadow_incidents.json",
        {"schema_version": "shadow-incidents/v1", "status": "unavailable", "incidents": []},
    )
    for name, fields in {
        "shadow_decisions.csv": ("shadow_decision_id", "strategy_version_id", "status", "reason_code"),
        "shadow_targets.csv": ("shadow_decision_id", "symbol", "target_weight"),
        "shadow_orders.csv": ("shadow_order_id", "shadow_decision_id", "symbol", "target_weight"),
        "shadow_attempts.csv": ("attempt_id", "shadow_decision_id", "symbol"),
        "shadow_trades.csv": ("fill_id", "attempt_id", "symbol", "quantity", "price"),
        "shadow_positions.csv": ("as_of_time", "symbol", "quantity"),
        "shadow_equity.csv": ("as_of_time", "shadow_ideal_nav", "shadow_executable_nav"),
    }.items():
        _write_csv(output_dir / name, fields)

    _write_json(
        output_dir / "qualification_evidence.json",
        {
            "schema_version": "qualification-evidence/v1",
            "status": "unavailable",
            "reason_code": QUALIFICATION_STATUS,
            "reason": "真实 forward 服务、U1 identity 和首个可用行情周期尚未提供",
            "evidence": {
                **evidence.__dict__,
                "artifact_hashes": (
                    _sha256_file(output_dir / "shadow_metrics.json"),
                    _sha256_file(output_dir / "shadow_decisions.csv"),
                ),
            },
            "metrics": {
                "forward_observation_weeks": "unavailable",
                "completed_rebalance_cycles": "unavailable",
                "regime_exposure_coverage": "unavailable",
            },
        },
    )

    hashes = {
        name: _sha256_file(output_dir / name)
        for name in EXPECTED_ARTIFACT_NAMES
        if name != "shadow_manifest.json"
    }
    shadow_manifest = {
        "schema_version": "shadow-run-manifest/v1",
        "status": SHADOW_STATUS,
        "deployment_status": deployment.status.value,
        "strategy_id": R40_STRATEGY_ID,
        "strategy_version_id": version.strategy_version_id,
        "r40_source_sha256": source_hash,
        "ceiling": R40_CEILING,
        "started_at": _iso(started_at),
        "qualification_status": QUALIFICATION_STATUS,
        "promotion_allowed": False,
        "qualified": False,
        "due_versions_at_start": due_versions,
        "inputs": inputs,
        "shadow_service_binding": service_binding,
        "artifact_contracts": default_artifact_contracts(),
        "artifact_hashes": hashes,
        "evidence_boundary": "Shadow started is not Shadow qualified; 26 weeks and 6 completed rebalance cycles are the hard gates; 104 weeks is a recommended observation length.",
    }
    _write_json(output_dir / "shadow_manifest.json", shadow_manifest)
    manifest_hash = _sha256_file(output_dir / "shadow_manifest.json")

    report = (
        "# Shadow A：冻结 R40 的前瞻影子验证\n\n"
        f"- 策略 ID：`{R40_STRATEGY_ID}`\n"
        f"- 冻结 ceiling：`{R40_CEILING}`\n"
        f"- strategy version：`{version.strategy_version_id}`\n"
        f"- R40 source SHA-256：`{source_hash}`\n"
        f"- R39 control source SHA-256：`{r39_source_hash}`\n"
        f"- Shadow 状态：`{SHADOW_STATUS}`\n"
        f"- 资格状态：`{QUALIFICATION_STATUS}`\n"
        f"- shadow_manifest SHA-256：`{manifest_hash}`\n\n"
        "## 启动与资格\n\n"
        "本次仅完成冻结版本与 Shadow deployment 启动边界。启动不等于资格通过；"
        "资格策略固定要求至少 26 周真实前瞻观察、至少 6 个已完成 rebalance cycle、完整证据和人工批准；104 周仅为建议观察长度。"
        f"当前真实 forward 服务、U1 identity 和行情输入不可用，因此资格状态明确为 `{QUALIFICATION_STATUS}`，"
        "`promotion_allowed=false`，没有声称 qualified。\n\n"
        "## 决策与执行边界\n\n"
        "本次只完成 `ShadowDecisionService`、`ShadowExecutionService` 和 `ShadowRunScheduler` 的"
        "bootstrap binding；没有 signal/行情，因此没有调用 decision seal 或 execution，"
        "也没有声称已完成一个 runtime cycle。正式输入到位后，决策与执行将沿用既有的不同幂等边界。"
        f"本次 runtime binding：`{service_binding}`。"
        "本次没有可用 signal 或执行行情，故没有伪造 Shadow decision、order、attempt、trade 或收益数据；"
        "连续账户的初始状态、ideal/executable NAV 字段和 append-only 空事件文件已写入。"
        "独立 `shadow_integrity.json` sidecar 保存 manifest、报告和全部预期 artifact 的 hash，"
        "用于复跑时检测完整性。\n\n"
        "## 输入与限制\n\n"
        f"- U1 identity：`{inputs['frozen_u1_manifest']['status']}`\n"
        f"- R39 control identity：`{inputs['r39_control_manifest']['status']}`\n"
        "- 26 周前瞻证据：`unavailable`（104 周为建议观察长度）\n"
        "- 事件收益、尾部损失、worst week、MDD、换手和执行延迟：`unavailable`\n\n"
        "## 最小改动自评\n\n"
        "仅新增 Shadow A 启动入口、focused tests 和中文报告；复用既有 FrozenStrategyVersion、"
        "ShadowDecisionService、ShadowExecutionService、ShadowRunScheduler 及 shadow artifact contracts。"
        "未修改 R39/R40 策略逻辑、公共 execution ledger、平台架构或历史记录，50% ceiling 未调参。\n"
    )
    _write_immutable(report_path, report)
    artifact_hashes = dict(hashes)
    artifact_hashes["shadow_manifest.json"] = manifest_hash
    artifact_hashes[report_path.name] = _sha256_file(report_path)
    integrity = {
        "schema_version": "shadow-integrity/v1",
        "manifest_sha256": manifest_hash,
        "artifact_hashes": artifact_hashes,
    }
    _write_json(output_dir / INTEGRITY_ARTIFACT_NAME, integrity)
    artifact_hashes[INTEGRITY_ARTIFACT_NAME] = _sha256_file(
        output_dir / INTEGRITY_ARTIFACT_NAME
    )
    return {
        "strategy_id": R40_STRATEGY_ID,
        "ceiling": R40_CEILING,
        "shadow_status": SHADOW_STATUS,
        "qualification_status": QUALIFICATION_STATUS,
        "promotion_allowed": False,
        "qualified": False,
        "strategy_version_id": version.strategy_version_id,
        "artifact_hashes": artifact_hashes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="冻结 R40 并启动 Shadow A")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--frozen-u1-manifest", type=Path)
    parser.add_argument("--r39-control-manifest", type=Path)
    args = parser.parse_args()
    start_shadow_a(
        output_dir=args.output_dir,
        frozen_u1_manifest=args.frozen_u1_manifest,
        r39_control_manifest=args.r39_control_manifest,
        report_path=args.report_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
