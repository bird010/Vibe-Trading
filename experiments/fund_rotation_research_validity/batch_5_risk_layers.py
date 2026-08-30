"""登记 Batch 5 风险层与防御资产实验边界，不伪造缺失的 paired backtest。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

EXPERIMENT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = EXPERIMENT_ROOT.parents[1]
DEFAULT_OUTPUT_DIR = EXPERIMENT_ROOT / "batch_5"
DEFAULT_REPORT_PATH = EXPERIMENT_ROOT / "batch_5_report.md"
GENERATED_AT = "2026-08-30T00:00:00+08:00"
TARGET_VOLATILITY = 0.15
FIXED_SHORT_BOND = "511010.SH"
DEFENSE_POOL = ("511010.SH", "511880.SH", "518880.SH")
ARMS = (
    {"id": "R75_VOL_TARGET", "strategy_id": "ai_rotation_r75_r39_vol_target", "defense": "cash"},
    {"id": "R76_CASH", "strategy_id": "ai_rotation_r76_cash_defense_baseline", "defense": "cash"},
    {"id": "FIXED_SHORT_BOND", "strategy_id": "ai_rotation_r76_fixed_short_bond", "defense": "fixed_short_bond"},
    {"id": "R77_DEFENSE_RELATIVE_MOMENTUM", "strategy_id": "ai_rotation_r77_defense_relative_momentum", "defense": "relative_momentum"},
)
METRICS = (
    "calmar",
    "max_drawdown",
    "cash_occupancy",
    "defense_turnover",
    "fold_contribution",
    "cagr",
    "sharpe",
)
STRESS_SCENARIOS = ("normal_cost", "2x_cost", "t_plus_1", "t_plus_2")


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
        "risk_layers_path": REPO_ROOT / "agent/backtest/fund_rotation/risk_layers.py",
        "r75_strategy_path": REPO_ROOT / "agent/backtest/fund_rotation/strategies/ai_rotation_r75_r39_vol_target/strategy.py",
        "r76_strategy_path": REPO_ROOT / "agent/backtest/fund_rotation/strategies/ai_rotation_r76_cash_defense_baseline/strategy.py",
        "r77_strategy_path": REPO_ROOT / "agent/backtest/fund_rotation/strategies/ai_rotation_r77_defense_relative_momentum/strategy.py",
        "registry_path": REPO_ROOT / "agent/backtest/fund_rotation/strategies/registry.py",
        "test_path": REPO_ROOT / "agent/tests/fund_rotation/test_risk_layers.py",
        "experiment_script_path": Path(__file__).resolve(),
    }
    source_identity: dict[str, str] = {}
    for name, path in source_paths.items():
        source_identity[name] = str(path)
        source_identity[name.removesuffix("_path") + "_sha256"] = _sha256(path)
    manifest_path = output_dir / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {
            "schema_version": "fund-rotation-risk-layer-experiment/v1",
            "experiment_id": "batch_5_risk_layers",
            "arms": list(ARMS),
            "volatility_target": {
                "target": TARGET_VOLATILITY,
                "formula": "min(1, target_volatility / portfolio_volatility)",
                "leverage": False,
            },
            "defense_pool": list(DEFENSE_POOL),
            "fixed_short_bond": FIXED_SHORT_BOND,
            "identity_breadth": "independent U1 identities only",
            "source_identity": source_identity,
            "inputs": {
                "u1_manifest": {"status": "unavailable", "reason": "no verified frozen U1 input supplied"},
                "paired_backtest": {"status": "unavailable", "reason": "no verified four-arm paired input supplied"},
                "causal_defense_history": {"status": "unavailable", "reason": "no verified defense-pool history supplied"},
            },
            "metrics": {metric: "unavailable" for metric in METRICS},
            "folds": {"status": "unavailable", "required": 3, "completed": 0},
            "stress_scenarios": {scenario: "unavailable" for scenario in STRESS_SCENARIOS},
            "status": "UNAVAILABLE_INPUTS",
            "promotion_allowed": False,
            "architecture_decision": "pending_u1_paired_risk_evidence",
            "reason": "缺少冻结 U1、相同快照下的四臂 paired backtest、三折和防御资产因果历史",
            "generated_at": GENERATED_AT,
        }
        _write_immutable(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    expected = {
        "schema_version": "fund-rotation-risk-layer-experiment/v1",
        "experiment_id": "batch_5_risk_layers",
        "arms": list(ARMS),
        "volatility_target": {
            "target": TARGET_VOLATILITY,
            "formula": "min(1, target_volatility / portfolio_volatility)",
            "leverage": False,
        },
        "defense_pool": list(DEFENSE_POOL),
        "fixed_short_bond": FIXED_SHORT_BOND,
        "identity_breadth": "independent U1 identities only",
        "source_identity": source_identity,
        "inputs": {
            "u1_manifest": {"status": "unavailable", "reason": "no verified frozen U1 input supplied"},
            "paired_backtest": {"status": "unavailable", "reason": "no verified four-arm paired input supplied"},
            "causal_defense_history": {"status": "unavailable", "reason": "no verified defense-pool history supplied"},
        },
        "metrics": {metric: "unavailable" for metric in METRICS},
        "folds": {"status": "unavailable", "required": 3, "completed": 0},
        "stress_scenarios": {scenario: "unavailable" for scenario in STRESS_SCENARIOS},
        "status": "UNAVAILABLE_INPUTS",
        "promotion_allowed": False,
        "architecture_decision": "pending_u1_paired_risk_evidence",
        "reason": "缺少冻结 U1、相同快照下的四臂 paired backtest、三折和防御资产因果历史",
    }
    if set(manifest) != set(expected) | {"generated_at"}:
        raise ValueError(f"immutable manifest schema mismatch: {manifest_path}")
    if manifest.get("generated_at") != GENERATED_AT or any(
        manifest.get(key) != value for key, value in expected.items()
    ):
        raise ValueError(f"immutable manifest source or timestamp mismatch: {manifest_path}")
    manifest_hash = _sha256(manifest_path)
    report = (
        "# Batch 5：风险层与防御资产\n\n"
        f"- 固定目标波动率：`{TARGET_VOLATILITY}`；exposure=min(1,target/σ)，不使用杠杆\n"
        "- 防御比较：现金基线、固定短债、冻结防御池相对动量；不做历史最优资产回填\n"
        "- breadth：只按独立 U1 identity 计数\n"
        f"- manifest SHA-256：`{manifest_hash}`\n"
        "- 实验状态：`UNAVAILABLE_INPUTS`\n"
        "- promotion_allowed：`false`\n\n"
        "## 证据边界\n\n"
        "R75 只增加一个固定目标波动率风险层，缺失或非正组合波动率时 fail-closed 到现金；"
        "R76 是现金防御基线；固定短债和 R77 防御相对动量分别作为防御层比较臂。"
        "首轮不与绝对动量组合，不使用杠杆，也不把事后表现最好的防御资产回填历史。\n\n"
        "当前缺少冻结 U1、四臂相同快照的 paired backtest、三折因果数据和防御池历史，"
        "因此 Calmar、MDD、现金占用、防御换手、fold contribution、CAGR/Sharpe 及 normal/2x/T+1/T+2"
        " 全部为 `unavailable`，不作风险层晋级结论。\n\n"
        "## 最小改动自评\n\n"
        "仅新增纯风险层、三个薄策略适配器、显式注册、focused tests、Batch 5 登记脚本和本中文报告；"
        "未修改 R39/R40、公共 Runner、execution ledger 或历史实验产物。\n"
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
    parser = argparse.ArgumentParser(description="登记 Batch 5 风险层与防御资产实验")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    args = parser.parse_args()
    run(output_dir=args.output_dir, report_path=args.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
