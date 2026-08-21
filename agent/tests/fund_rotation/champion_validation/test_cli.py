from __future__ import annotations

import json
import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backtest.fund_rotation.champion_validation.contracts import ValidationContract

_CLI_PATH = Path(__file__).resolve().parents[3] / "scripts" / "run_fund_rotation_champion_validation.py"
_CLI_SPEC = importlib.util.spec_from_file_location("fund_rotation_validation_cli", _CLI_PATH)
assert _CLI_SPEC and _CLI_SPEC.loader
_CLI_MODULE = importlib.util.module_from_spec(_CLI_SPEC)
_CLI_SPEC.loader.exec_module(_CLI_MODULE)
main = _CLI_MODULE.main


def test_cli_accepts_required_options_and_writes_chinese_report(tmp_path: Path):
    experiment_dir = tmp_path / "experiment"

    exit_code = main(
        [
            "--experiment-dir",
            str(experiment_dir),
            "--idempotency-key",
            "cli-key",
        ]
    )

    assert exit_code == 0
    report = (experiment_dir / "report.md").read_text(encoding="utf-8")
    assert "验证报告" in report
    assert "研究通过" not in report


def test_cli_resume_reuses_the_same_idempotency_key(tmp_path: Path):
    experiment_dir = tmp_path / "experiment"
    arguments = ["--experiment-dir", str(experiment_dir), "--idempotency-key", "cli-key"]

    assert main(arguments) == 0
    assert main([*arguments, "--resume"]) == 0


def test_cli_reads_and_applies_contract_file(tmp_path: Path):
    experiment_dir = tmp_path / "experiment"
    contract_path = tmp_path / "contract.json"
    contract = ValidationContract().frozen_spec()
    contract["experiment_id"] = "cli-contract-experiment"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")

    assert main(
        [
            "--experiment-dir",
            str(experiment_dir),
            "--contract",
            str(contract_path),
            "--idempotency-key",
            "contract-key",
        ]
    ) == 0

    stored = json.loads((experiment_dir / "validation_spec.json").read_text(encoding="utf-8"))
    assert stored["experiment_id"] == "cli-contract-experiment"


def test_cli_rejects_invalid_contract_file(tmp_path: Path):
    contract_path = tmp_path / "invalid-contract.json"
    contract = ValidationContract().frozen_spec()
    contract["trial_count"] = 29
    contract_path.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(ValueError, match="trial_count"):
        main(
            [
                "--experiment-dir",
                str(tmp_path / "experiment"),
                "--contract",
                str(contract_path),
                "--idempotency-key",
                "invalid-contract-key",
            ]
        )
