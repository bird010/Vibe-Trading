"""Lifecycle, evidence publication and recovery for batch child runs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pandas as pd

from backtest.fund_rotation.contracts import StrategyArtifact
from backtest.fund_rotation.evaluation import EvaluationContext
from src.stockpred.fund_rotation.artifact_publisher import (
    ArtifactPublisher,
    read_valid_manifest,
)
from src.stockpred.fund_rotation.artifacts import compute_file_checksum
from src.stockpred.fund_rotation.batch_models import StrategyBatchRequest
from src.stockpred.fund_rotation.persistence import BatchEventLog, atomic_write_json
from src.stockpred.fund_rotation.state_machine import (
    CHILD_TERMINAL_STAGES,
    ChildStage,
    ChildStateMachine,
    InvalidTransitionError,
    detect_interrupted_state,
    mark_state_interrupted,
)
from src.stockpred.fund_rotation.strategy_snapshot import (
    compute_run_identity_hash,
    record_runtime_versions,
)


class BatchChildRuntime:
    def __init__(self, runs_root: Path, catalog, framework_hash: str) -> None:
        self.runs_root = Path(runs_root)
        self.catalog = catalog
        self.framework_hash = framework_hash
        self.runtime_versions = record_runtime_versions()

    def record_stage(
        self,
        *,
        batch_id: str,
        request: StrategyBatchRequest,
        identity,
        run_id: str,
        snapshot,
        stage: str,
        message: str | None = None,
        error: str | None = None,
        quality_status: str | None = None,
    ) -> None:
        target = ChildStage(stage)
        run_dir = self.runs_root / run_id
        state_path = run_dir / "state.json"
        if state_path.exists():
            current = json.loads(state_path.read_text(encoding="utf-8"))
            ChildStateMachine(initial=ChildStage(current["stage"])).transition(target)
        elif target is not ChildStage.QUEUED:
            raise InvalidTransitionError(
                f"First child stage must be QUEUED, got {target.value}"
            )
        state = {
            "schema_version": "2",
            "stage": target.value,
            "batch_id": batch_id,
            "run_id": run_id,
            "variant_key": identity.variant_key,
            "strategy_id": identity.strategy_id,
            "mode": request.mode,
            "params_fingerprint": identity.resolved_config_hash,
            "data_snapshot_fingerprint": snapshot.fingerprint,
            "quality_status": quality_status,
        }
        if message is not None:
            state["message"] = message
        if error is not None:
            state["error"] = error
        atomic_write_json(state_path, state)
        self._append_event(
            run_dir,
            state,
            stage=target,
            message=message,
            error=error,
        )

    def publish_result(
        self,
        *,
        batch_id: str,
        request: StrategyBatchRequest,
        plan: dict[str, Any],
        snapshot,
        evaluation: EvaluationContext,
        result,
        terminal_stage: str = ChildStage.SUCCEEDED.value,
        execution_config=None,
    ) -> None:
        terminal = ChildStage(terminal_stage)
        if terminal not in {
            ChildStage.SUCCEEDED,
            ChildStage.FAILED,
            ChildStage.CANCELED,
        }:
            raise ValueError(f"unsupported child terminal stage: {terminal.value}")

        identity = plan["identity"]
        run_id = plan["run_id"]
        run_dir = self.runs_root / run_id
        publisher = ArtifactPublisher(run_dir)
        registered = self.catalog.require(identity.strategy_id)
        resolved_execution = (
            execution_config.model_dump(mode="json")
            if execution_config is not None
            else request.execution.model_dump(mode="json")
        )
        resolved_execution_hash = _canonical_hash(resolved_execution)
        research_contract = {
            "schema_version": request.schema_version,
            "mode": request.mode,
            "data_start": plan["data_start"],
            "decision_start_date": plan["decision_start_date"],
            "anchor_decision_date": plan["anchor_decision_date"],
            "evaluation_start_date": request.evaluation_start_date,
            "evaluation_end_date": request.evaluation_end_date,
            "resolved_requirements_hash": identity.resolved_requirements_hash,
        }
        run_identity_hash = compute_run_identity_hash(
            identity.implementation_hash,
            self.framework_hash,
            identity.resolved_config_hash,
            snapshot.fingerprint,
            research_contract,
            resolved_execution,
        )
        partial = terminal is not ChildStage.SUCCEEDED

        for artifact in (
            StrategyArtifact(
                role="resolved_spec",
                media_type="application/json",
                payload={
                    **identity.__dict__,
                    "batch_id": batch_id,
                    "run_id": run_id,
                    "schema_version": request.schema_version,
                    "mode": request.mode,
                    **research_contract,
                    "execution": resolved_execution,
                    "resolved_execution_hash": resolved_execution_hash,
                    "framework_implementation_hash": self.framework_hash,
                    "strategy_implementation_hash": identity.implementation_hash,
                    "data_snapshot_fingerprint": snapshot.fingerprint,
                    "run_identity_hash": run_identity_hash,
                    "runtime_versions": dict(self.runtime_versions),
                    "terminal_status": terminal.value,
                    "partial": partial,
                    "publishable_for_comparison": not partial,
                },
            ),
            StrategyArtifact(
                role="strategy_snapshot",
                media_type="application/json",
                payload={
                    "strategy_id": identity.strategy_id,
                    "implementation_hash": identity.implementation_hash,
                    "source_files": list(
                        registered.implementation_snapshot.source_files
                    ),
                    "file_hashes": dict(
                        registered.implementation_snapshot.file_hashes
                    ),
                    "descriptor": asdict(registered.descriptor),
                },
            ),
            StrategyArtifact(
                role="data_snapshot",
                media_type="application/json",
                payload=asdict(snapshot),
            ),
            StrategyArtifact(
                role="evaluation_calendar",
                media_type="application/json",
                payload=[
                    date.strftime("%Y%m%d")
                    for date in evaluation.trading_dates
                ],
            ),
        ):
            publisher.publish(artifact)

        decisions = pd.DataFrame(
            [
                {
                    "decision_id": decision.decision_id,
                    "signal_date": decision.signal_date,
                    "action": decision.action.value,
                    "target_weights": json.dumps(
                        dict(decision.target_weights),
                        sort_keys=True,
                        ensure_ascii=False,
                    ),
                    "cash_weight": decision.cash_weight,
                    "reason_code": decision.reason_code,
                    "quality_status": decision.quality_status.value,
                    "diagnostics": json.dumps(
                        dict(decision.diagnostics),
                        sort_keys=True,
                        ensure_ascii=False,
                        default=str,
                    ),
                }
                for decision in result.decisions
            ],
            columns=[
                "decision_id",
                "signal_date",
                "action",
                "target_weights",
                "cash_weight",
                "reason_code",
                "quality_status",
                "diagnostics",
            ],
        )
        targets = pd.DataFrame(
            [
                {
                    "week_ending": date,
                    "ts_code": code,
                    "weight": weight,
                }
                for date, weights in sorted(result.weekly_targets.items())
                for code, weight in sorted(weights.items())
            ],
            columns=["week_ending", "ts_code", "weight"],
        )
        orders = pd.DataFrame(result.orders)
        if orders.empty:
            orders = pd.DataFrame(columns=["order_id", "ts_code", "status"])
        fills = pd.DataFrame(result.trade_events)
        if fills.empty:
            fills = pd.DataFrame(
                columns=["trade_date", "ts_code", "quantity"]
            )
        positions = pd.DataFrame(_flatten_positions(result.positions_history))
        if positions.empty:
            positions = pd.DataFrame(
                columns=[
                    "trade_date",
                    "ts_code",
                    "quantity",
                    "market_value",
                    "cash",
                ]
            )
        equity = pd.DataFrame({"strategy": result.executed_equity})
        for name, series in result.benchmark_equity.items():
            equity[name] = series

        metrics_payload = {
            "strategy": dict(result.strategy_metrics),
            "benchmarks": {
                name: dict(metrics)
                for name, metrics in result.benchmark_metrics.items()
            },
            "execution": dict(result.execution_diagnostics),
            "quality_status": result.quality_status,
            "status": terminal.value,
            "partial": partial,
            "error_code": getattr(result, "error_code", ""),
            "error_message": getattr(result, "error_message", ""),
        }
        summary = {
            "mode": request.mode,
            "run_id": run_id,
            "strategy_id": identity.strategy_id,
            "variant_key": identity.variant_key,
            "status": terminal.value,
            "partial": partial,
            "publishable_for_comparison": not partial,
            "error_code": getattr(result, "error_code", ""),
            "error_message": getattr(result, "error_message", ""),
            "quality_status": result.quality_status,
            "annual_return": result.strategy_metrics.get("annual_return", 0.0),
            "max_drawdown": result.strategy_metrics.get("max_drawdown", 0.0),
            "sharpe": result.strategy_metrics.get("sharpe", 0.0),
            "total_return": result.strategy_metrics.get("total_return", 0.0),
            "turnover": result.execution_diagnostics.get("turnover", 0.0),
            "run_identity_hash": run_identity_hash,
        }
        for role, media_type, payload in (
            ("target_decisions", "text/csv", decisions),
            ("targets", "text/csv", targets),
            ("orders", "text/csv", orders),
            ("fills", "text/csv", fills),
            ("positions", "text/csv", positions),
            ("equity", "text/csv", equity),
            ("metrics", "application/json", metrics_payload),
            ("execution_diagnostics", "application/json", dict(result.execution_diagnostics)),
            ("summary", "application/json", summary),
        ):
            publisher.publish(
                StrategyArtifact(
                    role=role,
                    media_type=media_type,
                    payload=payload,
                )
            )
        if result.diagnostics is not None:
            for artifact in result.diagnostics.artifacts:
                publisher.publish(artifact, producer=identity.strategy_id)

        self.record_stage(
            batch_id=batch_id,
            request=request,
            identity=identity,
            run_id=run_id,
            snapshot=snapshot,
            stage=terminal.value,
            error=(
                (getattr(result, "error_message", "") or None)
                if partial
                else None
            ),
            quality_status=result.quality_status,
        )
        try:
            publisher.index_external("state")
            publisher.index_external("events")
            file_details = {
                entry["file"]: {
                    key: value
                    for key, value in entry.items()
                    if key
                    in {
                        "checksum",
                        "rows",
                        "schema_version",
                        "encoding",
                        "columns",
                    }
                }
                for entry in publisher.artifact_index().values()
            }
            publisher.finalize(
                status=terminal.value,
                identity={
                    "run_id": run_id,
                    "batch_id": batch_id,
                    "variant_key": identity.variant_key,
                    "strategy_id": identity.strategy_id,
                    "mode": request.mode,
                    "quality_status": result.quality_status,
                    "partial": partial,
                    "publishable_for_comparison": not partial,
                    "params_fingerprint": identity.resolved_config_hash,
                    "resolved_execution_hash": resolved_execution_hash,
                    "framework_implementation_hash": self.framework_hash,
                    "strategy_implementation_hash": identity.implementation_hash,
                    "data_snapshot_fingerprint": snapshot.fingerprint,
                    "run_identity_hash": run_identity_hash,
                    "state_checksum": compute_file_checksum(run_dir / "state.json"),
                    "file_details": file_details,
                },
            )
        except Exception as exc:
            self._mark_interrupted(
                run_dir,
                json.loads((run_dir / "state.json").read_text(encoding="utf-8")),
                error=f"child publication failed: {exc}",
            )
            raise

    def fail_publication_if_running(
        self,
        *,
        batch_id: str,
        request: StrategyBatchRequest,
        identity,
        run_id: str,
        snapshot,
        error: str,
    ) -> None:
        state_path = self.runs_root / run_id / "state.json"
        if not state_path.exists():
            return
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("stage") == ChildStage.FAILED_INTERRUPTED.value:
            return
        if detect_interrupted_state(state):
            self.record_stage(
                batch_id=batch_id,
                request=request,
                identity=identity,
                run_id=run_id,
                snapshot=snapshot,
                stage=ChildStage.FAILED.value,
                error=error,
            )

    def recover_interrupted(self) -> None:
        if not self.runs_root.exists():
            return
        for child_dir in sorted(self.runs_root.iterdir()):
            state_path = child_dir / "state.json"
            if not child_dir.is_dir() or not state_path.exists():
                continue
            state = json.loads(state_path.read_text(encoding="utf-8"))
            manifest = read_valid_manifest(
                child_dir,
                identity_field="run_id",
                expected_identity=str(state.get("run_id", child_dir.name)),
                allowed_statuses={ChildStage.SUCCEEDED.value},
            )
            if manifest is not None:
                continue
            if detect_interrupted_state(state) or state.get("stage") == ChildStage.SUCCEEDED.value:
                self._mark_interrupted(child_dir, state)

    @staticmethod
    def _append_event(
        child_dir: Path,
        state: dict[str, Any],
        *,
        stage: ChildStage,
        message: str | None = None,
        error: str | None = None,
    ) -> None:
        BatchEventLog(child_dir, batch_id=str(state["batch_id"])).append(
            event_type=(
                "TERMINAL" if stage in CHILD_TERMINAL_STAGES else "VARIANT_STAGE"
            ),
            scope="VARIANT",
            run_id=str(state.get("run_id", child_dir.name)),
            variant_key=str(state["variant_key"]),
            strategy_id=str(state["strategy_id"]),
            stage=stage.value,
            message=message or stage.value,
            error=error,
        )

    @classmethod
    def _mark_interrupted(
        cls,
        child_dir: Path,
        state: dict[str, Any],
        *,
        error: str | None = None,
    ) -> None:
        interrupted = mark_state_interrupted(state)
        if error is not None:
            interrupted["error"] = error
        atomic_write_json(child_dir / "state.json", interrupted)
        cls._append_event(
            child_dir,
            interrupted,
            stage=ChildStage.FAILED_INTERRUPTED,
            error=error,
        )


def _canonical_hash(value: object) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _flatten_positions(history: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for snapshot in history:
        trade_date = snapshot.get("trade_date", "")
        for holding in snapshot.get("holdings", []):
            rows.append(
                {
                    "trade_date": trade_date,
                    **holding,
                    "cash": snapshot.get("cash", 0.0),
                }
            )
    return rows
