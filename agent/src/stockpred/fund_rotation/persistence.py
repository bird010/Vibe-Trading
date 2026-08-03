"""File persistence — §16. Atomic writes, run directory, idempotency."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, data: dict | list) -> None:
    """Write JSON atomically: tmp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(str(tmp), str(path))


def atomic_write_text(path: Path, content: str) -> None:
    """Write text atomically: tmp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(str(tmp), str(path))


def append_jsonl(path: Path, record: dict) -> None:
    """Append one JSON line to a JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def deterministic_run_id(idempotency_key: str) -> str:
    """Generate deterministic run_id from idempotency key."""
    return hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:16]


def request_fingerprint(params: dict) -> str:
    """Compute a stable fingerprint of request parameters."""
    canonical = json.dumps(params, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


class RunDirectory:
    """§16 — Manages one run's file directory."""

    def __init__(self, root: Path, run_id: str) -> None:
        self.root = root
        self.run_id = run_id
        self.path = root / "fund_rotation" / run_id

    def ensure(self) -> None:
        self.path.mkdir(parents=True, exist_ok=True)

    def write_request(self, params: dict, schema_version: str = "v1") -> None:
        fp = request_fingerprint(params)
        data = {"schema_version": schema_version, "params": params, "params_fingerprint": fp}
        atomic_write_json(self.path / "request.json", data)

    def write_state(self, state: dict) -> None:
        atomic_write_json(self.path / "state.json", state)

    def read_state(self) -> dict | None:
        p = self.path / "state.json"
        if not p.exists():
            return None
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    def write_manifest(self, manifest: dict) -> None:
        atomic_write_json(self.path / "manifest.json", manifest)

    def has_manifest(self) -> bool:
        return (self.path / "manifest.json").exists()

    def append_event(self, event: dict) -> None:
        append_jsonl(self.path / "events.jsonl", event)


class IdempotencyGuard:
    """§16.2 — Idempotency: same key+params -> same run; different params -> 409."""

    def __init__(self, root: Path) -> None:
        self.root = root / "fund_rotation"

    def check(self, idempotency_key: str, params: dict) -> tuple[str, str | None]:
        """Check idempotency.

        Returns:
            (run_id, existing_status) where existing_status is None if new.

        Raises:
            ValueError: If same key with different params (409 case).
        """
        run_id = deterministic_run_id(idempotency_key)
        run_dir = RunDirectory(self.root.parent, run_id)
        request_path = run_dir.path / "request.json"

        if not request_path.exists():
            return run_id, None

        # Existing run — verify params match
        with open(request_path, encoding="utf-8") as f:
            stored = json.load(f)

        stored_fp = stored.get("params_fingerprint", "")
        new_fp = request_fingerprint(params)

        if stored_fp and stored_fp != new_fp:
            raise ValueError(
                f"Idempotency conflict: key={idempotency_key} already used with different params"
            )

        state = run_dir.read_state()
        status = state.get("stage", "UNKNOWN") if state else "UNKNOWN"
        return run_id, status


# ── Phase 4 Task 3: batch event envelope and log (§30.1) ──

EVENT_SCHEMA_VERSION = "v2"

VALID_EVENT_TYPES = {
    "BATCH_STAGE", "VARIANT_STAGE", "VARIANT_PROGRESS", "TERMINAL", "ERROR",
}
VALID_EVENT_SCOPES = {"BATCH", "VARIANT"}

# stage must use the public batch/child enums (§30.1); imported lazily-safe
# here because state_machine has no dependency on this module.
from src.stockpred.fund_rotation.state_machine import (  # noqa: E402
    BatchStage,
    ChildStage,
)

_BATCH_STAGE_NAMES = {s.value for s in BatchStage}
_VARIANT_STAGE_NAMES = {s.value for s in ChildStage} | {s.value for s in BatchStage}


class EventValidationError(ValueError):
    """Raised when an event envelope or its progress violates §30.1."""


def _validate_progress(progress: dict[str, Any]) -> dict[str, Any]:
    """§30.1 — completed/total non-negative ints, completed<=total; ratio is
    always recomputed from the two (never caller-supplied)."""
    completed = progress.get("completed")
    total = progress.get("total")
    unit = progress.get("unit", "")
    for name, value in (("completed", completed), ("total", total)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise EventValidationError(
                f"progress.{name} must be a non-negative integer, got {value!r}"
            )
    if completed > total:
        raise EventValidationError(
            f"progress.completed ({completed}) must be <= total ({total})"
        )
    ratio = completed / total if total > 0 else 0.0
    return {
        "completed": completed,
        "total": total,
        "unit": unit,
        "ratio": ratio,
    }


class BatchEventLog:
    """§30.1 — append-only parent-batch event log with a global monotonic seq.

    Events are atomically appended (persisted) BEFORE any SSE publication
    (the publisher is the service layer). Child-local seqs are diagnostic
    only and never enter this file; every record here carries a batch-global
    seq. ``strategy_substage`` is stored verbatim and never drives any state
    machine.
    """

    def __init__(self, batch_dir: Path, *, batch_id: str) -> None:
        self.batch_dir = Path(batch_dir)
        self.batch_id = batch_id
        self.path = self.batch_dir / "events.jsonl"
        self._lock = threading.Lock()
        self._isolate_half_written_tail()
        self._next_seq = self._recover_next_seq()
        # (run_id, unit) -> last completed; same-unit progress must not regress.
        self._last_completed: dict[tuple[str, str], int] = {}

    def _isolate_half_written_tail(self) -> None:
        """A crash mid-append leaves a truncated last line without a newline;
        terminate it so later appends never merge into the half-written row."""
        if self.path.exists() and self.path.stat().st_size > 0:
            with open(self.path, "rb+") as fh:
                fh.seek(-1, os.SEEK_END)
                if fh.read(1) != b"\n":
                    fh.write(b"\n")

    def _recover_next_seq(self) -> int:
        if not self.path.exists():
            return 1
        max_seq = 0
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    max_seq = max(max_seq, int(record.get("seq", 0)))
                except (json.JSONDecodeError, TypeError, ValueError,
                        AttributeError):
                    continue
        return max_seq + 1

    def append(
        self,
        *,
        event_type: str,
        scope: str,
        stage: str | None = None,
        run_id: str | None = None,
        variant_key: str | None = None,
        strategy_id: str | None = None,
        strategy_substage: str | None = None,
        progress: dict[str, Any] | None = None,
        message: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Validate, assign the global seq, atomically append, then return the
        event (publication happens after this returns)."""
        if event_type not in VALID_EVENT_TYPES:
            raise EventValidationError(f"unknown event_type {event_type!r}")
        if scope not in VALID_EVENT_SCOPES:
            raise EventValidationError(f"unknown scope {scope!r}")
        if scope == "VARIANT" and not (run_id and variant_key and strategy_id):
            raise EventValidationError(
                "VARIANT events require run_id, variant_key and strategy_id"
            )
        # §30.1: stage uses the public batch/child enums (TERMINAL/ERROR may
        # omit it; strategy_substage is the free-form display string).
        if stage is not None:
            allowed = (
                _VARIANT_STAGE_NAMES if scope == "VARIANT" else _BATCH_STAGE_NAMES
            )
            if stage not in allowed:
                raise EventValidationError(
                    f"stage {stage!r} is not a public batch/child stage"
                )

        validated_progress = None
        if progress is not None:
            validated_progress = _validate_progress(progress)

        with self._lock:
            if validated_progress is not None:
                unit_key = (run_id or "", validated_progress["unit"])
                last = self._last_completed.get(unit_key)
                if last is not None and validated_progress["completed"] < last:
                    raise EventValidationError(
                        f"progress for unit {validated_progress['unit']!r} went "
                        f"backwards: {validated_progress['completed']} < {last}"
                    )
            seq = self._next_seq
            event = {
                "schema_version": EVENT_SCHEMA_VERSION,
                "seq": seq,
                "event_type": event_type,
                "scope": scope,
                "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                "batch_id": self.batch_id,
                "run_id": run_id,
                "variant_key": variant_key,
                "strategy_id": strategy_id,
                "stage": stage,
                "strategy_substage": strategy_substage,
                "progress": validated_progress,
                "message": message,
                "error": error,
            }
            append_jsonl(self.path, event)
            if validated_progress is not None:
                unit_key = (run_id or "", validated_progress["unit"])
                self._last_completed[unit_key] = validated_progress["completed"]
            self._next_seq = seq + 1
        return event
