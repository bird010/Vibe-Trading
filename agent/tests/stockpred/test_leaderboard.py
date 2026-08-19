"""Tests for leaderboard service."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.stockpred.leaderboard import LeaderboardEntry, LeaderboardResult, LeaderboardService


def _create_run(
    batch_dir: Path,
    run_id: str,
    *,
    protocol_key: str = "proto_abc",
    mean_return: float = 0.02,
    win_rate: float = 0.7,
    ranking_eligible: bool = True,
    pit_assurance: str = "strict",
    strategy_id: str = "strategy_1",
) -> None:
    """Create a mock run directory with versioned artifacts."""
    run_dir = batch_dir / run_id
    version_dir = run_dir / "artifacts_versions" / "v1"
    version_dir.mkdir(parents=True)

    # config.json
    config = {"evaluation_protocol_key": protocol_key, "strategy_id": strategy_id, "pit_assurance": pit_assurance}
    (version_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

    # aggregate_metrics.json
    metrics = {"mean_return": mean_return, "win_rate": win_rate, "valid_cohort_count": 50, "total_cohort_count": 50}
    (version_dir / "aggregate_metrics.json").write_text(json.dumps(metrics), encoding="utf-8")

    # quality_report.json
    quality = {"ranking_eligible": ranking_eligible, "valid_eval_ratio": 1.0, "failures": []}
    (version_dir / "quality_report.json").write_text(json.dumps(quality), encoding="utf-8")

    # artifacts_current.json pointer
    pointer = {"version_id": "v1", "schema_version": "signal_cohort_v1"}
    (run_dir / "artifacts_current.json").write_text(json.dumps(pointer), encoding="utf-8")


class TestLeaderboardService:
    def test_returns_ranked_entries(self, tmp_path: Path):
        batch = tmp_path / "strategy_batches" / "batch_1"
        _create_run(batch, "run_a", mean_return=0.03)
        _create_run(batch, "run_b", mean_return=0.01)
        _create_run(batch, "run_c", mean_return=0.05)

        service = LeaderboardService(tmp_path)
        result = service.get_leaderboard("proto_abc")

        assert result.total == 3
        assert result.entries[0].mean_return == 0.05  # highest first
        assert result.entries[2].mean_return == 0.01

    def test_filters_by_protocol_key(self, tmp_path: Path):
        batch = tmp_path / "strategy_batches" / "batch_1"
        _create_run(batch, "run_a", protocol_key="proto_abc")
        _create_run(batch, "run_b", protocol_key="proto_xyz")

        service = LeaderboardService(tmp_path)
        result = service.get_leaderboard("proto_abc")

        assert result.total == 1
        assert result.entries[0].run_id == "run_a"

    def test_excludes_non_ranking_eligible(self, tmp_path: Path):
        batch = tmp_path / "strategy_batches" / "batch_1"
        _create_run(batch, "run_a", ranking_eligible=True)
        _create_run(batch, "run_b", ranking_eligible=False)

        service = LeaderboardService(tmp_path)
        result = service.get_leaderboard("proto_abc")

        assert result.total == 1
        assert result.entries[0].run_id == "run_a"

    def test_excludes_snapshot_only(self, tmp_path: Path):
        batch = tmp_path / "strategy_batches" / "batch_1"
        _create_run(batch, "run_a", pit_assurance="strict")
        _create_run(batch, "run_b", pit_assurance="snapshot_only")

        service = LeaderboardService(tmp_path)
        result = service.get_leaderboard("proto_abc")

        assert result.total == 1
        assert result.entries[0].run_id == "run_a"

    def test_limit_parameter(self, tmp_path: Path):
        batch = tmp_path / "strategy_batches" / "batch_1"
        for i in range(10):
            _create_run(batch, f"run_{i}", mean_return=0.01 * i)

        service = LeaderboardService(tmp_path)
        result = service.get_leaderboard("proto_abc", limit=3)

        assert result.total == 10  # total before limit
        assert len(result.entries) == 3

    def test_empty_when_no_runs(self, tmp_path: Path):
        service = LeaderboardService(tmp_path)
        result = service.get_leaderboard("proto_abc")

        assert result.total == 0
        assert result.entries == []

    def test_list_protocols(self, tmp_path: Path):
        batch = tmp_path / "strategy_batches" / "batch_1"
        _create_run(batch, "run_a", protocol_key="proto_1")
        _create_run(batch, "run_b", protocol_key="proto_2")
        _create_run(batch, "run_c", protocol_key="proto_1")

        service = LeaderboardService(tmp_path)
        protocols = service.list_protocols()

        assert "proto_1" in protocols
        assert "proto_2" in protocols

    def test_sort_by_win_rate(self, tmp_path: Path):
        batch = tmp_path / "strategy_batches" / "batch_1"
        _create_run(batch, "run_a", mean_return=0.01, win_rate=0.8)
        _create_run(batch, "run_b", mean_return=0.05, win_rate=0.4)

        service = LeaderboardService(tmp_path)
        result = service.get_leaderboard("proto_abc", sort_by="win_rate")

        assert result.entries[0].run_id == "run_a"  # higher win_rate first
