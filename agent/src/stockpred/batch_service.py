"""Batch orchestration for comparable StockPred strategy reports."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from src.stockpred.batch_store import BatchLockUnavailableError, StockPredBatchStore
from src.stockpred.contracts import StockPredDataError
from src.stockpred.strategies.contracts import StrategyBatchRequest, StrategyDescriptor


RunOne = Callable[..., tuple[str, dict[str, float]]]


class BatchLeaseUnavailableError(RuntimeError):
    """Raised when a concurrent worker owns the batch's operating-system lock."""


class StockPredStrategyBatchService:
    def __init__(self, store: StockPredBatchStore, catalog: Any, run_one: RunOne) -> None:
        self.store = store
        self.catalog = catalog
        self.run_one = run_one

    def run(self, request: StrategyBatchRequest) -> str:
        batch_id = self.reserve(request)
        return self.execute(batch_id)

    def reserve(self, request: StrategyBatchRequest) -> str:
        descriptors = self._select(request)
        manifest = getattr(self.run_one, "manifest_for", lambda _: None)(request)
        comparison_key = self._comparison_key(request, manifest)
        batch_id = self.store.create(request, descriptors, comparison_key=comparison_key)
        if manifest is not None:
            self.store.attach_manifest(batch_id, manifest)
        return batch_id

    def reserve_idempotent(self, request: StrategyBatchRequest, *, idempotency_key: str) -> tuple[str, bool]:
        """Reserve a batch with idempotency guarantee.

        Returns (batch_id, created). If the key was already used, returns the
        existing batch_id with created=False.
        """
        # First create a complete candidate batch
        candidate_id = self.reserve(request)
        # Try to atomically claim the idempotency key
        existing_id = self.store.claim_idempotency_key(idempotency_key, candidate_id)
        if existing_id is not None:
            # Key already claimed - release our candidate and return existing
            self.store.release_candidate(candidate_id)
            return existing_id, False
        return candidate_id, True

    def execute(self, batch_id: str, *, resume: bool = False) -> str:
        try:
            with self.store.batch_lock(batch_id):
                request = self.store.request(batch_id)
                comparison_key = self.store.comparison_key(batch_id)
                manifest = self.store.manifest(batch_id)
                strategy_ids = self.store.resume_candidates(batch_id) if resume else self.store.pending_candidates(batch_id)
                self.store.start_screening(batch_id)
                descriptors = [self.catalog.require(strategy_id) for strategy_id in strategy_ids]
                alpha = [descriptor for descriptor in descriptors if descriptor.kind == "alpha_zoo"]
                if len(alpha) > 1 and callable(getattr(self.run_one, "run_alpha_batch", None)):
                    self._run_alpha_batch(alpha, request, batch_id, comparison_key, manifest)
                    descriptors = [descriptor for descriptor in descriptors if descriptor.kind != "alpha_zoo"]
                for descriptor in descriptors:
                    try:
                        self.store.heartbeat(batch_id, descriptor.id)
                        run_id, metrics = self._run_with_retry(descriptor, request, batch_id, comparison_key, manifest)
                    except Exception as exc:  # Each strategy must fail independently.
                        error_class = "transient_io" if self._is_transient_io(exc) else "deterministic"
                        self.store.finish_report(
                            batch_id,
                            descriptor.id,
                            run_id=None,
                            status="failed",
                            reason=str(exc),
                            error_class=error_class,
                            attempt=self.store.attempt(batch_id, descriptor.id),
                        )
                    else:
                        self.store.finish_report(batch_id, descriptor.id, run_id=run_id, status="success", metrics=metrics)
                self.store.complete(batch_id)
        except BatchLockUnavailableError as exc:
            raise BatchLeaseUnavailableError("batch lease unavailable") from exc
        return batch_id

    def scan_stalled(self, *, stale_after_seconds: float) -> list[str]:
        return self.store.mark_expired_stalled(now=datetime.now(timezone.utc), stale_after_seconds=stale_after_seconds)

    def _run_alpha_batch(self, descriptors: list[StrategyDescriptor], request: StrategyBatchRequest, batch_id: str, comparison_key: str, manifest: dict[str, Any] | None) -> None:
        pending = list(descriptors)
        while pending:
            active: list[StrategyDescriptor] = []
            attempts: dict[str, int] = {}
            reported: set[str] = set()
            success_run_ids: dict[str, str] = {}
            transient_failures: dict[str, Exception] = {}
            for descriptor in pending:
                if self.store.attempt(batch_id, descriptor.id) >= 2:
                    self.store.finish_report(batch_id, descriptor.id, run_id=None, status="failed", reason="transient I/O retry attempts exhausted", error_class="transient_io", attempt=2)
                    continue
                self.store.heartbeat(batch_id, descriptor.id)
                attempts[descriptor.id] = self.store.start_attempt(batch_id, descriptor.id)
                active.append(descriptor)
            if not active:
                return

            def on_eval_done(_completed: int, _total: int, _eval_date: str) -> None:
                try:
                    self.store.heartbeat(batch_id)
                except Exception:
                    pass

            def on_strategy_done(strategy_id: str, outcome: object, run_id: str | None) -> None:
                try:
                    if isinstance(outcome, Exception):
                        if self._is_transient_io(outcome):
                            transient_failures[strategy_id] = outcome
                            return
                        self.store.finish_report(batch_id, strategy_id, run_id=None, status="failed", reason=str(outcome), error_class="deterministic", attempt=attempts.get(strategy_id))
                    else:
                        metrics = outcome if isinstance(outcome, dict) else {}
                        self.store.finish_report(batch_id, strategy_id, run_id=run_id, status="success", metrics=metrics, attempt=attempts.get(strategy_id))
                        if run_id:
                            success_run_ids[strategy_id] = run_id
                    reported.add(strategy_id)
                except Exception:
                    pass  # fallback loop will handle unreported strategies

            try:
                results = self.run_one.run_alpha_batch(active, request, batch_id, comparison_key, manifest, on_eval_done=on_eval_done, on_strategy_done=on_strategy_done)
            except Exception as exc:
                for descriptor in active:
                    if descriptor.id not in reported:
                        self.store.finish_report(batch_id, descriptor.id, run_id=None, status="failed", reason=str(exc), error_class="deterministic", attempt=attempts.get(descriptor.id))
                return
            retry: list[StrategyDescriptor] = []
            for descriptor in active:
                if descriptor.id in reported:
                    continue
                if descriptor.id in transient_failures:
                    outcome_exc = transient_failures[descriptor.id]
                    if attempts[descriptor.id] < 2:
                        self.store.record_transient_failure(batch_id, descriptor.id, reason=str(outcome_exc), attempt=attempts[descriptor.id])
                        retry.append(descriptor)
                    else:
                        self.store.finish_report(batch_id, descriptor.id, run_id=None, status="failed", reason=str(outcome_exc), error_class="transient_io", attempt=attempts[descriptor.id])
                    continue
                outcome = results.get(descriptor.id)
                if outcome is None:
                    self.store.finish_report(batch_id, descriptor.id, run_id=None, status="failed", reason="strategy result is missing (unexpected)", error_class="deterministic", attempt=attempts.get(descriptor.id))
                elif isinstance(outcome, Exception):
                    if self._is_transient_io(outcome) and attempts[descriptor.id] < 2:
                        self.store.record_transient_failure(batch_id, descriptor.id, reason=str(outcome), attempt=attempts[descriptor.id])
                        retry.append(descriptor)
                    else:
                        error_class = "transient_io" if self._is_transient_io(outcome) else "deterministic"
                        self.store.finish_report(batch_id, descriptor.id, run_id=None, status="failed", reason=str(outcome), error_class=error_class, attempt=attempts.get(descriptor.id))
                else:
                    run_id, metrics = outcome
                    self.store.finish_report(batch_id, descriptor.id, run_id=run_id, status="success", metrics=metrics, attempt=attempts.get(descriptor.id))
                    success_run_ids[descriptor.id] = run_id
            # finish_detail for all successful strategies (detail dir created by run_alpha_batch)
            for sid, rid in success_run_ids.items():
                try:
                    if (self.store.root / batch_id / rid / "detail").is_dir():
                        self.store.finish_detail(batch_id, sid, status="success")
                except Exception:
                    pass
            pending = retry

    def _run_with_retry(
        self,
        descriptor: StrategyDescriptor,
        request: StrategyBatchRequest,
        batch_id: str,
        comparison_key: str,
        manifest: dict[str, Any] | None,
    ) -> tuple[str, dict[str, float]]:
        while True:
            if self.store.attempt(batch_id, descriptor.id) >= 2:
                raise StockPredDataError("STOCKPRED_TRANSIENT_IO", "transient I/O retry attempts exhausted")
            attempt = self.store.start_attempt(batch_id, descriptor.id)
            try:
                return self._run_one(descriptor, request, batch_id, comparison_key, manifest)
            except Exception as exc:
                if not self._is_transient_io(exc):
                    raise
                self.store.record_transient_failure(batch_id, descriptor.id, reason=str(exc), attempt=attempt)
                if attempt >= 2:
                    raise
            self.store.heartbeat(batch_id, descriptor.id)

    def _run_one(
        self,
        descriptor: StrategyDescriptor,
        request: StrategyBatchRequest,
        batch_id: str,
        comparison_key: str,
        manifest: dict[str, Any] | None,
    ) -> tuple[str, dict[str, float]]:
        if hasattr(self.run_one, "manifest_for"):
            return self.run_one(descriptor, request, batch_id, comparison_key, manifest)
        return self.run_one(descriptor, request, batch_id, comparison_key)

    @staticmethod
    def _is_transient_io(exc: Exception) -> bool:
        return isinstance(exc, StockPredDataError) and exc.code == "STOCKPRED_TRANSIENT_IO"

    def _select(self, request: StrategyBatchRequest) -> list[StrategyDescriptor]:
        if request.select_all:
            return list(self.catalog.list())
        return [self.catalog.require(strategy_id) for strategy_id in request.strategy_ids]

    @staticmethod
    def _comparison_key(request: StrategyBatchRequest, manifest: Any = None) -> str:
        snapshot = manifest.model_dump(mode="json") if hasattr(manifest, "model_dump") else manifest
        payload = json.dumps({"request": request.model_dump(mode="json"), "data_snapshot": snapshot}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
