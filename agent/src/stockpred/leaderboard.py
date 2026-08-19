"""Strict leaderboard service.

Scans run directories, filters by evaluation_protocol_key + quality + pit_assurance,
and ranks by configurable metric. Implements design §14 and §15.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


@dataclass(frozen=True)
class LeaderboardEntry:
    """A single leaderboard entry."""

    run_id: str
    strategy_id: str
    mean_return: float
    win_rate: float
    valid_cohort_count: int
    ranking_eligible: bool
    pit_assurance: str
    quality_failures: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LeaderboardResult:
    """Leaderboard query result."""

    entries: list[LeaderboardEntry]
    total: int
    protocol_key: str


SortField = Literal["mean_return", "win_rate", "valid_cohort_count"]


class LeaderboardService:
    """Filesystem-based leaderboard service."""

    def __init__(self, runs_root: Path) -> None:
        self.runs_root = Path(runs_root)

    def get_leaderboard(
        self,
        evaluation_protocol_key: str,
        *,
        sort_by: SortField = "mean_return",
        limit: int = 50,
    ) -> LeaderboardResult:
        """Get ranked leaderboard for a given protocol key."""
        all_entries = self._scan_runs(evaluation_protocol_key)

        # Filter: ranking_eligible AND pit_assurance == strict
        eligible = [
            e for e in all_entries
            if e.ranking_eligible and e.pit_assurance == "strict"
        ]

        # Sort
        sort_key = lambda e: getattr(e, sort_by, 0.0)
        eligible.sort(key=sort_key, reverse=True)

        total = len(eligible)
        entries = eligible[:limit]

        return LeaderboardResult(
            entries=entries,
            total=total,
            protocol_key=evaluation_protocol_key,
        )

    def list_protocols(self) -> list[str]:
        """List all available evaluation protocol keys."""
        protocols: set[str] = set()
        batches_dir = self.runs_root / "strategy_batches"
        if not batches_dir.is_dir():
            return []

        for batch_dir in batches_dir.iterdir():
            if not batch_dir.is_dir():
                continue
            for run_dir in batch_dir.iterdir():
                config = self._read_config(run_dir)
                if config and "evaluation_protocol_key" in config:
                    protocols.add(config["evaluation_protocol_key"])

        return sorted(protocols)

    def _scan_runs(self, protocol_key: str) -> list[LeaderboardEntry]:
        """Scan all run directories for matching protocol key."""
        entries: list[LeaderboardEntry] = []
        batches_dir = self.runs_root / "strategy_batches"
        if not batches_dir.is_dir():
            return entries

        for batch_dir in batches_dir.iterdir():
            if not batch_dir.is_dir():
                continue
            for run_dir in batch_dir.iterdir():
                if not run_dir.is_dir():
                    continue
                entry = self._read_run_entry(run_dir, protocol_key)
                if entry is not None:
                    entries.append(entry)

        return entries

    def _read_run_entry(self, run_dir: Path, protocol_key: str) -> LeaderboardEntry | None:
        """Read a single run directory and return entry if matching."""
        # Check for versioned artifacts
        pointer_path = run_dir / "artifacts_current.json"
        if not pointer_path.is_file():
            return None

        try:
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            version_id = pointer["version_id"]
        except (json.JSONDecodeError, KeyError):
            return None

        version_dir = run_dir / "artifacts_versions" / version_id
        if not version_dir.is_dir():
            return None

        # Read config
        config = self._read_json(version_dir / "config.json")
        if config is None:
            return None

        # Filter by protocol key
        if config.get("evaluation_protocol_key") != protocol_key:
            return None

        # Read metrics
        metrics = self._read_json(version_dir / "aggregate_metrics.json")
        if metrics is None:
            return None

        # Read quality
        quality = self._read_json(version_dir / "quality_report.json")
        if quality is None:
            quality = {"ranking_eligible": False, "failures": ["missing_quality_report"]}

        return LeaderboardEntry(
            run_id=run_dir.name,
            strategy_id=config.get("strategy_id", ""),
            mean_return=float(metrics.get("mean_return", 0.0)),
            win_rate=float(metrics.get("win_rate", 0.0)),
            valid_cohort_count=int(metrics.get("valid_cohort_count", 0)),
            ranking_eligible=bool(quality.get("ranking_eligible", False)),
            pit_assurance=str(config.get("pit_assurance", "snapshot_only")),
            quality_failures=quality.get("failures", []),
        )

    def _read_config(self, run_dir: Path) -> dict[str, Any] | None:
        """Read config from a run directory (versioned)."""
        pointer_path = run_dir / "artifacts_current.json"
        if not pointer_path.is_file():
            return None
        try:
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            version_dir = run_dir / "artifacts_versions" / pointer["version_id"]
            return self._read_json(version_dir / "config.json")
        except (json.JSONDecodeError, KeyError, OSError):
            return None

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
