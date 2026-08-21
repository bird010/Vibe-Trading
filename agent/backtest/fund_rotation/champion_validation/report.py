"""结构化验证工件与中文决策摘要。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from .contracts import (
    SCHEMA_VERSION,
    StageStatus,
    ValidationContract,
    FrozenIdentity,
    _canonicalize,
    _reject_selection_fields,
    canonical_hash,
    freeze_identity,
)


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _default_identity(contract: ValidationContract) -> FrozenIdentity:
    return freeze_identity(
        data_snapshot={"research_interval": contract.research_interval.to_dict()},
        framework={"schema_version": contract.schema_version},
        strategy={"id": contract.subject_strategy, "status": contract.subject_status},
        execution={"contract": "frozen_execution_identity"},
        spec=contract.frozen_spec(),
    )


def build_structured_artifact(
    contract: ValidationContract,
    *,
    status: StageStatus | str,
    reason_codes: Sequence[str] = (),
    payload: Mapping[str, Any] | None = None,
    timestamp: str | None = None,
    identity: FrozenIdentity | Mapping[str, str] | None = None,
    input_checksum: str | None = None,
    data_hash: str | None = None,
    framework_hash: str | None = None,
    strategy_hash: str | None = None,
    execution_hash: str | None = None,
    spec_hash: str | None = None,
) -> dict[str, Any]:
    """Build one auditable artifact and reject any implicit selection output."""
    normalized_status = StageStatus(status).value
    normalized_payload = {} if payload is None else dict(payload)
    _reject_selection_fields(normalized_payload)
    frozen_identity = _default_identity(contract) if identity is None else identity
    identity_map = dict(frozen_identity)
    artifact = {
        "schema_version": contract.schema_version or SCHEMA_VERSION,
        "experiment_id": contract.experiment_id,
        "timestamp": timestamp or _timestamp(),
        "input_checksum": input_checksum or identity_map["input_checksum"],
        "data_hash": data_hash or identity_map["data_hash"],
        "framework_hash": framework_hash or identity_map["framework_hash"],
        "strategy_hash": strategy_hash or identity_map["strategy_hash"],
        "execution_hash": execution_hash or identity_map["execution_hash"],
        "spec_hash": spec_hash or identity_map["spec_hash"],
        "status": normalized_status,
        "reason_codes": list(reason_codes),
        "payload": _canonicalize(normalized_payload),
    }
    # Validate the complete object, including payload values, before returning it.
    canonical_hash(artifact)
    return artifact


def validate_structured_artifact(artifact: Mapping[str, Any]) -> None:
    required = {
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
    }
    missing = required - set(artifact)
    if missing:
        raise ValueError(f"structured artifact missing fields: {sorted(missing)}")
    StageStatus(artifact["status"])
    _reject_selection_fields(artifact)
    canonical_hash(artifact)


def render_chinese_report(
    decision: Any,
    stage_results: Mapping[str, Any],
) -> str:
    """Render a deliberately non-promotional, Chinese decision summary."""
    action = getattr(getattr(decision, "action", decision), "value", getattr(decision, "action", decision))
    lines = [
        "# R11 Champion 可信度验证报告",
        "",
        "本报告仅描述验证软件产生的可审计状态，不代表部署资格。",
        "",
        f"最终动作：`{action}`",
        "",
        "## 阶段状态",
        "",
    ]
    for stage, result in stage_results.items():
        status = getattr(getattr(result, "status", result), "value", getattr(result, "status", result))
        lines.append(f"- {stage}：`{status}`")
    reasons = getattr(decision, "reason_codes", ())
    if reasons:
        lines.extend(["", "## 原因代码", ""])
        lines.extend(f"- `{reason}`" for reason in reasons)
    return "\n".join(lines) + "\n"
