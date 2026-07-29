"""Unit tests for src.stockpred.run_store (StockPredRunStore)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.stockpred.graph.backtest_config import GraphBacktestConfig
from src.stockpred.run_store import StockPredRunStore, atomic_json


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> StockPredRunStore:
    return StockPredRunStore(tmp_path / "runs")


@pytest.fixture
def config() -> GraphBacktestConfig:
    return GraphBacktestConfig(start="2025-01-01", end="2025-01-31")


# ---------------------------------------------------------------------------
# atomic_json
# ---------------------------------------------------------------------------


class TestAtomicJson:
    def test_writes_valid_json(self, tmp_path: Path) -> None:
        target = tmp_path / "sub" / "data.json"
        atomic_json(target, {"key": "value", "number": 42})
        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload == {"key": "value", "number": 42}

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        target = tmp_path / "a" / "b" / "c" / "file.json"
        atomic_json(target, {"nested": True})
        assert target.exists()

    def test_no_temp_file_remains(self, tmp_path: Path) -> None:
        target = tmp_path / "clean.json"
        atomic_json(target, {"clean": True})
        temp = target.with_name(".clean.json.tmp")
        assert not temp.exists()


# ---------------------------------------------------------------------------
# StockPredRunStore.create
# ---------------------------------------------------------------------------


class TestCreate:
    def test_creates_run_directory(self, store: StockPredRunStore, config: GraphBacktestConfig) -> None:
        run_dir = store.create(config)
        assert run_dir.is_dir()
        assert run_dir.name.startswith("graph_")

    def test_writes_config_json(self, store: StockPredRunStore, config: GraphBacktestConfig) -> None:
        run_dir = store.create(config)
        config_path = run_dir / "config.json"
        assert config_path.exists()
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        assert payload["start"] == "20250101"
        assert payload["end"] == "20250131"

    def test_writes_req_json(self, store: StockPredRunStore, config: GraphBacktestConfig) -> None:
        run_dir = store.create(config)
        req_path = run_dir / "req.json"
        assert req_path.exists()
        payload = json.loads(req_path.read_text(encoding="utf-8"))
        assert payload["context"]["strategy_type"] == "stockpred_graph"
        assert payload["context"]["start_date"] == "20250101"

    def test_writes_initial_state(self, store: StockPredRunStore, config: GraphBacktestConfig) -> None:
        run_dir = store.create(config)
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        assert state["status"] == "queued"
        assert state["phase"] == "QUEUED"
        assert state["error_code"] is None

    def test_unique_run_ids(self, store: StockPredRunStore, config: GraphBacktestConfig) -> None:
        dir1 = store.create(config)
        dir2 = store.create(config)
        assert dir1.name != dir2.name


# ---------------------------------------------------------------------------
# StockPredRunStore.require
# ---------------------------------------------------------------------------


class TestRequire:
    def test_valid_run_id(self, store: StockPredRunStore, config: GraphBacktestConfig) -> None:
        run_dir = store.create(config)
        result = store.require(run_dir.name)
        assert result == run_dir

    def test_invalid_format_raises(self, store: StockPredRunStore) -> None:
        with pytest.raises(KeyError, match="invalid"):
            store.require("../etc/passwd")

    def test_nonexistent_run_raises(self, store: StockPredRunStore) -> None:
        (store.root).mkdir(parents=True, exist_ok=True)
        with pytest.raises(KeyError, match="not found"):
            store.require("graph_nonexistent")

    def test_rejects_path_traversal(self, store: StockPredRunStore) -> None:
        with pytest.raises(KeyError, match="invalid"):
            store.require("graph_../../secret")


# ---------------------------------------------------------------------------
# StockPredRunStore.transition
# ---------------------------------------------------------------------------


class TestTransition:
    def test_transition_to_running(self, store: StockPredRunStore, config: GraphBacktestConfig) -> None:
        run_dir = store.create(config)
        store.transition(run_dir, "RUNNING")
        state = store.read(run_dir)
        assert state["phase"] == "RUNNING"
        assert state["status"] == "running"

    def test_transition_to_succeeded(self, store: StockPredRunStore, config: GraphBacktestConfig) -> None:
        run_dir = store.create(config)
        store.transition(run_dir, "SUCCEEDED")
        state = store.read(run_dir)
        assert state["phase"] == "SUCCEEDED"
        assert state["status"] == "success"

    def test_full_lifecycle(self, store: StockPredRunStore, config: GraphBacktestConfig) -> None:
        run_dir = store.create(config)
        for phase in ["VALIDATING", "RUNNING", "FINALIZING", "SUCCEEDED"]:
            store.transition(run_dir, phase)
        state = store.read(run_dir)
        assert state["phase"] == "SUCCEEDED"
        assert state["status"] == "success"

    def test_invalid_phase_raises(self, store: StockPredRunStore, config: GraphBacktestConfig) -> None:
        run_dir = store.create(config)
        with pytest.raises(ValueError, match="unsupported"):
            store.transition(run_dir, "INVALID_PHASE")

    def test_updated_at_changes(self, store: StockPredRunStore, config: GraphBacktestConfig) -> None:
        run_dir = store.create(config)
        initial = store.read(run_dir)["updated_at"]
        store.transition(run_dir, "RUNNING")
        updated = store.read(run_dir)["updated_at"]
        assert updated >= initial


# ---------------------------------------------------------------------------
# StockPredRunStore.fail
# ---------------------------------------------------------------------------


class TestFail:
    def test_fail_sets_error_info(self, store: StockPredRunStore, config: GraphBacktestConfig) -> None:
        run_dir = store.create(config)
        store.fail(run_dir, error_code="TEST_ERROR", reason="something broke")
        state = store.read(run_dir)
        assert state["phase"] == "FAILED"
        assert state["status"] == "failed"
        assert state["error_code"] == "TEST_ERROR"
        assert state["reason"] == "something broke"


# ---------------------------------------------------------------------------
# StockPredRunStore.progress
# ---------------------------------------------------------------------------


class TestProgress:
    def test_progress_updates_state(self, store: StockPredRunStore, config: GraphBacktestConfig) -> None:
        run_dir = store.create(config)
        store.progress(run_dir, done=5, total=20, eval_date="20250115")
        state = store.read(run_dir)
        assert state["progress"]["done"] == 5
        assert state["progress"]["total"] == 20
        assert state["progress"]["eval_date"] == "20250115"

    def test_progress_overwrites_previous(self, store: StockPredRunStore, config: GraphBacktestConfig) -> None:
        run_dir = store.create(config)
        store.progress(run_dir, done=1, total=10, eval_date="20250105")
        store.progress(run_dir, done=5, total=10, eval_date="20250125")
        state = store.read(run_dir)
        assert state["progress"]["done"] == 5


# ---------------------------------------------------------------------------
# StockPredRunStore.load_config
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_roundtrip(self, store: StockPredRunStore, config: GraphBacktestConfig) -> None:
        run_dir = store.create(config)
        loaded = store.load_config(run_dir)
        assert loaded.start == config.start
        assert loaded.end == config.end
        assert loaded.mode == config.mode
        assert loaded.top_n == config.top_n
