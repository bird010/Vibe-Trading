from __future__ import annotations

import json
import zipfile
from pathlib import Path

from src.stockpred.strategies.contracts import StrategyDescriptor
from src.stockpred.strategies.snapshot import snapshot_strategy, write_strategy_archive


def _descriptor() -> StrategyDescriptor:
    return StrategyDescriptor(
        id="alpha101_1",
        name="Alpha #1",
        kind="alpha_zoo",
        zoo="alpha101",
        min_warmup_bars=20,
    )


def test_snapshot_version_changes_when_source_content_changes(tmp_path: Path) -> None:
    source = tmp_path / "alpha.py"
    source.write_text("def compute(panel): return panel['close']\n", encoding="utf-8")
    first = snapshot_strategy(_descriptor(), {"alpha.py": source}, repository_root=tmp_path)

    source.write_text("def compute(panel): return -panel['close']\n", encoding="utf-8")
    second = snapshot_strategy(_descriptor(), {"alpha.py": source}, repository_root=tmp_path)

    assert first.strategy_version != second.strategy_version


def test_snapshot_archive_preserves_hashed_source_and_metadata(tmp_path: Path) -> None:
    source = tmp_path / "alpha.py"
    source.write_text("def compute(panel): return panel['close']\n", encoding="utf-8")
    snapshot = snapshot_strategy(_descriptor(), {"alpha.py": source}, repository_root=tmp_path)

    archive = write_strategy_archive(tmp_path / "run", snapshot)

    with zipfile.ZipFile(archive) as bundle:
        assert bundle.read("alpha.py") == source.read_bytes()
    payload = json.loads((tmp_path / "run" / "strategy_snapshot.json").read_text(encoding="utf-8"))
    assert payload["strategy_version"] == snapshot.strategy_version
