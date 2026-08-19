"""Unified, fail-closed resolver for versioned and legacy StockPred artifacts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_VERSION_ID = re.compile(r"^[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COHORT_SCHEMA = "signal_cohort_v1"


class ArtifactsMissingError(FileNotFoundError):
    """Raised when the versioned artifact contract cannot be validated."""

    def __init__(self, run_dir: Path, reason: str = "") -> None:
        self.run_dir = run_dir
        message = f"No valid artifacts in {run_dir}"
        if reason:
            message += f": {reason}"
        super().__init__(message)


@dataclass(frozen=True)
class ResolvedArtifacts:
    schema_version: str
    version_dir: Path
    is_legacy: bool
    metrics_path: Path | None = None
    returns_path: Path | None = None
    chart_manifest_path: Path | None = None
    quality_path: Path | None = None
    period_breakdown_path: Path | None = None
    _snapshot: dict[str, bytes] = field(default_factory=dict, repr=False)
    _file_index: dict[str, tuple[str, int]] = field(default_factory=dict, repr=False)


def _within_version_dir(version_dir: Path, relative_path: str) -> tuple[Path, str]:
    candidate = (version_dir / relative_path).resolve()
    try:
        candidate.relative_to(version_dir.resolve())
    except ValueError as exc:
        raise ArtifactsMissingError(version_dir, "chart path outside version directory") from exc
    return candidate, candidate.relative_to(version_dir.resolve()).as_posix()


def _snapshot_version(version_dir: Path) -> tuple[dict[str, bytes], str]:
    """Read each version file once and reproduce the publisher's version ID."""
    snapshot: dict[str, bytes] = {}
    hasher = hashlib.sha256()
    for path in sorted(version_dir.rglob("*")):
        if path.is_file():
            relative = path.relative_to(version_dir)
            verified_path, _ = _within_version_dir(version_dir, relative.as_posix())
            payload = verified_path.read_bytes()
            snapshot[relative.as_posix()] = payload
            hasher.update(str(relative).encode("utf-8"))
            hasher.update(payload)
    return snapshot, hasher.hexdigest()[:32]


def artifact_bytes(resolved: ResolvedArtifacts, relative_path: str) -> bytes:
    """Return bytes from the resolver's verified in-memory snapshot."""
    _, key = _within_version_dir(resolved.version_dir, relative_path)
    if key in resolved._snapshot:
        return resolved._snapshot[key]
    try:
        expected_hash, expected_size = resolved._file_index[key]
    except KeyError as exc:
        raise ArtifactsMissingError(resolved.version_dir, f"artifact missing: {relative_path}") from exc
    path, _ = _within_version_dir(resolved.version_dir, relative_path)
    payload = path.read_bytes()
    if len(payload) != expected_size or hashlib.sha256(payload).hexdigest() != expected_hash:
        raise ArtifactsMissingError(resolved.version_dir, f"artifact sha256 mismatch: {relative_path}")
    return payload


def has_artifact(resolved: ResolvedArtifacts, relative_path: str) -> bool:
    """Tell whether an artifact was present in the verified snapshot."""
    _, key = _within_version_dir(resolved.version_dir, relative_path)
    return key in resolved._snapshot or key in resolved._file_index


def _file_index(version_dir: Path, manifest: dict[str, Any]) -> dict[str, tuple[str, int]]:
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ArtifactsMissingError(version_dir, "manifest file index missing")
    index: dict[str, tuple[str, int]] = {}
    for item in files:
        if not isinstance(item, dict):
            raise ArtifactsMissingError(version_dir, "invalid manifest file index")
        relative_path = item.get("relative_path")
        sha256 = item.get("sha256")
        byte_size = item.get("byte_size")
        if not isinstance(relative_path, str) or not isinstance(sha256, str) or not _SHA256.fullmatch(sha256) or not isinstance(byte_size, int) or byte_size < 0:
            raise ArtifactsMissingError(version_dir, "invalid manifest file index")
        _, key = _within_version_dir(version_dir, relative_path)
        if key == "chart_bundle_manifest.json" or key in index:
            raise ArtifactsMissingError(version_dir, "invalid manifest file index")
        index[key] = (sha256, byte_size)
    return index


def load_chart_manifest(resolved: ResolvedArtifacts) -> dict[str, Any]:
    """Parse the verified manifest without eagerly loading chart files."""
    if resolved.chart_manifest_path is None:
        raise ArtifactsMissingError(resolved.version_dir, "chart manifest missing")
    try:
        manifest = json.loads(artifact_bytes(resolved, "chart_bundle_manifest.json"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactsMissingError(resolved.version_dir, "corrupted chart manifest") from exc
    if not isinstance(manifest, dict) or manifest.get("version") != 1 or not isinstance(manifest.get("entries"), list):
        raise ArtifactsMissingError(resolved.version_dir, "invalid chart manifest fields")
    for entry in manifest["entries"]:
        if not isinstance(entry, dict):
            raise ArtifactsMissingError(resolved.version_dir, "invalid chart entry")
        required = ("code", "relative_path", "sha256", "byte_size", "row_count", "columns", "start_date", "end_date")
        if any(key not in entry for key in required) or not isinstance(entry["code"], str):
            raise ArtifactsMissingError(resolved.version_dir, "chart entry missing required fields")
        if not isinstance(entry["relative_path"], str) or not isinstance(entry["sha256"], str) or not _SHA256.fullmatch(entry["sha256"]):
            raise ArtifactsMissingError(resolved.version_dir, "invalid chart entry hash")
        if not isinstance(entry["byte_size"], int) or entry["byte_size"] < 0:
            raise ArtifactsMissingError(resolved.version_dir, "invalid chart entry hash")
        _, key = _within_version_dir(resolved.version_dir, entry["relative_path"])
        indexed_file = resolved._file_index.get(key)
        if indexed_file is None or indexed_file != (entry["sha256"], entry["byte_size"]):
            raise ArtifactsMissingError(resolved.version_dir, "chart file sha256 mismatch")
    return manifest


class RunArtifactResolver:
    """Resolve immutable cohort artifacts first, falling back only to legacy runs."""

    @staticmethod
    def resolve(run_dir: Path) -> ResolvedArtifacts:
        run_dir = Path(run_dir)
        pointer_path = run_dir / "artifacts_current.json"
        if pointer_path.is_file():
            return RunArtifactResolver._resolve_versioned(run_dir, pointer_path)
        legacy_dir = run_dir / "artifacts"
        if legacy_dir.is_dir():
            return RunArtifactResolver._resolve_legacy(legacy_dir)
        raise ArtifactsMissingError(run_dir)

    @staticmethod
    def _resolve_versioned(run_dir: Path, pointer_path: Path) -> ResolvedArtifacts:
        try:
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            version_id = pointer["version_id"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ArtifactsMissingError(run_dir, f"corrupted pointer: {exc}") from exc
        if not isinstance(version_id, str) or not _VERSION_ID.fullmatch(version_id):
            raise ArtifactsMissingError(run_dir, "invalid version id")
        if pointer.get("schema_version") != _COHORT_SCHEMA:
            raise ArtifactsMissingError(run_dir, "unknown schema")
        version_dir, _ = _within_version_dir(run_dir / "artifacts_versions", version_id)
        if not version_dir.is_dir():
            raise ArtifactsMissingError(run_dir, f"version directory missing: {version_id}")
        expected_manifest_hash = pointer.get("manifest_sha256")
        legacy_pointer = expected_manifest_hash == version_id
        if not ((isinstance(expected_manifest_hash, str) and _SHA256.fullmatch(expected_manifest_hash)) or legacy_pointer):
            raise ArtifactsMissingError(run_dir, "invalid manifest sha256")
        if legacy_pointer:
            snapshot, computed_version_id = _snapshot_version(version_dir)
            if computed_version_id != version_id:
                raise ArtifactsMissingError(run_dir, "version content hash mismatch")
            manifest = snapshot.get("chart_bundle_manifest.json")
            if manifest is None:
                raise ArtifactsMissingError(run_dir, "chart manifest missing")
            file_index = {
                path: (hashlib.sha256(payload).hexdigest(), len(payload))
                for path, payload in snapshot.items()
                if path != "chart_bundle_manifest.json"
            }
        else:
            manifest_path, _ = _within_version_dir(version_dir, "chart_bundle_manifest.json")
            if not manifest_path.is_file():
                raise ArtifactsMissingError(run_dir, "chart manifest missing")
            manifest = manifest_path.read_bytes()
            if hashlib.sha256(manifest).hexdigest() != expected_manifest_hash:
                raise ArtifactsMissingError(run_dir, "chart manifest sha256 mismatch")
            snapshot = {"chart_bundle_manifest.json": manifest}
            try:
                manifest_json = json.loads(manifest)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ArtifactsMissingError(run_dir, "corrupted chart manifest") from exc
            if not isinstance(manifest_json, dict):
                raise ArtifactsMissingError(run_dir, "invalid chart manifest fields")
            file_index = _file_index(version_dir, manifest_json)
        return ResolvedArtifacts(
            schema_version=_COHORT_SCHEMA,
            version_dir=version_dir,
            is_legacy=False,
            metrics_path=(version_dir / "aggregate_metrics.json") if "aggregate_metrics.json" in file_index else None,
            returns_path=(version_dir / "cohort_returns.csv") if "cohort_returns.csv" in file_index else None,
            chart_manifest_path=version_dir / "chart_bundle_manifest.json",
            quality_path=(version_dir / "quality_report.json") if "quality_report.json" in file_index else None,
            period_breakdown_path=(version_dir / "period_breakdown.csv") if "period_breakdown.csv" in file_index else None,
            _snapshot=snapshot,
            _file_index=file_index,
        )

    @staticmethod
    def _resolve_legacy(artifacts_dir: Path) -> ResolvedArtifacts:
        metrics_path = artifacts_dir / "metrics.csv"
        return ResolvedArtifacts(
            schema_version="legacy_portfolio_like_v1",
            version_dir=artifacts_dir,
            is_legacy=True,
            metrics_path=metrics_path if metrics_path.is_file() else None,
        )
