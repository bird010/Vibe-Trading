from __future__ import annotations

from src.stockpred.strategy_run_store import StrategyRunStore

from agent.tests.stockpred.test_strategy_runner import _config


def test_strategy_run_store_persists_versioned_report_context(tmp_path) -> None:
    store = StrategyRunStore(tmp_path)

    run_dir = store.create(_config())

    assert run_dir.name.startswith("strategy_")
    assert store.load_config(run_dir).strategy_snapshot.strategy_version == "b" * 64
    context = store.request_context(run_dir)
    assert context == {
        "strategy_type": "stockpred_strategy",
        "strategy_id": "alpha101_1",
        "strategy_version": "b" * 64,
        "batch_id": "batch_1",
        "comparison_key": "c" * 64,
        "start_date": "20250102",
        "end_date": "20250107",
    }


def test_strategy_run_store_tracks_progress_and_failure(tmp_path) -> None:
    store = StrategyRunStore(tmp_path)
    run_dir = store.create(_config())

    store.transition(run_dir, "RUNNING")
    store.progress(run_dir, done=2, total=4, eval_date="20250103")
    store.fail(run_dir, error_code="STOCKPRED_TEST", reason="failed input")

    assert store.read(run_dir) == {
        "status": "failed",
        "phase": "FAILED",
        "created_at": store.read(run_dir)["created_at"],
        "updated_at": store.read(run_dir)["updated_at"],
        "error_code": "STOCKPRED_TEST",
        "reason": "failed input",
        "progress": {"done": 2, "total": 4, "eval_date": "20250103"},
    }
