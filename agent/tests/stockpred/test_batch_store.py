from __future__ import annotations

from datetime import datetime, timezone

from src.stockpred.batch_store import StockPredBatchStore
from src.stockpred.strategies.contracts import StrategyBatchRequest, StrategyDescriptor


def test_batch_store_orders_successful_reports_by_sharpe(tmp_path) -> None:
    store = StockPredBatchStore(tmp_path)
    request = StrategyBatchRequest(start="2025-01-01", end="2025-03-31", strategy_ids=("alpha101_1", "alpha101_2"))
    batch_id = store.create(request, [_descriptor("alpha101_1"), _descriptor("alpha101_2")], comparison_key="a" * 64)
    store.finish_report(batch_id, "alpha101_1", run_id="strategy_low", status="success", metrics={"sharpe": 0.2})
    store.finish_report(batch_id, "alpha101_2", run_id="strategy_high", status="success", metrics={"sharpe": 1.2})

    assert [row["strategy_id"] for row in store.list_reports(batch_id)] == ["alpha101_2", "alpha101_1"]


def test_batch_store_keeps_failed_report_after_successes(tmp_path) -> None:
    store = StockPredBatchStore(tmp_path)
    request = StrategyBatchRequest(start="2025-01-01", end="2025-03-31", strategy_ids=("alpha101_1", "alpha101_2"))
    batch_id = store.create(request, [_descriptor("alpha101_1"), _descriptor("alpha101_2")], comparison_key="a" * 64)
    store.finish_report(batch_id, "alpha101_1", run_id="strategy_ok", status="success", metrics={"sharpe": 1.0})
    store.finish_report(batch_id, "alpha101_2", run_id=None, status="failed", reason="missing sector")

    rows = store.list_reports(batch_id)
    assert rows[-1]["status"] == "failed"
    assert rows[-1]["reason"] == "missing sector"


def test_batch_store_sorts_by_requested_metric_and_returns_summary(tmp_path) -> None:
    store = StockPredBatchStore(tmp_path)
    request = StrategyBatchRequest(start="2025-01-01", end="2025-03-31", strategy_ids=("low", "high"))
    batch_id = store.create(request, [_descriptor("low"), _descriptor("high")], comparison_key="a" * 64)
    store.finish_report(batch_id, "low", run_id="strategy_low", status="success", metrics={"annual_return": 0.3, "sharpe": 1.0})
    store.finish_report(batch_id, "high", run_id="strategy_high", status="success", metrics={"annual_return": 0.1, "sharpe": 2.0})
    store.complete(batch_id)

    summary = store.summary(batch_id, sort_by="annual_return", descending=False)

    assert [row["strategy_id"] for row in summary["reports"]] == ["high", "low"]
    assert summary["status"] == "completed"


def test_batch_store_lists_unfinished_batches_with_persisted_reports(tmp_path) -> None:
    store = StockPredBatchStore(tmp_path)
    request = StrategyBatchRequest(
        start="2025-01-01",
        end="2025-03-31",
        strategy_ids=("alpha101_1", "alpha101_2"),
    )
    queued = store.create(request, [_descriptor("alpha101_1")], comparison_key="a" * 64)
    running = store.create(
        request,
        [_descriptor("alpha101_1"), _descriptor("alpha101_2")],
        comparison_key="b" * 64,
    )
    store.finish_report(
        running,
        "alpha101_1",
        run_id="strategy_ok",
        status="success",
        metrics={"sharpe": 1.0},
    )
    completed = store.create(request, [_descriptor("alpha101_1")], comparison_key="c" * 64)
    store.finish_report(
        completed,
        "alpha101_1",
        run_id="strategy_completed",
        status="success",
        metrics={"sharpe": 1.0},
    )
    store.complete(completed)

    batches = store.list_unfinished()

    assert {batch["batch_id"] for batch in batches} == {queued, running}
    running_batch = next(batch for batch in batches if batch["batch_id"] == running)
    assert [report["status"] for report in running_batch["reports"]] == ["success", "queued"]


def test_stalled_batch_only_resumes_nonterminal_reports(tmp_path) -> None:
    store, batch_id = _running_store(tmp_path)
    store.finish_report(batch_id, "alpha101_1", run_id="strategy_ok", status="success", metrics={"sharpe": 1.0})
    store.mark_stalled(batch_id, now="2026-07-25T00:20:00Z")

    assert store.summary(batch_id)["phase"] == "stalled"
    assert store.resume_candidates(batch_id) == ["alpha101_2"]


def test_batch_store_marks_only_expired_running_batches_stalled(tmp_path) -> None:
    store, expired = _running_store(tmp_path)
    _, fresh = _running_store(tmp_path)
    state_path = tmp_path / expired / "state.json"
    state = store._read(state_path)
    state["heartbeat_at"] = "2026-07-25T00:00:00Z"
    state_path.write_text(__import__("json").dumps(state), encoding="utf-8")

    marked = store.mark_expired_stalled(
        now=datetime(2026, 7, 25, 0, 10, tzinfo=timezone.utc), stale_after_seconds=60
    )

    assert marked == [expired]
    assert store.summary(expired)["status"] == "stalled"
    assert store.summary(fresh)["status"] == "running"


def _running_store(tmp_path) -> tuple[StockPredBatchStore, str]:
    store = StockPredBatchStore(tmp_path)
    request = StrategyBatchRequest(
        start="2025-01-01",
        end="2025-03-31",
        strategy_ids=("alpha101_1", "alpha101_2"),
    )
    batch_id = store.create(
        request,
        [_descriptor("alpha101_1"), _descriptor("alpha101_2")],
        comparison_key="a" * 64,
    )
    store.start_screening(batch_id)
    return store, batch_id


def _descriptor(strategy_id: str) -> StrategyDescriptor:
    return StrategyDescriptor(id=strategy_id, name=strategy_id, kind="alpha_zoo", zoo="alpha101")
