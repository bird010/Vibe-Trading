"""Sequential strategy-batch orchestration — Phase 4 Task 4 (§22/§25/§26).

One bounded single-worker executor runs variants strictly ordered by
``variant_key``; every variant gets an isolated strategy/session/data
view/run directory while sharing ONE pinned snapshot and evaluation context.
Planning derives each variant's data start from its LAST pre-evaluation
decision date traced back by its OWN warmup (never a simple
``evaluation_start - max(warmup)``) and planning never scans market data.

A variant failure only fails that variant (PARTIAL_SUCCEEDED); a shared
snapshot failure fails the whole batch. Cancellation is cooperative:
unstarted variants never launch, the running one stops at the next
checkpoint, finished children stay read-only, and a canceled batch publishes
no comparison manifest. JSON/CSV persistence only — no resume promise.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from backtest.fund_rotation.catalog import CatalogError
from backtest.fund_rotation.contracts import (
    StrategyArtifact,
    StrategyInitializationContext,
)
from backtest.fund_rotation.evaluation import EvaluationContext
from backtest.fund_rotation.runner import (
    CancellationToken,
    ExecutionConfig,
    FundRotationBacktestRunner,
    SubRunStatus,
)
from backtest.fund_rotation.strategies.registry import (
    default_fund_rotation_strategies,
)
from src.stockpred.fund_rotation.batch_models import (
    StrategyBatchRequest,
    canonical_payload_hash,
)
from src.stockpred.fund_rotation.artifact_publisher import ArtifactPublisher
from src.stockpred.fund_rotation.comparison import (
    VariantComparisonInput,
    build_comparison,
)
from src.stockpred.fund_rotation.batch_persistence import (
    BatchPersistence,
    build_variant_identities,
)
from src.stockpred.fund_rotation.persistence import (
    BatchEventLog,
    atomic_write_json,
)
from src.stockpred.fund_rotation.state_machine import (
    BatchStateMachine,
    BatchStage,
    ChildStage,
    BATCH_TERMINAL_STAGES,
    CHILD_TERMINAL_STAGES,
    detect_interrupted_state,
    mark_state_interrupted,
)


class BatchPlanningError(Exception):
    """Structured planning failure (raised before any background task)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def catalog_identity_hash(catalog) -> str:
    """Stable hash over the catalog entries (version identity, §16)."""
    entries = [
        {
            "strategy_id": e.strategy_id,
            "interface_version": e.interface_version,
            "implementation_hash": e.implementation_hash,
        }
        for e in catalog.list()
    ]
    canonical = json.dumps(entries, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def framework_implementation_hash() -> str:
    """Identity of the common runner/execution framework sources."""
    import backtest.fund_rotation.execution as execution_mod
    import backtest.fund_rotation.runner as runner_mod

    hasher = hashlib.sha256()
    for module in (runner_mod, execution_mod):
        hasher.update(inspect.getsource(module).encode("utf-8"))
    return hasher.hexdigest()


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
        self.metadata_loader = metadata_loader
        self.frames_loader = frames_loader
        self.auto_start = auto_start
        # §25.1 — bounded queue; batches run one at a time.
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="fund-rotation-batch",
        )
        self._tokens: dict[str, CancellationToken] = {}
        self._prepared: dict[str, dict[str, Any]] = {}

    # ── submission ──

    def submit_batch(self, request: StrategyBatchRequest) -> dict[str, Any]:
        """Validate, plan and persist a batch BEFORE any background task.

        Returns ``{batch_id, status}`` where status is QUEUED (new) or
        EXISTING (idempotent replay — the original batch is returned and
        never re-run).
        """
        payload_hash = canonical_payload_hash(request)
        record, created = self.persistence.submit(
            request.idempotency_key, payload_hash,
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
            self._fail_batch(batch_id, stage="VALIDATING", error=f"{exc.code}: {exc.message}")
            raise BatchPlanningError(exc.code, exc.message) from exc
        except BatchPlanningError as exc:
            self._fail_batch(batch_id, stage="VALIDATING", error=f"{exc.code}: {exc.message}")
            raise
        except Exception as exc:
            # Any validation failure after the idempotency binding must leave a
            # real FAILED batch behind — never a ghost EXISTING key (§21.1).
            self._fail_batch(
                batch_id, stage="VALIDATING",
                error=f"variant validation failed: {exc}",
            )
            raise BatchPlanningError("FUND_ROTATION_BATCH_INVALID", str(exc)) from exc

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
            "framework_implementation_hash": framework_implementation_hash(),
            "variants": [
                {
                    **identity.__dict__,
                    "run_id": plan["run_id"],
                    "status": "QUEUED",
                    "snapshot_fingerprint": snapshot.fingerprint,
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
            identity=_resolved_identity(batch_id, request, identities),
        )
        # The extended resolved document (statuses/plan/executed_order) owns
        # resolved_batch.json from here on.
        self._write_resolved(batch_dir, resolved)
        self._write_state(batch_dir, "QUEUED", batch_id)

        if self.auto_start:
            self.executor.submit(self._safe_execute, batch_id)
        return {"batch_id": batch_id, "status": "QUEUED"}

    # ── planning (§22: calendar fixed, no market scan) ──

    def _plan(self, request, identities, snapshot):
        calendar = sorted(str(d) for d in snapshot.trading_dates)
        eval_start = request.evaluation_start_date
        eval_end = request.evaluation_end_date
        eval_dates = [d for d in calendar if eval_start <= d <= eval_end]

        plans: list[dict[str, Any]] = []
        for variant, identity in zip(request.variants, identities):
            binding = self.catalog.resolve(
                variant.strategy_id, dict(variant.params),
            )
            strategy = binding.strategy
            config = binding.registered.config_model.model_validate(
                dict(binding.spec.resolved_config)
            )
            requirements = strategy.resolve_requirements(config)
            warmup = int(requirements.warmup_trade_days)

            if len(calendar) <= warmup:
                raise BatchPlanningError(
                    "FUND_ROTATION_INSUFFICIENT_HISTORY",
                    f"variant {identity.variant_key}: calendar has "
                    f"{len(calendar)} trading days, warmup needs {warmup}",
                )
            provisional_start = calendar[warmup]

            # Schedule-only session: never evaluated during planning.
            session = strategy.create_session(
                StrategyInitializationContext(
                    run_id="planning", evaluation_calendar=tuple(eval_dates),
                ),
                config,
            )
            scheduled = session.scheduled_dates(
                tuple(calendar), provisional_start, eval_end,
            )
            if not scheduled:
                raise BatchPlanningError(
                    "FUND_ROTATION_INSUFFICIENT_HISTORY",
                    f"variant {identity.variant_key}: no decision date within "
                    f"[{provisional_start}, {eval_end}]",
                )
            pre_evaluation = [d for d in scheduled if d < eval_start]
            if not pre_evaluation:
                # §23 step 3: without a decision date before the evaluation
                # start there is no first-day target — fail before launch.
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
            simulation_start = calendar[position - warmup]
            plans.append({
                "identity": identity,
                "strategy": strategy,
                "config": config,
                "run_id": uuid.uuid4().hex[:12],
                "anchor_decision_date": anchor,
                "simulation_start": simulation_start,
            })

        data_start = min(p["simulation_start"] for p in plans)
        anchor_plan = next(
            p for p in plans if p["simulation_start"] == data_start
        )
        plan_summary = {
            "data_start": data_start,
            "anchor_decision_date": anchor_plan["anchor_decision_date"],
            "simulation_start": anchor_plan["simulation_start"],
            "evaluation_start_date": eval_start,
            "evaluation_end_date": eval_end,
            "variants": [
                {
                    "variant_key": p["identity"].variant_key,
                    "anchor_decision_date": p["anchor_decision_date"],
                    "simulation_start": p["simulation_start"],
                }
                for p in plans
            ],
        }
        return plans, plan_summary

    def _load_frames(self, snapshot, data_start: str, data_end: str):
        """Read every table through the already-pinned snapshot."""
        return self.frames_loader(snapshot, data_start, data_end)

    # ── execution ──

    def run_batch_sync(self, batch_id: str) -> None:
        """Run a prepared batch on the calling thread (tests / foreground)."""
        self._execute_batch(batch_id)

    def cancel_batch(self, batch_id: str) -> bool:
        token = self._tokens.get(batch_id)
        if token is None:
            return False
        state_path = self.persistence.batch_dir(batch_id) / "state.json"
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("stage") in {stage.value for stage in BATCH_TERMINAL_STAGES}:
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
                batch_id, stage="RUNNING_STRATEGIES",
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
        sm = BatchStateMachine()

        def advance(stage: BatchStage) -> None:
            sm.transition(stage)
            self._write_state(batch_dir, sm.stage.value, batch_id)
            events.append(
                event_type="BATCH_STAGE", scope="BATCH", stage=sm.stage.value,
            )

        advance(BatchStage.VALIDATING)
        advance(BatchStage.SNAPSHOTTING_DATA)

        if token.is_cancelled:
            run_ids = {
                plan["identity"].variant_key: plan["run_id"] for plan in plans
            }
            self._persist_parent_terminal(
                batch_dir=batch_dir,
                batch_id=batch_id,
                events=events,
                final_stage=BatchStage.CANCELED.value,
                statuses={key: ChildStage.CANCELED.value for key in run_ids},
                run_ids=run_ids,
                executed_order=[],
            )
            return

        # Shared snapshot: the exact object resolved once before planning drives
        # the only frame read for every variant.
        data_start = min(p["simulation_start"] for p in plans)
        try:
            fund_daily, fund_adj, dim_fund = self._load_frames(
                snapshot, data_start, request.evaluation_end_date,
            )
        except Exception as exc:
            self._fail_batch(
                batch_id, stage="SNAPSHOTTING_DATA",
                error=f"shared snapshot failed: {exc}",
            )
            return

        calendar = sorted(str(d) for d in snapshot.trading_dates)
        atomic_write_json(batch_dir / "data_snapshot.json", asdict(snapshot))
        evaluation = EvaluationContext.from_range(
            calendar,
            request.evaluation_start_date,
            request.evaluation_end_date,
        )
        execution_config = ExecutionConfig.model_validate(dict(request.execution))
        runner = FundRotationBacktestRunner(fund_daily, fund_adj, dim_fund)

        advance(BatchStage.RUNNING_STRATEGIES)

        # Strict stable execution order: variant_key ascending, independent
        # of the request order (§21).
        ordered = sorted(plans, key=lambda p: p["identity"].variant_key)
        statuses: dict[str, str] = {}
        executed_order: list[dict[str, Any]] = []
        run_results: dict[str, object] = {}
        run_ids: dict[str, str] = {
            plan["identity"].variant_key: plan["run_id"] for plan in ordered
        }

        # Once the shared snapshot succeeds, every child has durable QUEUED
        # state before any strategy is launched. Recovery can now account for
        # a crash between variants.
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
                event_type="VARIANT_STAGE", scope="VARIANT",
                run_id=run_id, variant_key=identity.variant_key,
                strategy_id=identity.strategy_id, stage=ChildStage.QUEUED.value,
            )

        for plan in ordered:
            identity = plan["identity"]
            variant_key = identity.variant_key
            run_id = plan["run_id"]
            if token.is_cancelled:
                # §26.1 — unstarted variants never launch.
                statuses[variant_key] = "CANCELED"
                executed_order.append({"variant_key": variant_key})
                self._record_child_stage(
                    batch_id=batch_id, request=request, identity=identity,
                    run_id=run_id, snapshot=snapshot,
                    stage=ChildStage.CANCELED.value,
                    message="CANCELED before start",
                )
                events.append(
                    event_type="TERMINAL", scope="VARIANT",
                    run_id=run_id, variant_key=variant_key,
                    strategy_id=identity.strategy_id, stage=ChildStage.CANCELED.value,
                    message="CANCELED before start",
                )
                continue

            for child_stage in (
                ChildStage.PREPARING_DATA,
                ChildStage.GENERATING_SIGNALS,
                ChildStage.EXECUTING,
            ):
                self._record_child_stage(
                    batch_id=batch_id, request=request, identity=identity,
                    run_id=run_id, snapshot=snapshot, stage=child_stage.value,
                )
                events.append(
                    event_type="VARIANT_STAGE", scope="VARIANT",
                    run_id=run_id, variant_key=variant_key,
                    strategy_id=identity.strategy_id, stage=child_stage.value,
                )
            try:
                result = runner.run(
                    strategy=plan["strategy"],
                    config=plan["config"],
                    snapshot=snapshot,
                    evaluation=evaluation,
                    execution=execution_config,
                    cancellation=token,
                    simulation_start_date=plan["simulation_start"],
                    run_id=run_id,
                )
                status = result.status.value
                run_results[variant_key] = result
                # §26.1: a run that finished SUCCEEDED before cancellation
                # landed stays read-only SUCCEEDED — no rewrite needed.
            except Exception as exc:
                status = SubRunStatus.FAILED.value
                events.append(
                    event_type="ERROR", scope="VARIANT",
                    run_id=run_id, variant_key=variant_key,
                    strategy_id=identity.strategy_id, error=str(exc),
                )

            if status == SubRunStatus.SUCCEEDED.value and token.is_cancelled:
                status = SubRunStatus.CANCELED.value

            if status == SubRunStatus.SUCCEEDED.value:
                for child_stage in (
                    ChildStage.COMPUTING_METRICS,
                    ChildStage.WRITING_RESULTS,
                ):
                    self._record_child_stage(
                        batch_id=batch_id, request=request, identity=identity,
                        run_id=run_id, snapshot=snapshot, stage=child_stage.value,
                    )
                    events.append(
                        event_type="VARIANT_STAGE", scope="VARIANT",
                        run_id=run_id, variant_key=variant_key,
                        strategy_id=identity.strategy_id, stage=child_stage.value,
                    )
                try:
                    self._publish_child_result(
                        batch_id=batch_id,
                        request=request,
                        plan=plan,
                        snapshot=snapshot,
                        evaluation=evaluation,
                        result=result,
                    )
                except Exception as exc:
                    status = SubRunStatus.FAILED.value
                    events.append(
                        event_type="ERROR", scope="VARIANT",
                        run_id=run_id, variant_key=variant_key,
                        strategy_id=identity.strategy_id,
                        error=f"child publication failed: {exc}",
                    )
                    self._record_child_stage(
                        batch_id=batch_id, request=request, identity=identity,
                        run_id=run_id, snapshot=snapshot,
                        stage=ChildStage.FAILED.value,
                        error=f"child publication failed: {exc}",
                    )
            else:
                terminal_stage = (
                    ChildStage.CANCELED.value
                    if status == SubRunStatus.CANCELED.value
                    else ChildStage.FAILED.value
                )
                result_error = getattr(run_results.get(variant_key), "error_message", "")
                self._record_child_stage(
                    batch_id=batch_id, request=request, identity=identity,
                    run_id=run_id, snapshot=snapshot, stage=terminal_stage,
                    error=result_error or None,
                )

            statuses[variant_key] = status
            executed_order.append({"variant_key": variant_key})
            events.append(
                event_type="TERMINAL", scope="VARIANT",
                run_id=run_id, variant_key=variant_key,
                strategy_id=identity.strategy_id, stage=status, message=status,
            )

        if not token.is_cancelled:
            advance(BatchStage.COMPARING)

            # §27 — strict common-calendar comparison: only sub-runs whose
            # equity index EXACTLY equals the evaluation calendar participate.
            eval_dates = [
                d for d in calendar
                if request.evaluation_start_date <= d <= request.evaluation_end_date
            ]
            comparison_inputs: list[VariantComparisonInput] = []
            for plan in ordered:
                identity = plan["identity"]
                vk = identity.variant_key
                result = run_results.get(vk)
                if result is None:
                    continue
                has_invalid = any(
                    d.action.value == "INVALID" for d in result.decisions
                )
                comparison_inputs.append(VariantComparisonInput(
                    variant_key=vk,
                    strategy_id=identity.strategy_id,
                    run_id=run_ids.get(vk, ""),
                    status=statuses.get(vk, "UNKNOWN"),
                    equity=result.executed_equity,
                    decision_quality=result.quality_status,
                    has_invalid_action=has_invalid,
                ))

            outcome = build_comparison(
                comparison_inputs,
                evaluation_calendar=eval_dates,
                framework_implementation_hash=framework_implementation_hash(),
                data_snapshot_fingerprint=snapshot.fingerprint,
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
                "excluded": outcome.excluded,
                "quality_warnings": outcome.quality_warnings,
            }
            atomic_write_json(batch_dir / "reports.json", reports)

            if comparison_available:
                tmp = batch_dir / "comparison_equity.csv.tmp"
                outcome.equity_frame.to_csv(tmp)
                os.replace(str(tmp), str(batch_dir / "comparison_equity.csv"))
                import pandas as _pd

                tmp = batch_dir / "comparison_metrics.csv.tmp"
                _pd.DataFrame(outcome.metrics).T.to_csv(tmp)
                os.replace(str(tmp), str(batch_dir / "comparison_metrics.csv"))

            advance(BatchStage.WRITING_RESULTS)

        # §26 parent aggregation.
        if token.is_cancelled:
            final_stage = "CANCELED"
        else:
            values = set(statuses.values())
            if values <= {"SUCCEEDED"}:
                final_stage = "SUCCEEDED"
            elif "SUCCEEDED" in values:
                final_stage = "PARTIAL_SUCCEEDED"
            else:
                final_stage = "FAILED"
        resolved = self._persist_parent_terminal(
            batch_dir=batch_dir,
            batch_id=batch_id,
            events=events,
            final_stage=final_stage,
            statuses=statuses,
            run_ids=run_ids,
            executed_order=executed_order,
        )

        # §27 — manifest.json is the sole atomic publish point; only written
        # for non-cancelled batches that completed comparison.
        if not token.is_cancelled and final_stage in ("SUCCEEDED", "PARTIAL_SUCCEEDED"):
            from src.stockpred.fund_rotation.artifacts import compute_file_checksum

            artifact_names = [
                "request.json", "resolved_batch.json", "state.json",
                "events.jsonl", "data_snapshot.json", "reports.json",
                "comparison_equity.csv", "comparison_metrics.csv",
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
                "framework_implementation_hash": framework_implementation_hash(),
                "data_snapshot_fingerprint": snapshot.fingerprint,
                "variants": [
                    {
                        "variant_key": v["variant_key"],
                        "strategy_id": v.get("strategy_id", ""),
                        "run_id": v.get("run_id"),
                        "status": v.get("status"),
                    }
                    for v in resolved.get("variants", [])
                ],
                "files": files,
                "file_details": file_details,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            # manifest.json is the final mutation of the published batch.
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
        """Persist child state before appending the corresponding local event."""
        run_dir = self.runs_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        state = {
            "schema_version": "2",
            "stage": stage,
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
        atomic_write_json(run_dir / "state.json", state)

        self._append_child_event(
            run_dir,
            state,
            stage=stage,
            message=message,
            error=error,
        )

    def _publish_child_result(
        self,
        *,
        batch_id: str,
        request: StrategyBatchRequest,
        plan: dict[str, Any],
        snapshot,
        evaluation: EvaluationContext,
        result,
    ) -> None:
        """Publish one successful child through the common ArtifactPublisher."""
        import pandas as pd

        from src.stockpred.fund_rotation.artifacts import compute_file_checksum

        identity = plan["identity"]
        run_id = plan["run_id"]
        run_dir = self.runs_root / run_id
        publisher = ArtifactPublisher(run_dir)

        registered = self.catalog.require(identity.strategy_id)
        resolved_spec = {
            **identity.__dict__,
            "batch_id": batch_id,
            "run_id": run_id,
            "schema_version": request.schema_version,
            "mode": request.mode,
            "simulation_start_date": plan["simulation_start"],
            "evaluation_start_date": request.evaluation_start_date,
            "evaluation_end_date": request.evaluation_end_date,
            "execution": dict(request.execution),
            "framework_implementation_hash": framework_implementation_hash(),
            "data_snapshot_fingerprint": snapshot.fingerprint,
        }
        strategy_snapshot = {
            "strategy_id": identity.strategy_id,
            "implementation_hash": identity.implementation_hash,
            "source_files": list(registered.implementation_snapshot.source_files),
            "descriptor": asdict(registered.descriptor),
        }
        publisher.publish(StrategyArtifact(
            role="resolved_spec", media_type="application/json",
            payload=resolved_spec,
        ))
        publisher.publish(StrategyArtifact(
            role="strategy_snapshot", media_type="application/json",
            payload=strategy_snapshot,
        ))
        publisher.publish(StrategyArtifact(
            role="data_snapshot", media_type="application/json",
            payload=asdict(snapshot),
        ))
        publisher.publish(StrategyArtifact(
            role="evaluation_calendar", media_type="application/json",
            payload=[date.strftime("%Y%m%d") for date in evaluation.trading_dates],
        ))

        decision_rows = [
            {
                "decision_id": decision.decision_id,
                "signal_date": decision.signal_date,
                "action": decision.action.value,
                "target_weights": json.dumps(
                    dict(decision.target_weights), sort_keys=True,
                    ensure_ascii=False,
                ),
                "cash_weight": decision.cash_weight,
                "reason_code": decision.reason_code,
                "quality_status": decision.quality_status.value,
                "diagnostics": json.dumps(
                    dict(decision.diagnostics), sort_keys=True,
                    ensure_ascii=False, default=str,
                ),
            }
            for decision in result.decisions
        ]
        decisions = pd.DataFrame(decision_rows, columns=[
            "decision_id", "signal_date", "action", "target_weights",
            "cash_weight", "reason_code", "quality_status", "diagnostics",
        ])
        target_rows = [
            {"week_ending": date, "ts_code": code, "weight": weight}
            for date, targets in sorted(result.weekly_targets.items())
            for code, weight in sorted(targets.items())
        ]
        targets = pd.DataFrame(
            target_rows, columns=["week_ending", "ts_code", "weight"],
        )
        orders = pd.DataFrame(result.orders)
        if orders.empty:
            orders = pd.DataFrame(columns=["order_id", "ts_code", "status"])
        fills = pd.DataFrame(result.trade_events)
        if fills.empty:
            fills = pd.DataFrame(columns=["trade_date", "ts_code", "quantity"])
        positions = pd.DataFrame(_flatten_positions(result.positions_history))
        if positions.empty:
            positions = pd.DataFrame(columns=[
                "trade_date", "ts_code", "quantity", "market_value", "cash",
            ])
        equity = result.executed_equity.rename("strategy")
        metrics = {
            "strategy": dict(result.strategy_metrics),
            "quality_status": result.quality_status,
        }
        summary = {
            "mode": request.mode,
            "run_id": run_id,
            "strategy_id": identity.strategy_id,
            "variant_key": identity.variant_key,
            "quality_status": result.quality_status,
            "annual_return": result.strategy_metrics.get("annual_return", 0.0),
            "max_drawdown": result.strategy_metrics.get("max_drawdown", 0.0),
            "sharpe": result.strategy_metrics.get("sharpe", 0.0),
            "total_return": result.strategy_metrics.get("total_return", 0.0),
        }

        for role, media_type, payload in (
            ("target_decisions", "text/csv", decisions),
            ("targets", "text/csv", targets),
            ("orders", "text/csv", orders),
            ("fills", "text/csv", fills),
            ("positions", "text/csv", positions),
            ("equity", "text/csv", equity),
            ("metrics", "application/json", metrics),
            ("summary", "application/json", summary),
        ):
            publisher.publish(StrategyArtifact(
                role=role, media_type=media_type, payload=payload,
            ))
        if result.diagnostics is not None:
            for artifact in result.diagnostics.artifacts:
                publisher.publish(artifact, producer=identity.strategy_id)

        # Publication boundary: terminal state and terminal event become
        # immutable before the publisher indexes their checksums.
        self._record_child_stage(
            batch_id=batch_id, request=request, identity=identity,
            run_id=run_id, snapshot=snapshot, stage=ChildStage.SUCCEEDED.value,
            quality_status=result.quality_status,
        )
        publisher.index_external("state")
        publisher.index_external("events")
        artifact_index = publisher.artifact_index()
        file_details = {
            entry["file"]: {
                key: value
                for key, value in entry.items()
                if key in {"checksum", "rows", "schema_version", "encoding", "columns"}
            }
            for entry in artifact_index.values()
        }
        publisher.finalize(identity={
            "run_id": run_id,
            "batch_id": batch_id,
            "variant_key": identity.variant_key,
            "strategy_id": identity.strategy_id,
            "mode": request.mode,
            "quality_status": result.quality_status,
            "params_fingerprint": identity.resolved_config_hash,
            "data_snapshot_fingerprint": snapshot.fingerprint,
            "state_checksum": compute_file_checksum(run_dir / "state.json"),
            "file_details": file_details,
        })

    # ── recovery (§26.1) ──

    def recover_interrupted(self) -> list[str]:
        """Mark every non-terminal batch (and child run) FAILED_INTERRUPTED.

        Finished artifacts stay readable; nothing is auto-resumed.
        """
        recovered: list[str] = []
        root = self.persistence.batches_dir
        if not root.exists():
            return recovered
        for batch_dir in sorted(root.iterdir()):
            if batch_dir.name == "idempotency" or not batch_dir.is_dir():
                continue
            state_path = batch_dir / "state.json"
            if state_path.exists() and not (batch_dir / "manifest.json").exists():
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if detect_interrupted_state(state):
                    atomic_write_json(state_path, mark_state_interrupted(state))
                    BatchEventLog(batch_dir, batch_id=batch_dir.name).append(
                        event_type="TERMINAL",
                        scope="BATCH",
                        stage=BatchStage.FAILED_INTERRUPTED.value,
                        message=BatchStage.FAILED_INTERRUPTED.value,
                    )
                    recovered.append(batch_dir.name)

        if not self.runs_root.exists():
            return recovered
        for child_dir in sorted(self.runs_root.iterdir()):
            child_state_path = child_dir / "state.json"
            if (
                not child_dir.is_dir()
                or not child_state_path.exists()
                or (child_dir / "manifest.json").exists()
            ):
                continue
            child_state = json.loads(child_state_path.read_text(encoding="utf-8"))
            if not detect_interrupted_state(child_state):
                continue
            interrupted = mark_state_interrupted(child_state)
            atomic_write_json(child_state_path, interrupted)
            self._append_child_event(
                child_dir,
                interrupted,
                stage=ChildStage.FAILED_INTERRUPTED.value,
            )
        return recovered

    # ── helpers ──

    def _write_state(self, batch_dir: Path, stage: str, batch_id: str) -> None:
        atomic_write_json(batch_dir / "state.json", {
            "schema_version": "2",
            "stage": stage,
            "batch_id": batch_id,
            "mode": "RESEARCH_ONLY",
        })

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
                variant["variant_key"], ChildStage.CANCELED.value,
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

    @staticmethod
    def _append_child_event(
        child_dir: Path,
        state: dict[str, Any],
        *,
        stage: str,
        message: str | None = None,
        error: str | None = None,
    ) -> None:
        BatchEventLog(
            child_dir, batch_id=str(state["batch_id"]),
        ).append(
            event_type=(
                "TERMINAL"
                if ChildStage(stage) in CHILD_TERMINAL_STAGES
                else "VARIANT_STAGE"
            ),
            scope="VARIANT",
            run_id=str(state.get("run_id", child_dir.name)),
            variant_key=str(state["variant_key"]),
            strategy_id=str(state["strategy_id"]),
            stage=stage,
            message=message or stage,
            error=error,
        )

    def _write_resolved(self, batch_dir: Path, resolved: dict) -> None:
        atomic_write_json(batch_dir / "resolved_batch.json", resolved)

    def _read_resolved(self, batch_dir: Path) -> dict:
        path = batch_dir / "resolved_batch.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))


def _resolved_identity(batch_id, request, identities):
    from src.stockpred.fund_rotation.batch_persistence import ResolvedBatchIdentity

    return ResolvedBatchIdentity(
        batch_id=batch_id,
        schema_version=request.schema_version,
        mode=request.mode,
        catalog_version=catalog_identity_hash_from_identities(identities),
        framework_implementation_hash=framework_implementation_hash(),
        variants=tuple(identities),
    )


def catalog_identity_hash_from_identities(identities) -> str:
    canonical = json.dumps(
        [
            {
                "variant_key": i.variant_key,
                "implementation_hash": i.implementation_hash,
            }
            for i in identities
        ],
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _flatten_positions(history: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for snapshot in history:
        trade_date = snapshot.get("trade_date", "")
        for holding in snapshot.get("holdings", []):
            rows.append({
                "trade_date": trade_date,
                **holding,
                "cash": snapshot.get("cash", 0.0),
            })
    return rows
