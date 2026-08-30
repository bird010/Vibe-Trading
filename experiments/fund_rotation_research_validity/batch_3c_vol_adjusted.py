"""登记 Batch 3C 的波动率调整排名实验边界，不伪造缺失的 paired backtest。"""

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

from backtest.fund_rotation.strategies.ai_rotation_r74_r39_vol_adjusted_score.strategy import (  # noqa: E402
    AiRotationR74R39VolAdjustedScoreStrategy,
)
from src.stockpred.fund_rotation.strategy_snapshot import snapshot_strategy_package  # noqa: E402

DEFAULT_OUTPUT_DIR = EXPERIMENT_ROOT / "batch_3c"
DEFAULT_REPORT_PATH = EXPERIMENT_ROOT / "batch_3c_report.md"
STRATEGY_ID = "ai_rotation_r74_r39_vol_adjusted_score"
R39_CONTROL_ID = "ai_rotation_r39_incumbent_carry"
STRESS_SCENARIOS = ("normal_cost", "2x_cost", "t_plus_1", "t_plus_2")
METRICS = (
    "volatility_60_coverage",
    "score_coverage",
    "rank_change_vs_r39",
    "holding_period",
    "switch_count",
    "turnover",
    "blocked_attempt_ratio",
    "fold_contribution",
    "cagr",
    "sharpe",
    "max_drawdown",
    "calmar",
    "cvar",
    "worst_3m",
    "cost_after_return",
)
COVERAGE = {
    "volatility_60": "unavailable",
    "momentum": "unavailable",
    "score": "unavailable",
    "paired_control": "unavailable",
    "three_fold": "unavailable",
}
PARAMETER_NEIGHBORHOOD = {
    "status": "unavailable",
    "reason": "fixed volatility_60 and fixed 1/top_n slots; no parameter sweep supplied",
}
FOLD_CONTRIBUTION = {"status": "unavailable", "required_folds": 3, "completed_folds": 0}
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
    strategy_path = REPO_ROOT / "agent/backtest/fund_rotation/strategies/ai_rotation_r74_r39_vol_adjusted_score/strategy.py"
    r39_path = REPO_ROOT / "agent/backtest/fund_rotation/strategies/ai_rotation_r39_incumbent_carry/strategy.py"
    script_path = Path(__file__).resolve()
    snapshot = snapshot_strategy_package(AiRotationR74R39VolAdjustedScoreStrategy)
    source_identity = {
        "r74_strategy_path": str(strategy_path),
        "r74_strategy_sha256": _sha256(strategy_path),
        "r74_dependency_snapshot_sha256": snapshot.implementation_hash,
        "r74_dependency_file_hashes": dict(snapshot.file_hashes),
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
            "experiment_id": "batch_3c_vol_adjusted_score",
            "strategy_id": STRATEGY_ID,
            "control_strategy_id": R39_CONTROL_ID,
            "mechanism_change": "replace only ranking with positive R39 momentum / annualized volatility_60",
            "volatility_window_days": 60,
            "volatility_annualization": 252,
            "minimum_volatility_epsilon": 1e-8,
            "weighting_rule": "unchanged fixed 1/top_n slots with vacant cash",
            "inputs": {
                "u1_manifest": {"status": "unavailable", "reason": "no verified frozen U1 input supplied"},
                "r39_control_manifest": {"status": "unavailable", "reason": "no verified paired-run control input supplied"},
            },
            "source_identity": source_identity,
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
        _write_immutable(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    expected = {
        "schema_version": "fund-rotation-single-variable-experiment/v1",
        "experiment_id": "batch_3c_vol_adjusted_score",
        "strategy_id": STRATEGY_ID,
        "control_strategy_id": R39_CONTROL_ID,
        "mechanism_change": "replace only ranking with positive R39 momentum / annualized volatility_60",
        "volatility_window_days": 60,
        "volatility_annualization": 252,
        "minimum_volatility_epsilon": 1e-8,
        "weighting_rule": "unchanged fixed 1/top_n slots with vacant cash",
        "inputs": {
            "u1_manifest": {"status": "unavailable", "reason": "no verified frozen U1 input supplied"},
            "r39_control_manifest": {"status": "unavailable", "reason": "no verified paired-run control input supplied"},
        },
        "source_identity": source_identity,
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
        "# Batch 3C：波动率调整动量排名\n\n"
        f"- 策略：`{STRATEGY_ID}`\n"
        f"- 控制组：`{R39_CONTROL_ID}`\n"
        "- 唯一机制变化：正的 R39 cluster momentum 除以年化 `volatility_60`；不改变仓位权重\n"
        "- 近零波动率 fail-closed 阈值：`1e-8`\n"
        f"- manifest SHA-256：`{manifest_hash}`\n"
        "- 实验状态：`UNAVAILABLE_INPUTS`\n"
        "- promotion_allowed：`false`\n\n"
        "## 证据边界\n\n"
        "R74 只在 R39 的 cluster ranking 层使用 signal cutoff 前可见的 60 个日收益标准差，"
        "年化因子固定为 252；正的 R39 momentum 才有资格形成 momentum/volatility_60 分数。"
        "聚类、代表基金、Top-K、固定槽位、staging、incumbent carry、执行和成本语义均保持不变，"
        "不引入 inverse-volatility allocation。\n\n"
        "当前没有可验证冻结 U1、R39 paired control、三折回测和因果行情输入，故 volatility_60/"
        "momentum/score 覆盖率、排名变化、持有期、switch、换手、阻塞率、fold contribution、"
        "CAGR/Sharpe/MDD/Calmar、CVaR、worst 3M、成本后收益及 normal/2x/T+1/T+2 全部为"
        "`unavailable`，没有用零值替代或声称晋级。\n\n"
        "## 最小改动自评\n\n"
        "仅新增 R74 score-only overlay、显式注册、focused tests、Batch 3C 登记脚本和本中文报告；"
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
    parser = argparse.ArgumentParser(description="登记 Batch 3C 波动率调整动量实验")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    args = parser.parse_args()
    run(output_dir=args.output_dir, report_path=args.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
