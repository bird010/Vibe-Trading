"""Tests for RunArtifactResolver."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from src.stockpred.artifact_resolver import (
    ArtifactsMissingError,
    RunArtifactResolver,
)


def _pointer(run_dir: Path, version_dir: Path) -> Path:
    manifest = version_dir / "chart_bundle_manifest.json"
    manifest.write_text(json.dumps({"version": 1, "entries": []}), encoding="utf-8")
    files = []
    for path in sorted(version_dir.rglob("*")):
        if path.is_file() and path != manifest:
            payload = path.read_bytes()
            files.append({"relative_path": path.relative_to(version_dir).as_posix(), "sha256": hashlib.sha256(payload).hexdigest(), "byte_size": len(payload)})
    manifest.write_text(json.dumps({"version": 1, "entries": [], "files": files}), encoding="utf-8")
    version_id = _version_id(version_dir)
    final_dir = version_dir.with_name(version_id)
    version_dir.rename(final_dir)
    manifest = final_dir / "chart_bundle_manifest.json"
    (run_dir / "artifacts_current.json").write_text(
        json.dumps(
            {
                "version_id": version_id,
                "schema_version": "signal_cohort_v1",
                "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    return final_dir


def _version_id(version_dir: Path) -> str:
    hasher = hashlib.sha256()
    for path in sorted(version_dir.rglob("*")):
        if path.is_file():
            hasher.update(str(path.relative_to(version_dir)).encode("utf-8"))
            hasher.update(path.read_bytes())
    return hasher.hexdigest()[:32]


def _legacy_pointer_version(run_dir: Path) -> Path:
    staging = run_dir / "artifacts_versions" / "staging"
    staging.mkdir(parents=True)
    (staging / "aggregate_metrics.json").write_text("{}", encoding="utf-8")
    (staging / "cohort_returns.csv").write_text("cohort_id\n", encoding="utf-8")
    (staging / "chart_bundle_manifest.json").write_text(
        json.dumps({"version": 1, "entries": []}), encoding="utf-8"
    )
    version_id = _version_id(staging)
    version_dir = staging.with_name(version_id)
    staging.rename(version_dir)
    (run_dir / "artifacts_current.json").write_text(
        json.dumps(
            {
                "version_id": version_id,
                "schema_version": "signal_cohort_v1",
                "manifest_sha256": version_id,
            }
        ),
        encoding="utf-8",
    )
    return version_dir


class TestRunArtifactResolver:
    def test_rejects_new_pointer_manifest_symlink_outside_version(self, tmp_path: Path):
        version_dir = tmp_path / "artifacts_versions" / ("a" * 32)
        version_dir.mkdir(parents=True)
        version_dir = _pointer(tmp_path, version_dir)
        manifest = version_dir / "chart_bundle_manifest.json"
        outside = tmp_path / "outside_manifest.json"
        outside.write_text(json.dumps({"version": 1, "entries": [], "files": []}), encoding="utf-8")
        manifest.unlink()
        try:
            manifest.symlink_to(outside)
        except OSError as exc:
            pytest.skip(f"symlink unavailable: {exc}")
        pointer_path = tmp_path / "artifacts_current.json"
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        pointer["manifest_sha256"] = hashlib.sha256(outside.read_bytes()).hexdigest()
        pointer_path.write_text(json.dumps(pointer), encoding="utf-8")

        with pytest.raises(ArtifactsMissingError, match="outside"):
            RunArtifactResolver.resolve(tmp_path)

    def test_rejects_legacy_pointer_symlink_outside_version(self, tmp_path: Path):
        version_dir = _legacy_pointer_version(tmp_path)
        artifact = version_dir / "aggregate_metrics.json"
        outside = tmp_path / "outside_metrics.json"
        outside.write_text("{}", encoding="utf-8")
        artifact.unlink()
        try:
            artifact.symlink_to(outside)
        except OSError as exc:
            pytest.skip(f"symlink unavailable: {exc}")

        with pytest.raises(ArtifactsMissingError, match="outside"):
            RunArtifactResolver.resolve(tmp_path)

    def test_accepts_legacy_pointer_only_when_content_hash_matches_version(self, tmp_path: Path):
        version_dir = _legacy_pointer_version(tmp_path)

        result = RunArtifactResolver.resolve(tmp_path)

        assert result.version_dir == version_dir

    def test_rejects_legacy_pointer_when_version_contents_are_tampered(self, tmp_path: Path):
        version_dir = _legacy_pointer_version(tmp_path)
        (version_dir / "aggregate_metrics.json").write_text('{"tampered": true}', encoding="utf-8")

        with pytest.raises(ArtifactsMissingError, match="version"):
            RunArtifactResolver.resolve(tmp_path)
    @pytest.mark.parametrize("version_id", ["../outside", "bad/path", "not-a-hash"])
    def test_rejects_invalid_version_id(self, tmp_path: Path, version_id: str):
        (tmp_path / "artifacts_versions").mkdir()
        (tmp_path / "artifacts_current.json").write_text(
            json.dumps({"version_id": version_id, "schema_version": "signal_cohort_v1"}),
            encoding="utf-8",
        )

        with pytest.raises(ArtifactsMissingError, match="version"):
            RunArtifactResolver.resolve(tmp_path)

    def test_rejects_unknown_versioned_schema(self, tmp_path: Path):
        version_dir = tmp_path / "artifacts_versions" / ("a" * 32)
        version_dir.mkdir(parents=True)
        (tmp_path / "artifacts_current.json").write_text(
            json.dumps({"version_id": "a" * 32, "schema_version": "unknown"}),
            encoding="utf-8",
        )

        with pytest.raises(ArtifactsMissingError, match="schema"):
            RunArtifactResolver.resolve(tmp_path)
    def test_resolves_new_versioned_structure(self, tmp_path: Path):
        # Create new structure
        version_dir = tmp_path / "artifacts_versions" / ("a" * 32)
        version_dir.mkdir(parents=True)
        (version_dir / "aggregate_metrics.json").write_text("{}", encoding="utf-8")
        (version_dir / "cohort_returns.csv").write_text("cohort_id\n", encoding="utf-8")
        version_dir = _pointer(tmp_path, version_dir)

        result = RunArtifactResolver.resolve(tmp_path)

        assert result.schema_version == "signal_cohort_v1"
        assert result.is_legacy is False
        assert result.version_dir == version_dir
        assert result.metrics_path == version_dir / "aggregate_metrics.json"

    def test_resolves_legacy_structure(self, tmp_path: Path):
        # Create old structure
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        (artifacts / "metrics.csv").write_text("total_return\n0.1\n", encoding="utf-8")

        result = RunArtifactResolver.resolve(tmp_path)

        assert result.schema_version == "legacy_portfolio_like_v1"
        assert result.is_legacy is True
        assert result.version_dir == artifacts

    def test_missing_artifacts_raises(self, tmp_path: Path):
        with pytest.raises(ArtifactsMissingError):
            RunArtifactResolver.resolve(tmp_path)

    def test_corrupted_pointer_raises_stable_error(self, tmp_path: Path):
        (tmp_path / "artifacts_current.json").write_text("not json{{{", encoding="utf-8")

        with pytest.raises(ArtifactsMissingError):
            RunArtifactResolver.resolve(tmp_path)

    def test_pointer_to_missing_version_raises(self, tmp_path: Path):
        (tmp_path / "artifacts_versions").mkdir()
        pointer = {"version_id": "a" * 32, "schema_version": "signal_cohort_v1", "manifest_sha256": "b" * 64}
        (tmp_path / "artifacts_current.json").write_text(json.dumps(pointer), encoding="utf-8")

        with pytest.raises(ArtifactsMissingError):
            RunArtifactResolver.resolve(tmp_path)

    def test_chart_manifest_path_when_present(self, tmp_path: Path):
        version_dir = tmp_path / "artifacts_versions" / ("a" * 32)
        version_dir.mkdir(parents=True)
        version_dir = _pointer(tmp_path, version_dir)

        result = RunArtifactResolver.resolve(tmp_path)

        assert result.chart_manifest_path == version_dir / "chart_bundle_manifest.json"

    def test_versioned_artifacts_require_chart_manifest(self, tmp_path: Path):
        version_dir = tmp_path / "artifacts_versions" / ("a" * 32)
        version_dir.mkdir(parents=True)
        version_dir = _pointer(tmp_path, version_dir)

        result = RunArtifactResolver.resolve(tmp_path)

        assert result.chart_manifest_path == version_dir / "chart_bundle_manifest.json"
