"""Persistent batch index for comparable StockPred strategy reports."""

from __future__ import annotations

import os
import json
import math
import re
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.stockpred.run_store import atomic_json
from src.stockpred.strategies.contracts import StrategyBatchRequest, StrategyDescriptor

def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# How long an unconfirmed execution lease stays exclusive before a retry may take over.
EXECUTION_LEASE_SECONDS = 300.0
# How long a persisted .exec.lock may survive (e.g. after a crash) before it is treated as stale.
EXEC_LOCK_STALE_SECONDS = 60.0


def _canonical_key(key: str) -> str:
    """Validate an idempotency key is a UUID and return its canonical hex form.

    Canonicalizing keeps all UUID spellings (dashed, hex, uppercase) on one
    mapping file and guarantees the key is safe to use as a path component.
    """
    try:
        return uuid.UUID(str(key)).hex
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"invalid idempotency key: {key!r}") from exc


class BatchLockUnavailableError(RuntimeError):
    """Raised when another worker owns the operating-system batch lock."""


class StockPredBatchStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def create(self, request: StrategyBatchRequest, descriptors: list[StrategyDescriptor], *, comparison_key: str) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        batch_id = f"batch_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:8]}"
        directory = self.root / batch_id
        directory.mkdir()
        atomic_json(directory / "request.json", request.model_dump(mode="json"))
        now = _now()
        atomic_json(
            directory / "state.json",
            {
                "batch_id": batch_id,
                "status": "queued",
                "phase": "queued",
                "screening_done": 0,
                "screening_total": len(descriptors),
                "detail_done": 0,
                "detail_total": 0,
                "heartbeat_at": None,
                "timings": {},
                "created_at": now,
                "updated_at": now,
                "comparison_key": comparison_key,
            },
        )
        atomic_json(directory / "reports.json", {"reports": [{"strategy_id": item.id, "strategy_name": item.name, "kind": item.kind, "zoo": item.zoo, "status": "queued", "run_id": None, "metrics": {}, "reason": None, "detail_status": None, "detail_reason": None} for item in descriptors]})
        return batch_id

    def claim_idempotency_key(self, key: str, batch_id: str) -> str | None:
        """Atomically claim an idempotency key mapping.

        Returns None if this call won the claim. Returns existing batch_id if
        the key was already claimed by another call.

        Uses atomic publish (temp+link) so competing readers never see partial JSON.
        """
        key = _canonical_key(key)
        mapping_dir = self.root / ".idempotency"
        mapping_dir.mkdir(parents=True, exist_ok=True)
        mapping = mapping_dir / f"{key}.json"
        payload = json.dumps({"batch_id": batch_id, "execution_scheduled": False}, ensure_ascii=False)
        temp = mapping_dir / f".{key}.{uuid.uuid4().hex[:8]}.tmp"
        temp.write_text(payload, encoding="utf-8")
        try:
            # os.link atomically creates mapping with full content; fails if mapping exists
            os.link(str(temp), str(mapping))
            return None
        except FileExistsError:
            # Another caller won - read their result
            return self._read_idempotency_mapping(mapping)["batch_id"]
        finally:
            temp.unlink(missing_ok=True)

    def _read_idempotency_mapping(self, path: Path) -> dict[str, Any]:
        """Read an idempotency mapping, retrying briefly if the file is being published."""
        import time

        for _ in range(20):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "batch_id" in data:
                    return data
            except (OSError, json.JSONDecodeError, KeyError):
                pass
            time.sleep(0.01)
        raise RuntimeError(f"idempotency mapping not readable: {path}")

    def _mapping_path(self, key: str) -> Path:
        return self.root / ".idempotency" / f"{_canonical_key(key)}.json"

    def mark_execution_scheduled(self, key: str) -> None:
        """Mark that an execution task has been created for this idempotency key."""
        mapping = self._mapping_path(key)
        data = self._read_idempotency_mapping(mapping)
        data["execution_scheduled"] = True
        data.setdefault("execution_lease", _now())
        data["execution_confirmed"] = True
        atomic_json(mapping, data)

    def needs_execution_takeover(self, key: str) -> bool:
        """Check if a queued batch needs execution takeover (no live lease)."""
        mapping = self._mapping_path(key)
        if not mapping.is_file():
            return False
        try:
            data = self._read_idempotency_mapping(mapping)
        except RuntimeError:
            return False
        if not data.get("execution_scheduled", False):
            return True
        if data.get("execution_confirmed", False):
            return False
        return self._lease_expired(data, datetime.now(timezone.utc), EXECUTION_LEASE_SECONDS)

    def try_claim_execution(
        self,
        key: str,
        *,
        now: datetime | None = None,
        lease_seconds: float = EXECUTION_LEASE_SECONDS,
    ) -> str | None:
        """Claim execution scheduling for an idempotency key as a recoverable lease.

        Returns an opaque owner token if this call holds the lease (exactly-once),
        or None if another caller holds a live lease. The token is a fencing token:
        confirm_execution/release_execution only mutate the mapping if presented
        with the token of the current owner, so an owner whose lease expired and
        was taken over can no longer confirm or release. A lease whose TTL elapsed
        without being confirmed (e.g. the owner crashed between claiming and
        creating the task) is reclaimable, so the same key can safely take over.
        """
        moment = now or datetime.now(timezone.utc)
        mapping = self._mapping_path(key)
        lock_path = mapping.with_suffix(".exec.lock")
        fence = uuid.uuid4().hex
        if not self._acquire_exec_lock(lock_path, fence):
            return None
        try:
            data = self._read_idempotency_mapping(mapping)
            if data.get("execution_scheduled", False):
                if data.get("execution_confirmed", False):
                    return None
                if not self._lease_expired(data, moment, lease_seconds):
                    return None
            token = uuid.uuid4().hex
            data["execution_scheduled"] = True
            data["execution_confirmed"] = False
            data["execution_lease"] = moment.isoformat().replace("+00:00", "Z")
            data["execution_owner"] = token
            atomic_json(mapping, data)
            return token
        finally:
            self._release_exec_lock(lock_path, fence)

    def release_execution(self, key: str, token: str) -> bool:
        """Release an execution lease so a retry can take over immediately.

        Called when task creation fails after the lease was claimed, so the key is
        not left permanently queued. The caller must present the owner token it
        received from try_claim_execution; a mismatched token (an owner that was
        superseded by a takeover) is refused and leaves the mapping untouched.
        Returns True if this call released the lease.
        """
        mapping = self._mapping_path(key)
        lock_path = mapping.with_suffix(".exec.lock")
        fence = uuid.uuid4().hex
        if not self._acquire_exec_lock(lock_path, fence):
            return False
        try:
            if not mapping.is_file():
                return False
            data = self._read_idempotency_mapping(mapping)
            if data.get("execution_owner") != token:
                return False
            data["execution_scheduled"] = False
            data["execution_confirmed"] = False
            data.pop("execution_lease", None)
            data.pop("execution_owner", None)
            atomic_json(mapping, data)
            return True
        finally:
            self._release_exec_lock(lock_path, fence)

    def confirm_execution(self, key: str, token: str) -> bool:
        """Mark the execution lease durable once the task has actually started.

        A confirmed lease never expires, so a running task cannot be stolen by a
        concurrent retry. The caller must present the owner token it received from
        try_claim_execution; the token is compared atomically (under the exec lock)
        against the persisted owner before mutating, so an owner whose lease was
        taken over after expiry cannot confirm. Returns True if confirmed.
        """
        mapping = self._mapping_path(key)
        lock_path = mapping.with_suffix(".exec.lock")
        fence = uuid.uuid4().hex
        if not self._acquire_exec_lock(lock_path, fence):
            return False
        try:
            if not mapping.is_file():
                return False
            data = self._read_idempotency_mapping(mapping)
            if data.get("execution_owner") != token:
                return False
            data["execution_scheduled"] = True
            data["execution_confirmed"] = True
            atomic_json(mapping, data)
            return True
        finally:
            self._release_exec_lock(lock_path, fence)

    @staticmethod
    def _lease_expired(data: dict[str, Any], now: datetime, lease_seconds: float) -> bool:
        lease = data.get("execution_lease")
        if not isinstance(lease, str):
            return True
        try:
            granted = datetime.fromisoformat(lease.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return True
        return (now - granted).total_seconds() >= lease_seconds

    def _acquire_exec_lock(self, lock_path: Path, token: str) -> bool:
        """Acquire the transient .exec.lock for ``token``, reclaiming a stale one.

        Acquisition is an atomic ``link`` of a unique temp file carrying the
        holder's identity. A stale lock (left by a crash) is reclaimed by atomically
        ``replace``-ing it with our own temp file and then verifying our identity
        survived; we never ``unlink`` a path we did not create, so a fresh active
        lock can neither be deleted nor silently overwritten by a reclaim race.
        """
        tmp = lock_path.with_name(f"{lock_path.name}.{token}.tmp")
        tmp.write_text(json.dumps({"token": token}), encoding="utf-8")
        try:
            try:
                os.link(str(tmp), str(lock_path))
                return True
            except FileExistsError:
                pass
            if not self._lock_is_stale(lock_path):
                return False
            try:
                os.replace(str(tmp), str(lock_path))
            except OSError:
                return False
            return self._lock_owner(lock_path) == token
        finally:
            tmp.unlink(missing_ok=True)

    @staticmethod
    def _release_exec_lock(lock_path: Path, token: str) -> None:
        """Remove the .exec.lock only if we still own it.

        Comparing identity before unlinking means a holder whose lock was reclaimed
        (e.g. it ran past the stale threshold) never deletes a successor's lock.
        """
        try:
            if json.loads(lock_path.read_text(encoding="utf-8")).get("token") == token:
                lock_path.unlink()
        except (OSError, ValueError):
            pass

    @staticmethod
    def _lock_is_stale(lock_path: Path) -> bool:
        try:
            mtime = lock_path.stat().st_mtime
        except OSError:
            return True
        return time.time() - mtime >= EXEC_LOCK_STALE_SECONDS

    @staticmethod
    def _lock_owner(lock_path: Path) -> str | None:
        try:
            return json.loads(lock_path.read_text(encoding="utf-8")).get("token")
        except (OSError, ValueError):
            return None

    def release_candidate(self, batch_id: str) -> None:
        """Remove a candidate batch directory that was not claimed."""
        import shutil
        directory = self.root / batch_id
        if directory.is_dir():
            shutil.rmtree(directory)

    def start_screening(self, batch_id: str) -> None:
        directory = self._require(batch_id)
        state = self._read(directory / "state.json")
        now = _now()
        state.update({"status": "running", "phase": "screening", "heartbeat_at": now, "updated_at": now})
        atomic_json(directory / "state.json", state)

    @contextmanager
    def batch_lock(self, batch_id: str):
        path = self._require(batch_id) / ".batch.lock"
        with path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise BatchLockUnavailableError("batch lock unavailable") from exc
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def heartbeat(self, batch_id: str, current_strategy_id: str | None = None) -> None:
        directory = self._require(batch_id)
        state = self._read(directory / "state.json")
        now = _now()
        update: dict[str, object] = {"status": "running", "phase": "screening", "heartbeat_at": now, "updated_at": now}
        if current_strategy_id is not None:
            update["current_strategy_id"] = current_strategy_id
        state.update(update)
        atomic_json(directory / "state.json", state)

    def finish_report(self, batch_id: str, strategy_id: str, *, run_id: str | None, status: str, metrics: dict[str, float] | None = None, reason: str | None = None, error_class: str | None = None, attempt: int | None = None) -> None:
        directory = self._require(batch_id)
        payload = self._read(directory / "reports.json")
        for row in payload["reports"]:
            if row["strategy_id"] == strategy_id:
                row.update({"run_id": run_id, "status": status, "metrics": metrics or {}, "reason": reason})
                if error_class is not None:
                    row["error_class"] = error_class
                if attempt is not None:
                    row["attempt"] = attempt
                break
        else:
            raise KeyError(f"strategy not in batch: {strategy_id}")
        atomic_json(directory / "reports.json", payload)
        state = self._read(directory / "state.json")
        now = _now()
        state.update(
            {
                "status": "running",
                "phase": "screening",
                "screening_done": sum(row["status"] in {"success", "failed"} for row in payload["reports"]),
                "current_strategy_id": strategy_id,
                "heartbeat_at": now,
                "updated_at": now,
            }
        )
        atomic_json(directory / "state.json", state)

    def start_attempt(self, batch_id: str, strategy_id: str) -> int:
        directory = self._require(batch_id)
        payload = self._read(directory / "reports.json")
        for row in payload["reports"]:
            if row["strategy_id"] == strategy_id:
                attempt = int(row.get("attempt", 0)) + 1
                row["attempt"] = attempt
                break
        else:
            raise KeyError(f"strategy not in batch: {strategy_id}")
        atomic_json(directory / "reports.json", payload)
        return attempt

    def record_transient_failure(self, batch_id: str, strategy_id: str, *, reason: str, attempt: int) -> None:
        directory = self._require(batch_id)
        payload = self._read(directory / "reports.json")
        for row in payload["reports"]:
            if row["strategy_id"] == strategy_id:
                row.update({"status": "interrupted", "reason": reason, "error_class": "transient_io", "attempt": attempt})
                break
        else:
            raise KeyError(f"strategy not in batch: {strategy_id}")
        atomic_json(directory / "reports.json", payload)

    def attempt(self, batch_id: str, strategy_id: str) -> int:
        rows = self._read(self._require(batch_id) / "reports.json")["reports"]
        for row in rows:
            if row["strategy_id"] == strategy_id:
                return int(row.get("attempt", 0))
        raise KeyError(f"strategy not in batch: {strategy_id}")

    def mark_stalled(self, batch_id: str, *, now: str | None = None) -> None:
        directory = self._require(batch_id)
        state = self._read(directory / "state.json")
        timestamp = now or _now()
        state.update({"status": "stalled", "phase": "stalled", "heartbeat_at": timestamp, "updated_at": timestamp})
        atomic_json(directory / "state.json", state)

    def mark_expired_stalled(self, *, now: datetime, stale_after_seconds: float) -> list[str]:
        """Mark abandoned running batches without executing or resuming anything."""
        if not self.root.is_dir():
            return []
        marked: list[str] = []
        for directory in self.root.iterdir():
            if not directory.is_dir():
                continue
            try:
                state = self._read(directory / "state.json")
                heartbeat = self._parse_time(state.get("heartbeat_at"))
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                continue
            if state.get("status") != "running" or heartbeat is None:
                continue
            if (now - heartbeat).total_seconds() > stale_after_seconds:
                self.mark_stalled(directory.name, now=now.isoformat().replace("+00:00", "Z"))
                marked.append(directory.name)
        return sorted(marked)

    def resume_candidates(self, batch_id: str) -> list[str]:
        rows = self._read(self._require(batch_id) / "reports.json")["reports"]
        return [str(row["strategy_id"]) for row in rows if row.get("status") in {"queued", "interrupted"}]

    def pending_candidates(self, batch_id: str) -> list[str]:
        rows = self._read(self._require(batch_id) / "reports.json")["reports"]
        return [str(row["strategy_id"]) for row in rows if row.get("status") == "queued"]

    def request(self, batch_id: str) -> StrategyBatchRequest:
        return StrategyBatchRequest.model_validate(self._read(self._require(batch_id) / "request.json"))

    def strategy_ids(self, batch_id: str) -> list[str]:
        payload = self._read(self._require(batch_id) / "reports.json")
        return [str(row["strategy_id"]) for row in payload["reports"]]

    def comparison_key(self, batch_id: str) -> str:
        return str(self._read(self._require(batch_id) / "state.json")["comparison_key"])

    def attach_manifest(self, batch_id: str, manifest: Any) -> None:
        directory = self._require(batch_id)
        payload = manifest.model_dump(mode="json") if hasattr(manifest, "model_dump") else dict(manifest)
        atomic_json(directory / "data_snapshot.json", payload)

    def manifest(self, batch_id: str) -> dict[str, Any] | None:
        path = self._require(batch_id) / "data_snapshot.json"
        return self._read(path) if path.is_file() else None

    def complete(self, batch_id: str) -> None:
        directory = self._require(batch_id)
        reports = self._read(directory / "reports.json")["reports"]
        if any(row.get("status") not in {"success", "failed"} for row in reports):
            state = self._read(directory / "state.json")
            state.update({"updated_at": _now()})
            atomic_json(directory / "state.json", state)
            return
        status = "completed" if all(row["status"] == "success" for row in reports) else "completed_with_failures"
        state = self._read(directory / "state.json")
        state.update({"status": status, "phase": "screening_completed", "updated_at": _now()})
        atomic_json(directory / "state.json", state)

    def finish_detail(self, batch_id: str, strategy_id: str, *, status: str, reason: str | None = None) -> None:
        directory = self._require(batch_id)
        payload = self._read(directory / "reports.json")
        for row in payload["reports"]:
            if row["strategy_id"] == strategy_id:
                row.update({"detail_status": status, "detail_reason": reason})
                break
        else:
            raise KeyError(f"strategy not in batch: {strategy_id}")
        atomic_json(directory / "reports.json", payload)
        state = self._read(directory / "state.json")
        now = _now()
        state.update({"detail_done": sum(row.get("detail_status") in {"success", "failed"} for row in payload["reports"]), "heartbeat_at": now, "updated_at": now})
        atomic_json(directory / "state.json", state)

    def list_reports(self, batch_id: str, *, sort_by: str = "sharpe", descending: bool = True) -> list[dict[str, Any]]:
        rows = self._read(self._require(batch_id) / "reports.json")["reports"]
        successful: list[tuple[float, dict[str, Any]]] = []
        other: list[dict[str, Any]] = []
        for row in rows:
            value = row.get("metrics", {}).get(sort_by)
            try:
                metric = float(value)
            except (TypeError, ValueError):
                metric = math.nan
            if row.get("status") == "success" and math.isfinite(metric):
                successful.append((metric, row))
            else:
                other.append(row)
        successful.sort(key=lambda item: (item[0], str(item[1]["strategy_id"])), reverse=descending)
        return [row for _, row in successful] + sorted(other, key=lambda row: str(row["strategy_id"]))

    def list_recent(self, *, limit: int = 20) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        items: list[dict[str, Any]] = []
        for directory in sorted(self.root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not directory.is_dir():
                continue
            try:
                state = self._read(directory / "state.json")
                items.append({"batch_id": directory.name, "status": state.get("status"), "phase": state.get("phase"), "created_at": state.get("created_at", ""), "updated_at": state.get("updated_at", ""), "screening_done": state.get("screening_done", 0), "screening_total": state.get("screening_total", 0), "comparison_key": state.get("comparison_key", "")})
            except (OSError, json.JSONDecodeError, KeyError, TypeError):
                continue
            if len(items) >= limit:
                break
        return items

    def list_unfinished(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        batches: list[dict[str, Any]] = []
        for directory in self.root.iterdir():
            if not directory.is_dir():
                continue
            try:
                state = self._read(directory / "state.json")
                legacy_detail_candidate = state.get("phase") == "completed" and state.get("detail_total", 0) == 0
                if state.get("status") not in {"queued", "running", "stalled"} and not legacy_detail_candidate:
                    continue
                batches.append(self.summary(directory.name))
            except (OSError, json.JSONDecodeError, KeyError, TypeError):
                continue
        return sorted(
            batches,
            key=lambda batch: (str(batch.get("updated_at", "")), str(batch["batch_id"])),
            reverse=True,
        )

    def summary(self, batch_id: str, *, sort_by: str = "sharpe", descending: bool = True) -> dict[str, Any]:
        directory = self._require(batch_id)
        state = self._read(directory / "state.json")
        reports = self.list_reports(batch_id, sort_by=sort_by, descending=descending)
        return {
            "batch_id": batch_id,
            "status": state["status"],
            "phase": state.get("phase", state["status"]),
            "comparison_key": state["comparison_key"],
            "created_at": state["created_at"],
            "updated_at": state["updated_at"],
            "screening_done": state.get("screening_done", 0),
            "screening_total": state.get("screening_total", 0),
            "detail_done": state.get("detail_done", 0),
            "detail_total": state.get("detail_total", 0),
            "heartbeat_at": state.get("heartbeat_at"),
            "reports": reports,
        }

    _BATCH_ID_RE = re.compile(r"^batch_[A-Za-z0-9_-]+$")

    def _require(self, batch_id: str) -> Path:
        if not self._BATCH_ID_RE.fullmatch(str(batch_id)):
            raise KeyError(f"invalid batch id: {batch_id}")
        directory = self.root / batch_id
        if not directory.is_dir():
            raise KeyError(f"batch not found: {batch_id}")
        return directory

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _parse_time(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)