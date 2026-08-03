"""Regression coverage for the Phase 4 batch-backend review findings."""

from __future__ import annotations

import json
import sys
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.fund_rotation_routes import register_fund_rotation_routes
from src.stockpred.fund_rotation.artifacts import compute_file_checksum
from src.stockpred.fund_rotation.batch_models import canonical_payload_hash
from src.stockpred.fund_rotation.batch_persistence import (
    BatchIdempotencyError,
    BatchPersistence,
)
from src.stockpred.fund_rotation.batch_service import BatchService
from src.stockpred.fund_rotation.data_snapshot import PinnedFundDataSnapshot
from src.stockpred.fund_rotation.persistence import BatchEventLog, atomic_write_json
from src.stockpred.fund_rotation.service import FundRotationBacktestService
from src.stockpred.fund_rotation.state_machine import InvalidTransitionError

from tests.fund_rotation.test_batch_service import (
    CALENDAR,
    FakeBatchStrategy,
    _frames_loader,
    _request,
    _service as _legacy_service,
)


def _snapshot() -> PinnedFundDataSnapshot:
    return PinnedFundDataSnapshot(
        fund_version=17,
        fund_adj_version=23,
        dim_version=31,
        universe_codes=("E1",),
        trading_dates=tuple(CALENDAR),
        fingerprint="pinned-real-versions",
    )


def _service(tmp_path, *, snapshot=None, frames_loader=None) -> BatchService:
    from backtest.fund_rotation.catalog import FundRotationStrategyCatalog

    pinned = snapshot or _snapshot()
    return BatchService(
        tmp_path / "strategy_batches",
        catalog=FundRotationStrategyCatalog([FakeBatchStrategy]),
        metadata_loader=lambda: pinned,
        frames_loader=frames_loader or (
            lambda received, data_start, data_end: _frames_loader(
                received, data_start, data_end,
            )
        ),
        auto_start=False,
    )


def _run_two(service: BatchService):
    request = _request([
        {"strategy_id": "fake_batch", "params": {"lookback_days": 30}},
        {"strategy_id": "fake_batch", "params": {"lookback_days": 40}},
    ])
    outcome = service.submit_batch(request)
    service.run_batch_sync(outcome["batch_id"])
    return outcome["batch_id"]


def test_one_complete_pinned_snapshot_drives_reads_and_replay_identity(tmp_path):
    """A second latest-version resolution must not replace the planned snapshot."""
    pinned = _snapshot()
    received = []

    def load_frames(snapshot, data_start, data_end):
        received.append((snapshot, data_start, data_end))
        return _frames_loader(snapshot, data_start, data_end)

    service = _service(tmp_path, snapshot=pinned, frames_loader=load_frames)
    batch_id = _run_two(service)

    assert len(received) == 1
    assert received[0][0] is pinned
    batch_dir = service.persistence.batch_dir(batch_id)
    persisted_snapshot = json.loads(
        (batch_dir / "data_snapshot.json").read_text(encoding="utf-8")
    )
    assert persisted_snapshot == {
        "fund_version": 17,
        "fund_adj_version": 23,
        "dim_version": 31,
        "universe_codes": ["E1"],
        "trading_dates": CALENDAR,
        "fingerprint": "pinned-real-versions",
    }

    resolved = json.loads(
        (batch_dir / "resolved_batch.json").read_text(encoding="utf-8")
    )
    assert resolved["data_snapshot"] == persisted_snapshot
    for variant in resolved["variants"]:
        assert variant["resolved_config"]["lookback_days"] in (30, 40)
        assert variant["resolved_requirements"]["warmup_trade_days"] in (30, 40)
        assert variant["snapshot_fingerprint"] == pinned.fingerprint


def test_successful_children_publish_globally_and_existing_read_apis_work(tmp_path):
    """Child runs are first-class published backtests, not batch-private states."""
    service = _legacy_service(tmp_path)
    batch_id = _run_two(service)
    batch_dir = service.persistence.batch_dir(batch_id)
    resolved = json.loads(
        (batch_dir / "resolved_batch.json").read_text(encoding="utf-8")
    )

    assert not (batch_dir / "runs").exists()
    expected_files = {
        "state.json",
        "events.jsonl",
        "resolved_spec.json",
        "strategy_snapshot.json",
        "data_snapshot.json",
        "evaluation_calendar.json",
        "target_decisions.csv",
        "targets.csv",
        "orders.csv",
        "trade_events.csv",
        "positions.csv",
        "equity.csv",
        "metrics.json",
        "summary.json",
        "manifest.json",
    }
    for variant in resolved["variants"]:
        run_id = variant["run_id"]
        run_dir = service.runs_root / run_id
        assert expected_files <= {path.name for path in run_dir.iterdir()}
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        assert state["stage"] == "SUCCEEDED"
        events = [
            json.loads(line)
            for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert [event["stage"] for event in events] == [
            "QUEUED",
            "PREPARING_DATA",
            "GENERATING_SIGNALS",
            "EXECUTING",
            "COMPUTING_METRICS",
            "WRITING_RESULTS",
            "SUCCEEDED",
        ]
        manifest = json.loads(
            (run_dir / "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["run_id"] == run_id
        assert manifest["state_checksum"] == compute_file_checksum(run_dir / "state.json")

    app = FastAPI()
    register_fund_rotation_routes(
        app,
        service.runs_root.parent,
        lambda: None,
        lambda: None,
    )
    client = TestClient(app)
    run_id = resolved["variants"][0]["run_id"]
    detail = client.get(f"/stockpred/fund-rotation/backtests/{run_id}")
    artifact = client.get(
        f"/stockpred/fund-rotation/backtests/{run_id}/artifacts/equity.csv"
    )
    chart = client.get(
        f"/stockpred/fund-rotation/backtests/{run_id}/instruments/E1/chart"
    )
    assert detail.status_code == 200
    assert detail.json()["summary"]["mode"] == "RESEARCH_ONLY"
    assert artifact.status_code == 200
    assert chart.status_code == 200
    assert chart.json()["run_id"] == run_id


def test_parent_manifest_checksums_cover_the_persisted_terminal_event(tmp_path):
    """The terminal event is fixed before checksums and the final manifest."""
    service = _legacy_service(tmp_path)
    batch_id = _run_two(service)
    batch_dir = service.persistence.batch_dir(batch_id)
    manifest = json.loads((batch_dir / "manifest.json").read_text(encoding="utf-8"))

    for name, details in manifest["file_details"].items():
        assert compute_file_checksum(batch_dir / name) == details["checksum"]
    terminal = json.loads(
        (batch_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )
    assert terminal["event_type"] == "TERMINAL"
    assert terminal["scope"] == "BATCH"
    assert terminal["stage"] == "SUCCEEDED"


def test_single_success_does_not_publish_formal_comparison(tmp_path):
    service = _legacy_service(tmp_path)
    request = _request([
        {"strategy_id": "fake_batch", "params": {"lookback_days": 30}},
    ])
    outcome = service.submit_batch(request)
    service.run_batch_sync(outcome["batch_id"])
    batch_dir = service.persistence.batch_dir(outcome["batch_id"])

    reports = json.loads((batch_dir / "reports.json").read_text(encoding="utf-8"))
    assert reports["comparison_available"] is False
    assert not (batch_dir / "comparison_equity.csv").exists()
    assert not (batch_dir / "comparison_metrics.csv").exists()


def test_terminal_batch_cannot_be_canceled_again(tmp_path):
    service = _legacy_service(tmp_path)
    request = _request([
        {"strategy_id": "fake_batch", "params": {"lookback_days": 30}},
    ])
    outcome = service.submit_batch(request)
    service.run_batch_sync(outcome["batch_id"])

    assert service.cancel_batch(outcome["batch_id"]) is False


def test_production_route_resolves_one_snapshot_and_reuses_it_for_frames(
    tmp_path, monkeypatch,
):
    """Route callbacks must not independently resolve two latest versions."""
    import src.stockpred.fund_rotation.batch_service as batch_module
    import src.stockpred.fund_rotation.data_snapshot as snapshot_module

    captured = {}

    class StubBatchService:
        def __init__(self, batches_dir, **kwargs):
            captured.update(kwargs)
            self.persistence = BatchPersistence(batches_dir)

        def recover_interrupted(self):
            return []

    pinned = _snapshot()
    resolve_calls = []
    load_calls = []

    def resolve_once(lance_dir):
        resolve_calls.append(lance_dir)
        return pinned

    def load_same(snapshot, lance_dir, *, data_start, data_end):
        load_calls.append((snapshot, lance_dir, data_start, data_end))
        return ("fund", "adj", "dim")

    monkeypatch.setattr(batch_module, "BatchService", StubBatchService)
    monkeypatch.setattr(snapshot_module, "resolve_pinned_snapshot", resolve_once)
    monkeypatch.setattr(snapshot_module, "load_pinned_frames", load_same)

    app = FastAPI()
    register_fund_rotation_routes(
        app, tmp_path / "runs", lambda: None, lambda: None,
        stockpred_root=tmp_path,
    )
    resolved = captured["metadata_loader"]()
    frames = captured["frames_loader"](resolved, CALENDAR[0], CALENDAR[-1])

    assert resolved is pinned
    assert frames == ("fund", "adj", "dim")
    assert len(resolve_calls) == 1
    assert load_calls[0][0] is pinned


def test_framework_snapshot_is_captured_once_at_service_startup(
    tmp_path, monkeypatch,
):
    import src.stockpred.fund_rotation.batch_service as batch_module

    calls = []

    def capture(_agent_root):
        calls.append("capture")
        return "framework-at-startup"

    monkeypatch.setattr(batch_module, "snapshot_framework", capture, raising=False)
    service = _legacy_service(tmp_path)
    batch_id = _run_two(service)
    batch_dir = service.persistence.batch_dir(batch_id)
    resolved = json.loads(
        (batch_dir / "resolved_batch.json").read_text(encoding="utf-8")
    )
    manifest = json.loads((batch_dir / "manifest.json").read_text(encoding="utf-8"))
    child_spec = json.loads(
        (
            service.runs_root / resolved["variants"][0]["run_id"]
            / "resolved_spec.json"
        ).read_text(encoding="utf-8")
    )

    assert calls == ["capture"]
    assert resolved["framework_implementation_hash"] == "framework-at-startup"
    assert manifest["framework_implementation_hash"] == "framework-at-startup"
    assert child_spec["framework_implementation_hash"] == "framework-at-startup"


def _api_with_submitter(tmp_path, monkeypatch, submitter):
    import src.stockpred.fund_rotation.batch_service as batch_module

    class StubBatchService:
        def __init__(self, batches_dir, **kwargs):
            self.persistence = BatchPersistence(batches_dir)

        def recover_interrupted(self):
            return []

        def submit_batch(self, request):
            return submitter(request)

    monkeypatch.setattr(batch_module, "BatchService", StubBatchService)
    app = FastAPI()
    register_fund_rotation_routes(
        app, tmp_path / "runs", lambda: None, lambda: None,
        stockpred_root=tmp_path,
    )
    return app


def _api_with_batch_storage(tmp_path, monkeypatch):
    import src.stockpred.fund_rotation.batch_service as batch_module

    captured = {}

    class StubBatchService:
        def __init__(self, batches_dir, *, runs_root, **kwargs):
            self.persistence = BatchPersistence(batches_dir)
            self.runs_root = runs_root
            captured["service"] = self

        def recover_interrupted(self):
            return []

    monkeypatch.setattr(batch_module, "BatchService", StubBatchService)
    app = FastAPI()
    register_fund_rotation_routes(
        app, tmp_path / "runs", lambda: None, lambda: None,
        stockpred_root=tmp_path,
    )
    return app, captured["service"]


def test_idempotent_existing_batch_returns_http_200(tmp_path, monkeypatch):
    app = _api_with_submitter(
        tmp_path,
        monkeypatch,
        lambda request: {"batch_id": "existing", "status": "EXISTING"},
    )
    response = TestClient(app).post(
        "/stockpred/fund-rotation/strategy-batches",
        json=_request([
            {"strategy_id": "fake_batch", "params": {"lookback_days": 30}},
        ]).model_dump(mode="json"),
    )

    assert response.status_code == 200
    assert response.json() == {"batch_id": "existing", "status": "EXISTING"}


def test_idempotency_conflict_is_structured_http_409(tmp_path, monkeypatch):
    def conflict(_request):
        raise BatchIdempotencyError("IDEMPOTENCY_CONFLICT", "payload differs")

    app = _api_with_submitter(tmp_path, monkeypatch, conflict)
    response = TestClient(app, raise_server_exceptions=False).post(
        "/stockpred/fund-rotation/strategy-batches",
        json=_request([
            {"strategy_id": "fake_batch", "params": {"lookback_days": 30}},
        ]).model_dump(mode="json"),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "IDEMPOTENCY_CONFLICT",
        "message": "payload differs",
    }


def _assert_parent_terminal(batch_dir, stage):
    state = json.loads((batch_dir / "state.json").read_text(encoding="utf-8"))
    events = [
        json.loads(line)
        for line in (batch_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert state["stage"] == stage
    assert events[-1]["event_type"] == "TERMINAL"
    assert events[-1]["scope"] == "BATCH"
    assert events[-1]["stage"] == stage
    assert events[-1]["message"] == stage


def test_snapshot_failure_persists_legal_terminal_envelope(tmp_path):
    service = _legacy_service(tmp_path, fail_frames=True)
    request = _request([
        {"strategy_id": "fake_batch", "params": {"lookback_days": 30}},
    ])
    outcome = service.submit_batch(request)
    service.run_batch_sync(outcome["batch_id"])

    _assert_parent_terminal(service.persistence.batch_dir(outcome["batch_id"]), "FAILED")


def test_validation_failure_persists_legal_terminal_envelope(tmp_path):
    service = _legacy_service(tmp_path)
    request = _request([
        {"strategy_id": "fake_batch", "params": {"lookback_days": 30}},
        {"strategy_id": "fake_batch", "params": {"lookback_days": 30}},
    ])
    with pytest.raises(Exception):
        service.submit_batch(request)
    record, created = service.persistence.submit(
        request.idempotency_key, canonical_payload_hash(request),
    )
    assert created is False

    _assert_parent_terminal(service.persistence.batch_dir(record["batch_id"]), "FAILED")


def test_canceled_parent_persists_legal_terminal_envelope(tmp_path):
    service = _legacy_service(tmp_path)
    request = _request([
        {"strategy_id": "fake_batch", "params": {"lookback_days": 30}},
    ])
    outcome = service.submit_batch(request)
    assert service.cancel_batch(outcome["batch_id"]) is True
    service.run_batch_sync(outcome["batch_id"])

    _assert_parent_terminal(service.persistence.batch_dir(outcome["batch_id"]), "CANCELED")


def test_unpublished_success_is_recovered_as_failed_interrupted(tmp_path):
    service = _legacy_service(tmp_path)
    batch_dir = service.persistence.batch_dir("parent-success")
    batch_dir.mkdir(parents=True)
    atomic_write_json(batch_dir / "state.json", {
        "schema_version": "2", "batch_id": "parent-success",
        "stage": "SUCCEEDED", "mode": "RESEARCH_ONLY",
    })
    child_dir = service.runs_root / "child-success"
    child_dir.mkdir(parents=True)
    atomic_write_json(child_dir / "state.json", {
        "schema_version": "2", "batch_id": "parent-success",
        "run_id": "child-success", "variant_key": "fake_batch@one",
        "strategy_id": "fake_batch", "stage": "SUCCEEDED",
        "mode": "RESEARCH_ONLY",
    })

    recovered = service.recover_interrupted()

    assert recovered == ["parent-success"]
    for run_dir in (batch_dir, child_dir):
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        terminal = json.loads(
            (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()[-1]
        )
        assert state["stage"] == "FAILED_INTERRUPTED"
        assert terminal["stage"] == "FAILED_INTERRUPTED"


def test_batch_detail_does_not_expose_success_without_valid_manifest(
    tmp_path, monkeypatch,
):
    app, service = _api_with_batch_storage(tmp_path, monkeypatch)
    batch_dir = service.persistence.batch_dir("unpublished")
    batch_dir.mkdir(parents=True)
    atomic_write_json(batch_dir / "state.json", {
        "schema_version": "2", "batch_id": "unpublished",
        "stage": "SUCCEEDED", "mode": "RESEARCH_ONLY",
    })

    response = TestClient(app).get(
        "/stockpred/fund-rotation/strategy-batches/unpublished"
    )

    assert response.status_code == 200
    assert response.json()["state"]["stage"] == "WRITING_RESULTS"


def test_batch_artifact_rejects_manifest_checksum_mismatch(tmp_path, monkeypatch):
    app, service = _api_with_batch_storage(tmp_path, monkeypatch)
    batch_dir = service.persistence.batch_dir("tampered")
    batch_dir.mkdir(parents=True)
    artifact = batch_dir / "reports.json"
    artifact.write_text('{"ranking": []}', encoding="utf-8")
    original_checksum = compute_file_checksum(artifact)
    atomic_write_json(batch_dir / "state.json", {
        "schema_version": "2", "batch_id": "tampered",
        "stage": "SUCCEEDED", "mode": "RESEARCH_ONLY",
    })
    atomic_write_json(batch_dir / "manifest.json", {
        "batch_id": "tampered", "status": "SUCCEEDED",
        "files": ["reports.json"],
        "file_details": {"reports.json": {"checksum": original_checksum}},
    })
    artifact.write_text('{"ranking": ["changed"]}', encoding="utf-8")

    response = TestClient(app).get(
        "/stockpred/fund-rotation/strategy-batches/tampered/artifacts/reports.json"
    )

    assert response.status_code == 409


def test_child_stage_rejects_skips_before_mutating_state(tmp_path):
    service = _service(tmp_path)
    request = _request([
        {"strategy_id": "fake_batch", "params": {"lookback_days": 30}},
    ])
    outcome = service.submit_batch(request)
    prepared = service._prepared[outcome["batch_id"]]
    plan = prepared["plans"][0]
    kwargs = {
        "batch_id": outcome["batch_id"], "request": request,
        "identity": plan["identity"], "run_id": plan["run_id"],
        "snapshot": prepared["snapshot"],
    }
    service._record_child_stage(**kwargs, stage="QUEUED")

    with pytest.raises(InvalidTransitionError):
        service._record_child_stage(**kwargs, stage="EXECUTING")

    state = json.loads(
        (service.runs_root / plan["run_id"] / "state.json").read_text(
            encoding="utf-8",
        )
    )
    assert state["stage"] == "QUEUED"


def test_batch_sse_never_invents_terminal_event_from_manifest(tmp_path, monkeypatch):
    """A terminal state may close SSE, but only persisted events may be emitted."""
    import src.stockpred.fund_rotation.batch_service as batch_module

    captured = {}

    class StubBatchService:
        def __init__(self, batches_dir, **kwargs):
            self.persistence = BatchPersistence(batches_dir)
            captured["service"] = self

        def recover_interrupted(self):
            return []

    monkeypatch.setattr(batch_module, "BatchService", StubBatchService)
    app = FastAPI()
    register_fund_rotation_routes(
        app, tmp_path / "runs", lambda: None, lambda: None,
        stockpred_root=tmp_path,
    )
    batch_dir = captured["service"].persistence.batch_dir("terminal-batch")
    batch_dir.mkdir(parents=True)
    atomic_write_json(batch_dir / "state.json", {
        "schema_version": "2",
        "batch_id": "terminal-batch",
        "stage": "SUCCEEDED",
        "mode": "RESEARCH_ONLY",
    })
    persisted = BatchEventLog(batch_dir, batch_id="terminal-batch").append(
        event_type="BATCH_STAGE", scope="BATCH", stage="WRITING_RESULTS",
    )
    atomic_write_json(batch_dir / "manifest.json", {
        "batch_id": "terminal-batch", "status": "SUCCEEDED", "files": [],
    })

    response = TestClient(app).get(
        "/stockpred/fund-rotation/strategy-batches/terminal-batch/events"
    )
    emitted = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]

    assert response.status_code == 200
    assert emitted == [persisted]


def test_batch_sse_does_not_emit_success_without_valid_manifest(
    tmp_path, monkeypatch,
):
    app, service = _api_with_batch_storage(tmp_path, monkeypatch)
    batch_dir = service.persistence.batch_dir("unpublished-sse")
    batch_dir.mkdir(parents=True)
    atomic_write_json(batch_dir / "state.json", {
        "schema_version": "2", "batch_id": "unpublished-sse",
        "stage": "SUCCEEDED", "mode": "RESEARCH_ONLY",
    })
    BatchEventLog(batch_dir, batch_id="unpublished-sse").append(
        event_type="TERMINAL", scope="BATCH", stage="SUCCEEDED",
        message="SUCCEEDED",
    )

    response = TestClient(app).get(
        "/stockpred/fund-rotation/strategy-batches/unpublished-sse/events"
    )

    assert response.status_code == 200
    assert "event: done" not in response.text
    assert '"stage": "SUCCEEDED"' not in response.text


def test_recovery_never_mutates_files_after_manifest_publication(tmp_path):
    service = _legacy_service(tmp_path)
    batch_id = _run_two(service)
    batch_dir = service.persistence.batch_dir(batch_id)
    before = {
        path.name: path.read_bytes()
        for path in batch_dir.iterdir()
        if path.is_file()
    }

    fresh = _legacy_service(tmp_path)
    fresh.recover_interrupted()

    after = {
        path.name: path.read_bytes()
        for path in batch_dir.iterdir()
        if path.is_file()
    }
    assert after == before


def test_recovery_rejects_manifest_with_tampered_parent_artifact(tmp_path):
    service = _legacy_service(tmp_path)
    batch_id = _run_two(service)
    batch_dir = service.persistence.batch_dir(batch_id)
    (batch_dir / "reports.json").write_text(
        '{"comparison_available": false}', encoding="utf-8",
    )

    fresh = _legacy_service(tmp_path)
    recovered = fresh.recover_interrupted()

    assert recovered == [batch_id]
    state = json.loads((batch_dir / "state.json").read_text(encoding="utf-8"))
    assert state["stage"] == "FAILED_INTERRUPTED"


def test_chart_reads_nonempty_ohlcv_from_v2_pinned_fund_version(
    tmp_path, monkeypatch,
):
    import pandas as pd

    runs_dir = tmp_path / "runs"
    stockpred_root = tmp_path / "stockpred"
    run_id = "chart-v2"
    run_dir = runs_dir / "fund_rotation" / run_id
    run_dir.mkdir(parents=True)
    state = {
        "schema_version": "2", "stage": "SUCCEEDED", "run_id": run_id,
        "params_fingerprint": "params-fp",
    }
    snapshot = {
        "fund_version": 17, "fund_adj_version": 23, "dim_version": 31,
        "universe_codes": ["E1"], "trading_dates": ["20240102"],
        "fingerprint": "snapshot-fp",
    }
    atomic_write_json(run_dir / "state.json", state)
    atomic_write_json(run_dir / "data_snapshot.json", snapshot)
    state_checksum = compute_file_checksum(run_dir / "state.json")
    snapshot_checksum = compute_file_checksum(run_dir / "data_snapshot.json")
    atomic_write_json(run_dir / "manifest.json", {
        "status": "SUCCEEDED", "run_id": run_id,
        "params_fingerprint": "params-fp", "state_checksum": state_checksum,
        "files": ["state.json", "data_snapshot.json", "manifest.json"],
        "file_details": {
            "state.json": {"checksum": state_checksum},
            "data_snapshot.json": {"checksum": snapshot_checksum},
        },
    })
    opened = []

    class FakeDataset:
        def to_table(self, *, filter=None):
            assert filter == "ts_code = 'E1'"
            return types.SimpleNamespace(to_pandas=lambda: pd.DataFrame([{
                "ts_code": "E1", "trade_date": "20240102", "open": 1.0,
                "high": 1.2, "low": 0.9, "close": 1.1, "vol": 100,
            }]))

    def dataset(path, *, version=None):
        opened.append((path, version))
        return FakeDataset()

    monkeypatch.setitem(sys.modules, "lance", types.SimpleNamespace(dataset=dataset))
    app = FastAPI()
    register_fund_rotation_routes(
        app, runs_dir, lambda: None, lambda: None,
        stockpred_root=stockpred_root,
    )

    response = TestClient(app).get(
        f"/stockpred/fund-rotation/backtests/{run_id}/instruments/E1/chart"
    )

    assert response.status_code == 200
    assert response.json()["ohlcv"] == [{
        "trade_date": "20240102", "open": 1.0, "high": 1.2,
        "low": 0.9, "close": 1.1, "vol": 100,
    }]
    assert opened == [(
        str(stockpred_root / "data" / "lance" / "market_core" / "fund.lance"),
        17,
    )]


def test_legacy_startup_recovery_does_not_claim_or_mutate_batch_children(tmp_path):
    """The route's legacy recovery runs first but must leave v2 children alone."""
    runs_dir = tmp_path / "runs"
    child_dir = runs_dir / "fund_rotation" / "child-run"
    child_dir.mkdir(parents=True)
    atomic_write_json(child_dir / "state.json", {
        "schema_version": "2",
        "stage": "EXECUTING",
        "batch_id": "batch-1",
        "run_id": "child-run",
        "variant_key": "fake_batch@abc",
        "strategy_id": "fake_batch",
    })
    atomic_write_json(child_dir / "manifest.json", {
        "status": "SUCCEEDED",
        "run_id": "child-run",
    })
    before = {
        path.name: path.read_bytes()
        for path in child_dir.iterdir()
        if path.is_file()
    }

    recovered = FundRotationBacktestService(runs_dir).recover_interrupted()

    after = {
        path.name: path.read_bytes()
        for path in child_dir.iterdir()
        if path.is_file()
    }
    assert recovered == 0
    assert after == before
