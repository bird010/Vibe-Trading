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
from backtest.fund_rotation.strategies.registry import (
    default_fund_rotation_strategies,
)
from backtest.fund_rotation.universe import filter_etf_universe
from src.stockpred.fund_rotation.batch_models import (
    StrategyBatchRequest,
    canonical_payload_hash,
)
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
        catalog=None,
        metadata_loader: Callable[[], dict[str, Any]] | None = None,
        frames_loader: Callable[[str, str], tuple] | None = None,
        auto_start: bool = True,
    ) -> None:
        from backtest.fund_rotation.catalog import FundRotationStrategyCatalog

        self.persistence = BatchPersistence(Path(batches_dir))
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
            metadata = self.metadata_loader()
            plans, plan_summary = self._plan(request, identities, metadata)
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
            "metadata": metadata,
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
                    "run_id": None,
                    "status": "QUEUED",
                    "snapshot_fingerprint": metadata["fingerprint"],
                }
                for identity in identities
            ],
            "plan": plan_summary,
            "executed_order": [],
        }
        self.persistence.write_batch_request(
            batch_id,
            request_payload=request.model_dump(mode="json"),
            identity=_resolved_identity(batch_id, request, identities, metadata),
        )
        # The extended resolved document (statuses/plan/executed_order) owns
        # resolved_batch.json from here on.
        self._write_resolved(batch_dir, resolved)
        self._write_state(batch_dir, "QUEUED", batch_id)

        if self.auto_start:
            self.executor.submit(self._safe_execute, batch_id)
        return {"batch_id": batch_id, "status": "QUEUED"}

    # ── planning (§22: calendar fixed, no market scan) ──

    def _plan(self, request, identities, metadata):
        calendar = sorted(str(d) for d in metadata["trading_dates"])
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

    # ── execution ──

    def run_batch_sync(self, batch_id: str) -> None:
        """Run a prepared batch on the calling thread (tests / foreground)."""
        self._execute_batch(batch_id)

    def cancel_batch(self, batch_id: str) -> bool:
        token = self._tokens.get(batch_id)
        if token is None:
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
        metadata = prepared["metadata"]
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

        # Shared snapshot: one read for ALL variants (fixed versions upstream).
        data_start = min(p["simulation_start"] for p in plans)
        try:
            fund_daily, fund_adj, dim_fund = self.frames_loader(
                data_start, request.evaluation_end_date,
            )
        except Exception as exc:
            events.append(
                event_type="ERROR", scope="BATCH",
                error=f"shared snapshot failed: {exc}",
            )
            self._fail_batch(
                batch_id, stage="SNAPSHOTTING_DATA",
                error=f"shared snapshot failed: {exc}",
            )
            events.append(event_type="TERMINAL", scope="BATCH", stage=None)
            return

        calendar = sorted(str(d) for d in metadata["trading_dates"])
        pool = filter_etf_universe(dim_fund)
        from src.stockpred.fund_rotation.data_snapshot import PinnedFundDataSnapshot

        snapshot = PinnedFundDataSnapshot(
            fund_version=0, fund_adj_version=0, dim_version=0,
            universe_codes=tuple(sorted(pool["ts_code"].astype(str))),
            trading_dates=tuple(calendar),
            fingerprint=metadata["fingerprint"],
        )
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
        run_ids: dict[str, str] = {}
        runs_dir = batch_dir / "runs"

        for plan in ordered:
            identity = plan["identity"]
            variant_key = identity.variant_key
            run_id = uuid.uuid4().hex[:12]
            run_ids[variant_key] = run_id
            if token.is_cancelled:
                # §26.1 — unstarted variants never launch.
                statuses[variant_key] = "CANCELED"
                executed_order.append({"variant_key": variant_key})
                events.append(
                    event_type="TERMINAL", scope="VARIANT",
                    run_id=run_id, variant_key=variant_key,
                    strategy_id=identity.strategy_id, stage=None,
                    message="CANCELED before start",
                )
                continue

            events.append(
                event_type="VARIANT_STAGE", scope="VARIANT",
                run_id=run_id, variant_key=variant_key,
                strategy_id=identity.strategy_id, stage="PREPARING_DATA",
            )
            try:
                result = runner.run(
                    strategy=plan["strategy"],
                    config=plan["config"],
                    snapshot=snapshot,
                    evaluation=evaluation,
                    execution=execution_config,
                    cancellation=token,
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

            statuses[variant_key] = status
            executed_order.append({"variant_key": variant_key})
            child_dir = runs_dir / _safe_dirname(variant_key)
            child_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_json(child_dir / "state.json", {
                "schema_version": "2",
                "stage": status,
                "batch_id": batch_id,
                "run_id": run_id,
                "variant_key": variant_key,
                "strategy_id": identity.strategy_id,
                "mode": request.mode,
            })
            events.append(
                event_type="TERMINAL", scope="VARIANT",
                run_id=run_id, variant_key=variant_key,
                strategy_id=identity.strategy_id, stage=None, message=status,
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
                data_snapshot_fingerprint=metadata["fingerprint"],
            )

            # Write comparison artifacts to the parent batch directory.
            reports = {
                "contract": {
                    "fingerprint": outcome.contract_fingerprint,
                    "components": outcome.contract_components,
                },
                "ranking": outcome.ranking,
                "excluded": outcome.excluded,
                "quality_warnings": outcome.quality_warnings,
            }
            atomic_write_json(batch_dir / "reports.json", reports)

            if not outcome.equity_frame.empty:
                tmp = batch_dir / "comparison_equity.csv.tmp"
                outcome.equity_frame.to_csv(tmp)
                os.replace(str(tmp), str(batch_dir / "comparison_equity.csv"))
            if outcome.metrics:
                import pandas as _pd

                tmp = batch_dir / "comparison_metrics.csv.tmp"
                _pd.DataFrame(outcome.metrics).T.to_csv(tmp)
                os.replace(str(tmp), str(batch_dir / "comparison_metrics.csv"))
            atomic_write_json(batch_dir / "data_snapshot.json", {
                "fingerprint": metadata["fingerprint"],
                "universe_size": len(snapshot.universe_codes),
                "trading_dates_count": len(snapshot.trading_dates),
                "evaluation_start": request.evaluation_start_date,
                "evaluation_end": request.evaluation_end_date,
                "evaluation_dates": len(eval_dates),
            })

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
        self._write_state(batch_dir, final_stage, batch_id)

        resolved = self._read_resolved(batch_dir)
        for variant in resolved.get("variants", []):
            variant["status"] = statuses.get(variant["variant_key"], "CANCELED")
            variant["run_id"] = run_ids.get(variant["variant_key"])
        resolved["executed_order"] = executed_order
        self._write_resolved(batch_dir, resolved)

        # §27 — manifest.json is the sole atomic publish point; only written
        # for non-cancelled batches that completed comparison.
        if not token.is_cancelled and final_stage in ("SUCCEEDED", "PARTIAL_SUCCEEDED"):
            from datetime import datetime, timezone

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
                "data_snapshot_fingerprint": metadata["fingerprint"],
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
            atomic_write_json(batch_dir / "manifest.json", manifest)

        events.append(
            event_type="TERMINAL", scope="BATCH", stage=None,
            message=final_stage,
        )

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
            if state_path.exists():
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if detect_interrupted_state(state):
                    atomic_write_json(state_path, mark_state_interrupted(state))
                    recovered.append(batch_dir.name)
            runs_dir = batch_dir / "runs"
            if runs_dir.exists():
                for child_dir in sorted(runs_dir.iterdir()):
                    child_state_path = child_dir / "state.json"
                    if not child_state_path.exists():
                        continue
                    child_state = json.loads(
                        child_state_path.read_text(encoding="utf-8")
                    )
                    if detect_interrupted_state(child_state):
                        atomic_write_json(
                            child_state_path, mark_state_interrupted(child_state),
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

    def _fail_batch(self, batch_id: str, *, stage: str, error: str) -> None:
        batch_dir = self.persistence.batch_dir(batch_id)
        batch_dir.mkdir(parents=True, exist_ok=True)
        self._write_state(batch_dir, "FAILED", batch_id)
        state = json.loads((batch_dir / "state.json").read_text(encoding="utf-8"))
        state["failed_stage"] = stage
        state["error"] = error
        atomic_write_json(batch_dir / "state.json", state)

    def _write_resolved(self, batch_dir: Path, resolved: dict) -> None:
        atomic_write_json(batch_dir / "resolved_batch.json", resolved)

    def _read_resolved(self, batch_dir: Path) -> dict:
        path = batch_dir / "resolved_batch.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))


def _resolved_identity(batch_id, request, identities, metadata):
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


def _safe_dirname(variant_key: str) -> str:
    return variant_key.replace("@", "_at_").replace("/", "_")
