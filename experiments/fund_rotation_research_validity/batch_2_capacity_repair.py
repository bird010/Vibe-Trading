"""生成 Batch 2 容量感知代表回退的可审计实验清单。

本脚本只登记冻结 U1、历史反事实和正式 paired backtest 的证据边界；
没有这些输入时明确输出 ``unavailable``，不把缺失指标填成零，也不伪造收益结论。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


EXPERIMENT_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = EXPERIMENT_ROOT / "batch_2"
DEFAULT_REPORT_PATH = EXPERIMENT_ROOT / "batch_2_report.md"
STRATEGY_ID = "ai_rotation_r71_r39_capacity_aware_representative"
STRESS_LEVELS = ("1x", "2x", "3x")
PRIMARY_METRICS = (
    "blocked_attempt_ratio",
    "parent_order_fill_ratio",
    "capacity_zero_count",
    "unfilled_opportunity_cost",
    "post_fill_turnover",
    "target_deviation_duration",
    "cagr",
    "sharpe",
    "max_drawdown",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_immutable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(content.encode("utf-8"))
    except FileExistsError:
        if path.read_text(encoding="utf-8") != content:
            raise FileExistsError(
                f"refusing to overwrite immutable artifact: {path}"
            ) from None


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "reason": reason,
        "metrics": {name: "unavailable" for name in PRIMARY_METRICS},
    }


def _input_record(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"path": None, "status": "missing"}
    if not path.exists() or not path.is_file():
        return {"path": str(path), "status": "missing"}
    return {"path": str(path), "status": "present", "sha256": _sha256(path)}


def _json_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def generate(
    *,
    frozen_u1_manifest: Path | None,
    historical_counterfactual: Path | None,
    paired_backtest: Path | None,
    output_dir: Path,
    report_path: Path,
) -> dict[str, Any]:
    inputs = {
        "frozen_u1_manifest": _input_record(frozen_u1_manifest),
        "historical_counterfactual": _input_record(historical_counterfactual),
        "paired_backtest": _input_record(paired_backtest),
    }
    missing = [name for name, record in inputs.items() if record["status"] != "present"]
    reason = (
        "缺少冻结 U1 manifest、历史反事实诊断或正式 paired backtest 输入："
        + ", ".join(missing)
        if missing
        else "输入存在但尚未确认冻结 U1 与 paired backtest 均完成"
    )

    u1_status = "unavailable"
    if frozen_u1_manifest is not None and frozen_u1_manifest.is_file():
        payload = _json_object(frozen_u1_manifest)
        snapshot_status = payload.get("snapshot_status") if payload else None
        if isinstance(snapshot_status, dict) and snapshot_status.get("status") == "available":
            u1_status = "available"

    paired_status = "unavailable"
    if paired_backtest is not None and paired_backtest.is_file():
        payload = _json_object(paired_backtest)
        if payload and payload.get("status") in {"completed", "available"}:
            paired_status = "available"

    available = not missing and u1_status == "available" and paired_status == "available"
    if not available and not missing:
        reason = (
            "输入文件存在，但未同时声明可用的冻结 U1 snapshot_status 和正式 paired backtest；"
            "保持 unavailable"
        )

    experiment_status = "ready_for_execution" if available else "unavailable"
    evidence_status = "ready_for_execution" if available else "unavailable"
    evidence_metrics = (
        {name: "pending_external_execution" for name in PRIMARY_METRICS}
        if available
        else {name: "unavailable" for name in PRIMARY_METRICS}
    )
    evidence_reason = (
        "输入文件已通过最小状态检查；正式回测仍须由受控执行器产生指标"
        if available
        else reason
    )

    def evidence_block() -> dict[str, Any]:
        return {
            "status": evidence_status,
            "reason": evidence_reason,
            "metrics": dict(evidence_metrics),
        }

    stress = {level: evidence_block() for level in STRESS_LEVELS}
    manifest: dict[str, Any] = {
        "schema_version": "fund_rotation_capacity_repair_v1",
        "task": "Task 3",
        "strategy_id": STRATEGY_ID,
        "experiment_status": experiment_status,
        "promotion_allowed": False,
        "inputs": inputs,
        "frozen_u1_status": u1_status,
        "historical_counterfactual": evidence_block(),
        "formal_u1_paired_backtest": evidence_block(),
        "stress_tests": stress,
        "evidence_boundary": (
            "capacity = ADV × max_participation × execution_horizon；只使用决策 cutoff 可见信息，"
            "未知/非法容量 fail-closed，候选按同簇同身份和确定性 tie-break 处理。"
        ),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    manifest_text = json.dumps(
        manifest, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
    ) + "\n"
    _write_immutable(manifest_path, manifest_text)
    manifest_hash = _sha256(manifest_path)
    report = (
        "# Batch 2：容量感知代表回退报告\n\n"
        f"- 策略 ID：`{STRATEGY_ID}`\n"
        f"- 实验状态：`{experiment_status}`\n"
        f"- U1 状态：`{u1_status}`\n"
        f"- manifest：`{manifest_path}`\n"
        f"- manifest SHA-256：`{manifest_hash}`\n\n"
        "## 规则与审计边界\n\n"
        "R71 继承 R39；只有决策 cutoff 可见、同簇同身份且容量证据明确足够时才解锁代表，"
        "否则按确定性候选顺序回退，全部不可用时进入现金。未知或未来成交量不被当作可用容量。\n\n"
        "## 主指标\n\n"
        "blocked attempt ratio、parent order fill ratio、capacity-zero 次数、未成交机会成本、"
        "成交后换手率和目标偏离持续天数为主指标；CAGR、Sharpe、MDD 仅作护栏。"
        "1×/2×/3× 成本与容量压力测试必须使用同一冻结 U1 和账户语义。\n\n"
        "## 当前证据\n\n"
        f"`{reason}`。本次没有把缺失证据填成零，也没有生成虚假的收益、成本或晋级结论；"
        "正式 U1 paired backtest 和历史反事实完成前，`promotion_allowed` 保持 `false`。\n"
    )
    _write_immutable(report_path, report)
    manifest["manifest_sha256"] = manifest_hash
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 Batch 2 容量感知代表回退实验清单")
    parser.add_argument("--frozen-u1-manifest", type=Path)
    parser.add_argument("--historical-counterfactual", type=Path)
    parser.add_argument("--paired-backtest", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    args = parser.parse_args()
    generate(
        frozen_u1_manifest=args.frozen_u1_manifest,
        historical_counterfactual=args.historical_counterfactual,
        paired_backtest=args.paired_backtest,
        output_dir=args.output_dir,
        report_path=args.report_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
