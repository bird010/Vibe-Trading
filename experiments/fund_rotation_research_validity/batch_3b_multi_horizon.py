"""登记 Batch 3B 的多周期排名实验边界，不伪造缺失的 paired backtest。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

EXPERIMENT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = EXPERIMENT_ROOT.parents[1]
for _path in (REPO_ROOT / "agent", REPO_ROOT / "agent" / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from backtest.fund_rotation.strategies.ai_rotation_r73_r39_multi_horizon_rank.strategy import (  # noqa: E402
    AiRotationR73R39MultiHorizonRankStrategy,
)
from src.stockpred.fund_rotation.strategy_snapshot import (  # noqa: E402
    snapshot_strategy_package,
)

DEFAULT_OUTPUT_DIR = EXPERIMENT_ROOT / "batch_3b"
DEFAULT_REPORT_PATH = EXPERIMENT_ROOT / "batch_3b_report.md"
STRATEGY_ID = "ai_rotation_r73_r39_multi_horizon_rank"
R39_CONTROL_ID = "ai_rotation_r39_incumbent_carry"
RANK_HORIZONS = (60, 120, 240)
STRESS_SCENARIOS = ("normal_cost", "2x_cost", "t_plus_1", "t_plus_2")
METRICS = (
    "rank_correlation_r60_r120",
    "rank_correlation_r60_r240",
    "rank_correlation_r120_r240",
    "rank_flip_rate",
    "score_coverage",
    "holding_period",
    "switch_count",
    "turnover",
    "fold_contribution",
    "cagr",
    "sharpe",
    "max_drawdown",
    "cvar",
    "worst_3m",
    "cost_after_return",
)
COVERAGE = {
    "r60": "unavailable",
    "r120": "unavailable",
    "r240": "unavailable",
    "rank_flip": "unavailable",
    "paired_control": "unavailable",
    "three_fold": "unavailable",
}
PARAMETER_NEIGHBORHOOD = {
    "status": "unavailable",
    "reason": "fixed equal weights only; no parameter sweep supplied",
}
FOLD_CONTRIBUTION = {
    "status": "unavailable",
    "required_folds": 3,
    "completed_folds": 0,
}
BLOCKING_EVIDENCE = {
    "u1_snapshot": "unavailable",
    "r39_paired_control": "unavailable",
    "causal_market_data": "unavailable",
    "forward_outcomes": "unavailable",
}
GENERATED_AT = "2026-08-30T00:00:00+08:00"


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
    strategy_path = REPO_ROOT / "agent/backtest/fund_rotation/strategies/ai_rotation_r73_r39_multi_horizon_rank/strategy.py"
    r39_path = REPO_ROOT / "agent/backtest/fund_rotation/strategies/ai_rotation_r39_incumbent_carry/strategy.py"
    script_path = Path(__file__).resolve()
    snapshot = snapshot_strategy_package(AiRotationR73R39MultiHorizonRankStrategy)
    source_identity = {
        "r73_strategy_path": str(strategy_path),
        "r73_strategy_sha256": _sha256(strategy_path),
        "r73_dependency_snapshot_sha256": snapshot.implementation_hash,
        "r73_dependency_file_hashes": dict(snapshot.file_hashes),
        "r39_control_path": str(r39_path),
        "r39_control_sha256": _sha256(r39_path),
        "experiment_script_path": str(script_path),
        "experiment_script_sha256": _sha256(script_path),
    }
    manifest_path = output_dir / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {
            "schema_version": "fund-rotation-single-variable-experiment/v1",
            "experiment_id": "batch_3b_multi_horizon_rank",
            "strategy_id": STRATEGY_ID,
            "control_strategy_id": R39_CONTROL_ID,
            "mechanism_change": "replace only ranking with equal-weight rank(R60)+rank(R120)+rank(R240)",
            "rank_horizons": list(RANK_HORIZONS),
            "source_identity": source_identity,
            "inputs": {
                "u1_manifest": {"status": "unavailable", "reason": "no verified frozen U1 input supplied"},
                "r39_control_manifest": {"status": "unavailable", "reason": "no verified paired-run control input supplied"},
            },
            "causal_contract": {
                "signal_date_close_included": True,
                "future_dates_excluded": True,
                "t_plus_1": "unavailable",
                "t_plus_2": "unavailable",
            },
            "coverage": COVERAGE,
            "parameter_neighborhood": PARAMETER_NEIGHBORHOOD,
            "fold_contribution": FOLD_CONTRIBUTION,
            "blocking_evidence": BLOCKING_EVIDENCE,
            "folds": {"status": "unavailable", "required": 3, "completed": 0},
            "stress_scenarios": {scenario: "unavailable" for scenario in STRESS_SCENARIOS},
            "metrics": {metric: "unavailable" for metric in METRICS},
            "status": "UNAVAILABLE_INPUTS",
            "promotion_allowed": False,
            "reason": "缺少可验证冻结 U1、R39 paired control、三折回测入口和因果行情输入",
            "generated_at": GENERATED_AT,
        }
        _write_immutable(
            manifest_path,
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
    expected = {
        "schema_version": "fund-rotation-single-variable-experiment/v1",
        "experiment_id": "batch_3b_multi_horizon_rank",
        "strategy_id": STRATEGY_ID,
        "control_strategy_id": R39_CONTROL_ID,
        "mechanism_change": "replace only ranking with equal-weight rank(R60)+rank(R120)+rank(R240)",
        "rank_horizons": list(RANK_HORIZONS),
        "source_identity": source_identity,
        "inputs": {
            "u1_manifest": {"status": "unavailable", "reason": "no verified frozen U1 input supplied"},
            "r39_control_manifest": {"status": "unavailable", "reason": "no verified paired-run control input supplied"},
        },
        "causal_contract": {
            "signal_date_close_included": True,
            "future_dates_excluded": True,
            "t_plus_1": "unavailable",
            "t_plus_2": "unavailable",
        },
        "coverage": COVERAGE,
        "parameter_neighborhood": PARAMETER_NEIGHBORHOOD,
        "fold_contribution": FOLD_CONTRIBUTION,
        "blocking_evidence": BLOCKING_EVIDENCE,
        "folds": {"status": "unavailable", "required": 3, "completed": 0},
        "stress_scenarios": {scenario: "unavailable" for scenario in STRESS_SCENARIOS},
        "metrics": {metric: "unavailable" for metric in METRICS},
        "status": "UNAVAILABLE_INPUTS",
        "promotion_allowed": False,
        "reason": "缺少可验证冻结 U1、R39 paired control、三折回测入口和因果行情输入",
    }
    if set(manifest) != set(expected) | {"generated_at"}:
        raise ValueError(f"immutable manifest schema mismatch: {manifest_path}")
    if manifest.get("generated_at") != GENERATED_AT:
        raise ValueError(f"immutable manifest timestamp mismatch: {manifest_path}")
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise ValueError(f"immutable manifest identity or status mismatch: {manifest_path}")
    manifest_hash = _sha256(manifest_path)
    report = (
        "# Batch 3B：多周期相对动量排名\n\n"
        f"- 策略：`{STRATEGY_ID}`\n"
        f"- 控制组：`{R39_CONTROL_ID}`\n"
        "- 唯一机制变化：等权 `rank(R60) + rank(R120) + rank(R240)`，不加入 R20\n"
        f"- manifest SHA-256：`{manifest_hash}`\n"
        "- 实验状态：`UNAVAILABLE_INPUTS`\n"
        "- promotion_allowed：`false`\n\n"
        "## 证据边界\n\n"
        "R73 只替换 R39 的 cluster ranking score；选中的槽位、代表基金、staging、"
        "incumbent carry、执行和成本语义保持不变。每个周期先在 signal cutoff 可见的"
        "adjusted close 上计算相对收益，再对 cluster 做确定性降序排名，ties 使用最小"
        "成员代码；只有三个周期均有分数的 cluster 才可聚合。\n\n"
        "当前没有可验证冻结 U1、R39 paired control、三折回测和因果行情输入，故 R60/"
        "R120/R240 覆盖率、周期排名相关性、rank flip、持有期、switch、换手、fold contribution、"
        "CAGR/Sharpe/MDD、CVaR、worst 3M、成本后收益及 normal/2x/T+1/T+2 全部为"
        "`unavailable`，没有用零值替代或声称晋级。\n\n"
        "参数邻域固定为三个等权周期，不执行事后搜索；blocking evidence 和 fold contribution"
        "均显式记录为 `unavailable`。\n\n"
        "## 最小改动自评\n\n"
        "仅新增 R73 score-only overlay、显式注册、focused tests、Batch 3B 登记脚本和本中文报告；"
        "未修改 R39/R40、Runner、execution ledger、平台架构或历史实验产物。\n"
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
    parser = argparse.ArgumentParser(description="登记 Batch 3B 多周期相对动量排名实验")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    args = parser.parse_args()
    run(output_dir=args.output_dir, report_path=args.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
