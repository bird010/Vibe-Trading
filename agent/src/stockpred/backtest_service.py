"""Unified application service for StockPred Graph backtests."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from backtest.stockpred_graph.artifacts import write_graph_artifacts
from backtest.stockpred_graph.runner import ProgressCallback
from src.stockpred.contracts import DataSnapshotManifest, StockPredDataError
from src.stockpred.graph.backtest_config import GraphBacktestConfig
from src.stockpred.parity import compare_backtest_bundle
from src.stockpred.run_store import StockPredRunStore, atomic_json


logger = logging.getLogger(__name__)
SnapshotFactory = Callable[[GraphBacktestConfig], DataSnapshotManifest]


class GraphBacktestService:
    def __init__(
        self,
        runs_root: Path,
        runner: Any,
        snapshot_factory: SnapshotFactory,
    ) -> None:
        self.store = StockPredRunStore(runs_root)
        self.runner = runner
        self.snapshot_factory = snapshot_factory

    def reserve(self, config: GraphBacktestConfig) -> str:
        return self.store.create(config).name

    def execute(
        self,
        run_id: str,
        on_progress: ProgressCallback | None = None,
    ) -> str:
        run_dir = self.store.require(run_id)
        config = self.store.load_config(run_dir)

        def persist_progress(done: int, total: int, eval_date: str) -> None:
            self.store.progress(
                run_dir,
                done=done,
                total=total,
                eval_date=eval_date,
            )
            if on_progress is not None:
                on_progress(done, total, eval_date)

        return self._execute(run_dir, config, persist_progress)

    def run(
        self,
        config: GraphBacktestConfig,
        on_progress: ProgressCallback | None = None,
    ) -> str:
        run_id = self.reserve(config)
        return self.execute(run_id, on_progress)

    def _execute(
        self,
        run_dir: Path,
        config: GraphBacktestConfig,
        on_progress: ProgressCallback | None,
    ) -> str:
        try:
            self.store.transition(run_dir, "VALIDATING")
            manifest = self.snapshot_factory(config)
            atomic_json(
                run_dir / "data_snapshot.json",
                manifest.model_dump(mode="json"),
            )
            self.store.transition(run_dir, "RUNNING")
            runner = self.runner(manifest) if callable(self.runner) else self.runner
            result = runner.run(config, on_progress=on_progress)
            parity_report = (
                compare_backtest_bundle(Path(config.parity_reference), result)
                if config.parity_reference
                else None
            )
            self.store.transition(run_dir, "FINALIZING")
            write_graph_artifacts(
                run_dir,
                result,
                manifest,
                config,
                parity_report=parity_report,
            )
            if parity_report is not None and not parity_report.passed:
                raise StockPredDataError(
                    "STOCKPRED_PARITY_FAILED",
                    parity_report.summary,
                )
            self.store.transition(run_dir, "SUCCEEDED")
        except StockPredDataError as exc:
            self.store.fail(run_dir, error_code=exc.code, reason=str(exc))
        except Exception:
            logger.exception("StockPred graph backtest crashed (run=%s)", run_dir.name)
            self.store.fail(
                run_dir,
                error_code="STOCKPRED_INTERNAL_ERROR",
                reason="internal error; see server logs",
            )
        return run_dir.name
