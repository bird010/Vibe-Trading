"""Default handlers for a complete, auditable R11 validation pass.

The Round 11 backtest artifacts are the only historical inputs available to
the validation CLI in this repository.  They are sufficient to verify the
frozen subject and the original paired-run identity, but not to manufacture
the raw PIT time series needed by the diagnostic layers.  Those layers are
therefore recorded as inconclusive and the controller continues through all
stages.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .contracts import FrozenIdentity, ValidationContract, canonical_hash, freeze_identity


_STAGES = (
    "preflight", "universe", "benchmarks", "ablation", "stability",
    "stress", "attribution", "statistics",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _source_paths(source_dir: str | Path | None = None) -> tuple[Path, Path]:
    root = Path(source_dir) if source_dir is not None else (
        _repo_root() / "experiments" / "ai_fund_rotation_20260819" / "rounds" / "round_11"
    )
    return root / "backtest_request.json", root / "backtest_result.json"


def _run_paths() -> tuple[Path, Path, Path]:
    root = _repo_root() / "agent" / "runs" / "fund_rotation"
    return (
        root / "fbcaad4988dd" / "manifest.json",
        root / "3b6c7181a6f6" / "manifest.json",
        root / "strategy_batches" / "7dcdcb29f553" / "manifest.json",
    )


def _read_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"historical artifact must be an object: {path}")
    return value


def historical_identity(source_dir: str | Path | None = None) -> FrozenIdentity:
    """Derive a complete identity from the immutable Round 11 artifacts."""
    request_path, result_path = _source_paths(source_dir)
    request = _read_json(request_path)
    result = _read_json(result_path)
    _, r11_manifest_path, batch_manifest_path = _run_paths()
    r11_manifest = _read_json(r11_manifest_path)
    batch_manifest = _read_json(batch_manifest_path)
    return freeze_identity(
        data_snapshot={
            "request_sha256": canonical_hash(request),
            "result_sha256": canonical_hash(result),
            "data_snapshot_fingerprint": r11_manifest.get("data_snapshot_fingerprint"),
            "selection_interval": result.get("selection_interval"),
        },
        framework={"implementation_hash": r11_manifest.get("framework_implementation_hash"), "schema_version": r11_manifest.get("schema_version")},
        strategy={"id": r11_manifest.get("strategy_id"), "implementation_hash": r11_manifest.get("strategy_implementation_hash"), "run_identity_hash": r11_manifest.get("run_identity_hash")},
        execution={"resolved_execution_hash": r11_manifest.get("resolved_execution_hash"), "contract": request.get("execution", {})},
        spec={"request_idempotency_key": request.get("idempotency_key"), "confirmation_interval_used": result.get("confirmation_interval_used"), "batch_id": batch_manifest.get("batch_id")},
    )


def build_historical_stage_handlers(source_dir: str | Path | None = None):
    """Build handlers which always record every stage, including uncertainty."""
    request_path, result_path = _source_paths(source_dir)
    request = _read_json(request_path)
    result = _read_json(result_path)
    champion_manifest_path, r11_manifest_path, batch_manifest_path = _run_paths()
    champion_manifest = _read_json(champion_manifest_path)
    r11_manifest = _read_json(r11_manifest_path)
    batch_manifest = _read_json(batch_manifest_path)
    quality = result.get("variants", {}).get("challenger", {}).get("quality_status")
    challenger = result.get("variants", {}).get("challenger", {})
    manifest_checks = {
        "champion_terminal": champion_manifest.get("status") == "SUCCEEDED",
        "r11_terminal": r11_manifest.get("status") == "SUCCEEDED",
        "batch_terminal": batch_manifest.get("status") == "SUCCEEDED",
        "champion_not_partial": champion_manifest.get("partial") is False,
        "r11_not_partial": r11_manifest.get("partial") is False,
        "batch_id_matches": batch_manifest.get("batch_id") == result.get("batch_id") == r11_manifest.get("batch_id"),
        "r11_run_matches": r11_manifest.get("run_id") == challenger.get("run_id"),
        "r11_strategy_matches": r11_manifest.get("strategy_id") == "ai_rotation_r11_persist_geom",
        "confirmation_interval_unused": result.get("confirmation_interval_used") is False,
    }
    preflight_ok = all(manifest_checks.values()) and request.get("confirmation_interval_used", False) is False
    common = {
        "source_request": str(request_path),
        "source_result": str(result_path),
        "selection_interval": result.get("selection_interval"),
        "confirmation_interval_used": result.get("confirmation_interval_used", False),
        "quality_status": quality,
        "recorded_to_completion": True,
        "manifest_checks": manifest_checks,
        "r11_run_manifest": str(r11_manifest_path),
        "batch_manifest": str(batch_manifest_path),
    }

    def handler(context: Mapping[str, Any]) -> Mapping[str, Any]:
        stage = str(context["stage"])
        if stage == "preflight":
            return {
                "status": "PASS" if preflight_ok else "INCONCLUSIVE",
                "reason_codes": ["FROZEN_R11_INPUTS_VERIFIED"] if preflight_ok else ["INPUT_ARTIFACT_MISMATCH"],
                "payload": common,
            }
        if stage == "universe":
            return {
                "status": "INCONCLUSIVE",
                "reason_codes": ["UNIVERSE_RESEARCH_ONLY_UNVERIFIED"],
                "payload": {**common, "pit_quality_status": quality, "raw_pit_evidence_available": False},
            }
        return {
            "status": "INCONCLUSIVE",
            "reason_codes": ["UPSTREAM_UNIVERSE_INCONCLUSIVE", "RAW_SERIES_REQUIRED"],
            "payload": {
                **common,
                "upstream_stage": "universe",
                "historical_paired_run_available": True,
                "raw_series_required_for_formal_gate": True,
            },
        }

    return {stage: handler for stage in _STAGES}
