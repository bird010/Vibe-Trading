"""Continue the documented research chain when optional evidence is absent."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from typing import Any
from pathlib import Path


EXPERIMENT_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = EXPERIMENT_ROOT / "research_chain_20260830"
DEFAULT_REPORT_PATH = EXPERIMENT_ROOT / "research_chain_20260830_report.md"
DEFAULT_U0_SOURCE = (
    EXPERIMENT_ROOT.parents[1]
    / "agent/runs/fund_rotation/1a8eb8560998/data_snapshot.json"
)


def stage_record(stage: str, result: dict[str, Any], *, reason: str) -> dict[str, Any]:
    """Normalize an entry-point result without turning missing evidence into failure."""

    status = str(result.get("status", "unavailable"))
    core_failure = status.lower() in {
        "invalid",
        "pit_invalid",
        "failed",
        "failed_core",
        "error",
    }
    unavailable = status.lower() in {"unavailable", "unavailable_inputs", "started"}
    return {
        "stage": stage,
        "execution_status": (
            "FAILED_CORE"
            if core_failure
            else "COMPLETED_RESEARCH_ONLY"
            if unavailable
            else "COMPLETED"
        ),
        "result_status": "INCONCLUSIVE" if unavailable else status,
        "recorded_to_completion": True,
        "research_execution_allowed": not core_failure,
        "promotion_allowed": False,
        "deployment_allowed": False,
        "reason": reason,
    }


def u1_basis_from_snapshot(path: Path) -> dict[str, Any]:
    """Record the configured research U1=U0 basis without inventing identity."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    codes = payload.get("universe_codes") if isinstance(payload, dict) else None
    if not isinstance(codes, list) or not all(isinstance(code, str) and code for code in codes):
        raise ValueError(f"research snapshot must contain non-empty universe_codes: {path}")
    normalized = sorted(set(codes))
    codes_hash = hashlib.sha256(
        json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "source": str(path),
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "derivation": "U1_FROM_U0",
        "u0_count": len(normalized),
        "u1_count": len(normalized),
        "u1_equals_u0": True,
        "eligible_codes_sha256": codes_hash,
        "identity_evidence_status": "UNAVAILABLE",
        "pit_evidence_status": "UNAVAILABLE",
        "quality_status": "RESEARCH_ONLY_UNVERIFIED_UNIVERSE",
        "research_execution_allowed": True,
        "promotion_allowed": False,
        "deployment_allowed": False,
    }


def build_chain_manifest(
    u1_basis: dict[str, Any], stages: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build the non-promoting chain summary from already completed stages."""

    if not stages or not all(stage.get("recorded_to_completion") is True for stage in stages):
        raise ValueError("every research-chain stage must be recorded_to_completion")
    core_failure = any(stage.get("execution_status") == "FAILED_CORE" for stage in stages)
    inconclusive = any(stage.get("result_status") == "INCONCLUSIVE" for stage in stages)
    return {
        "schema_version": "fund-rotation-research-chain/v1",
        "chain_status": "FAILED_CORE" if core_failure else "INCONCLUSIVE" if inconclusive else "SUCCEEDED",
        "recorded_to_completion": True,
        "research_execution_allowed": not core_failure,
        "promotion_allowed": False,
        "deployment_allowed": False,
        "u1_basis": dict(u1_basis),
        "stages": [dict(stage) for stage in stages],
    }


def _append_stage(stages: list[dict[str, Any]], record: dict[str, Any]) -> None:
    stages.append(record)
    if record["execution_status"] == "FAILED_CORE":
        raise RuntimeError(f"{record['stage']} failed closed: {record['reason']}")


def _write_immutable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            raise FileExistsError(f"refusing to overwrite immutable artifact: {path}")
        return
    path.write_text(content, encoding="utf-8", newline="\n")


def run_chain(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    report_path: Path = DEFAULT_REPORT_PATH,
    u0_source: Path = DEFAULT_U0_SOURCE,
) -> dict[str, Any]:
    """Run Batch 1-6 and Shadow A in order, recording unavailable evidence."""

    try:
        from . import batch_2_capacity_repair as batch_2
        from . import batch_3a_absolute_momentum as batch_3a
        from . import batch_3b_multi_horizon as batch_3b
        from . import batch_3c_vol_adjusted as batch_3c
        from . import batch_4_ablation as batch_4
        from . import batch_5_risk_layers as batch_5
        from . import batch_6_survivor_combo as batch_6
        from . import pit_identity
        from . import start_r40_shadow
    except ImportError:
        repo_root = EXPERIMENT_ROOT.parents[1]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from experiments.fund_rotation_research_validity import batch_2_capacity_repair as batch_2
        from experiments.fund_rotation_research_validity import batch_3a_absolute_momentum as batch_3a
        from experiments.fund_rotation_research_validity import batch_3b_multi_horizon as batch_3b
        from experiments.fund_rotation_research_validity import batch_3c_vol_adjusted as batch_3c
        from experiments.fund_rotation_research_validity import batch_4_ablation as batch_4
        from experiments.fund_rotation_research_validity import batch_5_risk_layers as batch_5
        from experiments.fund_rotation_research_validity import batch_6_survivor_combo as batch_6
        from experiments.fund_rotation_research_validity import pit_identity
        from experiments.fund_rotation_research_validity import start_r40_shadow

    output_dir.mkdir(parents=True, exist_ok=True)
    basis = u1_basis_from_snapshot(u0_source)
    stages: list[dict[str, Any]] = []

    batch_1_result = pit_identity.generate(
        master_path=None,
        dates_path=None,
        tradability_path=None,
        output_dir=output_dir / "batch_1",
        report_path=output_dir / "batch_1_report.md",
        snapshot_version=1,
        cutoff_time="15:00:00",
    )
    _append_stage(stages,
        stage_record(
            "batch_1",
            {"status": batch_1_result["snapshot_status"]["status"], "promotion_allowed": False},
            reason="PIT master、调仓日期和决策日可交易性输入缺失；按 U1=U0 研究基线继续记录",
        )
    )

    batch_2_result = batch_2.generate(
        frozen_u1_manifest=None,
        historical_counterfactual=None,
        paired_backtest=None,
        output_dir=output_dir / "batch_2",
        report_path=output_dir / "batch_2_report.md",
    )
    _append_stage(stages,
        stage_record(
            "batch_2",
            {"status": batch_2_result["experiment_status"], "promotion_allowed": False},
            reason="冻结 U1、历史反事实和 paired backtest 缺失；容量实验边界已记录并继续后续阶段",
        )
    )

    for name, module, reason in (
        ("batch_3a", batch_3a, "冻结 U1、R39 paired control 和三折因果行情缺失"),
        ("batch_3b", batch_3b, "冻结 U1、R39 paired control 和三折因果行情缺失"),
        ("batch_3c", batch_3c, "冻结 U1、R39 paired control 和三折因果行情缺失"),
        ("batch_4", batch_4, "冻结 U1、三臂 paired input 和三折行情缺失"),
        ("batch_5", batch_5, "冻结 U1、四臂 paired input、三折和防御历史缺失"),
    ):
        result = module.run(
            output_dir=output_dir / name,
            report_path=output_dir / f"{name}_report.md",
        )
        _append_stage(stages, stage_record(name, result, reason=reason))

    batch_6_result = batch_6.run(
        output_dir=output_dir / "batch_6",
        report_path=output_dir / "batch_6_report.md",
    )
    _append_stage(stages,
        stage_record(
            "batch_6",
            batch_6_result,
            reason="没有 promotion survivor；组合指标保持 unavailable，但组合入口已执行并记录",
        )
    )

    shadow_result = start_r40_shadow.start_shadow_a(
        output_dir=output_dir / "shadow_a",
        frozen_u1_manifest=None,
        r39_control_manifest=None,
        report_path=output_dir / "shadow_a_report.md",
    )
    _append_stage(stages,
        stage_record(
            "shadow_a",
            {"status": shadow_result["shadow_status"], "promotion_allowed": False},
            reason="Shadow 已启动 bootstrap；真实 forward/行情/执行输入缺失，资格保持 inconclusive",
        )
    )

    manifest = build_chain_manifest(basis, stages)
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _write_immutable(output_dir / "research_chain_manifest.json", manifest_text)
    manifest_hash = hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()
    report = (
        "# Fund Rotation 研究链路继续执行报告\n\n"
        "本次按 Batch 1→Batch 2→Batch 3A/3B/3C→Batch 4→Batch 5→Batch 6→Shadow A 顺序执行。"
        "缺失可选或资格证据不会终止研究链路；只将对应结论记为 `INCONCLUSIVE`，不填充虚假指标。\n\n"
        f"- 链路状态：`{manifest['chain_status']}`\n"
        f"- U1 基线：`{basis['derivation']}`，U0/U1 结果集相同：`{basis['u1_equals_u0']}`\n"
        f"- U0 来源：`{u0_source}`\n"
        f"- manifest SHA-256：`{manifest_hash}`\n"
        "- research_execution_allowed：`true`\n"
        "- promotion_allowed：`false`\n"
        "- deployment_allowed：`false`\n\n"
        "## 阶段结论\n\n"
        + "\n".join(
            f"- {stage['stage']}：执行状态 `{stage['execution_status']}`，结论 `{stage['result_status']}`，"
            f"原因：{stage['reason']}"
            for stage in stages
        )
        + "\n\n## 证据边界\n\n"
        "本报告记录的是研究链路已走完，不等于 PIT/U1 证据已验证。缺失的 identity、PIT master、"
        "paired backtest、三折历史、真实 forward 和执行行情均保持 unavailable；没有将其转换为零收益、"
        "零风险或资格通过。核心行情、快照完整性和执行账本错误仍按 fail-closed 处理。\n"
    )
    _write_immutable(report_path, report)
    return {
        "manifest": str(output_dir / "research_chain_manifest.json"),
        "manifest_sha256": manifest_hash,
        "report": str(report_path),
        "chain_status": manifest["chain_status"],
        "stages": stages,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="继续执行 fund-rotation 研究链路")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--u0-source", type=Path, default=DEFAULT_U0_SOURCE)
    args = parser.parse_args()
    run_chain(
        output_dir=args.output_dir,
        report_path=args.report_path,
        u0_source=args.u0_source,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
