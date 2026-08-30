"""登记 Batch 4 三臂消融边界；没有 U1 输入时不伪造比较结果。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "experiments/fund_rotation_research_validity/batch_4"
DEFAULT_REPORT_PATH = REPO_ROOT / "experiments/fund_rotation_research_validity/batch_4_report.md"
GENERATED_AT = "2026-08-30T00:00:00+08:00"
ARM_CONTRACT = {
    "data_snapshot": "unavailable",
    "calendar": "unavailable",
    "execution_contract": "unavailable",
    "cost_model": "unavailable",
    "delay_model": "unavailable",
}
ARMS = (
    {"id": "M0", "momentum": True, "cluster": False, "carry": False, "identity_dedup": True},
    {"id": "M1", "momentum": True, "cluster": True, "carry": False, "identity_dedup": True},
    {"id": "M2", "momentum": True, "cluster": True, "carry": True, "identity_dedup": True},
)
METRICS = (
    "duplicate_underlying_exposure",
    "cluster_marginal_contribution",
    "carry_marginal_contribution",
    "holding_period",
    "switch_count",
    "turnover",
    "blocked_attempt_ratio",
    "cagr",
    "sharpe",
    "calmar",
    "max_drawdown",
    "fold_contribution",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_immutable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(content.encode("utf-8"))
    except FileExistsError:
        if path.read_text(encoding="utf-8") != content:
            raise FileExistsError(f"refusing to overwrite immutable artifact: {path}") from None


def run(*, output_dir: Path = DEFAULT_OUTPUT_DIR, report_path: Path = DEFAULT_REPORT_PATH) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    adapter_path = REPO_ROOT / "agent/backtest/fund_rotation/ablation.py"
    test_path = REPO_ROOT / "agent/tests/fund_rotation/test_ablation.py"
    source_identity = {
        "ablation_adapter_path": str(adapter_path),
        "ablation_adapter_sha256": _sha256(adapter_path),
        "ablation_test_path": str(test_path),
        "ablation_test_sha256": _sha256(test_path),
        "experiment_script_path": str(Path(__file__).resolve()),
        "experiment_script_sha256": _sha256(Path(__file__).resolve()),
    }
    manifest_path = output_dir / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {
            "schema_version": "fund-rotation-ablation-experiment/v1",
            "experiment_id": "batch_4_ablation",
            "arms": list(ARMS),
            "shared_contract": ARM_CONTRACT,
            "identity_dedup_required": True,
            "source_identity": source_identity,
            "inputs": {
                "u1_manifest": {"status": "unavailable", "reason": "no verified frozen U1 input supplied"},
                "paired_backtest": {"status": "unavailable", "reason": "no verified three-arm paired input supplied"},
            },
            "metrics": {metric: "unavailable" for metric in METRICS},
            "folds": {"status": "unavailable", "required": 3, "completed": 0},
            "stress_scenarios": {
                "normal_cost": "unavailable",
                "2x_cost": "unavailable",
                "t_plus_1": "unavailable",
                "t_plus_2": "unavailable",
            },
            "status": "UNAVAILABLE_INPUTS",
            "promotion_allowed": False,
            "architecture_decision": "pending_u1_paired_evidence",
            "reason": "缺少可验证冻结 U1、三臂相同快照/执行输入和三折 paired backtest",
            "generated_at": GENERATED_AT,
        }
        _write_immutable(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    expected = {
        "schema_version": "fund-rotation-ablation-experiment/v1",
        "experiment_id": "batch_4_ablation",
        "arms": list(ARMS),
        "shared_contract": ARM_CONTRACT,
        "identity_dedup_required": True,
        "source_identity": source_identity,
        "inputs": {
            "u1_manifest": {"status": "unavailable", "reason": "no verified frozen U1 input supplied"},
            "paired_backtest": {"status": "unavailable", "reason": "no verified three-arm paired input supplied"},
        },
        "metrics": {metric: "unavailable" for metric in METRICS},
        "folds": {"status": "unavailable", "required": 3, "completed": 0},
        "stress_scenarios": {
            "normal_cost": "unavailable",
            "2x_cost": "unavailable",
            "t_plus_1": "unavailable",
            "t_plus_2": "unavailable",
        },
        "status": "UNAVAILABLE_INPUTS",
        "promotion_allowed": False,
        "architecture_decision": "pending_u1_paired_evidence",
        "reason": "缺少可验证冻结 U1、三臂相同快照/执行输入和三折 paired backtest",
    }
    if set(manifest) != set(expected) | {"generated_at"}:
        raise ValueError(f"immutable manifest schema mismatch: {manifest_path}")
    if manifest.get("generated_at") != GENERATED_AT:
        raise ValueError(f"immutable manifest timestamp mismatch: {manifest_path}")
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise ValueError(f"immutable manifest identity or status mismatch: {manifest_path}")
    manifest_hash = _sha256(manifest_path)
    report = (
        "# Batch 4：Momentum/Cluster/Carry 三臂消融\n\n"
        "- M0：Momentum only；M1：Momentum + Cluster；M2：Momentum + Cluster + R39 carry\n"
        "- 三臂共同身份去重：`true`\n"
        f"- manifest SHA-256：`{manifest_hash}`\n"
        "- 实验状态：`UNAVAILABLE_INPUTS`\n"
        "- promotion_allowed：`false`\n\n"
        "## 证据边界\n\n"
        "三臂只改变声明的 mechanism toggle；数据快照、日历、执行合约、成本和延迟应完全共享。"
        "M0 关闭 cluster 选择但不关闭 PIT identity de-dup；M1 加入 cluster/representative 选择；"
        "M2 只在 M1 基础上使用既有 R39 staging 与 incumbent carry。没有引入 direct-correlation 迁移。\n\n"
        "当前没有可验证 U1、三臂 paired backtest 或三折输入，故 duplicate underlying exposure、"
        "cluster/carry marginal contribution、持有期、switch、换手、阻塞率、收益风险指标、"
        "fold contribution 和 normal/2x/T+1/T+2 全部为 `unavailable`；不把单元测试结果当作收益证据，"
        "不作架构晋级结论。\n\n"
        "## 最小改动自评\n\n"
        "仅新增固定消融适配器、focused tests、Batch 4 登记脚本和中文报告；未修改 R39/R40、"
        "公共 Runner、execution ledger、平台架构或 direct-correlation 实现。\n"
    )
    _write_immutable(report_path, report)
    return {
        "manifest": str(manifest_path),
        "manifest_sha256": manifest_hash,
        "report": str(report_path),
        "report_sha256": _sha256(report_path),
        "status": manifest["status"],
        "promotion_allowed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="登记 Batch 4 三臂消融实验")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    args = parser.parse_args()
    run(output_dir=args.output_dir, report_path=args.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
