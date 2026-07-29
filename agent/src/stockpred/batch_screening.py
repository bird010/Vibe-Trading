"""Evaluation-date coordination for Alpha Zoo batch screening."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pandas as pd

from backtest.stockpred_strategy.artifacts import write_screening_artifacts
from backtest.stockpred_strategy.runner import StockPredStrategyBacktestRunner, StrategyBacktestResult, StrategyScreeningSession
from src.stockpred.batch_data import BatchDataContext
from src.stockpred.batch_metrics import PhaseTimer, write_phase_metrics
from src.stockpred.contracts import DataSnapshotManifest
from src.stockpred.gateway import StockPredDataGateway
from src.stockpred.strategies.adapters import AlphaZooStrategyAdapter
from src.stockpred.strategies.contracts import StrategyBacktestConfig
from src.stockpred.strategies.panel import StockPredPanelBuilder


def _patch_cohort_metric_schema(run_dir: Path) -> None:
    req_path = run_dir / "req.json"
    if not req_path.is_file():
        return
    from src.stockpred.run_store import atomic_json

    req_data = json.loads(req_path.read_text(encoding="utf-8"))
    req_data.setdefault("context", {})["metric_schema_version"] = "signal_cohort_v1"
    atomic_json(req_path, req_data)

# Process pool workers: configurable via env var, default min(2, cpu_count)
_EVAL_PROCESS_WORKERS = int(os.environ.get("STOCKPRED_BATCH_WORKERS", min(2, os.cpu_count() or 2)))


class AlphaBatchScreeningCoordinator:
    """Run Alpha strategies each in a dedicated worker process.

    Each worker runs a strategy end-to-end: LanceDB reads, panel build,
    factor computation, trade simulation, artifact write.  The main
    process collects only per-strategy metrics dicts (hundreds of bytes).
    """

    def __init__(
        self,
        gateway: Any,
        registry: Any,
        configs: list[StrategyBacktestConfig],
        *,
        snapshot_digest: str,
        phase_timer: PhaseTimer | None = None,
        on_eval_done: Callable[[int, int, str], None] | None = None,
        on_strategy_done: Callable[[str, object, str | None], None] | None = None,
        run_id_by_strategy: dict[str, str] | None = None,
        evaluation_engine: str = "portfolio",
    ) -> None:
        if any(config.strategy_snapshot.descriptor.kind != "alpha_zoo" for config in configs):
            raise ValueError("AlphaBatchScreeningCoordinator only accepts alpha_zoo strategies")
        self.gateway = gateway
        self.registry = registry
        self.configs = configs
        self.phase_timer = phase_timer or PhaseTimer()
        self.on_eval_done = on_eval_done
        self.on_strategy_done = on_strategy_done
        self.run_id_by_strategy = run_id_by_strategy or {}
        self.evaluation_engine = evaluation_engine
        maximum = max((max(config.data_lookback_days, config.strategy_snapshot.descriptor.min_warmup_bars + 1) for config in configs), default=1)
        self.context = BatchDataContext(gateway, snapshot_digest, batch_max_lookback=maximum, phase_timer=self.phase_timer)
        self.timers = {config.strategy_snapshot.descriptor.id: PhaseTimer() for config in configs}

    def run(self) -> dict[str, StrategyBacktestResult | Exception]:
        _use_process_pool = hasattr(self.gateway, "manifest") and hasattr(self.gateway, "root")

        if _use_process_pool:
            return self._run_process_pool()
        else:
            return self._run_in_process()

    def _run_process_pool(self) -> dict[str, StrategyBacktestResult | Exception]:
        """One strategy per worker process — true parallelism for production."""
        gateway_manifest_dict = (
            self.gateway.manifest.model_dump(mode="json")
            if hasattr(self.gateway.manifest, "model_dump")
            else dict(self.gateway.manifest)
        )
        gateway_manifest_json_str = json.dumps(gateway_manifest_dict, ensure_ascii=False, default=str)

        tasks: list[dict[str, object]] = []
        for config in self.configs:
            strategy_id = config.strategy_snapshot.descriptor.id
            run_dir = Path(str(self.run_id_by_strategy.get(strategy_id, "")))
            tasks.append({
                "strategy_id": strategy_id,
                "config_json": config.model_dump_json(),
                "gateway_root": str(self.gateway.root),
                "gateway_manifest_json": gateway_manifest_json_str,
                "run_dir": str(run_dir),
                "evaluation_engine": self.evaluation_engine,
                "snapshot_digest": self.context.snapshot_digest,
            })

        n_workers = min(_EVAL_PROCESS_WORKERS, len(tasks)) if tasks else 1
        results: dict[str, StrategyBacktestResult | Exception] = {}

        with ProcessPoolExecutor(max_workers=n_workers) as executor:
            futures = {executor.submit(_strategy_worker, task): task for task in tasks}
            for future in as_completed(futures):
                task = futures[future]
                strategy_id = str(task["strategy_id"])
                try:
                    arena = future.result()
                except Exception as exc:
                    arena = {"strategy_id": strategy_id, "metrics": None, "error": f"worker crashed: {exc}"}
                if arena.get("error"):
                    results[strategy_id] = Exception(arena["error"])
                    if self.on_strategy_done:
                        run_id = self.run_id_by_strategy.get(strategy_id)
                        self.on_strategy_done(strategy_id, Exception(arena["error"]),
                                              Path(run_id).name if run_id else None)
                else:
                    metrics = arena.get("metrics") or {}
                    results[strategy_id] = _bare_result(strategy_id, metrics)
                    if self.on_strategy_done:
                        run_id = self.run_id_by_strategy.get(strategy_id)
                        self.on_strategy_done(strategy_id, metrics,
                                              Path(run_id).name if run_id else None)
                if self.on_eval_done:
                    done = sum(1 for v in results.values() if not isinstance(v, Exception))
                    # NOTE: In process-pool path, callback args are
                    # (completed_strategies, total_strategies, strategy_id).
                    self.on_eval_done(done, len(self.configs), strategy_id)

        return results

    def _run_in_process(self) -> dict[str, StrategyBacktestResult | Exception]:
        """Fallback: run strategies sequentially in the main process.

        Uses the shared ``BatchDataContext`` and ``phase_timer`` for caching
        and timing, matching the original ThreadPoolExecutor behaviour.
        """
        if self.evaluation_engine == "cohort":
            from backtest.stockpred.cohort.engine import CohortRunner, cohort_config_from_strategy_config

            results: dict[str, StrategyBacktestResult | Exception] = {}
            for config in self.configs:
                descriptor = config.strategy_snapshot.descriptor
                strategy_id = descriptor.id
                try:
                    strategy = AlphaZooStrategyAdapter(
                        self.registry,
                        StockPredPanelBuilder(self.gateway, data_lookback_days=config.data_lookback_days),
                        descriptor,
                    )
                    cohort_config = cohort_config_from_strategy_config(
                        config, data_snapshot_id=self.context.snapshot_digest,
                        run_dir=self.run_id_by_strategy.get(strategy_id, "."),
                    )
                    cohort_result = CohortRunner(gateway=self.gateway, strategy=strategy).run(cohort_config)
                    _patch_cohort_metric_schema(Path(self.run_id_by_strategy.get(strategy_id, ".")))
                    results[strategy_id] = _bare_result(strategy_id, cohort_result.metrics)
                    if self.on_strategy_done:
                        run_id = self.run_id_by_strategy.get(strategy_id)
                        self.on_strategy_done(strategy_id, cohort_result.metrics, Path(run_id).name if run_id else None)
                except Exception as exc:
                    results[strategy_id] = exc
                    if self.on_strategy_done:
                        run_id = self.run_id_by_strategy.get(strategy_id)
                        self.on_strategy_done(strategy_id, exc, Path(run_id).name if run_id else None)
            return results

        trade_dates = self.context.static_inputs().trade_dates
        sessions: list[tuple[StrategyBacktestConfig, StrategyScreeningSession]] = []
        for config in self.configs:
            descriptor = config.strategy_snapshot.descriptor
            strategy = AlphaZooStrategyAdapter(self.registry, None, descriptor)
            scheduled_dates = [d for d in trade_dates if config.start <= d <= config.end][::config.eval_step]
            sessions.append((config, StrategyScreeningSession(
                StockPredStrategyBacktestRunner(self.gateway, strategy), config,
                scheduled_dates=scheduled_dates, phase_timer=self.timers[descriptor.id],
            )))

        dates = sorted({d for _, s in sessions for d in s.scheduled_dates})
        results: dict[str, StrategyBacktestResult | Exception] = {}
        for i, eval_date in enumerate(dates, start=1):
            active = [(c, s) for c, s in sessions if eval_date in s.scheduled_dates]
            if not active:
                continue
            for config, session in active:
                descriptor = config.strategy_snapshot.descriptor
                try:
                    panel = self.context.panel_for_strategy(eval_date, descriptor, data_lookback_days=config.data_lookback_days)
                    session.evaluate(eval_date, panel)
                except Exception:
                    pass
            self.context.release_eval_date()
            if self.on_eval_done:
                # NOTE: In in-process path, callback args are
                # (date_index, total_dates, eval_date).
                self.on_eval_done(i, len(dates), eval_date)

        for config, session in sessions:
            strategy_id = config.strategy_snapshot.descriptor.id
            run_id = self.run_id_by_strategy.get(strategy_id)
            run_name = Path(run_id).name if run_id else None
            try:
                result = session.finalize()
                results[strategy_id] = result
                if self.on_strategy_done:
                    self.on_strategy_done(strategy_id, result.metrics, run_name)
            except Exception as exc:
                results[strategy_id] = exc
                if self.on_strategy_done:
                    self.on_strategy_done(strategy_id, exc, run_name)

        return results


def _strategy_worker(task: dict[str, object]) -> dict[str, object]:
    """Run one strategy end-to-end in a worker process.

    Opens its own LanceDB handles, creates a Registry, evaluates every
    scheduled date, calls session.finalize() (live DataFrame — no
    serialisation), writes artifacts, and returns only metrics.

    The main process never sees intermediate DataFrames.
    """
    from src.factors.registry import Registry

    try:
        config = StrategyBacktestConfig.model_validate_json(str(task["config_json"]))
        gateway_root = Path(str(task["gateway_root"]))
        gateway_manifest = DataSnapshotManifest.model_validate(
            json.loads(str(task["gateway_manifest_json"]))
        )
        run_dir = Path(str(task["run_dir"]))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {"strategy_id": str(task.get("strategy_id", "?")), "metrics": None, "error": f"invalid task: {exc}"}

    strategy_id = config.strategy_snapshot.descriptor.id
    phase_timer = PhaseTimer()
    evaluation_engine = str(task.get("evaluation_engine", "portfolio"))

    try:
        # ── setup ──
        gateway = StockPredDataGateway(gateway_root, gateway_manifest)
        gateway.set_phase_timer(phase_timer)
        registry = Registry()

        descriptor = config.strategy_snapshot.descriptor
        panel_builder = StockPredPanelBuilder(gateway, data_lookback_days=config.data_lookback_days)
        strategy = AlphaZooStrategyAdapter(registry, panel_builder, descriptor)

        # Branch: cohort engine vs legacy portfolio engine
        if evaluation_engine == "cohort":
            from backtest.stockpred.cohort.engine import CohortRunner, cohort_config_from_strategy_config

            with phase_timer.phase("execution"):
                cohort_config = cohort_config_from_strategy_config(
                    config, data_snapshot_id=str(task["snapshot_digest"]), run_dir=run_dir,
                )
                runner = CohortRunner(gateway=gateway, strategy=strategy)
                cohort_result = runner.run(cohort_config)

            with phase_timer.phase("artifact_write"):
                write_phase_metrics(run_dir, phase_timer, read_metrics=gateway.read_metrics())
                _patch_cohort_metric_schema(run_dir)

            return {"strategy_id": strategy_id, "metrics": cohort_result.metrics, "error": None}

        # Legacy portfolio path
        runner = StockPredStrategyBacktestRunner(gateway, strategy)
        trade_dates = gateway.trade_dates(config.start, config.end)
        scheduled_dates = trade_dates[::config.eval_step]
        session = StrategyScreeningSession(
            runner, config, scheduled_dates=scheduled_dates, phase_timer=phase_timer,
        )

        # ── evaluate: for each date, build panel → compute factor -> build targets ──
        panel_builder = StockPredPanelBuilder(gateway, data_lookback_days=config.data_lookback_days)
        for eval_date in scheduled_dates:
            try:
                with phase_timer.phase("data_load"):
                    panel = panel_builder.build(eval_date, descriptor)
                with phase_timer.phase("factor_compute"):
                    session.evaluate(eval_date, panel)
            except Exception:
                pass  # skip this eval_date for this strategy

        # ── execution: simulate trades (live DataFrame from session, no serialisation) ──
        with phase_timer.phase("execution"):
            result = session.finalize()

        # ── artifact_write ──
        with phase_timer.phase("artifact_write"):
            write_screening_artifacts(run_dir, result, gateway_manifest, config)
            write_phase_metrics(run_dir, phase_timer, read_metrics=gateway.read_metrics())

        return {"strategy_id": strategy_id, "metrics": result.metrics, "error": None}

    except Exception as exc:
        # Best-effort: write phase metrics even on failure
        try:
            write_phase_metrics(run_dir, phase_timer, read_metrics={})
        except Exception:
            pass
        return {"strategy_id": strategy_id, "metrics": None, "error": str(exc)}


def _bare_result(strategy_id: str, metrics: dict[str, float]) -> StrategyBacktestResult:
    """Return a minimal StrategyBacktestResult carrying only metrics."""
    return StrategyBacktestResult(
        strategy_id=strategy_id,
        eval_dates=[],
        signals=pd.DataFrame(),
        selected=pd.DataFrame(),
        trades=pd.DataFrame(),
        positions=pd.DataFrame(),
        equity=pd.DataFrame(),
        metrics=metrics,
    )
