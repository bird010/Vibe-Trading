"""Real report executor used by unified StockPred strategy batches."""

from __future__ import annotations

import errno
import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from backtest.stockpred_strategy.artifacts import write_screening_artifacts
from backtest.stockpred_strategy.runner import StockPredStrategyBacktestRunner
from src.factors.registry import Registry
from src.stockpred.contracts import DataSnapshotManifest, ModelSnapshot, StockPredDataError
from src.stockpred.gateway import StockPredDataGateway
from src.stockpred.batch_screening import AlphaBatchScreeningCoordinator
from src.stockpred.batch_metrics import PhaseTimer, write_phase_metrics
from src.stockpred.graph.service import GraphSignalService
from src.stockpred.snapshot import build_snapshot
from src.stockpred.strategy_detail import materialize_strategy_detail
from src.stockpred.strategy_run_store import StrategyRunStore
from src.stockpred.strategies.adapters import AlphaZooStrategyAdapter, GraphStrategyAdapter
from src.stockpred.strategies.contracts import StrategyBacktestConfig, StrategyBatchRequest, StrategyDescriptor
from src.stockpred.strategies.panel import StockPredPanelBuilder
from src.stockpred.strategies.snapshot import snapshot_strategy


class StrategyReportExecutor:
    def __init__(self, runs_root: Path, stockpred_root: Path, registry: Registry | None = None) -> None:
        self.runs = StrategyRunStore(runs_root)
        self.stockpred_root = Path(stockpred_root)
        self.registry = registry or Registry()
        self.repository_root = Path(__file__).resolve().parents[2]
        self._manifests: dict[str, object] = {}

    def __call__(self, descriptor: StrategyDescriptor, request: StrategyBatchRequest, batch_id: str, comparison_key: str, manifest: DataSnapshotManifest | dict[str, object] | None = None) -> tuple[str, dict[str, float]]:
        manifest = self._manifest(request, batch_id, manifest)
        snapshot = snapshot_strategy(descriptor, self._sources(descriptor), repository_root=self.repository_root)
        config = StrategyBacktestConfig(
            strategy_snapshot=snapshot, start=request.start, end=request.end, batch_id=batch_id,
            comparison_key=comparison_key, mode=request.mode, top_n=request.top_n, eval_step=request.eval_step,
            forward_days=request.forward_days, portfolio_capital=request.portfolio_capital,
            max_participation=request.max_participation,
        )
        run_dir = self.runs.create(config, parent=f"strategy_batches/{config.batch_id}")
        timer = PhaseTimer()
        try:
            self.runs.transition(run_dir, "VALIDATING")
            with timer.phase("data_load"):
                gateway = StockPredDataGateway(self.stockpred_root, manifest)

            # Branch: cohort engine vs legacy portfolio engine
            if request.evaluation_engine == "cohort":
                return self._run_cohort(descriptor, request, config, run_dir, gateway, timer)

            with timer.phase("panel_build"):
                strategy = self._strategy(descriptor, gateway)
            self.runs.transition(run_dir, "RUNNING")
            with timer.phase("execution"):
                result = StockPredStrategyBacktestRunner(gateway, strategy).run(config)
            self.runs.transition(run_dir, "FINALIZING")
            with timer.phase("artifact_write"):
                write_screening_artifacts(run_dir, result, manifest, config)
                write_phase_metrics(run_dir, timer, read_metrics=gateway.read_metrics())
            self.runs.transition(run_dir, "SUCCEEDED")
            return run_dir.name, result.metrics
        except StockPredDataError as exc:
            self.runs.fail(run_dir, error_code=exc.code, reason=str(exc))
            raise
        except Exception as exc:
            code = "STOCKPRED_TRANSIENT_IO" if self._is_transient_io(exc) else "STOCKPRED_INTERNAL_ERROR"
            self.runs.fail(run_dir, error_code=code, reason="internal error; see server logs")
            raise StockPredDataError(code, str(exc)) from exc

    def _run_cohort(self, descriptor: StrategyDescriptor, request: StrategyBatchRequest, config: StrategyBacktestConfig, run_dir: Path, gateway: Any, timer: PhaseTimer) -> tuple[str, dict[str, float]]:
        """Run using the new Cohort evaluation engine."""
        from backtest.stockpred.cohort.engine import CohortRunner, cohort_config_from_strategy_config
        from src.stockpred.run_store import atomic_json

        self.runs.transition(run_dir, "RUNNING")
        with timer.phase("execution"):
            strategy = self._strategy(descriptor, gateway)
            # Resolve data_snapshot_id from gateway manifest
            manifest_obj = getattr(gateway, 'manifest', None)
            snapshot_id = str(getattr(manifest_obj, 'as_of', '')) if manifest_obj is not None else ''
            cohort_config = cohort_config_from_strategy_config(
                config, data_snapshot_id=snapshot_id, run_dir=run_dir,
            )
            runner = CohortRunner(gateway=gateway, strategy=strategy)
            result = runner.run(cohort_config)

        self.runs.transition(run_dir, "FINALIZING")
        with timer.phase("artifact_write"):
            write_phase_metrics(run_dir, timer, read_metrics=gateway.read_metrics() if hasattr(gateway, 'read_metrics') else {})
            # Patch run context with metric_schema_version for frontend routing
            req_path = run_dir / "req.json"
            if req_path.is_file():
                import json as _json
                req_data = _json.loads(req_path.read_text(encoding="utf-8"))
                req_data.setdefault("context", {})["metric_schema_version"] = "signal_cohort_v1"
                atomic_json(req_path, req_data)
        self.runs.transition(run_dir, "SUCCEEDED")
        return run_dir.name, result.metrics

    def manifest_for(self, request: StrategyBatchRequest):
        return self._manifest(request, f"prepared:{request.start}:{request.end}")

    def materialize_detail(self, run_id: str, manifest: DataSnapshotManifest | dict[str, object] | None) -> None:
        if manifest is None:
            raise StockPredDataError("STOCKPRED_DETAIL_SNAPSHOT_MISSING", "batch snapshot is missing")
        run_dir = self.runs.require(run_id)
        gateway = StockPredDataGateway(self.stockpred_root, DataSnapshotManifest.model_validate(manifest))
        materialize_strategy_detail(run_dir, gateway)

    def run_alpha_batch(self, descriptors: list[StrategyDescriptor], request: StrategyBatchRequest, batch_id: str, comparison_key: str, manifest: DataSnapshotManifest | dict[str, object] | None = None, *, on_eval_done: Callable[[int, int, str], None] | None = None, on_strategy_done: Callable[[str, object, str | None], None] | None = None) -> dict[str, tuple[str, dict[str, float]] | Exception]:
        if any(descriptor.kind != "alpha_zoo" for descriptor in descriptors):
            raise ValueError("run_alpha_batch only accepts alpha_zoo strategies")
        manifest = self._manifest(request, batch_id, manifest)
        batch_timer = PhaseTimer()
        gateway = StockPredDataGateway(self.stockpred_root, manifest)
        set_phase_timer = getattr(gateway, "set_phase_timer", None)
        if callable(set_phase_timer):
            set_phase_timer(batch_timer)
        prepared: list[tuple[StrategyBacktestConfig, Path]] = []
        results: dict[str, tuple[str, dict[str, float]] | Exception] = {}
        for descriptor in descriptors:
            run_dir: Path | None = None
            try:
                snapshot = snapshot_strategy(descriptor, self._sources(descriptor), repository_root=self.repository_root)
                config = StrategyBacktestConfig(
                    strategy_snapshot=snapshot, start=request.start, end=request.end, batch_id=batch_id,
                    comparison_key=comparison_key, mode=request.mode, top_n=request.top_n, eval_step=request.eval_step,
                    forward_days=request.forward_days, portfolio_capital=request.portfolio_capital,
                    max_participation=request.max_participation,
                )
                run_dir = self.runs.create(config, parent=f"strategy_batches/{batch_id}")
                self.runs.transition(run_dir, "VALIDATING")
                self.runs.transition(run_dir, "RUNNING")
                prepared.append((config, run_dir))
            except Exception as exc:
                if run_dir is not None:
                    code = "STOCKPRED_TRANSIENT_IO" if self._is_transient_io(exc) else "STOCKPRED_INTERNAL_ERROR"
                    self.runs.fail(run_dir, error_code=code, reason="internal error; see server logs")
                results[descriptor.id] = exc
                if on_strategy_done is not None:
                    on_strategy_done(descriptor.id, exc, None)
        if not prepared:
            return results
        run_id_by_strategy: dict[str, str] = {config.strategy_snapshot.descriptor.id: str(run_dir) for config, run_dir in prepared}
        payload = manifest.model_dump(mode="json") if hasattr(manifest, "model_dump") else str(manifest)
        digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        try:
            coordinator = AlphaBatchScreeningCoordinator(
                gateway,
                self.registry,
                [config for config, _ in prepared],
                snapshot_digest=digest,
                phase_timer=batch_timer,
                on_eval_done=on_eval_done,
                on_strategy_done=on_strategy_done,
                run_id_by_strategy=run_id_by_strategy,
                evaluation_engine=request.evaluation_engine,
            )
            outcomes = coordinator.run()
        except Exception as exc:
            outcomes = {config.strategy_snapshot.descriptor.id: exc for config, _ in prepared}
        for config, run_dir in prepared:
            strategy_id = config.strategy_snapshot.descriptor.id
            outcome = outcomes[strategy_id]
            if isinstance(outcome, Exception):
                code = outcome.code if isinstance(outcome, StockPredDataError) else "STOCKPRED_INTERNAL_ERROR"
                self.runs.fail(run_dir, error_code=code, reason=str(outcome))
                results[strategy_id] = outcome
                continue
            try:
                # Worker already wrote artifacts + per-strategy phase_metrics
                self.runs.transition(run_dir, "SUCCEEDED")
                try:
                    materialize_strategy_detail(run_dir, gateway)
                except Exception:
                    pass
                results[strategy_id] = (run_dir.name, outcome.metrics)
            except Exception as exc:
                code = "STOCKPRED_TRANSIENT_IO" if self._is_transient_io(exc) else "STOCKPRED_INTERNAL_ERROR"
                self.runs.fail(run_dir, error_code=code, reason="internal error; see server logs")
                results[strategy_id] = StockPredDataError(code, str(exc))
        return results

    def _manifest(self, request: StrategyBatchRequest, batch_id: str, persisted: DataSnapshotManifest | dict[str, object] | None = None):
        key = request.end
        if persisted is not None:
            manifest = DataSnapshotManifest.model_validate(persisted)
            self._manifests[key] = manifest
            return manifest
        if key not in self._manifests:
            as_of = datetime.strptime(request.end, "%Y%m%d").replace(hour=15, tzinfo=ZoneInfo("Asia/Taipei"))
            self._manifests[key] = build_snapshot(self.stockpred_root, as_of=as_of, model=ModelSnapshot(id="stockpred-strategy", version="v1", config_sha256="0" * 64))
        return self._manifests[key]

    @staticmethod
    def _is_transient_io(exc: Exception) -> bool:
        return isinstance(exc, (TimeoutError, ConnectionResetError)) or getattr(exc, "errno", None) in {
            errno.EAGAIN,
            errno.ETIMEDOUT,
            errno.ECONNRESET,
            errno.EBUSY,
        }

    def _strategy(self, descriptor: StrategyDescriptor, gateway: StockPredDataGateway):
        if descriptor.kind == "graph":
            return GraphStrategyAdapter(GraphSignalService(gateway))
        return AlphaZooStrategyAdapter(self.registry, StockPredPanelBuilder(gateway), descriptor)

    def _sources(self, descriptor: StrategyDescriptor) -> dict[str, Path]:
        base = self.repository_root / "src"
        common = {"factors/base.py": base / "factors" / "base.py", "factors/registry.py": base / "factors" / "registry.py"}
        if descriptor.kind == "graph":
            return {**common, "stockpred/graph/service.py": base / "stockpred" / "graph" / "service.py"}
        alpha = self.registry.get(descriptor.id)
        return {**common, f"factors/zoo/{alpha.zoo}/{descriptor.id.rsplit('_', 1)[-1]}.py": self.registry._py_paths[descriptor.id]}
