from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

from experiments.fund_rotation_research_validity.batch_0_summary_repair import (
    build_repaired_summary,
    repair_summary,
)


def test_batch_0_cli_runs_from_repository_root_without_pythonpath(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    script = repo_root / "experiments/fund_rotation_research_validity/batch_0_summary_repair.py"
    source_run = tmp_path / "source"
    _write_source_run(source_run)
    output_dir = tmp_path / "batch0-cli"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)

    result = subprocess.run(
        [sys.executable, str(script), "--run-dir", str(source_run), "--output-dir", str(output_dir)],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["turnover"] == 2.0
    assert summary["metric_contract_version"] == "execution_diagnostics_v2"
    manifest = json.loads((output_dir / "repair_manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_artifact_sha256"] == {
        name: hashlib.sha256((source_run / name).read_bytes()).hexdigest()
        for name in ("orders.csv", "positions.csv", "equity.csv", "trade_events.csv")
    }
    assert (output_dir / "batch_0_report.md").is_file()


def test_build_repaired_summary_projects_v2_metrics_without_zero_defaults() -> None:
    before = {
        "run_id": "run-1",
        "turnover": 0.0,
        "annual_return": 0.12,
    }
    diagnostics = {
        "metric_contract_version": "execution_diagnostics_v2",
        "attempts": {"blocked_attempt_rate": 0.132267441860465},
        "trades": {
            "one_way_turnover": 41.8862336903681,
            "annualized_one_way_turnover": 6.62191398367175,
            "commission": 24232.377515,
            "explicit_fee": 0.0,
            "slippage_opportunity_cost": 54990.4874868904,
        },
    }

    repaired = build_repaired_summary(before, diagnostics)

    assert repaired["turnover"] == 41.8862336903681
    assert repaired["one_way_turnover"] == 41.8862336903681
    assert repaired["annualized_one_way_turnover"] == 6.62191398367175
    assert repaired["blocked_attempt_rate"] == 0.132267441860465
    assert repaired["metric_contract_version"] == "execution_diagnostics_v2"
    assert repaired["execution_metrics_status"] == "available"
    assert repaired["annual_return"] == 0.12
    json.dumps(repaired, allow_nan=False)


def test_build_repaired_summary_marks_missing_metrics_partial() -> None:
    repaired = build_repaired_summary(
        {"turnover": 0.0},
        {
            "metric_contract_version": "execution_diagnostics_v2",
            "attempts": {},
            "trades": {},
        },
    )

    assert repaired["turnover"] is None
    assert repaired["execution_metrics_status"] == "unavailable"


def _write_source_run(run_dir: Path) -> None:
    run_dir.mkdir()
    (run_dir / "summary.json").write_text(
        json.dumps({"run_id": "run-1", "turnover": 0.0}),
        encoding="utf-8",
    )
    (run_dir / "strategy_execution_diagnostics.json").write_text(
        json.dumps(
            {
                "metric_contract_version": "execution_diagnostics_v2",
                "attempts": {"blocked_attempt_rate": 0.1},
                "trades": {
                    "one_way_turnover": 2.0,
                    "annualized_one_way_turnover": 1.0,
                    "commission": 3.0,
                    "explicit_fee": 0.0,
                    "slippage_opportunity_cost": 4.0,
                },
            }
        ),
        encoding="utf-8",
    )
    for name in ("orders.csv", "positions.csv", "equity.csv", "trade_events.csv"):
        (run_dir / name).write_text("data\n", encoding="utf-8")


def test_repair_summary_isolated_and_deterministic(tmp_path: Path) -> None:
    run_dir = tmp_path / "source"
    output_dir = tmp_path / "output"
    _write_source_run(run_dir)
    source_hashes = {
        name: (run_dir / name).read_bytes()
        for name in ("summary.json", "orders.csv", "positions.csv", "equity.csv", "trade_events.csv")
    }

    first = repair_summary(run_dir, output_dir)
    second = repair_summary(run_dir, output_dir)

    assert first["source_artifact_sha256"] == second["source_artifact_sha256"]
    assert json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))["turnover"] == 2.0
    assert all((run_dir / name).read_bytes() == content for name, content in source_hashes.items())
    assert (output_dir / "batch_0_report.md").exists()


def test_repair_summary_rejects_source_output_collision(tmp_path: Path) -> None:
    run_dir = tmp_path / "source"
    _write_source_run(run_dir)

    with pytest.raises(ValueError, match="independent"):
        repair_summary(run_dir, run_dir)


def test_repair_summary_rejects_missing_source_artifact(tmp_path: Path) -> None:
    run_dir = tmp_path / "source"
    _write_source_run(run_dir)
    (run_dir / "orders.csv").unlink()

    with pytest.raises(FileNotFoundError, match="orders.csv"):
        repair_summary(run_dir, tmp_path / "output")
