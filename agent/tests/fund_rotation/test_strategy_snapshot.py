"""Phase 1 Task 5 — strategy/framework snapshot and run identity tests (§19)."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from src.stockpred.fund_rotation.strategy_snapshot import (
    compute_run_identity_hash,
    record_runtime_versions,
    snapshot_framework,
    snapshot_strategy_package,
)


@pytest.fixture
def strategy_pkg(tmp_path: Path) -> Path:
    """A tiny strategy package with two .py files and a __pycache__ dir."""
    pkg = tmp_path / "my_strategy"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "strategy.py").write_text("VALUE = 1\n", encoding="utf-8")
    (pkg / "config.py").write_text("CFG = 'a'\n", encoding="utf-8")
    cache = pkg / "__pycache__"
    cache.mkdir()
    (cache / "strategy.cpython-311.pyc").write_bytes(b"\x00\x01")
    return pkg


class _FakeStrategy:
    pass


def _bind_class_to_file(cls: type, file_path: Path, monkeypatch) -> None:
    """Point inspect.getfile(cls) at a temp file via a monkeypatched module
    (auto-cleaned by monkeypatch)."""
    mod = types.ModuleType("my_strategy.strategy")
    mod.__file__ = str(file_path)
    monkeypatch.setitem(sys.modules, "my_strategy.strategy", mod)
    cls.__module__ = "my_strategy.strategy"


class TestStrategyPackageSnapshot:
    def test_excludes_pycache(self, strategy_pkg: Path, monkeypatch):
        _bind_class_to_file(_FakeStrategy, strategy_pkg / "strategy.py", monkeypatch)
        snap = snapshot_strategy_package(_FakeStrategy)
        assert all("__pycache__" not in p for p in snap.relative_paths)
        assert "strategy.py" in snap.relative_paths
        assert "config.py" in snap.relative_paths

    def test_stable_across_calls(self, strategy_pkg: Path, monkeypatch):
        _bind_class_to_file(_FakeStrategy, strategy_pkg / "strategy.py", monkeypatch)
        assert (
            snapshot_strategy_package(_FakeStrategy).implementation_hash
            == snapshot_strategy_package(_FakeStrategy).implementation_hash
        )

    def test_sensitive_to_source_change(self, strategy_pkg: Path, monkeypatch):
        _bind_class_to_file(_FakeStrategy, strategy_pkg / "strategy.py", monkeypatch)
        before = snapshot_strategy_package(_FakeStrategy).implementation_hash
        (strategy_pkg / "strategy.py").write_text("VALUE = 2\n", encoding="utf-8")
        after = snapshot_strategy_package(_FakeStrategy).implementation_hash
        assert before != after

    def test_records_per_file_sha256(self, strategy_pkg: Path, monkeypatch):
        _bind_class_to_file(_FakeStrategy, strategy_pkg / "strategy.py", monkeypatch)
        snap = snapshot_strategy_package(_FakeStrategy)
        file_hash_map = dict(snap.file_hashes)
        assert set(file_hash_map) == set(snap.relative_paths)
        # Each per-file hash is a 64-char hex SHA-256.
        assert all(len(h) == 64 for h in file_hash_map.values())

    def test_captured_snapshot_immutable_to_lateral_disk_change(self, strategy_pkg: Path, monkeypatch):
        _bind_class_to_file(_FakeStrategy, strategy_pkg / "strategy.py", monkeypatch)
        captured = snapshot_strategy_package(_FakeStrategy)
        original_hash = captured.implementation_hash
        # Modify disk after capture; the captured object must not change (§19.1).
        (strategy_pkg / "strategy.py").write_text("VALUE = 999\n", encoding="utf-8")
        assert captured.implementation_hash == original_hash


class TestFrameworkSnapshot:
    def test_sensitive_to_framework_change(self, tmp_path: Path):
        agent_root = tmp_path / "agent"
        (agent_root / "backtest" / "fund_rotation").mkdir(parents=True)
        contracts = agent_root / "backtest" / "fund_rotation" / "contracts.py"
        contracts.write_text("A = 1\n", encoding="utf-8")
        before = snapshot_framework(agent_root)
        contracts.write_text("A = 2\n", encoding="utf-8")
        after = snapshot_framework(agent_root)
        assert before != after

    def test_missing_files_skipped(self, tmp_path: Path):
        agent_root = tmp_path / "agent"
        agent_root.mkdir()
        # No framework files exist -> empty hash, no error.
        assert isinstance(snapshot_framework(agent_root), str)


class TestRunIdentityHash:
    def test_combines_all_components(self):
        h1 = compute_run_identity_hash("s", "f", "c", "d", {"r": 1}, {"e": 1})
        h2 = compute_run_identity_hash("s", "f", "c", "d", {"r": 1}, {"e": 1})
        assert h1 == h2

    def test_sensitive_to_each_component(self):
        base = compute_run_identity_hash("s", "f", "c", "d", {"r": 1}, {"e": 1})
        assert compute_run_identity_hash("S", "f", "c", "d", {"r": 1}, {"e": 1}) != base
        assert compute_run_identity_hash("s", "F", "c", "d", {"r": 1}, {"e": 1}) != base
        assert compute_run_identity_hash("s", "f", "C", "d", {"r": 1}, {"e": 1}) != base
        assert compute_run_identity_hash("s", "f", "c", "D", {"r": 1}, {"e": 1}) != base
        assert compute_run_identity_hash("s", "f", "c", "d", {"r": 2}, {"e": 1}) != base
        assert compute_run_identity_hash("s", "f", "c", "d", {"r": 1}, {"e": 2}) != base

    def test_key_order_independent(self):
        h1 = compute_run_identity_hash("s", "f", "c", "d", {"a": 1, "b": 2}, {"e": 1})
        h2 = compute_run_identity_hash("s", "f", "c", "d", {"b": 2, "a": 1}, {"e": 1})
        assert h1 == h2


class TestRuntimeVersions:
    def test_records_versions_not_in_hash(self):
        versions = record_runtime_versions()
        assert "python" in versions
        assert "pandas" in versions
        assert "app" in versions
        # Versions are audit metadata; identity hash does not consume them.
        assert isinstance(versions["python"], str)
