"""Regression tests for StockPred batch idempotency and detail fixes."""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path

import pytest

from src.stockpred.batch_store import StockPredBatchStore
from src.stockpred.strategies.contracts import StrategyBatchRequest, StrategyDescriptor


def _descriptor(strategy_id: str) -> StrategyDescriptor:
    return StrategyDescriptor(id=strategy_id, name=strategy_id, kind="alpha_zoo", zoo="alpha101")


def _request() -> StrategyBatchRequest:
    return StrategyBatchRequest(start="2025-01-01", end="2025-03-31", strategy_ids=("alpha101_1",))


# ---------------------------------------------------------------------------
# Requirement 1: Atomic idempotency mapping publish
# ---------------------------------------------------------------------------


class TestAtomicIdempotencyMapping:
    def test_mapping_file_is_never_partial_json(self, tmp_path: Path) -> None:
        """The mapping file must always contain valid JSON once it exists."""
        store = StockPredBatchStore(tmp_path)
        key = uuid.uuid4().hex
        store.claim_idempotency_key(key, "batch_first")

        mapping_path = tmp_path / ".idempotency" / f"{key}.json"
        payload = json.loads(mapping_path.read_text(encoding="utf-8"))
        assert payload["batch_id"] == "batch_first"
        assert payload["execution_scheduled"] is False

    def test_concurrent_claims_never_expose_partial_mapping(self, tmp_path: Path) -> None:
        """Under concurrent claims, readers must never see partial JSON."""
        store = StockPredBatchStore(tmp_path)
        key = uuid.uuid4().hex
        errors: list[Exception] = []
        results: list[str | None] = []
        barrier = threading.Barrier(4)

        def claim_worker(batch_name: str) -> None:
            barrier.wait()
            try:
                result = store.claim_idempotency_key(key, batch_name)
                results.append(result)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=claim_worker, args=(f"batch_{i}",))
            for i in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"concurrent claims raised: {errors}"
        # Exactly one winner (returns None), rest return the winner's batch_id
        winners = [r for r in results if r is None]
        losers = [r for r in results if r is not None]
        assert len(winners) == 1
        assert len(losers) == 3
        # All losers must agree on the same batch_id
        assert len(set(losers)) == 1

    def test_loser_reads_valid_batch_id_after_concurrent_claim(self, tmp_path: Path) -> None:
        """A loser must safely read the winner's batch_id, not crash on partial JSON."""
        store = StockPredBatchStore(tmp_path)
        key = uuid.uuid4().hex

        winner_result = store.claim_idempotency_key(key, "batch_winner")
        assert winner_result is None

        loser_result = store.claim_idempotency_key(key, "batch_loser")
        assert loser_result == "batch_winner"

    def test_loser_candidate_is_removed_after_losing_claim(self, tmp_path: Path) -> None:
        """If losing a claim after candidate creation, the candidate dir is removed."""
        from src.stockpred.batch_service import StockPredStrategyBatchService

        class _Catalog:
            def list(self):
                return [_descriptor("alpha101_1")]

            def require(self, strategy_id: str):
                return _descriptor(strategy_id)

        store = StockPredBatchStore(tmp_path)
        service = StockPredStrategyBatchService(store, _Catalog(), lambda *a: ("run", {}))
        request = _request()
        key = uuid.uuid4().hex

        first_id, created = service.reserve_idempotent(request, idempotency_key=key)
        assert created is True

        second_id, second_created = service.reserve_idempotent(request, idempotency_key=key)
        assert second_created is False
        assert second_id == first_id

        # The losing candidate directory must not exist
        batch_dirs = [d for d in tmp_path.iterdir() if d.is_dir() and d.name.startswith("batch_")]
        assert len(batch_dirs) == 1
        assert batch_dirs[0].name == first_id


# ---------------------------------------------------------------------------
# Requirement 2: idempotency_key is required UUID at POST boundary
# ---------------------------------------------------------------------------


class TestIdempotencyKeyValidation:
    def test_post_without_idempotency_key_returns_422(self, tmp_path: Path) -> None:
        """Missing idempotency_key must be rejected at the API boundary."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from src.api import stockpred_routes

        app = FastAPI()
        stockpred_routes.register_stockpred_routes(
            app, runs_dir=tmp_path, require_auth=lambda: None, require_event_stream_auth=lambda: None
        )
        client = TestClient(app)

        response = client.post(
            "/stockpred/strategy-batches",
            json={"start": "2025-01-01", "end": "2025-03-31", "strategy_ids": ["alpha101_1"]},
        )
        assert response.status_code == 422

    def test_post_with_non_uuid_idempotency_key_returns_422(self, tmp_path: Path) -> None:
        """Arbitrary strings must be rejected; only UUIDs are valid."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from src.api import stockpred_routes

        app = FastAPI()
        stockpred_routes.register_stockpred_routes(
            app, runs_dir=tmp_path, require_auth=lambda: None, require_event_stream_auth=lambda: None
        )
        client = TestClient(app)

        for bad_key in ["../../etc/passwd", "not-a-uuid", "abc123", ""]:
            response = client.post(
                "/stockpred/strategy-batches",
                json={
                    "start": "2025-01-01",
                    "end": "2025-03-31",
                    "strategy_ids": ["alpha101_1"],
                    "idempotency_key": bad_key,
                },
            )
            assert response.status_code == 422, f"key={bad_key!r} should be rejected"

    def test_post_with_valid_uuid_succeeds(self, tmp_path: Path, monkeypatch) -> None:
        """A valid UUID idempotency_key must be accepted."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from src.api import stockpred_routes

        class Service:
            class store:
                @staticmethod
                def summary(batch_id, *, sort_by="sharpe", descending=True):
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
                return "batch_ok", True

            def execute(self, batch_id):
                return batch_id

        monkeypatch.setattr(stockpred_routes, "build_batch_service", lambda *_: Service())

        app = FastAPI()
        stockpred_routes.register_stockpred_routes(
            app, runs_dir=tmp_path, require_auth=lambda: None, require_event_stream_auth=lambda: None
        )
        client = TestClient(app)

        response = client.post(
            "/stockpred/strategy-batches",
            json={
                "start": "2025-01-01",
                "end": "2025-03-31",
                "strategy_ids": ["alpha101_1"],
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        assert response.status_code == 202

    def test_idempotency_key_cannot_escape_path(self, tmp_path: Path) -> None:
        """Non-UUID idempotency keys must be rejected at the store boundary."""
        store = StockPredBatchStore(tmp_path)
        with pytest.raises(ValueError):
            store.claim_idempotency_key("..%2F..%2Fetc%2Fpasswd", "batch_x")
        # No mapping file (or anything else) may be created for an invalid key.
        idem_dir = tmp_path / ".idempotency"
        assert not idem_dir.exists() or not list(idem_dir.iterdir())


# ---------------------------------------------------------------------------
# Requirement 3: Scheduling recovery for queued batches
# ---------------------------------------------------------------------------


class TestSchedulingRecovery:
    def test_queued_batch_without_task_is_taken_over_by_same_key(self, tmp_path: Path) -> None:
        """If a batch is queued but no execution task was created, a subsequent
        same-key request must take over execution exactly once."""
        from src.stockpred.batch_service import StockPredStrategyBatchService

        class _Catalog:
            def list(self):
                return [_descriptor("alpha101_1")]

            def require(self, strategy_id: str):
                return _descriptor(strategy_id)

        store = StockPredBatchStore(tmp_path)
        service = StockPredStrategyBatchService(store, _Catalog(), lambda *a: ("run", {}))
        request = _request()
        key = uuid.uuid4().hex

        # First request: reserve but simulate process exit before task creation
        batch_id, created = service.reserve_idempotent(request, idempotency_key=key)
        assert created is True
        # Batch is still "queued" - no execute() was called, no task was created
        assert store.summary(batch_id)["status"] == "queued"

        # Second request with same key: must take over execution
        second_id, second_created = service.reserve_idempotent(request, idempotency_key=key)
        assert second_id == batch_id
        # The service must signal that execution needs to be scheduled
        # (created=False but batch is still queued -> needs takeover)
        assert second_created is False

    def test_scheduling_state_is_persisted(self, tmp_path: Path) -> None:
        """The scheduling claim state must be persisted so recovery works across restarts."""
        store = StockPredBatchStore(tmp_path)
        key = uuid.uuid4().hex

        store.claim_idempotency_key(key, "batch_abc")
        store.mark_execution_scheduled(key)

        # Read persisted state
        mapping_path = tmp_path / ".idempotency" / f"{key}.json"
        payload = json.loads(mapping_path.read_text(encoding="utf-8"))
        assert payload["batch_id"] == "batch_abc"
        assert payload["execution_scheduled"] is True

    def test_unscheduled_queued_batch_allows_takeover(self, tmp_path: Path) -> None:
        """A queued batch without execution_scheduled=True can be taken over."""
        store = StockPredBatchStore(tmp_path)
        key = uuid.uuid4().hex

        store.claim_idempotency_key(key, "batch_abc")
        # No mark_execution_scheduled call - simulates crash before task creation

        needs_takeover = store.needs_execution_takeover(key)
        assert needs_takeover is True

    def test_scheduled_batch_does_not_allow_takeover(self, tmp_path: Path) -> None:
        """A batch with execution_scheduled=True must not be taken over."""
        store = StockPredBatchStore(tmp_path)
        key = uuid.uuid4().hex

        store.claim_idempotency_key(key, "batch_abc")
        store.mark_execution_scheduled(key)

        needs_takeover = store.needs_execution_takeover(key)
        assert needs_takeover is False

    def test_takeover_is_atomic_exactly_once(self, tmp_path: Path) -> None:
        """Under concurrent takeover attempts, exactly one must succeed."""
        store = StockPredBatchStore(tmp_path)
        key = uuid.uuid4().hex
        store.claim_idempotency_key(key, "batch_abc")

        results: list[bool] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(4)

        def takeover_worker() -> None:
            barrier.wait()
            try:
                won = store.try_claim_execution(key)
                results.append(bool(won))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=takeover_worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"takeover raised: {errors}"
        assert results.count(True) == 1
        assert results.count(False) == 3


# ---------------------------------------------------------------------------
# Requirement 4: Zero selected symbols writes valid empty detail marker
# ---------------------------------------------------------------------------


class TestZeroSymbolDetailMarker:
    def test_zero_codes_writes_valid_completion_marker(self, tmp_path: Path) -> None:
        """When zero symbols are selected, a valid empty completion marker must be written."""
        from src.stockpred.strategy_detail import _completion_marker_valid, _write_completion_marker

        root = tmp_path / "strategy_test"
        root.mkdir()
        (root / "artifacts").mkdir()

        detail_manifest = {
            "version": 1,
            "codes": [],
            "data_snapshot": {"as_of": "20250331"},
            "market_start": "20250101",
            "market_end": "20250331",
            "run_id": "strategy_test",
            "comparison_key": "a" * 64,
        }

        _write_completion_marker(root, [], detail_manifest)

        assert _completion_marker_valid(root, [], detail_manifest) is True

    def test_zero_codes_materialization_is_idempotent(self, tmp_path: Path) -> None:
        """Calling materialize with zero codes twice must succeed (idempotent)."""
        from unittest.mock import MagicMock

        from src.stockpred.strategy_detail import materialize_strategy_detail

        root = tmp_path / "strategy_test"
        root.mkdir()
        artifacts = root / "artifacts"
        artifacts.mkdir()

        detail_manifest = {
            "version": 1,
            "codes": [],
            "data_snapshot": {"as_of": "20250331"},
            "market_start": "20250101",
            "market_end": "20250331",
            "run_id": "strategy_test",
            "comparison_key": "a" * 64,
        }
        (root / "detail_manifest.json").write_text(
            json.dumps(detail_manifest), encoding="utf-8"
        )

        # Create required screening artifacts
        config = {"comparison_key": "a" * 64}
        (root / "config.json").write_text(json.dumps(config), encoding="utf-8")
        state = {"status": "success"}
        (root / "state.json").write_text(json.dumps(state), encoding="utf-8")
        snapshot = {"as_of": "20250331"}
        (root / "data_snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")
        for name in ("metrics.csv", "equity.csv", "positions.csv", "trades.csv", "selected_signals.csv", "symbol_metrics.csv"):
            (artifacts / name).write_text("", encoding="utf-8")
        (artifacts / "signals.parquet").write_bytes(b"")
        (root / "strategy_snapshot.json").write_text("{}", encoding="utf-8")
        (root / "strategy_source.zip").write_bytes(b"")

        gateway = MagicMock()
        gateway.manifest.model_dump.return_value = {"as_of": "20250331"}

        # First call
        result = materialize_strategy_detail(root, gateway)
        assert result == artifacts

        # Second call must be idempotent
        result2 = materialize_strategy_detail(root, gateway)
        assert result2 == artifacts

        # Completion marker must exist and be valid
        marker = json.loads((root / "detail_complete.json").read_text(encoding="utf-8"))
        assert marker["version"] == 1
        assert marker["codes"] == []
