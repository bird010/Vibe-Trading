"""可恢复的 R11 Champion Validation 阶段编排。"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping

from .contracts import (
    StageResult,
    StageStatus,
    ValidationContract,
    append_ledger_entry,
    build_structured_artifact,
    freeze_identity,
    _canonicalize,
    canonical_hash,
)
from .decision import FinalAction, ValidationDecision, ValidationState, evaluate_final_decision
from .report import render_chinese_report, validate_structured_artifact

EXPECTED_STAGE_ORDER = (
    "preflight", "universe", "benchmarks", "ablation", "stability",
    "stress", "attribution", "statistics", "final",
)
_GATE_ALIASES = {
    "benchmarks": "economic",
    "ablation": "mechanism",
    "stability": "robustness",
    "stress": "robustness",
    "attribution": "robustness",
}
_IDENTITY_FIELDS = (
    "input_checksum",
    "data_hash",
    "framework_hash",
    "strategy_hash",
    "execution_hash",
    "spec_hash",
)
_ARTIFACT_CONTEXT_FIELDS = ("schema_version", "experiment_id", "stage")


class IdentityDriftError(RuntimeError):
    pass


class ArtifactIntegrityError(IdentityDriftError):
    pass


class LedgerIntegrityError(RuntimeError):
    pass


class IdempotencyKeyError(RuntimeError):
    pass


@dataclass(frozen=True)
class ValidationRunResult:
    stage_order: tuple[str, ...]
    stage_results: Mapping[str, StageResult]
    decision: ValidationDecision


def _stage_result(stage: str, value: Mapping[str, Any], contract: ValidationContract, identity: Mapping[str, str]) -> StageResult:
    status = StageStatus(value.get("status", "INCONCLUSIVE"))
    return StageResult(
        stage=stage,
        status=status,
        reason_codes=tuple(value.get("reason_codes", ())),
        metrics=dict(value.get("payload", value.get("metrics", {}))),
        experiment_id=contract.experiment_id,
        input_checksum=identity["input_checksum"], data_hash=identity["data_hash"],
        framework_hash=identity["framework_hash"], strategy_hash=identity["strategy_hash"],
        execution_hash=identity["execution_hash"], spec_hash=identity["spec_hash"],
    )


def _default_identity(contract: ValidationContract):
    return freeze_identity(
        data_snapshot={"research_interval": contract.research_interval.to_dict()},
        framework={"schema_version": contract.schema_version},
        strategy={"id": contract.subject_strategy, "status": contract.subject_status},
        execution={"contract": "frozen_execution_identity"},
        spec=contract.frozen_spec(),
    )


class ChampionValidationController:
    def __init__(
        self,
        experiment_dir: str | Path,
        *,
        contract: ValidationContract | None = None,
        identity: Mapping[str, str] | None = None,
        stage_handlers: Mapping[str, Callable[[Mapping[str, Any]], Mapping[str, Any]]] | None = None,
    ) -> None:
        self.experiment_dir = Path(experiment_dir)
        self.contract = contract or ValidationContract()
        self._has_real_identity = identity is not None
        self.identity = identity if identity is not None else _default_identity(self.contract)
        self._identity_map = dict(self.identity)
        missing = set(_IDENTITY_FIELDS + ("identity_hash",)) - set(self._identity_map)
        if missing:
            raise ValueError(f"identity missing fields: {sorted(missing)}")
        self.stage_handlers = dict(stage_handlers or {})

    def _identity_path(self) -> Path:
        return self.experiment_dir / "frozen_subject.json"

    def _spec_path(self) -> Path:
        return self.experiment_dir / "validation_spec.json"

    def _ledger_path(self) -> Path:
        return self.experiment_dir / "validation_ledger.jsonl"

    def _idempotency_path(self) -> Path:
        return self.experiment_dir / "idempotency.json"

    def _has_existing_state(self) -> bool:
        return self.experiment_dir.exists() and any(self.experiment_dir.iterdir())

    def _check_identity(self) -> None:
        if not self.experiment_dir.exists():
            return
        identity_path = self._identity_path()
        if not identity_path.exists():
            if self._has_existing_state():
                raise IdentityDriftError("BLOCKED_IDENTITY_DRIFT: frozen identity is missing")
            return
        try:
            stored = json.loads(identity_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError, TypeError) as exc:
            raise IdentityDriftError("BLOCKED_IDENTITY_DRIFT: frozen identity is unreadable") from exc
        if not isinstance(stored, Mapping):
            raise IdentityDriftError("BLOCKED_IDENTITY_DRIFT: frozen identity is not an object")
        mismatches = [
            field for field in _IDENTITY_FIELDS + ("identity_hash",)
            if stored.get(field) != self._identity_map[field]
        ]
        if mismatches:
            raise IdentityDriftError(
                "BLOCKED_IDENTITY_DRIFT: frozen identity mismatch in " + ", ".join(mismatches)
            )

    def _check_spec(self) -> None:
        spec_path = self._spec_path()
        if not spec_path.exists():
            if self._identity_path().exists():
                raise IdentityDriftError("BLOCKED_IDENTITY_DRIFT: validation spec is missing")
            return
        try:
            stored = json.loads(spec_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError, TypeError) as exc:
            raise IdentityDriftError("BLOCKED_IDENTITY_DRIFT: validation spec is unreadable") from exc
        if _canonicalize(stored) != _canonicalize(self.contract.frozen_spec()):
            raise IdentityDriftError("BLOCKED_IDENTITY_DRIFT: validation spec mismatch")

    def _write_json(self, path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_canonicalize(value), ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    def _validate_artifact_context(self, value: Mapping[str, Any], stage: str) -> None:
        present = set(value).intersection(_ARTIFACT_CONTEXT_FIELDS + _IDENTITY_FIELDS)
        required = set(_ARTIFACT_CONTEXT_FIELDS + _IDENTITY_FIELDS)
        if present and present != required:
            raise ArtifactIntegrityError(
                "BLOCKED_ARTIFACT_METADATA_MISSING: "
                + ", ".join(sorted(required - present))
            )
        expected = {
            "schema_version": self.contract.schema_version,
            "experiment_id": self.contract.experiment_id,
            "stage": stage,
            **{field: self._identity_map[field] for field in _IDENTITY_FIELDS},
        }
        mismatches = [field for field, expected_value in expected.items() if value.get(field) != expected_value]
        checksum_mismatches = [field for field in mismatches if field in _IDENTITY_FIELDS]
        if checksum_mismatches:
            raise ArtifactIntegrityError(
                "BLOCKED_ARTIFACT_CHECKSUM_MISMATCH: " + ", ".join(checksum_mismatches)
            )
        if mismatches:
            raise ArtifactIntegrityError(
                "BLOCKED_ARTIFACT_FIELD_MISMATCH: " + ", ".join(mismatches)
            )
        payload = value.get("payload")
        if isinstance(payload, Mapping) and "stage" in payload and payload["stage"] != stage:
            raise ArtifactIntegrityError("BLOCKED_ARTIFACT_FIELD_MISMATCH: payload.stage")

    def _load_complete(self, path: Path, stage: str) -> StageResult | None:
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError, TypeError):
            return None
        if not isinstance(value, Mapping):
            return None
        self._validate_artifact_context(value, stage)
        try:
            validate_structured_artifact(value)
        except (ValueError, TypeError, json.JSONDecodeError):
            return None
        payload = value.get("payload")
        if value.get("partial") or (isinstance(payload, Mapping) and payload.get("partial")):
            return None
        return _stage_result(stage, value, self.contract, self._identity_map)

    def _validate_ledger_chain(self) -> list[dict[str, Any]]:
        path = self._ledger_path()
        if not path.exists():
            return []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise LedgerIntegrityError("BLOCKED_LEDGER_CHAIN: ledger is unreadable") from exc
        records: list[dict[str, Any]] = []
        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except (ValueError, json.JSONDecodeError) as exc:
                raise LedgerIntegrityError(
                    f"BLOCKED_LEDGER_CHAIN: invalid JSON at line {line_number}"
                ) from exc
            if not isinstance(record, dict):
                raise LedgerIntegrityError(f"BLOCKED_LEDGER_CHAIN: line {line_number} is not an object")
            expected_sequence = len(records) + 1
            if record.get("sequence") != expected_sequence:
                raise LedgerIntegrityError(f"BLOCKED_LEDGER_CHAIN: sequence mismatch at line {line_number}")
            previous_hash = records[-1].get("entry_hash", "") if records else ""
            if record.get("previous_entry_hash") != previous_hash:
                raise LedgerIntegrityError(f"BLOCKED_LEDGER_CHAIN: previous hash mismatch at line {line_number}")
            claimed_hash = record.get("entry_hash")
            unsigned = dict(record)
            unsigned.pop("entry_hash", None)
            if not isinstance(claimed_hash, str) or canonical_hash(unsigned) != claimed_hash:
                raise LedgerIntegrityError(f"BLOCKED_LEDGER_CHAIN: entry hash mismatch at line {line_number}")
            stage = record.get("stage")
            if not isinstance(stage, str):
                raise LedgerIntegrityError(f"BLOCKED_LEDGER_CHAIN: missing stage at line {line_number}")
            try:
                self._validate_artifact_context(record, stage)
            except ArtifactIntegrityError as exc:
                raise LedgerIntegrityError(f"BLOCKED_LEDGER_CHAIN: {exc}") from exc
            records.append(record)
        return records

    def _load_idempotency_key(self, idempotency_key: str) -> bool:
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise IdempotencyKeyError("IDEMPOTENCY_KEY_REQUIRED")
        path = self._idempotency_path()
        if not path.exists():
            if self._has_existing_state():
                raise IdempotencyKeyError("IDEMPOTENCY_KEY_MISSING")
            return False
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError, TypeError) as exc:
            raise IdempotencyKeyError("IDEMPOTENCY_KEY_INVALID") from exc
        if not isinstance(stored, Mapping) or stored.get("idempotency_key") != idempotency_key:
            raise IdempotencyKeyError("IDEMPOTENCY_KEY_CONFLICT")
        return True

    def _final_status(self, results: Mapping[str, StageResult]) -> str:
        statuses = tuple(result.status for result in results.values())
        if StageStatus.FAIL in statuses:
            return StageStatus.FAIL.value
        if StageStatus.INCONCLUSIVE in statuses:
            return StageStatus.INCONCLUSIVE.value
        return StageStatus.PASS.value

    def _final_reasons(self, decision: ValidationDecision, results: Mapping[str, StageResult]) -> tuple[str, ...]:
        reasons = list(decision.reason_codes)
        for result in results.values():
            reasons.extend(result.reason_codes)
        return tuple(dict.fromkeys(reasons))

    def _guard_default_identity(self, decision: ValidationDecision) -> ValidationDecision:
        if self._has_real_identity or decision.action is not FinalAction.P2_RESEARCH_AUTHORIZED:
            return decision
        return replace(
            decision,
            action=FinalAction.FORWARD_SHADOW_ONLY,
            state=ValidationState.STATISTICAL_INCONCLUSIVE,
            reason_codes=tuple(dict.fromkeys((*decision.reason_codes, "REAL_IDENTITY_REQUIRED"))),
        )

    def run(self, *, resume: bool = False, idempotency_key: str = "") -> ValidationRunResult:
        # These checks intentionally happen before mkdir or any output write.
        self._check_identity()
        self._check_spec()
        ledger_records = self._validate_ledger_chain()
        same_key = self._load_idempotency_key(idempotency_key)

        self.experiment_dir.mkdir(parents=True, exist_ok=True)
        if not self._identity_path().exists():
            self._write_json(self._identity_path(), self._identity_map)
        if not self._spec_path().exists():
            self._write_json(self._spec_path(), self.contract.frozen_spec())
        if not self._idempotency_path().exists():
            self._write_json(self._idempotency_path(), {"idempotency_key": idempotency_key})

        effective_resume = resume or same_key
        results: dict[str, StageResult] = {}
        repair_chain = False
        ledger_stages = {record.get("stage") for record in ledger_records}

        def append_once(stage: str, artifact: Mapping[str, Any]) -> None:
            if stage in ledger_stages:
                return
            append_ledger_entry(self._ledger_path(), artifact)
            ledger_stages.add(stage)

        for index, stage in enumerate(EXPECTED_STAGE_ORDER[:-1]):
            canonical_gate = _GATE_ALIASES.get(stage, stage)
            if any(
                r.status is StageStatus.FAIL
                and _GATE_ALIASES.get(name, name) == canonical_gate
                for name, r in results.items()
            ):
                break
            stage_dir = self.experiment_dir / "stages" / f"{index:02d}_{stage}"
            result_path = stage_dir / "result.json"
            prior = None if repair_chain else (
                self._load_complete(result_path, stage) if effective_resume else None
            )
            if effective_resume and result_path.exists() and prior is None:
                repair_chain = True
            if prior is not None:
                results[stage] = prior
                if prior.status is StageStatus.FAIL:
                    break
                continue
            handler = self.stage_handlers.get(stage)
            value = (
                handler(
                    {
                        "stage": stage,
                        "contract": self.contract,
                        "identity": self.identity,
                        "idempotency_key": idempotency_key,
                    }
                )
                if handler
                else {"status": "INCONCLUSIVE", "reason_codes": ["NO_STAGE_HANDLER"], "payload": {}}
            )
            artifact = build_structured_artifact(
                self.contract,
                status=value.get("status", "INCONCLUSIVE"),
                reason_codes=value.get("reason_codes", ()),
                payload={"stage": stage, **dict(value.get("payload", {}))},
                identity=self.identity,
            )
            artifact["stage"] = stage
            artifact["identity_hash"] = self._identity_map["identity_hash"]
            self._write_json(result_path, artifact)
            result = _stage_result(stage, artifact, self.contract, self._identity_map)
            results[stage] = result
            append_once(stage, artifact)
            if result.status is StageStatus.FAIL:
                break

        decision = self._guard_default_identity(
            evaluate_final_decision(results)
        )
        final_path = self.experiment_dir / "stages" / "08_final" / "result.json"
        reasons = self._final_reasons(decision, results)
        final_artifact = build_structured_artifact(
            self.contract,
            status=self._final_status(results),
            reason_codes=reasons,
            payload={
                "stage": "final",
                "action": decision.action.value,
                "decision_state": decision.state.value,
                "reason_codes": list(reasons),
            },
            identity=self.identity,
        )
        final_artifact["stage"] = "final"
        final_artifact["identity_hash"] = self._identity_map["identity_hash"]
        final = self._load_complete(final_path, "final") if effective_resume else None
        if final is None:
            self._write_json(final_path, final_artifact)
            append_once("final", final_artifact)
        else:
            stored_final = json.loads(final_path.read_text(encoding="utf-8"))
            if (
                stored_final.get("status") != final_artifact["status"]
                or stored_final.get("reason_codes") != final_artifact["reason_codes"]
                or stored_final.get("payload", {}).get("action") != decision.action.value
            ):
                raise ArtifactIntegrityError("BLOCKED_ARTIFACT_FIELD_MISMATCH: final decision")

        self._write_json(self.experiment_dir / "decision.json", decision.to_dict())
        (self.experiment_dir / "report.md").write_text(
            render_chinese_report(decision, results), encoding="utf-8"
        )
        return ValidationRunResult(EXPECTED_STAGE_ORDER, results, decision)
