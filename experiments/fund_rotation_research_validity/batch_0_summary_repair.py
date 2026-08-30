"""Rebuild one fund-rotation summary from immutable execution-v2 artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

AGENT_ROOT = Path(__file__).resolve().parents[2] / "agent"
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from src.stockpred.fund_rotation.batch_child_runtime import (
    project_execution_summary_metrics,
)


SOURCE_ARTIFACTS = (
    "orders.csv",
    "positions.csv",
    "equity.csv",
    "trade_events.csv",
)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _execution_diagnostics(run_dir: Path) -> dict[str, Any]:
    diagnostics_path = run_dir / "strategy_execution_diagnostics.json"
    if diagnostics_path.is_file():
        return _read_json(diagnostics_path)
    metrics = _read_json(run_dir / "metrics.json")
    execution = metrics.get("execution")
    return dict(execution) if isinstance(execution, Mapping) else {}


def build_repaired_summary(
    before: Mapping[str, Any],
    diagnostics: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a repaired summary without mutating the source summary."""
    result = dict(before)
    projected = project_execution_summary_metrics(
        SimpleNamespace(execution_diagnostics=dict(diagnostics))
    )
    result.update(projected)
    return result


def repair_summary(run_dir: Path, output_dir: Path) -> dict[str, Any]:
    run_dir = Path(run_dir).resolve()
    output_dir = Path(output_dir).resolve()
    if output_dir == run_dir or run_dir in output_dir.parents:
        raise ValueError("output directory must be independent of source run")
    missing = [
        name
        for name in SOURCE_ARTIFACTS
        if not (run_dir / name).is_file()
    ]
    if missing:
        raise FileNotFoundError(
            "source run is missing required artifacts: " + ", ".join(missing)
        )
    before = _read_json(run_dir / "summary.json")
    diagnostics = _execution_diagnostics(run_dir)
    source_hashes = {
        name: _sha256(run_dir / name)
        for name in SOURCE_ARTIFACTS
    }
    repaired = build_repaired_summary(before, diagnostics)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(
        json.dumps(repaired, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "batch_0_summary_repair_v1",
        "source_run_dir": str(run_dir),
        "source_artifact_sha256": source_hashes,
        "source_summary": before,
        "repaired_summary": repaired,
        "source_summary_sha256": _sha256(run_dir / "summary.json"),
        "repaired_summary_sha256": _sha256(output_dir / "summary.json"),
    }
    (output_dir / "repair_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    report = (
        "# Batch 0 Summary 修复报告\n\n"
        f"- 源运行：`{run_dir}`\n"
        f"- 原始 turnover：`{before.get('turnover')}`\n"
        f"- 修复后 one_way_turnover：`{repaired.get('one_way_turnover')}`\n"
        f"- 修复后 annualized_one_way_turnover：`{repaired.get('annualized_one_way_turnover')}`\n"
        f"- 修复后 blocked_attempt_rate：`{repaired.get('blocked_attempt_rate')}`\n"
        f"- 指标契约：`{repaired.get('metric_contract_version')}`\n\n"
        "源订单、持仓、净值和成交产物只读取并记录 SHA-256，未被覆盖。"
        "修复产物写入独立目录；R39 策略引擎未重新运行。\n"
    )
    (output_dir / "batch_0_report.md").write_text(report, encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    repair_summary(args.run_dir, args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
