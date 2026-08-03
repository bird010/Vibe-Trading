"""Strategy and framework source snapshots plus deterministic run identity.

The snapshot boundary is deliberately explicit and fail-fast: every declared
framework source must exist, and a strategy snapshot includes its package plus
cross-package strategy helpers that are imported into the strategy module.
Runtime library versions are recorded for audit but are not identity inputs.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Iterable


FRAMEWORK_SOURCE_FILES: tuple[str, ...] = (
    "backtest/fund_rotation/benchmarks.py",
    "backtest/fund_rotation/capacity.py",
    "backtest/fund_rotation/catalog.py",
    "backtest/fund_rotation/causal_data.py",
    "backtest/fund_rotation/clustering.py",
    "backtest/fund_rotation/config.py",
    "backtest/fund_rotation/contracts.py",
    "backtest/fund_rotation/correlation.py",
    "backtest/fund_rotation/etf_rules.py",
    "backtest/fund_rotation/evaluation.py",
    "backtest/fund_rotation/execution.py",
    "backtest/fund_rotation/executor.py",
    "backtest/fund_rotation/ideal_executor.py",
    "backtest/fund_rotation/metrics.py",
    "backtest/fund_rotation/momentum.py",
    "backtest/fund_rotation/orders.py",
    "backtest/fund_rotation/pipeline.py",
    "backtest/fund_rotation/returns.py",
    "backtest/fund_rotation/robustness.py",
    "backtest/fund_rotation/runner.py",
    "backtest/fund_rotation/share_adjustment.py",
    "backtest/fund_rotation/target_builder.py",
    "backtest/fund_rotation/universe.py",
    "src/stockpred/fund_rotation/artifact_publisher.py",
    "src/stockpred/fund_rotation/batch_child_runtime.py",
    "src/stockpred/fund_rotation/batch_models.py",
    "src/stockpred/fund_rotation/batch_persistence.py",
    "src/stockpred/fund_rotation/batch_service.py",
    "src/stockpred/fund_rotation/comparison.py",
    "src/stockpred/fund_rotation/data_snapshot.py",
    "src/stockpred/fund_rotation/persistence.py",
    "src/stockpred/fund_rotation/state_machine.py",
    "src/stockpred/fund_rotation/strategy_snapshot.py",
)


class FrameworkSnapshotError(RuntimeError):
    """Raised when a declared framework source cannot be snapshotted."""


@dataclass(frozen=True)
class StrategySourceSnapshot:
    implementation_hash: str
    relative_paths: tuple[str, ...]
    file_hashes: tuple[tuple[str, str], ...] = ()


def _hash_file_contents(paths_with_rel: list[tuple[str, bytes]]) -> str:
    hasher = hashlib.sha256()
    for relative_path, content in paths_with_rel:
        hasher.update(relative_path.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(content)
        hasher.update(b"\x00")
    return hasher.hexdigest()


def _strategy_dependency_files(strategy_cls: type) -> set[Path]:
    """Collect package files and imported strategy helper modules.

    The recursive module walk is constrained to
    ``backtest.fund_rotation.strategies``. It captures explicit imports such as
    the representative strategy's use of baseline signal helpers without
    making unrelated framework modules part of the strategy identity.
    """
    strategy_file = Path(inspect.getfile(strategy_cls)).resolve()
    package_dir = strategy_file.parent
    files = {
        path.resolve()
        for path in package_dir.rglob("*.py")
        if "__pycache__" not in path.parts
    }

    root_module = inspect.getmodule(strategy_cls)
    queue: list[ModuleType] = [root_module] if root_module is not None else []
    seen_modules: set[str] = set()
    while queue:
        module = queue.pop()
        module_name = getattr(module, "__name__", "")
        if (
            not module_name.startswith("backtest.fund_rotation.strategies")
            or module_name in seen_modules
        ):
            continue
        seen_modules.add(module_name)
        try:
            module_file = Path(inspect.getfile(module)).resolve()
        except (TypeError, OSError):
            module_file = None
        if module_file is not None and module_file.suffix == ".py":
            files.add(module_file)

        for value in vars(module).values():
            dependency_module: ModuleType | None = None
            if isinstance(value, ModuleType):
                dependency_module = value
            else:
                candidate_name = getattr(value, "__module__", "")
                if candidate_name.startswith("backtest.fund_rotation.strategies"):
                    dependency_module = sys.modules.get(candidate_name)
            if dependency_module is not None:
                queue.append(dependency_module)

    for dependency in getattr(strategy_cls, "implementation_dependencies", ()):
        try:
            path = Path(inspect.getfile(dependency)).resolve()
        except (TypeError, OSError) as exc:
            raise FrameworkSnapshotError(
                f"cannot resolve declared strategy dependency {dependency!r}: {exc}"
            ) from exc
        if path.suffix == ".py":
            files.add(path)
    return files


def snapshot_strategy_package(strategy_cls: type) -> StrategySourceSnapshot:
    strategy_file = Path(inspect.getfile(strategy_cls)).resolve()
    strategies_root = strategy_file.parent.parent
    files = _strategy_dependency_files(strategy_cls)
    if not files:
        raise FrameworkSnapshotError(
            f"strategy {strategy_cls!r} produced an empty source snapshot"
        )

    pairs: list[tuple[str, bytes]] = []
    relative_paths: list[str] = []
    file_hashes: list[tuple[str, str]] = []
    for path in sorted(files, key=lambda value: value.as_posix()):
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise FrameworkSnapshotError(
                f"cannot read strategy source {path}: {exc}"
            ) from exc
        try:
            relative_path = path.relative_to(strategies_root).as_posix()
        except ValueError:
            relative_path = f"external/{path.name}"
        relative_paths.append(relative_path)
        pairs.append((relative_path, content))
        file_hashes.append(
            (relative_path, hashlib.sha256(content).hexdigest())
        )

    return StrategySourceSnapshot(
        implementation_hash=_hash_file_contents(pairs),
        relative_paths=tuple(relative_paths),
        file_hashes=tuple(file_hashes),
    )


def snapshot_framework(
    agent_root: Path,
    *,
    source_files: Iterable[str] = FRAMEWORK_SOURCE_FILES,
) -> str:
    """Hash all declared common-framework files and fail on any omission."""
    root = Path(agent_root)
    pairs: list[tuple[str, bytes]] = []
    missing: list[str] = []
    for relative_path in sorted(set(source_files)):
        path = root / relative_path
        if not path.is_file():
            missing.append(relative_path)
            continue
        try:
            pairs.append((relative_path, path.read_bytes()))
        except OSError as exc:
            raise FrameworkSnapshotError(
                f"cannot read framework source {relative_path}: {exc}"
            ) from exc
    if missing:
        raise FrameworkSnapshotError(
            "declared framework sources are missing: " + ", ".join(missing)
        )
    if not pairs:
        raise FrameworkSnapshotError("framework source registry is empty")
    return _hash_file_contents(pairs)


def compute_run_identity_hash(
    strategy_implementation_hash: str,
    framework_implementation_hash: str,
    resolved_config_hash: str,
    data_snapshot_fingerprint: str,
    research_contract: object,
    execution_contract: object,
) -> str:
    canonical = json.dumps(
        {
            "strategy_implementation_hash": strategy_implementation_hash,
            "framework_implementation_hash": framework_implementation_hash,
            "resolved_config_hash": resolved_config_hash,
            "data_snapshot_fingerprint": data_snapshot_fingerprint,
            "research_contract": research_contract,
            "execution_contract": execution_contract,
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def record_runtime_versions() -> dict[str, str]:
    versions: dict[str, str] = {"python": sys.version.split()[0]}
    try:
        from cli._version import __version__ as app_version

        versions["app"] = app_version
    except ImportError:  # pragma: no cover
        versions["app"] = "unavailable"

    packages = {
        "pandas": "pandas",
        "numpy": "numpy",
        "scipy": "scipy",
        "scikit_learn": "sklearn",
        "lance": "lance",
    }
    for output_name, module_name in packages.items():
        try:
            module = __import__(module_name)
            versions[output_name] = str(
                getattr(module, "__version__", "unknown")
            )
        except ImportError:  # pragma: no cover
            versions[output_name] = "unavailable"
    return versions
