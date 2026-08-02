"""File persistence — §16. Atomic writes, run directory, idempotency."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path


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
