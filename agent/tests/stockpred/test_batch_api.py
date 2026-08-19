from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from fastapi import FastAPI
import pytest

from src.api import stockpred_routes
from src.stockpred.batch_store import StockPredBatchStore
from src.stockpred.strategies.contracts import StrategyBatchRequest
from src.stockpred.strategies.contracts import StrategyDescriptor


async def _auth() -> None:
    return None


@pytest.fixture
def api(tmp_path):
    app = FastAPI()
    stockpred_routes.register_stockpred_routes(
        app,
        runs_dir=tmp_path,
        require_auth=_auth,
        require_event_stream_auth=_auth,
    )
    return TestClient(app), tmp_path


def test_strategy_catalog_lists_graph_and_alpha(api: tuple[TestClient, object], monkeypatch) -> None:
    class Catalog:
        def list(self):
            return [
                StrategyDescriptor(id="stockpred_graph", name="Graph", kind="graph"),
                StrategyDescriptor(id="alpha101_1", name="Alpha", kind="alpha_zoo", zoo="alpha101"),
            ]

    monkeypatch.setattr(stockpred_routes, "build_catalog", lambda: Catalog())

    response = api[0].get("/stockpred/strategies")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["strategies"]] == ["stockpred_graph", "alpha101_1"]


def test_create_and_read_strategy_batch(api: tuple[TestClient, object], monkeypatch) -> None:
    class Service:
        class store:
            @staticmethod
            def summary(batch_id, *, sort_by="sharpe", descending=True):
                assert (batch_id, sort_by, descending) == ("batch_123", "annual_return", False)
                return {"batch_id": batch_id, "reports": []}

            @staticmethod
            def try_claim_execution(key):
                return "lease-token"

            @staticmethod
            def confirm_execution(key, token):
                return True

            @staticmethod
            def release_execution(key, token):
                return True

        def reserve_idempotent(self, request, *, idempotency_key):
            assert request.strategy_ids == ("alpha101_1",)
            return "batch_123", True

        def execute(self, batch_id):
            return batch_id

    monkeypatch.setattr(stockpred_routes, "build_batch_service", lambda *_: Service())

    created = api[0].post("/stockpred/strategy-batches", json={"start": "2025-01-01", "end": "2025-03-31", "strategy_ids": ["alpha101_1"], "idempotency_key": "00000000-0000-0000-0000-000000000001"})
    summary = api[0].get("/stockpred/strategy-batches/batch_123?sort_by=annual_return&descending=false")

    assert created.status_code == 202
    assert created.json() == {"batch_id": "batch_123", "events_url": "/stockpred/strategy-batches/batch_123/events"}
    assert summary.json() == {"batch_id": "batch_123", "reports": []}


def test_list_unfinished_strategy_batches(api: tuple[TestClient, object], monkeypatch) -> None:
    class Service:
        class store:
            @staticmethod
            def list_unfinished():
                return [{"batch_id": "batch_running", "status": "running", "reports": []}]

    monkeypatch.setattr(stockpred_routes, "build_batch_service", lambda *_: Service())

    response = api[0].get("/stockpred/strategy-batches")

    assert response.status_code == 200
    assert response.json() == [{"batch_id": "batch_running", "status": "running", "reports": []}]


def test_strategy_batch_sse_emits_new_heartbeat_without_writing_state(tmp_path) -> None:
    state_path = tmp_path / "state.json"
    first = {"status": "running", "heartbeat_at": "2026-07-25T00:00:00Z"}
    second = {"status": "running", "heartbeat_at": "2026-07-25T00:00:01Z"}
    reads = iter((first, second))

    class Request:
        def __init__(self) -> None:
            self.calls = 0

        async def is_disconnected(self) -> bool:
            self.calls += 1
            return self.calls > 2

    original_read = stockpred_routes._read_json
    stockpred_routes._read_json = lambda _path: next(reads)
    try:
        async def collect() -> list[str]:
            events = stockpred_routes.iter_strategy_batch_events(state_path, Request(), poll_seconds=0)
            return [await anext(events), await anext(events)]

        events = asyncio.run(collect())
    finally:
        stockpred_routes._read_json = original_read

    assert '"heartbeat_at": "2026-07-25T00:00:00Z"' in events[0]
    assert '"heartbeat_at": "2026-07-25T00:00:01Z"' in events[1]


def test_strategy_batch_sse_progress_includes_persisted_heartbeat(tmp_path) -> None:
    store = StockPredBatchStore(tmp_path / "strategy_batches")
    descriptor = StrategyDescriptor(id="alpha101_1", name="Alpha", kind="alpha_zoo", zoo="alpha101")
    batch_id = store.create(
        StrategyBatchRequest(start="2025-01-01", end="2025-03-31", strategy_ids=("alpha101_1",)),
        [descriptor],
        comparison_key="a" * 64,
    )
    store.start_screening(batch_id)
    store.finish_report(batch_id, "alpha101_1", run_id="strategy_1", status="success", metrics={"sharpe": 1.0})
    store.complete(batch_id)
    app = FastAPI()
    stockpred_routes.register_stockpred_routes(app, runs_dir=tmp_path, require_auth=_auth, require_event_stream_auth=_auth)

    response = TestClient(app).get(f"/stockpred/strategy-batches/{batch_id}/events")

    assert response.status_code == 200
    assert 'event: progress' in response.text
    assert '"heartbeat_at":' in response.text


def test_startup_scan_marks_expired_batch_without_executing_work(tmp_path) -> None:
    store = StockPredBatchStore(tmp_path / "strategy_batches")
    descriptor = StrategyDescriptor(id="alpha101_1", name="Alpha", kind="alpha_zoo", zoo="alpha101")
    batch_id = store.create(
        StrategyBatchRequest(start="2025-01-01", end="2025-03-31", strategy_ids=("alpha101_1",)),
        [descriptor],
        comparison_key="a" * 64,
    )
    store.start_screening(batch_id)
    state_path = tmp_path / "strategy_batches" / batch_id / "state.json"
    state = store._read(state_path)
    state["heartbeat_at"] = "2000-01-01T00:00:00Z"
    state_path.write_text(__import__("json").dumps(state), encoding="utf-8")
    app = FastAPI()
    stockpred_routes.register_stockpred_routes(app, runs_dir=tmp_path, require_auth=_auth, require_event_stream_auth=_auth)

    for handler in app.router.on_startup:
        handler()

    assert store.summary(batch_id)["status"] == "stalled"


@pytest.mark.asyncio
async def test_stalled_batch_sse_emits_batch_error_and_stops() -> None:
    """stalled 状态必须发送 batch_error 终态事件并停止。"""
    state = {"status": "stalled", "batch_id": "batch_test"}
    reads = iter([state])

    class Request:
        async def is_disconnected(self) -> bool:
            return False

    original_read = stockpred_routes._read_json
    stockpred_routes._read_json = lambda _path: next(reads)
    try:
        events: list[str] = []
        gen = stockpred_routes.iter_strategy_batch_events(
            __import__("pathlib").Path("/fake"), Request(), poll_seconds=0
        )
        async for event in gen:
            events.append(event)
    finally:
        stockpred_routes._read_json = original_read

    # Should have progress event followed by batch_error event
    assert len(events) == 2
    assert "event: progress" in events[0]
    assert "event: batch_error" in events[1]
    assert '"status": "stalled"' in events[1]


def test_idempotent_post_same_key_returns_same_batch(api: tuple[TestClient, object], monkeypatch) -> None:
    """Same idempotency_key in two POSTs returns same batch_id, execute called once."""
    import uuid
    execute_calls = []
    claimed_keys: dict[str, str] = {}
    execution_claimed: set[str] = set()

    class Service:
        class store:
            @staticmethod
            def summary(batch_id, *, sort_by="sharpe", descending=True):
                return {"batch_id": batch_id, "reports": []}

            @staticmethod
            def try_claim_execution(key):
                if key in execution_claimed:
                    return None
                execution_claimed.add(key)
                return "lease-token"

            @staticmethod
            def confirm_execution(key, token):
                return True

            @staticmethod
            def release_execution(key, token):
                return True

        def reserve_idempotent(self, request, *, idempotency_key):
            if idempotency_key in claimed_keys:
                return claimed_keys[idempotency_key], False
            batch_id = "batch_idem_123"
            claimed_keys[idempotency_key] = batch_id
            return batch_id, True

        def execute(self, batch_id):
            execute_calls.append(batch_id)
            return batch_id

    shared_service = Service()
    monkeypatch.setattr(stockpred_routes, "build_batch_service", lambda *_: shared_service)
    key = str(uuid.uuid4())
    body = {"start": "2025-01-01", "end": "2025-03-31", "strategy_ids": ["alpha101_1"], "idempotency_key": key}

    first = api[0].post("/stockpred/strategy-batches", json=body)
    second = api[0].post("/stockpred/strategy-batches", json=body)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["batch_id"] == second.json()["batch_id"] == "batch_idem_123"
    # Execute should only be scheduled once (first POST)
    import time
    time.sleep(0.1)  # Allow async task to run
    assert len(execute_calls) == 1
