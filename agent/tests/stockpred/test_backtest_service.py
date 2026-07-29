from __future__ import annotations

import json
from pathlib import Path

from agent.backtest.stockpred_graph.runner import GraphBacktestResult
from src.stockpred.backtest_service import GraphBacktestService
from src.stockpred.contracts import (
    DataSnapshotManifest,
    ModelSnapshot,
    StockPredDataError,
)
from src.stockpred.graph.backtest_config import GraphBacktestConfig

from agent.tests.stockpred.test_artifacts import _result


CONFIG = GraphBacktestConfig(start="2025-01-01", end="2025-01-31")


def _snapshot_factory(config: GraphBacktestConfig) -> DataSnapshotManifest:
    return DataSnapshotManifest(
        as_of=f"{config.end}T15:00:00+08:00",
        tables={},
        model=ModelSnapshot(id="stockpred-graph", version="graph-v1", config_sha256="old"),
    )


class _Runner:
    def __init__(self, error: StockPredDataError | None = None) -> None:
        self.error = error

    def run(self, config, on_progress=None) -> GraphBacktestResult:  # noqa: ANN001, ARG002
        if self.error is not None:
            raise self.error
        if on_progress is not None:
            on_progress(1, 1, "20250102")
        return _result()


def test_service_publishes_complete_run_atomically(tmp_path: Path) -> None:
    service = GraphBacktestService(tmp_path, _Runner(), _snapshot_factory)

    run_id = service.run(CONFIG)
    run_dir = tmp_path / run_id

    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "success"
    assert state["phase"] == "SUCCEEDED"
    assert state["created_at"]
    assert (run_dir / "artifacts" / "metrics.csv").is_file()
    assert (run_dir / "data_snapshot.json").is_file()
    assert not list(tmp_path.glob(f".{run_id}.staging"))


def test_failed_run_keeps_snapshot_and_error_code(tmp_path: Path) -> None:
    error = StockPredDataError("STOCKPRED_ADJUSTMENT_COVERAGE", "coverage failed")
    service = GraphBacktestService(tmp_path, _Runner(error), _snapshot_factory)

    run_id = service.run(CONFIG)
    run_dir = tmp_path / run_id
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))

    assert state["status"] == "failed"
    assert state["error_code"] == "STOCKPRED_ADJUSTMENT_COVERAGE"
    assert (run_dir / "data_snapshot.json").is_file()


def test_failed_parity_writes_report_before_marking_failed(tmp_path: Path) -> None:
    golden = tmp_path / "failing-golden"
    golden.mkdir()
    config = CONFIG.model_copy(update={"parity_reference": str(golden)})
    service = GraphBacktestService(tmp_path, _Runner(), _snapshot_factory)

    run_id = service.run(config)
    run_dir = tmp_path / run_id
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))

    assert state["error_code"] == "STOCKPRED_PARITY_FAILED"
    assert json.loads((run_dir / "parity.json").read_text())["passed"] is False


def test_reserve_then_execute_uses_persisted_config(tmp_path: Path) -> None:
    service = GraphBacktestService(tmp_path, _Runner(), _snapshot_factory)

    run_id = service.reserve(CONFIG)
    queued = service.store.read(tmp_path / run_id)
    completed = service.execute(run_id)

    assert queued["status"] == "queued"
    assert completed == run_id
    assert service.store.read(tmp_path / run_id)["status"] == "success"


def test_graph_run_context_has_unified_strategy_identity(tmp_path: Path) -> None:
    service = GraphBacktestService(tmp_path, _Runner(), _snapshot_factory)

    run_id = service.reserve(CONFIG)
    context = json.loads((tmp_path / run_id / "req.json").read_text(encoding="utf-8"))["context"]

    assert context["strategy_id"] == "stockpred_graph"
    assert context["strategy_kind"] == "graph"
