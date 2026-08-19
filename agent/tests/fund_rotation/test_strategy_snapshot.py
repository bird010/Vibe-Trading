"""Strategy/framework snapshot and run identity tests."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from src.stockpred.fund_rotation.strategy_snapshot import (
    FRAMEWORK_SOURCE_FILES,
    FrameworkSnapshotError,
    compute_run_identity_hash,
    record_runtime_versions,
    snapshot_framework,
    snapshot_strategy_package,
)


@pytest.fixture
def strategy_pkg(tmp_path: Path) -> Path:
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
    mod = types.ModuleType("my_strategy.strategy")
    mod.__file__ = str(file_path)
    monkeypatch.setitem(sys.modules, "my_strategy.strategy", mod)
    cls.__module__ = "my_strategy.strategy"


class TestStrategyPackageSnapshot:
    def test_representative_strategy_snapshot_includes_score_model(self):
        from backtest.fund_rotation.strategies.correlation_representative.strategy import (
            CorrelationRepresentativeStrategy,
        )

        snapshot = snapshot_strategy_package(CorrelationRepresentativeStrategy)

        assert any("scoring" in path for path in snapshot.relative_paths)

    def test_excludes_pycache(self, strategy_pkg: Path, monkeypatch):
        _bind_class_to_file(_FakeStrategy, strategy_pkg / "strategy.py", monkeypatch)
        snapshot = snapshot_strategy_package(_FakeStrategy)
        assert all("__pycache__" not in path for path in snapshot.relative_paths)
        assert "strategy.py" in snapshot.relative_paths
        assert "config.py" in snapshot.relative_paths

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
        snapshot = snapshot_strategy_package(_FakeStrategy)
        file_hash_map = dict(snapshot.file_hashes)
        assert set(file_hash_map) == set(snapshot.relative_paths)
        assert all(len(file_hash) == 64 for file_hash in file_hash_map.values())

    def test_captured_snapshot_immutable_to_lateral_disk_change(
        self,
        strategy_pkg: Path,
        monkeypatch,
    ):
        _bind_class_to_file(_FakeStrategy, strategy_pkg / "strategy.py", monkeypatch)
        captured = snapshot_strategy_package(_FakeStrategy)
        original_hash = captured.implementation_hash
        (strategy_pkg / "strategy.py").write_text("VALUE = 999\n", encoding="utf-8")
        assert captured.implementation_hash == original_hash


class TestFrameworkSnapshot:
    def test_covers_every_design_required_framework_component(self):
        assert {
            "backtest/fund_rotation/runner.py",
            "backtest/fund_rotation/contracts.py",
            "backtest/fund_rotation/causal_data.py",
            "backtest/fund_rotation/universe.py",
            "backtest/fund_rotation/returns.py",
            "backtest/fund_rotation/execution.py",
            "backtest/fund_rotation/etf_rules.py",
            "backtest/fund_rotation/benchmarks.py",
            "backtest/fund_rotation/metrics.py",
            "backtest/fund_rotation/correlation.py",
            "backtest/fund_rotation/clustering.py",
            "backtest/fund_rotation/momentum.py",
            "backtest/fund_rotation/scoring/__init__.py",
            "backtest/fund_rotation/scoring/contracts.py",
            "backtest/fund_rotation/scoring/cluster_momentum.py",
            "backtest/fund_rotation/share_adjustment.py",
            "src/stockpred/fund_rotation/artifact_publisher.py",
        } <= set(FRAMEWORK_SOURCE_FILES)

    def test_sensitive_to_framework_change(self, tmp_path: Path):
        agent_root = tmp_path / "agent"
        path = agent_root / "backtest" / "fund_rotation" / "contracts.py"
        path.parent.mkdir(parents=True)
        path.write_text("A = 1\n", encoding="utf-8")
        sources = ("backtest/fund_rotation/contracts.py",)
        before = snapshot_framework(agent_root, source_files=sources)
        path.write_text("A = 2\n", encoding="utf-8")
        after = snapshot_framework(agent_root, source_files=sources)
        assert before != after

    def test_missing_declared_file_fails_fast(self, tmp_path: Path):
        agent_root = tmp_path / "agent"
        agent_root.mkdir()
        with pytest.raises(FrameworkSnapshotError, match="missing.py"):
            snapshot_framework(
                agent_root,
                source_files=("backtest/fund_rotation/missing.py",),
            )

    def test_empty_registry_fails_fast(self, tmp_path: Path):
        agent_root = tmp_path / "agent"
        agent_root.mkdir()
        with pytest.raises(FrameworkSnapshotError, match="registry is empty"):
            snapshot_framework(agent_root, source_files=())


class TestRunIdentityHash:
    def test_combines_all_components(self):
        first = compute_run_identity_hash(
            "s", "f", "c", "d", {"r": 1}, {"e": 1}
        )
        second = compute_run_identity_hash(
            "s", "f", "c", "d", {"r": 1}, {"e": 1}
        )
        assert first == second

    def test_sensitive_to_each_component(self):
        base = compute_run_identity_hash(
            "s", "f", "c", "d", {"r": 1}, {"e": 1}
        )
        assert compute_run_identity_hash(
            "S", "f", "c", "d", {"r": 1}, {"e": 1}
        ) != base
        assert compute_run_identity_hash(
            "s", "F", "c", "d", {"r": 1}, {"e": 1}
        ) != base
        assert compute_run_identity_hash(
            "s", "f", "C", "d", {"r": 1}, {"e": 1}
        ) != base
        assert compute_run_identity_hash(
            "s", "f", "c", "D", {"r": 1}, {"e": 1}
        ) != base
        assert compute_run_identity_hash(
            "s", "f", "c", "d", {"r": 2}, {"e": 1}
        ) != base
        assert compute_run_identity_hash(
            "s", "f", "c", "d", {"r": 1}, {"e": 2}
        ) != base

    def test_key_order_independent(self):
        first = compute_run_identity_hash(
            "s", "f", "c", "d", {"a": 1, "b": 2}, {"e": 1}
        )
        second = compute_run_identity_hash(
            "s", "f", "c", "d", {"b": 2, "a": 1}, {"e": 1}
        )
        assert first == second


class TestRuntimeVersions:
    def test_records_versions_not_in_hash(self):
        versions = record_runtime_versions()
        assert "python" in versions
        assert "pandas" in versions
        assert "numpy" in versions
        assert "scipy" in versions
        assert "scikit_learn" in versions
        assert "app" in versions
        assert isinstance(versions["python"], str)
