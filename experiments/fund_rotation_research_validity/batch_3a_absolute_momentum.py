"""登记 Batch 3A 的绝对动量实验边界，不伪造缺失的 paired backtest。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

EXPERIMENT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = EXPERIMENT_ROOT.parents[1]
AGENT_ROOT = REPO_ROOT / "agent"
AGENT_SRC_ROOT = AGENT_ROOT / "src"
for _path in (AGENT_ROOT, AGENT_SRC_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from backtest.fund_rotation.strategies.ai_rotation_r72_r39_absolute_momentum.strategy import (  # noqa: E402
    AiRotationR72R39AbsoluteMomentumStrategy,
)
from src.stockpred.fund_rotation.strategy_snapshot import (  # noqa: E402
    snapshot_strategy_package,
)
DEFAULT_OUTPUT_DIR = EXPERIMENT_ROOT / "batch_3a"
DEFAULT_REPORT_PATH = EXPERIMENT_ROOT / "batch_3a_report.md"
STRATEGY_ID = "ai_rotation_r72_r39_absolute_momentum"
R39_CONTROL_ID = "ai_rotation_r39_incumbent_carry"
R126D_LOOKBACK = 126
STRESS_SCENARIOS = ("normal_cost", "2x_cost", "3x_cost", "t_plus_1", "t_plus_2")
METRICS = (
    "negative_trend_frequency",
    "forward_negative_trend_frequency",
    "cagr",
    "sharpe",
    "max_drawdown",
    "cvar",
    "worst_3m",
    "cash_ratio",
    "switch_count",
    "holding_period",
    "turnover",
    "cost_after_return",
)
COVERAGE = {
    "historical_negative_trend_frequency": "unavailable",
    "forward_outcome_coverage": "unavailable",
    "paired_control_coverage": "unavailable",
    "three_fold_coverage": "unavailable",
    "candidate_observations": "unavailable",
}
PARAMETER_NEIGHBORHOOD = {
    "status": "unavailable",
    "reason": "no verified paired-run input or pre-registered parameter sweep supplied",
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


def _source_hash(path: Path) -> str | None:
    return _sha256(path) if path.is_file() else None


def _write_immutable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(content.encode("utf-8"))
    except FileExistsError:
        if path.read_text(encoding="utf-8") != content:
            raise FileExistsError(f"refusing to overwrite immutable artifact: {path}") from None


def _json(path: Path, payload: dict[str, Any]) -> None:
    _write_immutable(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def run(*, output_dir: Path = DEFAULT_OUTPUT_DIR, report_path: Path = DEFAULT_REPORT_PATH) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    r72_path = REPO_ROOT / "agent/backtest/fund_rotation/strategies/ai_rotation_r72_r39_absolute_momentum/strategy.py"
    r39_path = REPO_ROOT / "agent/backtest/fund_rotation/strategies/ai_rotation_r39_incumbent_carry/strategy.py"
    script_path = Path(__file__).resolve()
    r72_snapshot = snapshot_strategy_package(AiRotationR72R39AbsoluteMomentumStrategy)
    source_identity = {
        "r72_strategy_path": str(r72_path),
        "r72_strategy_sha256": _source_hash(r72_path),
        "r72_dependency_snapshot_sha256": r72_snapshot.implementation_hash,
        "r72_dependency_file_hashes": dict(r72_snapshot.file_hashes),
        "r39_control_path": str(r39_path),
        "r39_control_sha256": _source_hash(r39_path),
        "experiment_script_path": str(script_path),
        "experiment_script_sha256": _source_hash(script_path),
    }
    manifest_path = output_dir / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {
            "schema_version": "fund-rotation-single-variable-experiment/v1",
            "experiment_id": "batch_3a_absolute_momentum",
            "strategy_id": STRATEGY_ID,
            "control_strategy_id": R39_CONTROL_ID,
            "mechanism_change": "R126d > 0; failed candidates go to cash",
            "lookback_days": R126D_LOOKBACK,
            "source_identity": {
                **source_identity,
            },
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
            "folds": {"status": "unavailable", "required": 3, "completed": 0},
            "coverage": dict(COVERAGE),
            "parameter_neighborhood": dict(PARAMETER_NEIGHBORHOOD),
            "fold_contribution": dict(FOLD_CONTRIBUTION),
            "blocking_evidence": dict(BLOCKING_EVIDENCE),
            "stress_scenarios": {scenario: "unavailable" for scenario in STRESS_SCENARIOS},
            "metrics": {metric: "unavailable" for metric in METRICS},
            "status": "UNAVAILABLE_INPUTS",
            "promotion_allowed": False,
            "reason": "缺少可验证冻结 U1、R39 paired control、三折回测入口和因果行情输入",
            "generated_at": GENERATED_AT,
        }
        _json(manifest_path, manifest)
    expected = {
        "schema_version": "fund-rotation-single-variable-experiment/v1",
        "experiment_id": "batch_3a_absolute_momentum",
        "strategy_id": STRATEGY_ID,
        "control_strategy_id": R39_CONTROL_ID,
        "mechanism_change": "R126d > 0; failed candidates go to cash",
        "lookback_days": R126D_LOOKBACK,
        "source_identity": {
            **source_identity,
        },
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
        "folds": {"status": "unavailable", "required": 3, "completed": 0},
        "coverage": dict(COVERAGE),
        "parameter_neighborhood": dict(PARAMETER_NEIGHBORHOOD),
        "fold_contribution": dict(FOLD_CONTRIBUTION),
        "blocking_evidence": dict(BLOCKING_EVIDENCE),
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
        "# Batch 3A：R126d 绝对动量门\n\n"
        f"- 策略：`{STRATEGY_ID}`\n"
        f"- 控制组：`{R39_CONTROL_ID}`\n"
        f"- 唯一机制变化：`R126d > 0`；失败候选只进入现金\n"
        f"- manifest SHA-256：`{manifest_hash}`\n"
        "- 实验状态：`UNAVAILABLE_INPUTS`\n"
        "- promotion_allowed：`false`\n\n"
        "## 证据边界\n\n"
        "R72 仅在 R39 已生成目标之后读取 signal cutoff 可见的 adjusted close，"
        "严格计算 126 日收益；缺失窗口、非有限值、零收益和负收益分别记录，失败目标"
        "释放为现金，不重排或重分配幸存目标。\n\n"
        "当前没有可验证冻结 U1、R39 paired control、三折回测和因果行情输入，故历史"
        "负趋势频率、前瞻负趋势频率、三折 CAGR/Sharpe/MDD、CVaR、worst 3M、现金占比、"
        "switch、持有期、换手、成本后收益以及 normal/2x/3x 与 T+1/T+2 场景全部为"
        "`unavailable`，没有用零值替代或声称晋级。\n\n"
        "覆盖率（历史负趋势、前瞻结果、paired control、三折和候选观测）全部为"
        "`unavailable`，不将缺失数据解释为零覆盖或零风险。\n\n"
        "参数邻域、fold contribution 和阻断证据字段均明确记录为 `unavailable`；"
        "因此不执行参数搜索、不计算 fold 晋级贡献，也不允许晋级。\n\n"
        "## 最小改动自评\n\n"
        "仅新增 R72 overlay、显式注册、focused tests、Batch 3A 登记脚本和本中文报告；"
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
    parser = argparse.ArgumentParser(description="登记 Batch 3A 绝对动量实验")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    args = parser.parse_args()
    run(output_dir=args.output_dir, report_path=args.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
