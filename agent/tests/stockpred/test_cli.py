from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from cli import _legacy
from src.stockpred import cli_handlers

AGENT_DIR = Path(__file__).resolve().parents[2]


class _Store:
    def __init__(self, state: dict[str, object]) -> None:
        self.state = state

    def require(self, run_id: str) -> Path:
        return Path(run_id)

    def read(self, run_dir: Path) -> dict[str, object]:  # noqa: ARG002
        return self.state


class _Service:
    def __init__(self, *, status: str = "success") -> None:
        self.run_id = "graph_123" if status == "success" else "graph_failed"
        self.store = _Store(
            {
                "status": status,
                "error_code": None if status == "success" else "STOCKPRED_PARITY_FAILED",
                "reason": None if status == "success" else "parity failed",
            }
        )
        self.config = None

    def run(self, config) -> str:  # noqa: ANN001
        self.config = config
        return self.run_id


def _parse(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    cli_handlers.add_subparser(subparsers)
    return parser.parse_args(["stockpred", *arguments])


def test_graph_backtest_json_calls_shared_service(monkeypatch, capsys) -> None:
    service = _Service()
    monkeypatch.setattr(cli_handlers, "build_service", lambda: service)
    args = _parse(
        [
            "graph-backtest",
            "--start",
            "2025-01-01",
            "--end",
            "2025-03-31",
            "--json",
        ]
    )

    assert cli_handlers.dispatch(args) == 0
    assert json.loads(capsys.readouterr().out)["run_id"] == "graph_123"
    assert service.config.start == "20250101"


def test_parity_cli_rejects_top_n_override(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli_handlers, "build_service", lambda: _Service())
    args = _parse(
        [
            "graph-backtest",
            "--start",
            "2025-01-01",
            "--end",
            "2025-03-31",
            "--top-n",
            "20",
            "--json",
        ]
    )

    assert cli_handlers.dispatch(args) == 2
    assert json.loads(capsys.readouterr().out)["error_code"] == "STOCKPRED_CONFIG_INVALID"


def test_graph_backtest_returns_nonzero_when_persisted_run_failed(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(cli_handlers, "build_service", lambda: _Service(status="failed"))
    args = _parse(
        [
            "graph-backtest",
            "--start",
            "2025-01-01",
            "--end",
            "2025-03-31",
            "--json",
        ]
    )

    assert cli_handlers.dispatch(args) == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == "STOCKPRED_PARITY_FAILED"


def test_missing_parity_golden_returns_usage_error(tmp_path: Path, capsys) -> None:
    args = _parse(
        [
            "graph-backtest",
            "--start",
            "2025-01-01",
            "--end",
            "2025-03-31",
            "--parity-golden",
            str(tmp_path / "missing"),
            "--json",
        ]
    )

    assert cli_handlers.dispatch(args) == 2
    assert json.loads(capsys.readouterr().out)["error_code"] == "STOCKPRED_GOLDEN_MISSING"


def test_legacy_cli_registers_and_dispatches_stockpred(monkeypatch) -> None:
    monkeypatch.setattr(cli_handlers, "dispatch", lambda args: 7)

    assert _legacy.main(["stockpred", "status", "--json"]) == 7


def test_legacy_cli_builds_parser_with_only_installed_top_level_packages() -> None:
    environment = os.environ | {"PYTHONPATH": str(AGENT_DIR)}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from cli import _legacy; _legacy._build_parser()",
        ],
        cwd=AGENT_DIR,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
