"""Common artifact publisher — Phase 2 Task 5 (design §12/§13.5).

The publisher is the ONLY component that writes run artifacts to disk. It owns:

* the fixed common role registry for identity, lifecycle, execution and result
  artifacts with stable file names;
* safe file naming for strategy-declared ``StrategyArtifact`` s (roles are
  namespaced and can never override a common role; path-traversal roles are
  rejected before any write);
* JSON/CSV serialization;
* the manifest index (role, file, media type, producer, checksum, rows,
  columns) written atomically by ``finalize`` — a failed publication can
  never produce a success manifest (§13.5).

Strategies declare artifacts; they never write file paths themselves (§12).
"""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Collection

import pandas as pd

from backtest.fund_rotation.contracts import StrategyArtifact
from src.stockpred.fund_rotation.artifacts import describe_file, write_csv_atomic
from src.stockpred.fund_rotation.persistence import atomic_write_json

# role -> fixed file name for the common artifacts (§12).
COMMON_ROLES: dict[str, str] = {
    "manifest": "manifest.json",
    "state": "state.json",
    "resolved_spec": "resolved_spec.json",
    "strategy_snapshot": "strategy_snapshot.json",
    "data_snapshot": "data_snapshot.json",
    "evaluation_calendar": "evaluation_calendar.json",
    "target_decisions": "target_decisions.csv",
    "targets": "targets.csv",
    "orders": "orders.csv",
    "fills": "trade_events.csv",
    "positions": "positions.csv",
    "equity": "equity.csv",
    "metrics": "metrics.json",
    "summary": "summary.json",
    "events": "events.jsonl",
}

_MEDIA_JSON = "application/json"
_MEDIA_CSV = "text/csv"
_MEDIA_JSONL = "application/x-ndjson"

# Safe role names: alnum start, then alnum/_/- only (no dots, no separators),
# which makes path traversal structurally impossible.
_ROLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class ArtifactPublicationError(Exception):
    """Raised on any publication failure; the run never gets a success manifest."""

    code = "ARTIFACT_PUBLICATION_ERROR"


def _json_compatible(value: object, *, path: str = "$") -> object:
    """Normalize supported value objects to strict JSON-compatible values.

    Strategy diagnostics may expose immutable domain value objects such as
    dataclasses and string enums. The publisher owns serialization, so those
    types are converted recursively at this boundary. Unknown objects still
    fail closed instead of being stringified implicitly.
    """
    if isinstance(value, Enum):
        return _json_compatible(value.value, path=path)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError(f"{path} contains a non-finite float")
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _json_compatible(
                getattr(value, field.name),
                path=f"{path}.{field.name}",
            )
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(
                    f"{path} contains non-string mapping key {key!r}"
                )
            normalized[key] = _json_compatible(
                item,
                path=f"{path}.{key}",
            )
        return normalized
    if isinstance(value, (list, tuple)):
        return [
            _json_compatible(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise TypeError(
        f"{path} contains unsupported type {type(value).__name__}"
    )


class ArtifactPublisher:
    """Publishes one sub-run's artifacts into ``run_dir`` (single use)."""

    def __init__(self, run_dir: Path) -> None:
        self._run_dir = Path(run_dir)
        self._index: dict[str, dict] = {}
        self._failed = False
        self._finalized = False

    # ── publication ──

    def publish(self, artifact: StrategyArtifact, *, producer: str = "common") -> Path:
        """Serialize and write one declared artifact; index it by role.

        Safety rejections (reserved/duplicate/unsafe roles, common-role
        override) fail BEFORE any write and do not poison the publication —
        nothing was written, so the manifest invariant is intact. Failures
        during serialization/write poison it: ``finalize`` can then never
        produce a success manifest.
        """
        if self._finalized:
            raise ArtifactPublicationError("publication is closed after finalize()")

        role = artifact.role
        if role == "manifest":
            raise ArtifactPublicationError(
                "the manifest role is reserved for finalize(); it cannot be published"
            )
        if role in {"events", "state"}:
            raise ArtifactPublicationError(
                f"the {role} role is persisted externally (§29/§30.1); "
                "use index_external() to include it without rewriting"
            )
        if role in COMMON_ROLES and producer != "common":
            raise ArtifactPublicationError(
                f"{role!r} is a common role; strategy {producer!r} cannot override it"
            )
        if role in self._index:
            raise ArtifactPublicationError(f"role {role!r} already published")

        if role in COMMON_ROLES:
            filename = COMMON_ROLES[role]
        else:
            if not _ROLE_RE.match(role) or ".." in role:
                raise ArtifactPublicationError(f"unsafe artifact role {role!r}")
            ext = ".csv" if artifact.media_type == _MEDIA_CSV else ".json"
            filename = f"strategy_{role}{ext}"

        try:
            path = self._write(filename, artifact)
        except ArtifactPublicationError:
            self._failed = True
            raise
        except Exception as exc:
            self._failed = True
            raise ArtifactPublicationError(
                f"failed to publish role {role!r}: {exc}"
            ) from exc

        entry = describe_file(path)
        entry.update({
            "file": filename,
            "role": role,
            "media_type": artifact.media_type,
            "producer": producer,
        })
        self._index[role] = entry
        return path

    def _write(self, filename: str, artifact: StrategyArtifact) -> Path:
        self._run_dir.mkdir(parents=True, exist_ok=True)
        path = self._run_dir / filename
        if artifact.media_type == _MEDIA_JSON:
            try:
                normalized = _json_compatible(artifact.payload)
                json.dumps(
                    normalized,
                    ensure_ascii=False,
                    allow_nan=False,
                )
            except (TypeError, ValueError) as exc:
                raise ArtifactPublicationError(
                    f"payload for role {artifact.role!r} is not JSON serializable: {exc}"
                ) from exc
            atomic_write_json(path, normalized)
            return path
        if artifact.media_type == _MEDIA_CSV:
            df = self._to_dataframe(artifact)
            write_csv_atomic(path, df)
            return path
        raise ArtifactPublicationError(
            f"unsupported media_type {artifact.media_type!r} for role {artifact.role!r}"
        )

    @staticmethod
    def _to_dataframe(artifact: StrategyArtifact) -> pd.DataFrame:
        payload = artifact.payload
        if isinstance(payload, pd.DataFrame):
            return payload
        if isinstance(payload, pd.Series):
            return payload.to_frame()
        if isinstance(payload, list) and all(isinstance(r, dict) for r in payload):
            return pd.DataFrame(payload)
        raise ArtifactPublicationError(
            f"payload for role {artifact.role!r} is not CSV serializable "
            "(expected DataFrame, Series, or list of row dicts)"
        )

    # ── manifest ──

    def index_external(self, role: str) -> Path:
        """Index a common artifact written by an existing external mechanism.

        The run event log (``events.jsonl``) is append-persisted by the state
        machine before SSE emission (§29/§30.1); the publisher must index it
        without ever rewriting it.
        """
        if self._finalized:
            raise ArtifactPublicationError("publication is closed after finalize()")
        if role not in COMMON_ROLES:
            raise ArtifactPublicationError(f"{role!r} is not a common role")
        if role in self._index:
            raise ArtifactPublicationError(f"role {role!r} already published")
        filename = COMMON_ROLES[role]
        path = self._run_dir / filename
        if not path.exists():
            raise ArtifactPublicationError(
                f"external artifact {filename!r} does not exist; nothing to index"
            )
        entry = describe_file(path)
        if path.suffix == ".jsonl":
            with open(path, encoding="utf-8") as handle:
                entry["rows"] = sum(1 for _ in handle)
        media_type = {
            ".jsonl": _MEDIA_JSONL,
            ".csv": _MEDIA_CSV,
        }.get(path.suffix, _MEDIA_JSON)
        entry.update({
            "file": filename,
            "role": role,
            "media_type": media_type,
            "producer": "common",
        })
        self._index[role] = entry
        return path

    def artifact_index(self) -> dict[str, dict]:
        return {role: dict(entry) for role, entry in self._index.items()}

    def finalize(self, *, status: str = "SUCCEEDED", identity: dict | None = None) -> dict:
        """Atomically write manifest.json — the visibility boundary (§13.5).

        Refuses to run after any publication failure or twice.
        """
        if self._finalized:
            raise ArtifactPublicationError("publication is already finalized")
        if self._failed:
            raise ArtifactPublicationError(
                "publication failed earlier; no success manifest can be produced"
            )
        artifacts = dict(self._index)
        # The manifest indexes itself under the reserved role (no checksum:
        # it cannot contain the digest of its own final content).
        artifacts["manifest"] = {
            "file": "manifest.json",
            "role": "manifest",
            "media_type": _MEDIA_JSON,
            "producer": "common",
        }
        manifest = {
            "schema_version": "v1",
            "status": status,
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "artifacts": artifacts,
            "files": sorted(entry["file"] for entry in artifacts.values()),
        }
        if identity:
            reserved = {"schema_version", "status", "completed_at", "artifacts", "files"}
            conflict = reserved & set(identity)
            if conflict:
                raise ArtifactPublicationError(
                    f"identity must not override reserved manifest keys: {sorted(conflict)}"
                )
            manifest.update(identity)
        self._run_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = self._run_dir / "manifest.json"
        atomic_write_json(manifest_path, manifest)
        self._finalized = True
        return manifest


def read_valid_manifest(
    run_dir: Path,
    *,
    identity_field: str,
    expected_identity: str,
    allowed_statuses: Collection[str],
) -> dict | None:
    """Return a fully checksum-validated publication, otherwise ``None``.

    ``manifest.json`` is the visibility boundary. A terminal state without a
    valid manifest remains an unpublished candidate and must not be exposed as
    success by readers or recovery code.
    """
    run_dir = Path(run_dir)
    state_path = run_dir / "state.json"
    manifest_path = run_dir / "manifest.json"
    if not state_path.is_file() or not manifest_path.is_file():
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    status = manifest.get("status")
    if (
        status not in set(allowed_statuses)
        or state.get("stage") != status
        or manifest.get(identity_field) != expected_identity
    ):
        return None

    from src.stockpred.fund_rotation.artifacts import compute_file_checksum

    for name in manifest.get("files", []):
        if name == "manifest.json":
            continue
        path = run_dir / str(name)
        expected = manifest.get("file_details", {}).get(name, {}).get("checksum")
        if not path.is_file() or not expected:
            return None
        if compute_file_checksum(path) != expected:
            return None
    state_checksum = manifest.get("state_checksum")
    if state_checksum and compute_file_checksum(state_path) != state_checksum:
        return None
    return manifest
