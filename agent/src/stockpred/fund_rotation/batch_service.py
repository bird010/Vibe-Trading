"""Sequential strategy-batch orchestration — Phase 4 Task 4 (§22/§25/§26).

One bounded single-worker executor runs variants strictly ordered by
``variant_key``; every variant gets an isolated strategy/session/data view/run
directory while sharing one pinned snapshot and evaluation context.

Planning keeps two different time boundaries explicit:

* ``data_start`` is the earliest date loaded so the anchor decision has its
  declared warmup history;
* ``decision_start_date`` is the last scheduled decision before the formal
  evaluation interval and is the first date on which the strategy may run.

A variant failure only fails that variant (PARTIAL_SUCCEEDED); a shared snapshot
failure fails the whole batch. Failed/canceled child runs publish the partial
evidence already produced, but they are never eligible for comparison.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from backtest.fund_rotation.catalog import CatalogError
from backtest.fund_rotation.contracts import StrategyInitializationContext
from backtest.fund_rotation.evaluation import EvaluationContext
from backtest.fund_rotation.runner import (
    CancellationToken,
    ExecutionConfig,
    FundRotationBacktestRunner,
    SubRunStatus,
)
from backtest.fund_rotation.strategies.registry import default_fund_rotation_strategies
from src.stockpred.fund_rotation.artifact_publisher import read_valid_manifest
from src.stockpred.fund_rotation.batch_child_runtime import BatchChildRuntime
from src.stockpred.fund_rotation.batch_models import (
    StrategyBatchRequest,
    canonical_payload_hash,
)
from src.stockpred.fund_rotation.batch_persistence import (
    BatchPersistence,
    build_variant_identities,
)
from src.stockpred.fund_rotation.comparison import (
    VariantComparisonInput,
    build_comparison,
)
from src.stockpred.fund_rotation.persistence import BatchEventLog, atomic_write_json
from src.stockpred.fund_rotation.state_machine import (
    BATCH_TERMINAL_STAGES,
    BatchStage,
    BatchStateMachine,
    ChildStage,
    detect_interrupted_state,
    mark_state_interrupted,
)
from src.stockpred.fund_rotation.strategy_snapshot import snapshot_framework


class BatchPlanningError(Exception):
    """Structured planning failure raised before any background task starts."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def catalog_identity_hash(catalog) -> str:
    """Stable hash over the startup-fixed catalog entries (§16)."""
    entries = [
        {
            "strategy_id": entry.strategy_id,
            "interface_version": entry.interface_version,
            "implementation_hash": entry.implementation_hash,
        }
        for entry in catalog.list()
    ]
    canonical = json.dumps(entries, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class BatchService:
    """Bounded sequential batch orchestrator (single-user local service)."""

    def __init__(
        self,
        batches_dir: Path,
        *,
        runs_root: Path | None = None,
        catalog=None,
        metadata_loader: Callable[[], Any] | None = None,
        frames_loader: Callable[..., tuple] | None = None,
        auto_start: bool = True,
    ) -> None:
        from backtest.fund_rotation.catalog import FundRotationStrategyCatalog

        self.persistence = BatchPersistence(Path(batches_dir))
        self.runs_root = (
            Path(runs_root)
            if runs_root is not None
            else self.persistence.batches_dir / "fund_rotation"
        )
        self.catalog = catalog or FundRotationStrategyCatalog(
            list(default_fund_rotation_strategies())
        )
        self.framework_implementation_hash = snapshot_framework(
            Path(__file__).resolve().parents[3]
        )
        self.child_runtime = BatchChildRuntime(
            self.runs_root,
            self.catalog,
            self.framework_implementation_hash,
        )
        self.metadata_loader = metadata_loader
        self.frames_loader = frames_loader
        self.auto_start = auto_start
        self.executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="fund-rotation-batch",
        )
        self._tokens: dict[str, CancellationToken] = {}
        self._prepared: dict[str, dict[str, Any]] = {}

    # ── submission ──

    def submit_batch(self, request: StrategyBatchRequest) -> dict[str, Any]:
        """Validate, plan and persist a batch before any background task."""
        payload_hash = canonical_payload_hash(request)
        record, created = self.persistence.submit(
            request.idempotency_key,
            payload_hash,
        )
        batch_id = record["batch_id"]
        if not created:
            return {"batch_id": batch_id, "status": "EXISTING"}

        try:
            identities = build_variant_identities(self.catalog, request.variants)
            snapshot = self.metadata_loader()
            from src.stockpred.fund_rotation.data_snapshot import PinnedFundDataSnapshot

            if not isinstance(snapshot, PinnedFundDataSnapshot):
                raise TypeError(
                    "metadata_loader must return PinnedFundDataSnapshot"
                )
            plans, plan_summary = self._plan(request, identities, snapshot)
        except CatalogError as exc:
            self._fail_batch(
                batch_id,
                stage="VALIDATING",
                error=f"{exc.code}: {exc.message}",
            )
            raise BatchPlanningError(exc.code, exc.message) from exc
        except BatchPlanningError as exc:
            self._fail_batch(
                batch_id,
                stage="VALIDATING",
                error=f"{exc.code}: {exc.message}",
            )
            raise
        except Exception as exc:
            self._fail_batch(
                batch_id,
                stage="VALIDATING",
                error=f"variant validation failed: {exc}",
            )
            raise BatchPlanningError(
                "FUND_ROTATION_BATCH_INVALID",
                str(exc),
            ) from exc

        token = CancellationToken()
        self._tokens[batch_id] = token
        self._prepared[batch_id] = {
            "request": request,
            "identities": identities,
            "plans": plans,
            "plan_summary": plan_summary,
            "snapshot": snapshot,
            "payload_hash": payload_hash,
        }

        batch_dir = self.persistence.batch_dir(batch_id)
        batch_dir.mkdir(parents=True, exist_ok=True)
        resolved = {
            "batch_id": batch_id,
            "schema_version": request.schema_version,
            "mode": request.mode,
            "catalog_version": catalog_identity_hash(self.catalog),
            "framework_implementation_hash": self.framework_implementation_hash,
            "variants": [
                {
                    **identity.__dict__,
                    "run_id": plan["run_id"],
                    "status": ChildStage.QUEUED.value,
                    "snapshot_fingerprint": snapshot.fingerprint,
                    "data_start": plan["data_start"],
                    "decision_start_date": plan["decision_start_date"],
                    "anchor_decision_date": plan["anchor_decision_date"],
                }
                for identity, plan in zip(identities, plans)
            ],
            "plan": plan_summary,
            "data_snapshot": asdict(snapshot),
            "executed_order": [],
        }
        self.persistence.write_batch_request(
            batch_id,
            request_payload=request.model_dump(mode="json"),
            identity=_resolved_identity(
                batch_id,
                request,
                identities,
                self.framework_implementation_hash,
            ),
        )
        self._write_resolved(batch_dir, resolved)
        self._write_state(batch_dir, BatchStage.QUEUED.value, batch_id)

        if self.auto_start:
            self.executor.submit(self._safe_execute, batch_id)
        return {"batch_id": batch_id, "status": BatchStage.QUEUED.value}

    # ── planning (§22: calendar fixed, no market scan) ──

    def _plan(self, request, identities, snapshot):
        calendar = sorted(str(date) for date in snapshot.trading_dates)
        eval_start = request.evaluation_start_date
        eval_end = request.evaluation_end_date
        eval_dates = [date for date in calendar if eval_start <= date <= eval_end]
        if not eval_dates:
            raise BatchPlanningError(
                "FUND_ROTATION_EMPTY_EVALUATION_CALENDAR",
                f"no trading date in [{eval_start}, {eval_end}]",
            )

        plans: list[dict[str, Any]] = []
        for variant, identity in zip(request.variants, identities):
            binding = self.catalog.resolve(
                variant.strategy_id,
                dict(variant.params),
            )
            strategy = binding.strategy
            config = binding.registered.config_model.model_validate(
                dict(binding.spec.resolved_config)
            )
            requirements = binding.spec.resolved_requirements
            warmup = int(requirements.warmup_trade_days)

            if len(calendar) <= warmup:
                raise BatchPlanningError(
                    "FUND_ROTATION_INSUFFICIENT_HISTORY",
                    f"variant {identity.variant_key}: calendar has "
                    f"{len(calendar)} trading days, warmup needs {warmup}",
                )
            provisional_decision_start = calendar[warmup]

            session = strategy.create_session(
                StrategyInitializationContext(
                    run_id="planning",
                    evaluation_calendar=tuple(eval_dates),
                ),
                config,
            )
            scheduled = session.scheduled_dates(
                tuple(calendar),
                provisional_decision_start,
                eval_end,
            )
            if not scheduled:
                raise BatchPlanningError(
                    "FUND_ROTATION_INSUFFICIENT_HISTORY",
                    f"variant {identity.variant_key}: no decision date within "
                    f"[{provisional_decision_start}, {eval_end}]",
                )

            pre_evaluation = [date for date in scheduled if date < eval_start]
            if not pre_evaluation:
                raise BatchPlanningError(
                    "FUND_ROTATION_INSUFFICIENT_HISTORY",
                    f"variant {identity.variant_key}: no pre-evaluation "
                    f"decision date before {eval_start}",
                )

            anchor = max(pre_evaluation)
            position = calendar.index(anchor)
            if position < warmup:
                raise BatchPlanningError(
                    "FUND_ROTATION_INSUFFICIENT_HISTORY",
                    f"variant {identity.variant_key}: anchor {anchor} leaves "
                    f"only {position} days before it, warmup needs {warmup}",
                )

            data_start = calendar[position - warmup]
            plans.append(
                {
                    "identity": identity,
                    "binding": binding,
                    "strategy": strategy,
                    "config": config,
                    "resolved_requirements": requirements,
                    "run_id": uuid.uuid4().hex[:12],
                    "anchor_decision_date": anchor,
                    "decision_start_date": anchor,
                    "data_start": data_start,
                }
            )

        batch_data_start = min(plan["data_start"] for plan in plans)
        plan_summary = {
            "data_start": batch_data_start,
            "earliest_decision_start_date": min(
                plan["decision_start_date"] for plan in plans
            ),
            "evaluation_start_date": eval_start,
            "evaluation_end_date": eval_end,
            "variants": [
                {
                    "variant_key": plan["identity"].variant_key,
                    "data_start": plan["data_start"],
                    "decision_start_date": plan["decision_start_date"],
                    "anchor_decision_date": plan["anchor_decision_date"],
                }
                for plan in plans
            ],
        }
        return plans, plan_summary

    def _load_frames(self, snapshot, data_start: str, data_end: str):
        return self.frames_loader(snapshot, data_start, data_end)

    # ── execution ──

    def run_batch_sync(self, batch_id: str) -> None:
        self._execute_batch(batch_id)

    def cancel_batch(self, batch_id: str) -> bool:
        token = self._tokens.get(batch_id)
        if token is None:
            return False
        state_path = self.persistence.batch_dir(batch_id) / "state.json"
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("stage") in {
                stage.value for stage in BATCH_TERMINAL_STAGES
            }:
                return False
        token.cancel()
        return True

    def wait_until_idle(self, timeout: float | None = None) -> None:
        self.executor.submit(lambda: None).result(timeout=timeout)

    def _safe_execute(self, batch_id: str) -> None:
        try:
            self._execute_batch(batch_id)
        except Exception as exc:  # pragma: no cover - defensive
            self._fail_batch(
                batch_id,
                stage=BatchStage.RUNNING_STRATEGIES.value,
                error=f"unexpected orchestrator failure: {exc}",
            )

    def _execute_batch(self, batch_id: str) -> None:
        prepared = self._prepared[batch_id]
        request: StrategyBatchRequest = prepared["request"]
        plans: list[dict[str, Any]] = prepared["plans"]
        snapshot = prepared["snapshot"]
        token = self._tokens[batch_id]

        batch_dir = self.persistence.batch_dir(batch_id)
        batch_dir.mkdir(parents=True, exist_ok=True)
        events = BatchEventLog(batch_dir, batch_id=batch_id)
        state_machine = BatchStateMachine()

        def advance(stage: BatchStage) -> None:
            state_machine.transition(stage)
            self._write_state(batch_dir, state_machine.stage.value, batch_id)
            events.append(
                event_type="BATCH_STAGE",
                scope="BATCH",
                stage=state_machine.stage.value,
            )

        advance(BatchStage.VALIDATING)
        advance(BatchStage.SNAPSHOTTING_DATA)

        if token.is_cancelled:
            run_ids = {
                plan["identity"].variant_key: plan["run_id"]
                for plan in plans
            }
            self._persist_parent_terminal(
                batch_dir=batch_dir,
                batch_id=batch_id,
                events=events,
                final_stage=BatchStage.CANCELED.value,
                statuses={
                    key: ChildStage.CANCELED.value for key in run_ids
                },
                run_ids=run_ids,
                executed_order=[],
            )
            return

        data_start = min(plan["data_start"] for plan in plans)
        try:
            fund_daily, fund_adj, dim_fund = self._load_frames(
                snapshot,
                data_start,
                request.evaluation_end_date,
            )
        except Exception as exc:
            self._fail_batch(
                batch_id,
                stage=BatchStage.SNAPSHOTTING_DATA.value,
                error=f"shared snapshot failed: {exc}",
            )
            return

        calendar = sorted(str(date) for date in snapshot.trading_dates)
        atomic_write_json(batch_dir / "data_snapshot.json", asdict(snapshot))
        evaluation = EvaluationContext.from_range(
            calendar,
            request.evaluation_start_date,
            request.evaluation_end_date,
        )
        execution_config = ExecutionConfig.model_validate(dict(request.execution))
        runner = FundRotationBacktestRunner(fund_daily, fund_adj, dim_fund)

        advance(BatchStage.RUNNING_STRATEGIES)

        ordered = sorted(plans, key=lambda plan: plan["identity"].variant_key)
        statuses: dict[str, str] = {}
        executed_order: list[dict[str, Any]] = []
        run_results: dict[str, object] = {}
        run_ids: dict[str, str] = {
            plan["identity"].variant_key: plan["run_id"] for plan in ordered
        }

        for plan in ordered:
            identity = plan["identity"]
            run_id = plan["run_id"]
            self._record_child_stage(
                batch_id=batch_id,
                request=request,
                identity=identity,
                run_id=run_id,
                snapshot=snapshot,
                stage=ChildStage.QUEUED.value,
            )
            events.append(
                event_type="VARIANT_STAGE",
                scope="VARIANT",
                run_id=run_id,
                variant_key=identity.variant_key,
                strategy_id=identity.strategy_id,
                stage=ChildStage.QUEUED.value,
            )

        for plan in ordered:
            identity = plan["identity"]
            variant_key = identity.variant_key
            run_id = plan["run_id"]
            if token.is_cancelled:
                statuses[variant_key] = SubRunStatus.CANCELED.value
                executed_order.append({"variant_key": variant_key})
                self._record_child_stage(
                    batch_id=batch_id,
                    request=request,
                    identity=identity,
                    run_id=run_id,
                    snapshot=snapshot,
                    stage=ChildStage.CANCELED.value,
                    message="CANCELED before start",
                )
                events.append(
                    event_type="TERMINAL",
                    scope="VARIANT",
                    run_id=run_id,
                    variant_key=variant_key,
                    strategy_id=identity.strategy_id,
                    stage=ChildStage.CANCELED.value,
                    message="CANCELED before start",
                )
                continue

            for child_stage in (
                ChildStage.PREPARING_DATA,
                ChildStage.GENERATING_SIGNALS,
                ChildStage.EXECUTING,
            ):
                self._record_child_stage(
                    batch_id=batch_id,
                    request=request,
                    identity=identity,
                    run_id=run_id,
                    snapshot=snapshot,
                    stage=child_stage.value,
                )
                events.append(
                    event_type="VARIANT_STAGE",
                    scope="VARIANT",
                    run_id=run_id,
                    variant_key=variant_key,
                    strategy_id=identity.strategy_id,
                    stage=child_stage.value,
                )

            result = None
            try:
                result = runner.run(
                    strategy=plan["strategy"],
                    config=plan["config"],
                    snapshot=snapshot,
                    evaluation=evaluation,
                    execution=execution_config,
                    cancellation=token,
                    decision_start_date=plan["decision_start_date"],
                    resolved_requirements=plan["resolved_requirements"],
                    run_id=run_id,
                )
                status = result.status.value
                run_results[variant_key] = result
            except Exception as exc:
                status = SubRunStatus.FAILED.value
                events.append(
                    event_type="ERROR",
                    scope="VARIANT",
                    run_id=run_id,
                    variant_key=variant_key,
                    strategy_id=identity.strategy_id,
                    error=str(exc),
                )

            if status == SubRunStatus.SUCCEEDED.value and token.is_cancelled:
                status = SubRunStatus.CANCELED.value

            if result is not None:
                if status == SubRunStatus.SUCCEEDED.value:
                    for child_stage in (
                        ChildStage.COMPUTING_METRICS,
                        ChildStage.WRITING_RESULTS,
                    ):
                        self._record_child_stage(
                            batch_id=batch_id,
                            request=request,
                            identity=identity,
                            run_id=run_id,
                            snapshot=snapshot,
                            stage=child_stage.value,
                        )
                        events.append(
                            event_type="VARIANT_STAGE",
                            scope="VARIANT",
                            run_id=run_id,
                            variant_key=variant_key,
                            strategy_id=identity.strategy_id,
                            stage=child_stage.value,
                        )
                try:
                    self.child_runtime.publish_result(
                        batch_id=batch_id,
                        request=request,
                        plan=plan,
                        snapshot=snapshot,
                        evaluation=evaluation,
                        result=result,
                        terminal_stage=status,
                        execution_config=execution_config,
                    )
                except Exception as exc:
                    status = SubRunStatus.FAILED.value
                    events.append(
                        event_type="ERROR",
                        scope="VARIANT",
                        run_id=run_id,
                        variant_key=variant_key,
                        strategy_id=identity.strategy_id,
                        error=f"child publication failed: {exc}",
                    )
                    self.child_runtime.fail_publication_if_running(
                        batch_id=batch_id,
                        request=request,
                        identity=identity,
                        run_id=run_id,
                        snapshot=snapshot,
                        error=f"child publication failed: {exc}",
                    )
            else:
                self._record_child_stage(
                    batch_id=batch_id,
                    request=request,
                    identity=identity,
                    run_id=run_id,
                    snapshot=snapshot,
                    stage=ChildStage.FAILED.value,
                    error="runner raised before returning a result",
                )

            statuses[variant_key] = status
            executed_order.append({"variant_key": variant_key})
            events.append(
                event_type="TERMINAL",
                scope="VARIANT",
                run_id=run_id,
                variant_key=variant_key,
                strategy_id=identity.strategy_id,
                stage=status,
                message=status,
            )

        if not token.is_cancelled:
            advance(BatchStage.COMPARING)
            eval_dates = [
                date
                for date in calendar
                if request.evaluation_start_date
                <= date
                <= request.evaluation_end_date
            ]
            comparison_inputs: list[VariantComparisonInput] = []
            for plan in ordered:
                identity = plan["identity"]
                variant_key = identity.variant_key
                result = run_results.get(variant_key)
                if result is None:
                    continue
                has_invalid = any(
                    decision.action.value == "INVALID"
                    for decision in result.decisions
                )
                comparison_inputs.append(
                    VariantComparisonInput(
                        variant_key=variant_key,
                        strategy_id=identity.strategy_id,
                        run_id=run_ids.get(variant_key, ""),
                        status=statuses.get(variant_key, "UNKNOWN"),
                        equity=result.executed_equity,
                        decision_quality=result.quality_status,
                        has_invalid_action=has_invalid,
                    )
                )

            outcome = build_comparison(
                comparison_inputs,
                evaluation_calendar=eval_dates,
                framework_implementation_hash=self.framework_implementation_hash,
                data_snapshot_fingerprint=snapshot.fingerprint,
                execution_contract=execution_config.model_dump(mode="json"),
            )

            comparable_count = len(outcome.equity_frame.columns)
            comparison_available = comparable_count >= 2
            reports = {
                "comparison_available": comparison_available,
                "comparable_variant_count": comparable_count,
                "contract": {
                    "fingerprint": outcome.contract_fingerprint,
                    "components": outcome.contract_components,
                },
                "ranking": outcome.ranking if comparison_available else [],
                "metrics": outcome.metrics,
                "excluded": outcome.excluded,
                "quality_warnings": outcome.quality_warnings,
            }
            atomic_write_json(batch_dir / "reports.json", reports)

            if comparison_available:
                tmp = batch_dir / "comparison_equity.csv.tmp"
                outcome.equity_frame.to_csv(tmp)
                os.replace(str(tmp), str(batch_dir / "comparison_equity.csv"))

                import pandas as pd

                tmp = batch_dir / "comparison_metrics.csv.tmp"
                pd.DataFrame(outcome.metrics).T.to_csv(tmp)
                os.replace(str(tmp), str(batch_dir / "comparison_metrics.csv"))

            advance(BatchStage.WRITING_RESULTS)

        if token.is_cancelled:
            final_stage = BatchStage.CANCELED.value
        else:
            values = set(statuses.values())
            if values <= {SubRunStatus.SUCCEEDED.value}:
                final_stage = BatchStage.SUCCEEDED.value
            elif SubRunStatus.SUCCEEDED.value in values:
                final_stage = BatchStage.PARTIAL_SUCCEEDED.value
            else:
                final_stage = BatchStage.FAILED.value

        resolved = self._persist_parent_terminal(
            batch_dir=batch_dir,
            batch_id=batch_id,
            events=events,
            final_stage=final_stage,
            statuses=statuses,
            run_ids=run_ids,
            executed_order=executed_order,
        )

        if (
            not token.is_cancelled
            and final_stage
            in (BatchStage.SUCCEEDED.value, BatchStage.PARTIAL_SUCCEEDED.value)
        ):
            from src.stockpred.fund_rotation.artifacts import compute_file_checksum

            artifact_names = [
                "request.json",
                "resolved_batch.json",
                "state.json",
                "events.jsonl",
                "data_snapshot.json",
                "reports.json",
                "comparison_equity.csv",
                "comparison_metrics.csv",
            ]
            files = []
            file_details = {}
            for name in artifact_names:
                path = batch_dir / name
                if path.exists():
                    files.append(name)
                    file_details[name] = {
                        "checksum": compute_file_checksum(path),
                    }
            manifest = {
                "batch_id": batch_id,
                "status": final_stage,
                "mode": request.mode,
                "catalog_version": catalog_identity_hash(self.catalog),
                "framework_implementation_hash": self.framework_implementation_hash,
                "data_snapshot_fingerprint": snapshot.fingerprint,
                "variants": [
                    {
                        "variant_key": variant["variant_key"],
                        "strategy_id": variant.get("strategy_id", ""),
                        "run_id": variant.get("run_id"),
                        "status": variant.get("status"),
                    }
                    for variant in resolved.get("variants", [])
                ],
                "files": files,
                "file_details": file_details,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            atomic_write_json(batch_dir / "manifest.json", manifest)

    # ── child-run persistence/publication (§25/§29) ──

    def _record_child_stage(
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
        self.child_runtime.record_stage(
            batch_id=batch_id,
            request=request,
            identity=identity,
            run_id=run_id,
            snapshot=snapshot,
            stage=stage,
            message=message,
            error=error,
            quality_status=quality_status,
        )

    # ── recovery (§26.1) ──

    def recover_interrupted(self) -> list[str]:
        recovered: list[str] = []
        root = self.persistence.batches_dir
        if root.exists():
            for batch_dir in sorted(root.iterdir()):
                if batch_dir.name == "idempotency" or not batch_dir.is_dir():
                    continue
                state_path = batch_dir / "state.json"
                if not state_path.exists():
                    continue
                state = json.loads(state_path.read_text(encoding="utf-8"))
                manifest = read_valid_manifest(
                    batch_dir,
                    identity_field="batch_id",
                    expected_identity=batch_dir.name,
                    allowed_statuses={
                        BatchStage.SUCCEEDED.value,
                        BatchStage.PARTIAL_SUCCEEDED.value,
                    },
                )
                if manifest is not None:
                    continue
                if (
                    detect_interrupted_state(state)
                    or state.get("stage")
                    in {
                        BatchStage.SUCCEEDED.value,
                        BatchStage.PARTIAL_SUCCEEDED.value,
                    }
                ):
                    interrupted = mark_state_interrupted(state)
                    atomic_write_json(state_path, interrupted)
                    BatchEventLog(
                        batch_dir,
                        batch_id=batch_dir.name,
                    ).append(
                        event_type="TERMINAL",
                        scope="BATCH",
                        stage=BatchStage.FAILED_INTERRUPTED.value,
                        message=BatchStage.FAILED_INTERRUPTED.value,
                    )
                    recovered.append(batch_dir.name)

        self.child_runtime.recover_interrupted()
        return recovered

    # ── helpers ──

    def _write_state(self, batch_dir: Path, stage: str, batch_id: str) -> None:
        atomic_write_json(
            batch_dir / "state.json",
            {
                "schema_version": "2",
                "stage": stage,
                "batch_id": batch_id,
                "mode": "RESEARCH_ONLY",
            },
        )

    def _persist_parent_terminal(
        self,
        *,
        batch_dir: Path,
        batch_id: str,
        events: BatchEventLog,
        final_stage: str,
        statuses: dict[str, str],
        run_ids: dict[str, str],
        executed_order: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self._write_state(batch_dir, final_stage, batch_id)
        resolved = self._read_resolved(batch_dir)
        for variant in resolved.get("variants", []):
            variant["status"] = statuses.get(
                variant["variant_key"],
                ChildStage.CANCELED.value,
            )
            variant["run_id"] = run_ids.get(variant["variant_key"])
        resolved["executed_order"] = executed_order
        self._write_resolved(batch_dir, resolved)
        events.append(
            event_type="TERMINAL",
            scope="BATCH",
            stage=final_stage,
            message=final_stage,
        )
        return resolved

    def _fail_batch(self, batch_id: str, *, stage: str, error: str) -> None:
        batch_dir = self.persistence.batch_dir(batch_id)
        batch_dir.mkdir(parents=True, exist_ok=True)
        state = {
            "schema_version": "2",
            "stage": BatchStage.FAILED.value,
            "batch_id": batch_id,
            "mode": "RESEARCH_ONLY",
            "failed_stage": stage,
            "error": error,
        }
        atomic_write_json(batch_dir / "state.json", state)
        events = BatchEventLog(batch_dir, batch_id=batch_id)
        events.append(event_type="ERROR", scope="BATCH", error=error)
        events.append(
            event_type="TERMINAL",
            scope="BATCH",
            stage=BatchStage.FAILED.value,
            message=BatchStage.FAILED.value,
        )

    def _write_resolved(self, batch_dir: Path, resolved: dict) -> None:
        atomic_write_json(batch_dir / "resolved_batch.json", resolved)

    def _read_resolved(self, batch_dir: Path) -> dict:
        path = batch_dir / "resolved_batch.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))


def _resolved_identity(batch_id, request, identities, framework_hash):
    from src.stockpred.fund_rotation.batch_persistence import ResolvedBatchIdentity

    return ResolvedBatchIdentity(
        batch_id=batch_id,
        schema_version=request.schema_version,
        mode=request.mode,
        catalog_version=catalog_identity_hash_from_identities(identities),
        framework_implementation_hash=framework_hash,
        variants=tuple(identities),
    )


def catalog_identity_hash_from_identities(identities) -> str:
    canonical = json.dumps(
        [
            {
                "variant_key": identity.variant_key,
                "implementation_hash": identity.implementation_hash,
            }
            for identity in identities
        ],
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
