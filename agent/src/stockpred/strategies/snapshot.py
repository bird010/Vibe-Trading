"""Immutable source snapshots for auditable StockPred strategy reports."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from importlib import metadata
from pathlib import Path
from typing import Mapping

from src.stockpred.contracts import StockPredDataError
from src.stockpred.run_store import atomic_json
from src.stockpred.strategies.contracts import (
    StrategyDescriptor,
    StrategySnapshot,
    StrategySourceFile,
)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _git_value(repository_root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value or None


def _dependencies() -> tuple[str, ...]:
    values = [f"{item.metadata['Name']}=={item.version}" for item in metadata.distributions() if item.metadata.get("Name")]
    return tuple(sorted(set(values), key=str.lower))


def snapshot_strategy(
    descriptor: StrategyDescriptor,
    source_paths: Mapping[str, Path],
    *,
    repository_root: Path,
) -> StrategySnapshot:
    """Freeze exact source bytes and runtime identity before a strategy starts."""

    files: list[StrategySourceFile] = []
    for archive_path, source_path in sorted(source_paths.items()):
        path = Path(source_path).resolve()
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise StockPredDataError(
                "STOCKPRED_STRATEGY_SNAPSHOT",
                f"cannot read strategy source {archive_path}: {exc}",
            ) from exc
        if Path(archive_path).is_absolute() or ".." in Path(archive_path).parts:
            raise StockPredDataError(
                "STOCKPRED_STRATEGY_SNAPSHOT",
                f"unsafe strategy archive path: {archive_path}",
            )
        files.append(
            StrategySourceFile(
                path=archive_path.replace("\\", "/"),
                sha256=_sha256(content),
                content=content.decode("utf-8"),
            )
        )
    if not files:
        raise StockPredDataError("STOCKPRED_STRATEGY_SNAPSHOT", "strategy source list is empty")

    version_payload = {
        "descriptor": descriptor.model_dump(mode="json"),
        "source_files": [{"path": item.path, "sha256": item.sha256} for item in files],
    }
    encoded = json.dumps(version_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    dirty = _git_value(repository_root, "status", "--porcelain")
    return StrategySnapshot(
        descriptor=descriptor,
        source_files=tuple(files),
        strategy_version=_sha256(encoded),
        git_commit=_git_value(repository_root, "rev-parse", "HEAD"),
        git_dirty=bool(dirty),
        python_version=sys.version.split()[0],
        dependencies=_dependencies(),
    )


def write_strategy_archive(run_dir: Path, snapshot: StrategySnapshot) -> Path:
    """Persist the metadata and content-addressed source archive for one report."""

    root = Path(run_dir)
    root.mkdir(parents=True, exist_ok=True)
    atomic_json(root / "strategy_snapshot.json", snapshot.model_dump(mode="json"))
    archive = root / "strategy_source.zip"
    temporary = archive.with_name(f".{archive.name}.tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for source in snapshot.source_files:
            bundle.writestr(source.path, source.content)
    temporary.replace(archive)
    return archive
