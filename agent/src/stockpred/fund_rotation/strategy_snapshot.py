"""Strategy & framework source snapshots and run identity — Phase 1 Task 5.

Provides the full snapshot machinery (design §19) that the Catalog's basic
snapshot deferred to:

* ``snapshot_strategy_package`` — hash every ``.py`` in a strategy's package
  directory (excluding ``__pycache__``), order- and path-separator-stable.
* ``snapshot_framework`` — hash the declared common-framework source files.
* ``compute_run_identity_hash`` — combine strategy/framework/config/data hashes
  plus the research and execution contracts into one run identity.
* ``record_runtime_versions`` — record interpreter/library versions for
  cross-run interpretation (NOT part of any hash).

Snapshots are fixed at capture time; later on-disk changes do not alter an
already-captured snapshot (§19.1).
"""

from __future__ import annotations

import hashlib
import inspect
import json
import sys
from dataclasses import dataclass
from pathlib import Path

# Common framework source files (relative to the ``agent`` directory) whose
# contents define the framework implementation hash. Missing files are skipped
# so the snapshot stays valid as modules are added across phases.
FRAMEWORK_SOURCE_FILES: tuple[str, ...] = (
    "backtest/fund_rotation/contracts.py",
    "backtest/fund_rotation/catalog.py",
    "backtest/fund_rotation/evaluation.py",
    "backtest/fund_rotation/pipeline.py",
    "backtest/fund_rotation/executor.py",
    "backtest/fund_rotation/etf_rules.py",
    "backtest/fund_rotation/capacity.py",
    "backtest/fund_rotation/orders.py",
    "backtest/fund_rotation/returns.py",
    "backtest/fund_rotation/metrics.py",
    "backtest/fund_rotation/universe.py",
    "backtest/fund_rotation/ideal_executor.py",
)


@dataclass(frozen=True)
class StrategySourceSnapshot:
    """Captured strategy package source snapshot."""

    implementation_hash: str
    relative_paths: tuple[str, ...]


def _hash_file_contents(paths_with_rel: list[tuple[str, bytes]]) -> str:
    """Stable hash over (relative_path, content) pairs.

    The relative path (POSIX separators) is mixed in so renamed files change the
    hash; sorting upstream makes enumeration order irrelevant.
    """
    hasher = hashlib.sha256()
    for rel, content in paths_with_rel:
        hasher.update(rel.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(content)
    return hasher.hexdigest()


def snapshot_strategy_package(strategy_cls: type) -> StrategySourceSnapshot:
    """Hash every ``.py`` in the strategy's package directory.

    Excludes ``__pycache__``. Uses paths relative to the package root with POSIX
    separators so the hash is stable across enumeration order and OS path
    separators. Captured once — later disk edits do not change this object.
    """
    package_file = Path(inspect.getfile(strategy_cls))
    package_dir = package_file.parent
    py_files = sorted(
        p for p in package_dir.rglob("*.py") if "__pycache__" not in p.parts
    )
    pairs: list[tuple[str, bytes]] = []
    rel_paths: list[str] = []
    for path in py_files:
        rel = path.relative_to(package_dir).as_posix()
        rel_paths.append(rel)
        pairs.append((rel, path.read_bytes()))
    return StrategySourceSnapshot(
        implementation_hash=_hash_file_contents(pairs),
        relative_paths=tuple(rel_paths),
    )


def snapshot_framework(agent_root: Path) -> str:
    """Hash the declared common-framework source files (missing files skipped)."""
    pairs: list[tuple[str, bytes]] = []
    for rel in sorted(FRAMEWORK_SOURCE_FILES):
        path = agent_root / rel
        if path.exists():
            pairs.append((rel, path.read_bytes()))
    return _hash_file_contents(pairs)


def compute_run_identity_hash(
    strategy_implementation_hash: str,
    framework_implementation_hash: str,
    resolved_config_hash: str,
    data_snapshot_fingerprint: str,
    research_contract: object,
    execution_contract: object,
) -> str:
    """§19 — combine all identity components into one run identity hash.

    Subsequent batch comparison reuses these exact fields (no second source-hash
    alias).
    """
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
    """Record interpreter/library versions for cross-run interpretation.

    These are audit metadata only — never part of any hash (so identical source
    + config + data yields the same identity regardless of where it runs).
    """
    versions: dict[str, str] = {"python": sys.version.split()[0]}
    try:
        import pandas

        versions["pandas"] = pandas.__version__
    except ImportError:  # pragma: no cover
        versions["pandas"] = "unavailable"
    try:
        import lance

        versions["lance"] = lance.__version__
    except ImportError:  # pragma: no cover
        versions["lance"] = "unavailable"
    return versions
