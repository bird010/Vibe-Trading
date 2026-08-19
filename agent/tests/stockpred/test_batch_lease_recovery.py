"""P1 review fixes: recoverable execution lease, crash-safe lock, UUID store boundary."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.stockpred.batch_store import StockPredBatchStore


# ---------------------------------------------------------------------------
# Finding 1: scheduling ownership as a recoverable, expiring lease
# ---------------------------------------------------------------------------


class TestExecutionLeaseRecovery:
    def test_fresh_lease_is_exclusive(self, tmp_path: Path) -> None:
        store = StockPredBatchStore(tmp_path)
        key = str(uuid.uuid4())
        store.claim_idempotency_key(key, "batch_a")

        assert isinstance(store.try_claim_execution(key), str)
        assert store.try_claim_execution(key) is None

    def test_stale_lease_can_be_taken_over(self, tmp_path: Path) -> None:
        """A lease whose TTL elapsed (owner crashed) is reclaimable exactly once."""
        store = StockPredBatchStore(tmp_path)
        key = str(uuid.uuid4())
        store.claim_idempotency_key(key, "batch_a")

        now = datetime.now(timezone.utc)
        assert isinstance(store.try_claim_execution(key, now=now, lease_seconds=300), str)
        # Still exclusive while the lease is within its TTL.
        assert store.try_claim_execution(key, now=now + timedelta(seconds=100), lease_seconds=300) is None
        # After the TTL elapses, a retry takes over.
        assert isinstance(store.try_claim_execution(key, now=now + timedelta(seconds=400), lease_seconds=300), str)

    def test_release_allows_immediate_takeover(self, tmp_path: Path) -> None:
        """Releasing the lease (create_task failed) lets the next claim win at once."""
        store = StockPredBatchStore(tmp_path)
        key = str(uuid.uuid4())
        store.claim_idempotency_key(key, "batch_a")

        token = store.try_claim_execution(key)
        assert isinstance(token, str)
        assert store.release_execution(key, token) is True
        assert isinstance(store.try_claim_execution(key), str)

    def test_release_is_idempotent(self, tmp_path: Path) -> None:
        store = StockPredBatchStore(tmp_path)
        key = str(uuid.uuid4())
        store.claim_idempotency_key(key, "batch_a")
        store.release_execution(key, "no-such-owner")  # nothing claimed yet
        assert isinstance(store.try_claim_execution(key), str)

    def test_confirm_makes_lease_durable(self, tmp_path: Path) -> None:
        """Once the task confirms it started, the lease no longer expires."""
        store = StockPredBatchStore(tmp_path)
        key = str(uuid.uuid4())
        store.claim_idempotency_key(key, "batch_a")

        past = datetime.now(timezone.utc) - timedelta(seconds=1000)
        token = store.try_claim_execution(key, now=past, lease_seconds=300)
        assert isinstance(token, str)
        assert store.confirm_execution(key, token) is True
        future = past + timedelta(seconds=10_000)
        assert store.try_claim_execution(key, now=future, lease_seconds=300) is None

    def test_lease_state_is_persisted(self, tmp_path: Path) -> None:
        store = StockPredBatchStore(tmp_path)
        key = str(uuid.uuid4())
        store.claim_idempotency_key(key, "batch_a")
        store.try_claim_execution(key)

        payload = json.loads((tmp_path / ".idempotency" / f"{uuid.UUID(key).hex}.json").read_text(encoding="utf-8"))
        assert payload["execution_scheduled"] is True
        assert payload["execution_lease"]

    def test_route_releases_lease_when_task_creation_fails(self, tmp_path: Path, monkeypatch) -> None:
        """If asyncio.create_task throws, the route releases the lease and a retry schedules."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from src.api import stockpred_routes

        store = StockPredBatchStore(tmp_path / "strategy_batches")
        key = str(uuid.uuid4())
        store.claim_idempotency_key(key, "batch_x")

        class Service:
            def __init__(self) -> None:
                self.store = store
                self.executed: list[str] = []

            def reserve_idempotent(self, request, *, idempotency_key):
                return "batch_x", False

            def execute(self, batch_id):
                self.executed.append(batch_id)
                return batch_id

        service = Service()
        monkeypatch.setattr(stockpred_routes, "build_batch_service", lambda *_: service)

        calls = {"n": 0}
        def flaky_schedule_task(coro):
            calls["n"] += 1
            if calls["n"] == 1:
                coro.close()
                raise RuntimeError("event loop saturated")

        monkeypatch.setattr(stockpred_routes, "_schedule_task", flaky_schedule_task)

        app = FastAPI()
        stockpred_routes.register_stockpred_routes(
            app, runs_dir=tmp_path, require_auth=lambda: None, require_event_stream_auth=lambda: None
        )
        client = TestClient(app, raise_server_exceptions=False)
        body = {"start": "2025-01-01", "end": "2025-03-31", "strategy_ids": ["alpha101_1"], "idempotency_key": key}

        first = client.post("/stockpred/strategy-batches", json=body)
        assert first.status_code == 500
        # Lease was released -> the same key is immediately reclaimable.
        assert isinstance(store.try_claim_execution(key), str)


# ---------------------------------------------------------------------------
# Finding 2: crash-safe .exec.lock (stale persisted lock recovery)
# ---------------------------------------------------------------------------


class TestStaleExecLockRecovery:
    def test_stale_persisted_lock_is_recovered(self, tmp_path: Path) -> None:
        """A .exec.lock left behind by a crashed process must not block forever."""
        store = StockPredBatchStore(tmp_path)
        key = str(uuid.uuid4())
        store.claim_idempotency_key(key, "batch_a")

        lock = tmp_path / ".idempotency" / f"{uuid.UUID(key).hex}.exec.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(json.dumps({"token": "crashed-owner"}), encoding="utf-8")
        stale = time.time() - 3600
        os.utime(lock, (stale, stale))

        assert isinstance(store.try_claim_execution(key), str)
        assert not lock.exists()

    def test_fresh_lock_is_not_stolen(self, tmp_path: Path) -> None:
        """A recently-created .exec.lock (active owner) must not be taken over."""
        store = StockPredBatchStore(tmp_path)
        key = str(uuid.uuid4())
        store.claim_idempotency_key(key, "batch_a")

        lock = tmp_path / ".idempotency" / f"{uuid.UUID(key).hex}.exec.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(json.dumps({"token": "active-owner"}), encoding="utf-8")

        assert store.try_claim_execution(key) is None
        assert lock.exists()
        assert json.loads(lock.read_text(encoding="utf-8"))["token"] == "active-owner"


# ---------------------------------------------------------------------------
# Finding 3: UUID validation/canonicalization at the store boundary
# ---------------------------------------------------------------------------


class TestStoreBoundaryKeyValidation:
    def test_real_path_traversal_key_is_rejected(self, tmp_path: Path) -> None:
        store = StockPredBatchStore(tmp_path)
        with pytest.raises(ValueError):
            store.claim_idempotency_key("../../escape", "batch_x")
        assert not (tmp_path.parent / "escape.json").exists()
        assert not (tmp_path / ".." / "escape.json").exists()
        assert list(tmp_path.iterdir()) == []

    def test_traversal_key_rejected_on_every_public_mapping_fn(self, tmp_path: Path) -> None:
        store = StockPredBatchStore(tmp_path)
        with pytest.raises(ValueError):
            store.try_claim_execution("../../escape")
        with pytest.raises(ValueError):
            store.release_execution("../../escape", "token")
        with pytest.raises(ValueError):
            store.confirm_execution("../../escape", "token")
        with pytest.raises(ValueError):
            store.mark_execution_scheduled("../../escape")
        with pytest.raises(ValueError):
            store.needs_execution_takeover("../../escape")

    def test_uuid_forms_canonicalize_to_same_mapping(self, tmp_path: Path) -> None:
        store = StockPredBatchStore(tmp_path)
        value = uuid.uuid4()
        assert store.claim_idempotency_key(str(value), "batch_a") is None
        # Hex (no dashes) and uppercase refer to the same key.
        assert store.claim_idempotency_key(value.hex, "batch_b") == "batch_a"
        assert store.claim_idempotency_key(str(value).upper(), "batch_c") == "batch_a"
        assert len(list((tmp_path / ".idempotency").iterdir())) == 1


# ---------------------------------------------------------------------------
# P1 (review round 2): fencing / owner token on the recoverable lease
# ---------------------------------------------------------------------------


def _read_mapping(tmp_path: Path, key: str) -> dict:
    return json.loads(
        (tmp_path / ".idempotency" / f"{uuid.UUID(key).hex}.json").read_text(encoding="utf-8")
    )


class TestLeaseFencingToken:
    def test_claim_returns_opaque_lease_token(self, tmp_path: Path) -> None:
        """try_claim_execution must hand back an ownership token, not a bare bool."""
        store = StockPredBatchStore(tmp_path)
        key = str(uuid.uuid4())
        store.claim_idempotency_key(key, "batch_a")

        token = store.try_claim_execution(key)
        assert isinstance(token, str) and token
        # A contender gets nothing while the lease is live.
        assert store.try_claim_execution(key) is None
        # The token is persisted as the mapping owner.
        assert _read_mapping(tmp_path, key)["execution_owner"] == token

    def test_confirm_requires_matching_token(self, tmp_path: Path) -> None:
        """confirm_execution must atomically compare the token before mutating."""
        store = StockPredBatchStore(tmp_path)
        key = str(uuid.uuid4())
        store.claim_idempotency_key(key, "batch_a")
        token = store.try_claim_execution(key)

        # A forged/stale token is rejected and leaves the mapping untouched.
        assert store.confirm_execution(key, "not-the-owner") is False
        assert _read_mapping(tmp_path, key)["execution_confirmed"] is False
        # The lease is still live and unconfirmed -> a contender cannot claim.
        assert store.try_claim_execution(key) is None

        # The real owner confirms.
        assert store.confirm_execution(key, token) is True
        assert _read_mapping(tmp_path, key)["execution_confirmed"] is True

    def test_release_requires_matching_token(self, tmp_path: Path) -> None:
        """release_execution must atomically compare the token before mutating."""
        store = StockPredBatchStore(tmp_path)
        key = str(uuid.uuid4())
        store.claim_idempotency_key(key, "batch_a")
        token = store.try_claim_execution(key)

        # A non-owner cannot release someone else's lease.
        assert store.release_execution(key, "not-the-owner") is False
        assert store.try_claim_execution(key) is None  # still held

        # The owner releases, making the key immediately reclaimable.
        assert store.release_execution(key, token) is True
        assert _read_mapping(tmp_path, key)["execution_scheduled"] is False
        assert isinstance(store.try_claim_execution(key), str)

    def test_expired_owner_cannot_confirm_after_takeover(self, tmp_path: Path) -> None:
        """Once a new owner takes over an expired lease, the old owner is fenced off."""
        store = StockPredBatchStore(tmp_path)
        key = str(uuid.uuid4())
        store.claim_idempotency_key(key, "batch_a")

        now = datetime.now(timezone.utc)
        old_token = store.try_claim_execution(key, now=now, lease_seconds=300)
        assert isinstance(old_token, str)

        # TTL elapses without confirm (owner crashed) -> a new owner takes over.
        new_token = store.try_claim_execution(key, now=now + timedelta(seconds=400), lease_seconds=300)
        assert isinstance(new_token, str) and new_token != old_token
        assert _read_mapping(tmp_path, key)["execution_owner"] == new_token

        # The expired owner can no longer confirm...
        assert store.confirm_execution(key, old_token) is False
        # ...nor release the new owner's lease.
        assert store.release_execution(key, old_token) is False
        assert _read_mapping(tmp_path, key)["execution_owner"] == new_token
        assert _read_mapping(tmp_path, key)["execution_scheduled"] is True

        # The new owner still holds a live lease and is the only one who can confirm.
        assert store.try_claim_execution(key, now=now + timedelta(seconds=401), lease_seconds=300) is None
        assert store.confirm_execution(key, new_token) is True
        assert _read_mapping(tmp_path, key)["execution_confirmed"] is True


# ---------------------------------------------------------------------------
# P1 (review round 2): atomic stale-lock reclaim (no TOCTOU unlink)
# ---------------------------------------------------------------------------


class TestAtomicStaleLockReclaim:
    @staticmethod
    def _plant_lock(tmp_path: Path, key: str, token: str, age_seconds: float) -> Path:
        lock = tmp_path / ".idempotency" / f"{uuid.UUID(key).hex}.exec.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(json.dumps({"token": token}), encoding="utf-8")
        mtime = time.time() - age_seconds
        os.utime(lock, (mtime, mtime))
        return lock

    def test_concurrent_stale_recovery_admits_one_lease_owner(self, tmp_path: Path) -> None:
        """Many contenders racing to reclaim one stale lock must yield exactly one
        effective lease owner: exactly one returned token can confirm."""
        store = StockPredBatchStore(tmp_path)
        key = str(uuid.uuid4())
        store.claim_idempotency_key(key, "batch_a")
        self._plant_lock(tmp_path, key, "crashed-owner", age_seconds=3600)

        tokens: list[str] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(8)

        def worker() -> None:
            barrier.wait()
            try:
                token = store.try_claim_execution(key)
                if token:
                    tokens.append(token)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"concurrent reclaim raised: {errors}"
        assert tokens, "at least one contender must reclaim the stale lock"
        # Exactly one of the issued tokens is the persisted owner and can confirm.
        confirmed = [tok for tok in tokens if store.confirm_execution(key, tok)]
        assert len(confirmed) == 1
        assert _read_mapping(tmp_path, key)["execution_owner"] == confirmed[0]

    def test_active_lock_is_never_deleted_or_replaced(self, tmp_path: Path) -> None:
        """A fresh (active) lock must not be unlinked or overwritten by reclaim."""
        store = StockPredBatchStore(tmp_path)
        key = str(uuid.uuid4())
        store.claim_idempotency_key(key, "batch_a")
        lock = self._plant_lock(tmp_path, key, "active-owner", age_seconds=0)

        assert store.try_claim_execution(key) is None
        assert lock.exists()
        assert json.loads(lock.read_text(encoding="utf-8"))["token"] == "active-owner"

    def test_stale_lock_is_reclaimed_and_released(self, tmp_path: Path) -> None:
        """A stale lock is reclaimed for the claim and removed once the claim ends."""
        store = StockPredBatchStore(tmp_path)
        key = str(uuid.uuid4())
        store.claim_idempotency_key(key, "batch_a")
        lock = self._plant_lock(tmp_path, key, "crashed-owner", age_seconds=3600)

        token = store.try_claim_execution(key)
        assert isinstance(token, str)
        # The transient lock must not be left behind to wedge future claims.
        assert not lock.exists()


class TestRouteExecutionFence:
    def test_superseded_lease_task_does_not_execute(self) -> None:
        """An old scheduled coroutine must not run the batch after lease takeover."""
        from src.api import stockpred_routes

        executed: list[str] = []

        class Store:
            def confirm_execution(self, key: str, token: str) -> bool:
                assert key == "key"
                assert token == "expired-owner"
                return False

        class Service:
            store = Store()

            @staticmethod
            def execute(batch_id: str) -> None:
                executed.append(batch_id)

        asyncio.run(stockpred_routes._execute_claimed_batch(Service(), "batch_x", "key", "expired-owner"))

        assert executed == []
