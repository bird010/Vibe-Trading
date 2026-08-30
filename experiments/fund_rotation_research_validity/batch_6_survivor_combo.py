"""登记 Batch 6 幸存机制组合边界；没有晋级 survivor 时严格阻断。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

EXPERIMENT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = EXPERIMENT_ROOT.parents[1]
DEFAULT_OUTPUT_DIR = EXPERIMENT_ROOT / "batch_6_cycles6_v3"
DEFAULT_REPORT_PATH = EXPERIMENT_ROOT / "batch_6_cycles6_v3_report.md"
GENERATED_AT = "2026-08-30T00:00:00+08:00"
SURVIVOR_CANDIDATES = (
    {"mechanism_id": "batch_3a_absolute_momentum", "promotion_allowed": False},
    {"mechanism_id": "batch_3b_multi_horizon_rank", "promotion_allowed": False},
    {"mechanism_id": "batch_3c_vol_adjusted_score", "promotion_allowed": False},
    {"mechanism_id": "batch_5_risk_layers", "promotion_allowed": False},
)
METRICS = (
    "layer_marginal_contribution",
    "switch_count",
    "holding_period",
    "turnover",
    "blocked_attempt_ratio",
    "cash_occupancy",
    "cagr",
    "sharpe",
    "calmar",
    "max_drawdown",
    "fold_contribution",
)
STRESS_SCENARIOS = ("normal_cost", "2x_cost", "t_plus_1", "t_plus_2", "neighbor")


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
    source_paths = {
        "combo_strategy_path": REPO_ROOT / "agent/backtest/fund_rotation/strategies/ai_rotation_r78_survivor_combo/strategy.py",
        "registry_path": REPO_ROOT / "agent/backtest/fund_rotation/strategies/registry.py",
        "test_path": REPO_ROOT / "agent/tests/fund_rotation/test_r78_survivor_combo.py",
        "batch_3a_manifest_path": EXPERIMENT_ROOT / "batch_3a/manifest.json",
        "batch_3b_manifest_path": EXPERIMENT_ROOT / "batch_3b/manifest.json",
        "batch_3c_manifest_path": EXPERIMENT_ROOT / "batch_3c/manifest.json",
        "batch_5_manifest_path": EXPERIMENT_ROOT / "batch_5/manifest.json",
        "shadow_manifest_path": EXPERIMENT_ROOT / "shadow_a_cycles6_v2/shadow_manifest.json",
        "experiment_script_path": Path(__file__).resolve(),
    }
    source_identity: dict[str, str] = {}
    for name, path in source_paths.items():
        source_identity[name] = str(path)
        source_identity[name.removesuffix("_path") + "_sha256"] = _sha256(path)
    survivor_inputs = [
        {
            **candidate,
            "status": "unavailable",
            "reason": "single-variable experiment is not promotion-allowed",
        }
        for candidate in SURVIVOR_CANDIDATES
    ]
    manifest_path = output_dir / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {
            "schema_version": "fund-rotation-survivor-combo-experiment/v1",
            "experiment_id": "batch_6_survivor_combo",
            "strategy_id": "ai_rotation_r78_survivor_combo",
            "survivor_candidates": survivor_inputs,
            "composition_rule": "only promotion_allowed=true mechanisms with review P0/P1=0",
            "composition_order": ["ranking", "risk", "defense"],
            "new_parameters": False,
            "source_identity": source_identity,
            "inputs": {
                "paired_backtest": {"status": "unavailable", "reason": "no promoted survivor inputs"},
                "u1_manifest": {"status": "unavailable", "reason": "no verified frozen U1 input supplied"},
            },
            "shadow_qualification_requirement": {
                "hard_gates": {"forward_observation_weeks": 26, "completed_rebalance_cycles": 6},
                "recommended_observation_weeks": 104,
                "status": "UNRESOLVED",
                "qualification": "not complete",
                "active_append_only_process": True,
            },
            "metrics": {metric: "unavailable" for metric in METRICS},
            "folds": {"status": "unavailable", "required": 3, "completed": 0},
            "stress_scenarios": {scenario: "unavailable" for scenario in STRESS_SCENARIOS},
            "status": "UNAVAILABLE_INPUTS",
            "promotion_allowed": False,
            "architecture_decision": "no_survivor_combo_until_single_variable_promotion",
            "reason": "没有任何单变量机制获得 promotion_allowed=true，组合和边际贡献实验被 fail-closed 阻断",
            "generated_at": GENERATED_AT,
        }
        _write_immutable(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    expected = {
        "schema_version": "fund-rotation-survivor-combo-experiment/v1",
        "experiment_id": "batch_6_survivor_combo",
        "strategy_id": "ai_rotation_r78_survivor_combo",
        "survivor_candidates": survivor_inputs,
        "composition_rule": "only promotion_allowed=true mechanisms with review P0/P1=0",
        "composition_order": ["ranking", "risk", "defense"],
        "new_parameters": False,
        "source_identity": source_identity,
        "inputs": {
            "paired_backtest": {"status": "unavailable", "reason": "no promoted survivor inputs"},
            "u1_manifest": {"status": "unavailable", "reason": "no verified frozen U1 input supplied"},
        },
        "shadow_qualification_requirement": {
            "hard_gates": {"forward_observation_weeks": 26, "completed_rebalance_cycles": 6},
            "recommended_observation_weeks": 104,
            "status": "UNRESOLVED",
            "qualification": "not complete",
            "active_append_only_process": True,
        },
        "metrics": {metric: "unavailable" for metric in METRICS},
        "folds": {"status": "unavailable", "required": 3, "completed": 0},
        "stress_scenarios": {scenario: "unavailable" for scenario in STRESS_SCENARIOS},
        "status": "UNAVAILABLE_INPUTS",
        "promotion_allowed": False,
        "architecture_decision": "no_survivor_combo_until_single_variable_promotion",
        "reason": "没有任何单变量机制获得 promotion_allowed=true，组合和边际贡献实验被 fail-closed 阻断",
    }
    if set(manifest) != set(expected) | {"generated_at"} or manifest.get("generated_at") != GENERATED_AT:
        raise ValueError(f"immutable manifest schema mismatch: {manifest_path}")
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise ValueError(f"immutable manifest identity or status mismatch: {manifest_path}")
    manifest_hash = _sha256(manifest_path)
    report = (
        "# Batch 6：幸存机制组合\n\n"
        "- 组合策略：`ai_rotation_r78_survivor_combo`\n"
        "- 组合规则：只有单变量 `promotion_allowed=true` 且 review P0/P1=0 的冻结机制才可进入\n"
        "- 当前 survivor：无；组合状态 fail-closed\n"
        f"- manifest SHA-256：`{manifest_hash}`\n"
        "- 实验状态：`UNAVAILABLE_INPUTS`\n"
        "- promotion_allowed：`false`\n\n"
        "## 证据边界\n\n"
        "Batch 3A/3B/3C 与 Batch 5 的单变量产物均未提供 promotion_allowed=true 的实际前瞻证据，"
        "因此不组合任何机制，不声称 winner，不把代码审查通过当作收益晋级。组合后的三折、"
        "normal/2x/T+1/T+2/neighbor 和逐层边际贡献全部保持 unavailable。\n\n"
        "Shadow 26 周 + 6 次硬门槛仍为 `UNRESOLVED`；104 周是建议观察长度。Shadow 是持续 append-only 过程，不能标记为完成资格。\n\n"
        "## 停止方向\n\n"
        "在获得真实单变量晋级证据前，停止 Batch 6 收益组合、参数邻域比较和 winner 选择；"
        "不扩大 Rxx 网格，不引入新可调参数。\n\n"
        "## 最小改动自评\n\n"
        "仅新增 R78 evidence-gated composition adapter、显式 registry 条目、focused tests、Batch 6 登记脚本、"
        "本中文报告和 acceptance matrix；未修改既有策略算法、Runner、execution ledger 或 Shadow 账本。\n"
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
    parser = argparse.ArgumentParser(description="登记 Batch 6 幸存机制组合实验")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    args = parser.parse_args()
    run(output_dir=args.output_dir, report_path=args.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
