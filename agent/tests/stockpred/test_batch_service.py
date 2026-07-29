from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta

import pytest

from src.stockpred.batch_service import StockPredStrategyBatchService
from src.stockpred.batch_store import StockPredBatchStore
from src.stockpred.contracts import StockPredDataError
from src.stockpred.strategies.contracts import StrategyBatchRequest, StrategyDescriptor


class _Catalog:
    def list(self):
        return [_descriptor("alpha101_1"), _descriptor("alpha101_2")]

    def require(self, strategy_id: str):
        return next(item for item in self.list() if item.id == strategy_id)


def test_batch_service_runs_selected_strategies_and_isolates_failure(tmp_path) -> None:
    def run_one(descriptor, request, batch_id, comparison_key):
        if descriptor.id == "alpha101_2":
            raise ValueError("unsupported input")
        return "strategy_1", {"sharpe": 1.5}

    service = StockPredStrategyBatchService(StockPredBatchStore(tmp_path), _Catalog(), run_one)
    batch_id = service.run(StrategyBatchRequest(start="2025-01-01", end="2025-03-31", strategy_ids=("alpha101_1", "alpha101_2")))

    rows = service.store.list_reports(batch_id)
    assert rows[0]["status"] == "success"
    assert rows[0]["run_id"] == "strategy_1"
    assert rows[1]["status"] == "failed"
    assert "unsupported input" in rows[1]["reason"]
    assert service.store.strategy_ids(batch_id) == ["alpha101_1", "alpha101_2"]
    assert service.store.request(batch_id).start == "20250101"
    assert len(service.store.comparison_key(batch_id)) == 64
    state = json.loads((tmp_path / batch_id / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "completed_with_failures"


def test_batch_service_select_all_uses_catalog(tmp_path) -> None:
    service = StockPredStrategyBatchService(StockPredBatchStore(tmp_path), _Catalog(), lambda descriptor, *_: (f"run_{descriptor.id}", {"sharpe": 0.0}))

    batch_id = service.run(StrategyBatchRequest(start="2025-01-01", end="2025-03-31", select_all=True))

    assert len(service.store.list_reports(batch_id)) == 2


def test_batch_service_uses_alpha_batch_executor_for_multiple_alpha_strategies(tmp_path) -> None:
    class _Executor:
        def __init__(self) -> None:
            self.calls: list[tuple[str, ...]] = []

        def run_alpha_batch(self, descriptors, *_, **__):  # noqa: ANN001
            self.calls.append(tuple(item.id for item in descriptors))
            return {item.id: (f"run_{item.id}", {"sharpe": 1.0}) for item in descriptors}

        def __call__(self, *_):  # noqa: ANN002
            raise AssertionError("alpha strategies must use the batch executor")

    executor = _Executor()
    service = StockPredStrategyBatchService(StockPredBatchStore(tmp_path), _Catalog(), executor)

    batch_id = service.run(StrategyBatchRequest(start="2025-01-01", end="2025-03-31", strategy_ids=("alpha101_1", "alpha101_2")))

    assert executor.calls == [("alpha101_1", "alpha101_2")]
    assert [row["status"] for row in service.store.list_reports(batch_id)] == ["success", "success"]


def test_batch_service_retries_only_transient_alpha_batch_failures(tmp_path) -> None:
    class _Executor:
        def __init__(self) -> None:
            self.calls: list[tuple[str, ...]] = []

        def run_alpha_batch(self, descriptors, *_, **__):  # noqa: ANN001
            ids = tuple(item.id for item in descriptors)
            self.calls.append(ids)
            if ids == ("alpha101_1", "alpha101_2"):
                return {
                    "alpha101_1": ("run_alpha101_1", {"sharpe": 1.0}),
                    "alpha101_2": StockPredDataError("STOCKPRED_TRANSIENT_IO", "temporary timeout"),
                }
            return {"alpha101_2": ("run_alpha101_2", {"sharpe": 1.0})}

        def __call__(self, *_):  # noqa: ANN002
            raise AssertionError("alpha strategies must use the batch executor")

    executor = _Executor()
    store = StockPredBatchStore(tmp_path)
    batch_id = StockPredStrategyBatchService(store, _Catalog(), executor).run(
        StrategyBatchRequest(start="2025-01-01", end="2025-03-31", strategy_ids=("alpha101_1", "alpha101_2"))
    )

    assert executor.calls == [("alpha101_1", "alpha101_2"), ("alpha101_2",)]
    rows = {row["strategy_id"]: row for row in store.list_reports(batch_id)}
    assert rows["alpha101_1"]["attempt"] == 1
    assert rows["alpha101_2"]["attempt"] == 2
    assert rows["alpha101_1"]["status"] == rows["alpha101_2"]["status"] == "success"


def test_batch_service_converges_alpha_prepare_failure_independently(tmp_path) -> None:
    class _Executor:
        def run_alpha_batch(self, descriptors, *_, **__):  # noqa: ANN001
            return {
                "alpha101_1": ValueError("broken source"),
                "alpha101_2": ("run_alpha101_2", {"sharpe": 1.0}),
            }

        def __call__(self, *_):  # noqa: ANN002
            raise AssertionError("alpha strategies must use the batch executor")

    store = StockPredBatchStore(tmp_path)
    batch_id = StockPredStrategyBatchService(store, _Catalog(), _Executor()).run(
        StrategyBatchRequest(start="2025-01-01", end="2025-03-31", strategy_ids=("alpha101_1", "alpha101_2"))
    )

    rows = {row["strategy_id"]: row for row in store.list_reports(batch_id)}
    assert rows["alpha101_1"]["status"] == "failed"
    assert rows["alpha101_2"]["status"] == "success"


def test_batch_persists_shared_snapshot_and_includes_it_in_comparison_key(tmp_path) -> None:
    class _Executor:
        def manifest_for(self, request):  # noqa: ANN001
            return {"as_of": request.end, "version": 7}

        def __call__(self, descriptor, *_):  # noqa: ANN001
            return f"run_{descriptor.id}", {"sharpe": 0.0}

    store = StockPredBatchStore(tmp_path)
    service = StockPredStrategyBatchService(store, _Catalog(), _Executor())
    batch_id = service.run(StrategyBatchRequest(start="2025-01-01", end="2025-03-31", strategy_ids=("alpha101_1",)))

    assert json.loads((tmp_path / batch_id / "data_snapshot.json").read_text(encoding="utf-8")) == {"as_of": "20250331", "version": 7}
    assert store.comparison_key(batch_id) != StockPredStrategyBatchService._comparison_key(store.request(batch_id))


def test_batch_service_resume_runs_only_nonterminal_reports(tmp_path) -> None:
    calls: list[str] = []

    def run_one(descriptor, *_):  # noqa: ANN001
        calls.append(descriptor.id)
        return f"run_{descriptor.id}", {"sharpe": 1.0}

    store = StockPredBatchStore(tmp_path)
    service = StockPredStrategyBatchService(store, _Catalog(), run_one)
    batch_id = service.reserve(
        StrategyBatchRequest(start="2025-01-01", end="2025-03-31", strategy_ids=("alpha101_1", "alpha101_2"))
    )
    store.start_screening(batch_id)
    store.finish_report(batch_id, "alpha101_1", run_id="strategy_ok", status="success", metrics={"sharpe": 1.0})
    store.mark_stalled(batch_id)

    service.execute(batch_id, resume=True)

    assert calls == ["alpha101_2"]
    assert [row["status"] for row in store.list_reports(batch_id)] == ["success", "success"]


def test_batch_service_retries_transient_io_once_then_records_terminal_failure(tmp_path) -> None:
    attempts = 0

    def run_one(*_):  # noqa: ANN002
        nonlocal attempts
        attempts += 1
        raise StockPredDataError("STOCKPRED_TRANSIENT_IO", "temporary network outage")

    store = StockPredBatchStore(tmp_path)
    service = StockPredStrategyBatchService(store, _Catalog(), run_one)
    batch_id = service.run(StrategyBatchRequest(start="2025-01-01", end="2025-03-31", strategy_ids=("alpha101_1",)))

    row = store.list_reports(batch_id)[0]
    assert attempts == 2
    assert row["status"] == "failed"
    assert row["error_class"] == "transient_io"
    assert row["attempt"] == 2


def test_batch_service_resume_uses_persisted_manifest_without_rebuilding_snapshot(tmp_path) -> None:
    persisted_manifest = {"as_of": "2025-03-31", "version": 7}

    class _ReservingExecutor:
        def manifest_for(self, request):  # noqa: ANN001
            return persisted_manifest

        def __call__(self, *_):  # noqa: ANN002
            raise AssertionError("the reserving executor must not run")

    class _ResumedExecutor:
        def __init__(self) -> None:
            self.manifests: list[dict[str, object]] = []

        def manifest_for(self, request):  # noqa: ANN001
            raise AssertionError("resume must not rebuild the snapshot")

        def __call__(self, descriptor, request, batch_id, comparison_key, manifest):  # noqa: ANN001
            self.manifests.append(manifest)
            return f"run_{descriptor.id}", {"sharpe": 1.0}

    store = StockPredBatchStore(tmp_path)
    batch_id = StockPredStrategyBatchService(store, _Catalog(), _ReservingExecutor()).reserve(
        StrategyBatchRequest(start="2025-01-01", end="2025-03-31", strategy_ids=("alpha101_1",))
    )
    resumed_executor = _ResumedExecutor()

    StockPredStrategyBatchService(store, _Catalog(), resumed_executor).execute(batch_id, resume=True)

    assert resumed_executor.manifests == [persisted_manifest]


def test_batch_service_claim_prevents_second_resume_from_running_same_strategy(tmp_path) -> None:
    calls: list[str] = []
    store = StockPredBatchStore(tmp_path)
    request = StrategyBatchRequest(start="2025-01-01", end="2025-03-31", strategy_ids=("alpha101_1",))
    batch_id = StockPredStrategyBatchService(store, _Catalog(), lambda *_: ("unused", {})).reserve(request)

    def second_run(descriptor, *_):  # noqa: ANN001
        calls.append(f"second:{descriptor.id}")
        return "run_second", {"sharpe": 1.0}

    second = StockPredStrategyBatchService(store, _Catalog(), second_run)

    def first_run(descriptor, *_):  # noqa: ANN001
        calls.append(f"first:{descriptor.id}")
        second.execute(batch_id, resume=True)
        return "run_first", {"sharpe": 1.0}

    StockPredStrategyBatchService(store, _Catalog(), first_run).execute(batch_id, resume=True)

    assert calls == ["first:alpha101_1"]


def test_batch_service_does_not_retry_file_not_found(tmp_path) -> None:
    attempts = 0

    def run_one(*_):  # noqa: ANN002
        nonlocal attempts
        attempts += 1
        raise FileNotFoundError("required input is absent")

    store = StockPredBatchStore(tmp_path)
    service = StockPredStrategyBatchService(store, _Catalog(), run_one)
    batch_id = service.run(StrategyBatchRequest(start="2025-01-01", end="2025-03-31", strategy_ids=("alpha101_1",)))

    row = store.list_reports(batch_id)[0]
    assert attempts == 1
    assert row["error_class"] == "deterministic"
    assert row["attempt"] == 1


def test_batch_service_keeps_interrupted_reports_unfinished_on_normal_execute(tmp_path) -> None:
    store = StockPredBatchStore(tmp_path)
    service = StockPredStrategyBatchService(store, _Catalog(), lambda *_: ("unused", {}))
    batch_id = service.reserve(
        StrategyBatchRequest(start="2025-01-01", end="2025-03-31", strategy_ids=("alpha101_1", "alpha101_2"))
    )
    store.start_screening(batch_id)
    store.finish_report(batch_id, "alpha101_1", run_id="strategy_ok", status="success", metrics={"sharpe": 1.0})
    store.finish_report(batch_id, "alpha101_2", run_id=None, status="interrupted", reason="worker stopped")

    service.execute(batch_id)

    assert store.summary(batch_id)["phase"] == "screening"
    assert store.summary(batch_id)["status"] == "running"
    assert [batch["batch_id"] for batch in store.list_unfinished()] == [batch_id]


def test_batch_service_rejects_second_execute_while_os_lock_is_held(tmp_path) -> None:
    calls: list[str] = []
    store = StockPredBatchStore(tmp_path)
    request = StrategyBatchRequest(start="2025-01-01", end="2025-03-31", strategy_ids=("alpha101_1",))
    batch_id = StockPredStrategyBatchService(store, _Catalog(), lambda *_: ("unused", {})).reserve(request)

    def second_run(descriptor, *_):  # noqa: ANN001
        calls.append(f"second:{descriptor.id}")
        return "run_second", {"sharpe": 1.0}

    second = StockPredStrategyBatchService(store, _Catalog(), second_run)

    def first_run(descriptor, *_):  # noqa: ANN001
        calls.append(f"first:{descriptor.id}")
        with pytest.raises(RuntimeError, match="batch lease unavailable"):
            second.execute(batch_id, resume=True)
        assert store.list_reports(batch_id)[0]["status"] == "queued"
        return "run_first", {"sharpe": 1.0}

    StockPredStrategyBatchService(store, _Catalog(), first_run).execute(batch_id, resume=True)

    assert calls == ["first:alpha101_1"]


def test_batch_service_resume_uses_persisted_attempt_after_interrupted_retry(tmp_path, monkeypatch) -> None:
    calls = 0
    store = StockPredBatchStore(tmp_path)
    request = StrategyBatchRequest(start="2025-01-01", end="2025-03-31", strategy_ids=("alpha101_1",))
    batch_id = StockPredStrategyBatchService(store, _Catalog(), lambda *_: ("unused", {})).reserve(request)
    original_heartbeat = store.heartbeat
    heartbeat_calls = 0

    def heartbeat(batch, strategy):  # noqa: ANN001
        nonlocal heartbeat_calls
        heartbeat_calls += 1
        if heartbeat_calls == 2:
            raise KeyboardInterrupt("worker stopped after recording the transient failure")
        original_heartbeat(batch, strategy)

    def run_one(*_):  # noqa: ANN002
        nonlocal calls
        calls += 1
        if calls == 1:
            raise StockPredDataError("STOCKPRED_TRANSIENT_IO", "network timeout")
        return "run_recovered", {"sharpe": 1.0}

    monkeypatch.setattr(store, "heartbeat", heartbeat)
    service = StockPredStrategyBatchService(store, _Catalog(), run_one)

    with pytest.raises(KeyboardInterrupt, match="worker stopped"):
        service.execute(batch_id)

    row = store.list_reports(batch_id)[0]
    assert row["attempt"] == 1
    assert row["error_class"] == "transient_io"
    assert row["status"] == "interrupted"
    monkeypatch.setattr(store, "heartbeat", original_heartbeat)

    StockPredStrategyBatchService(store, _Catalog(), run_one).execute(batch_id, resume=True)

    assert calls == 2
    assert store.list_reports(batch_id)[0]["attempt"] == 2


def test_batch_service_retries_only_explicit_transient_error_code(tmp_path) -> None:
    attempts = 0

    def run_one(*_):  # noqa: ANN002
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise StockPredDataError("STOCKPRED_TRANSIENT_IO", "network timeout")
        return "run_retried", {"sharpe": 1.0}

    store = StockPredBatchStore(tmp_path)
    batch_id = StockPredStrategyBatchService(store, _Catalog(), run_one).run(
        StrategyBatchRequest(start="2025-01-01", end="2025-03-31", strategy_ids=("alpha101_1",))
    )

    assert attempts == 2
    assert store.list_reports(batch_id)[0]["status"] == "success"


def test_batch_service_never_starts_a_third_persisted_attempt(tmp_path) -> None:
    calls = 0
    store = StockPredBatchStore(tmp_path)
    service = StockPredStrategyBatchService(store, _Catalog(), lambda *_: ("unused", {}))
    batch_id = service.reserve(
        StrategyBatchRequest(start="2025-01-01", end="2025-03-31", strategy_ids=("alpha101_1",))
    )
    store.finish_report(
        batch_id,
        "alpha101_1",
        run_id=None,
        status="interrupted",
        reason="worker stopped during attempt two",
        error_class="transient_io",
        attempt=2,
    )

    def run_one(*_):  # noqa: ANN002
        nonlocal calls
        calls += 1
        return "run_third", {"sharpe": 1.0}

    StockPredStrategyBatchService(store, _Catalog(), run_one).execute(batch_id, resume=True)

    assert calls == 0
    row = store.list_reports(batch_id)[0]
    assert row["status"] == "failed"
    assert row["attempt"] == 2


def _descriptor(strategy_id: str) -> StrategyDescriptor:
    return StrategyDescriptor(id=strategy_id, name=strategy_id, kind="alpha_zoo", zoo="alpha101")


# ---------------------------------------------------------------------------
# Idempotent reservation
# ---------------------------------------------------------------------------


def test_reserve_idempotent_same_key_returns_same_batch(tmp_path) -> None:
    """Same idempotency key must return the same batch_id and only create once."""
    import uuid
    service = StockPredStrategyBatchService(
        StockPredBatchStore(tmp_path), _Catalog(), lambda *a: ("run", {})
    )
    request = StrategyBatchRequest(start="2025-01-01", end="2025-03-31", strategy_ids=("alpha101_1",))
    key = str(uuid.uuid4())

    first_id, first_created = service.reserve_idempotent(request, idempotency_key=key)
    second_id, second_created = service.reserve_idempotent(request, idempotency_key=key)

    assert first_created is True
    assert second_created is False
    assert second_id == first_id


def test_reserve_idempotent_different_keys_create_different_batches(tmp_path) -> None:
    """Different idempotency keys represent independent user intents."""
    import uuid
    service = StockPredStrategyBatchService(
        StockPredBatchStore(tmp_path), _Catalog(), lambda *a: ("run", {})
    )
    request = StrategyBatchRequest(start="2025-01-01", end="2025-03-31", strategy_ids=("alpha101_1",))

    first_id, first_created = service.reserve_idempotent(request, idempotency_key=str(uuid.uuid4()))
    second_id, second_created = service.reserve_idempotent(request, idempotency_key=str(uuid.uuid4()))

    assert first_created is True
    assert second_created is True
    assert first_id != second_id
