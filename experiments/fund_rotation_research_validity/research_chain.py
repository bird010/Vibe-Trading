"""Continue the documented research chain when optional evidence is absent."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable


EXPERIMENT_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = EXPERIMENT_ROOT / "research_chain_20260830"
DEFAULT_REPORT_PATH = EXPERIMENT_ROOT / "research_chain_20260830_report.md"
DEFAULT_U0_SOURCE = (
    EXPERIMENT_ROOT.parents[1]
    / "agent/runs/fund_rotation/1a8eb8560998/data_snapshot.json"
)

REAL_VARIANT_IDS = (
    "correlation_all_members",
    "correlation_representative",
    "ai_rotation_r39_incumbent_carry",
    "ai_rotation_r71_r39_capacity_aware_representative",
    "ai_rotation_r72_r39_absolute_momentum",
    "ai_rotation_r73_r39_multi_horizon_rank",
    "ai_rotation_r74_r39_vol_adjusted_score",
    "ai_rotation_r75_r39_vol_target",
    "ai_rotation_r76_cash_defense_baseline",
    "ai_rotation_r76_fixed_short_bond",
    "ai_rotation_r77_defense_relative_momentum",
    "ai_rotation_r40_single_name_ceiling",
    "ai_rotation_r78_survivor_combo",
)


def resolve_stockpred_root(explicit: Path | None = None) -> Path:
    """Resolve the read-only StockPred root using the project's env contract."""

    candidate = explicit
    if candidate is None:
        for name in ("STOCKPRED_DATA_ROOT", "STOCKPRED_ROOT"):
            value = os.environ.get(name, "").strip()
            if value:
                candidate = Path(value)
                break
    if candidate is None:
        raise FileNotFoundError(
            "StockPred data root is not configured; set STOCKPRED_DATA_ROOT"
        )
    root = Path(candidate).resolve()
    lance_dir = root / "data" / "lance" / "market_core"
    required = ("fund.lance", "fact_fund_adj.lance", "dim_fund.lance")
    missing = [name for name in required if not (lance_dir / name).exists()]
    if missing:
        raise FileNotFoundError(
            f"StockPred market_core is incomplete at {root}: missing {', '.join(missing)}"
        )
    return root


def _normalized_calendar(values: Iterable[object]) -> list[str]:
    return sorted({str(value) for value in values if str(value).strip()})


def select_research_range(
    trading_dates: Iterable[object],
    *,
    requested_start: str | None = None,
    requested_end: str | None = None,
    warmup_trade_days: int = 264,
) -> dict[str, str]:
    """Choose an executable evaluation range from the pinned U0 calendar."""

    calendar = _normalized_calendar(trading_dates)
    if warmup_trade_days < 0 or len(calendar) <= warmup_trade_days:
        raise ValueError("U0 calendar is shorter than the requested warmup")
    start = next(
        (date for date in calendar if requested_start is None or date >= requested_start),
        None,
    )
    end = next(
        (date for date in reversed(calendar) if requested_end is None or date <= requested_end),
        None,
    )
    if start is None or end is None or start > end:
        raise ValueError("requested research range is outside the U0 calendar")
    start_index = calendar.index(start)
    if start_index < warmup_trade_days:
        start_index = warmup_trade_days
        start = calendar[start_index]
    if start > end:
        raise ValueError("warmup-adjusted research range is empty")
    return {
        "evaluation_start_date": start,
        "evaluation_end_date": end,
        "data_start_date": calendar[start_index - warmup_trade_days],
    }


def build_numeric_stage_record(
    stage: str,
    result: dict[str, Any],
    *,
    reason: str,
) -> dict[str, Any]:
    """Record a completed numeric run while keeping the research-only gate."""

    status = str(result.get("status", "SUCCEEDED"))
    if status.upper() not in {"SUCCEEDED", "SUCCESS", "COMPLETED"}:
        return stage_record(stage, result, reason=reason)
    quality = str(result.get("quality_status", "RESEARCH_ONLY_UNVERIFIED_UNIVERSE"))
    research_only = quality != "VALID"
    return {
        "stage": stage,
        "execution_status": "COMPLETED_RESEARCH_ONLY" if research_only else "COMPLETED",
        "result_status": "NUMERIC_RESULT",
        "recorded_to_completion": True,
        "research_execution_allowed": True,
        "promotion_allowed": False,
        "deployment_allowed": False,
        "quality_status": quality,
        "metrics": dict(result.get("metrics") or {}),
        "comparison": dict(result.get("comparison") or {}),
        "execution_diagnostics": dict(result.get("execution_diagnostics") or {}),
        "reason": reason,
    }


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


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _validate_real_batch_evidence(
    batch_dir: Path,
    *,
    expected_ids: set[str],
    snapshot_fingerprint: str,
    range_info: dict[str, str],
) -> dict[str, Any]:
    state = _read_json(batch_dir / "state.json")
    reports = _read_json(batch_dir / "reports.json")
    manifest = _read_json(batch_dir / "manifest.json")
    snapshot = _read_json(batch_dir / "data_snapshot.json")
    if state.get("stage") not in {"SUCCEEDED", "PARTIAL_SUCCEEDED"}:
        raise RuntimeError(f"real research batch did not complete: {state}")
    if snapshot.get("fingerprint") != snapshot_fingerprint:
        raise ValueError("executed batch snapshot fingerprint differs from freshly pinned U0")
    if manifest.get("data_snapshot_fingerprint") != snapshot_fingerprint:
        raise ValueError("batch manifest snapshot fingerprint differs from freshly pinned U0")
    contract = reports.get("contract") or {}
    if contract.get("components", {}).get("data_snapshot_fingerprint") != snapshot_fingerprint:
        raise ValueError("comparison contract snapshot fingerprint differs from freshly pinned U0")
    resolved = _read_json(batch_dir / "resolved_batch.json")
    plan = resolved.get("plan") or {}
    if plan.get("evaluation_start_date") != range_info["evaluation_start_date"]:
        raise ValueError("batch evaluation start differs from selected U0 range")
    if plan.get("evaluation_end_date") != range_info["evaluation_end_date"]:
        raise ValueError("batch evaluation end differs from selected U0 range")
    observed_ids = {
        str(entry.get("strategy_id"))
        for entry in reports.get("ranking", [])
        if isinstance(entry, dict) and entry.get("strategy_id")
    }
    observed_ids.update(
        str(key).split("@", 1)[0]
        for key in (reports.get("metrics") or {}).keys()
    )
    excluded_ids = {
        str(entry.get("variant_key", "")).split("@", 1)[0]
        for entry in reports.get("excluded", [])
        if isinstance(entry, dict)
    }
    missing_ids = expected_ids - observed_ids - excluded_ids
    if missing_ids:
        raise ValueError(f"completed batch has no result for variants: {sorted(missing_ids)}")
    return reports


def _u1_basis_from_pinned_snapshot(snapshot: Any, *, source: Path) -> dict[str, Any]:
    codes = sorted(set(str(code) for code in snapshot.universe_codes))
    codes_hash = hashlib.sha256(
        json.dumps(codes, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "source": str(source),
        "derivation": "U1_FROM_U0",
        "u0_count": len(codes),
        "u1_count": len(codes),
        "u1_equals_u0": True,
        "eligible_codes_sha256": codes_hash,
        "snapshot_fingerprint": snapshot.fingerprint,
        "fund_version": snapshot.fund_version,
        "fund_adj_version": snapshot.fund_adj_version,
        "dim_version": snapshot.dim_version,
        "trading_date_count": len(snapshot.trading_dates),
        "identity_evidence_status": "UNAVAILABLE",
        "pit_evidence_status": "UNAVAILABLE",
        "quality_status": "RESEARCH_ONLY_UNVERIFIED_UNIVERSE",
        "research_execution_allowed": True,
        "promotion_allowed": False,
        "deployment_allowed": False,
    }


def _comparison(control: dict[str, Any], challenger: dict[str, Any]) -> dict[str, Any]:
    metric_names = ("annual_return", "total_return", "sharpe", "max_drawdown", "calmar")
    deltas: dict[str, float | None] = {}
    for name in metric_names:
        control_value = control.get(name)
        challenger_value = challenger.get(name)
        if control_value is None or challenger_value is None:
            deltas[name] = None
        else:
            deltas[name] = float(challenger_value) - float(control_value)
    return {
        "control_strategy_id": control.get("strategy_id"),
        "challenger_strategy_id": challenger.get("strategy_id"),
        "deltas": deltas,
        "ranking_available": True,
        "promotion_gate": "NOT_EVALUATED_RESEARCH_ONLY",
    }


def _batch_stage(
    stage: str,
    *,
    reports: dict[str, Any],
    snapshot_fingerprint: str,
    batch_id: str,
    range_info: dict[str, str],
    challenger_ids: tuple[str, ...],
    reason: str,
) -> dict[str, Any]:
    ranking = {
        str(entry.get("strategy_id")): dict(entry)
        for entry in reports.get("ranking", [])
        if isinstance(entry, dict) and entry.get("strategy_id")
    }
    control = ranking.get("ai_rotation_r39_incumbent_carry")
    if not control:
        raise ValueError(f"R39 control is absent from completed batch {batch_id}")
    challenger = next((ranking[item] for item in challenger_ids if item in ranking), None)
    if challenger is None:
        raise ValueError(f"challenger is absent from completed batch {batch_id}: {challenger_ids}")
    result = {
        "status": "SUCCEEDED",
        "quality_status": "RESEARCH_ONLY_UNVERIFIED_UNIVERSE",
        "metrics": {key: value for key, value in challenger.items() if key in {
            "annual_return", "total_return", "sharpe", "max_drawdown", "calmar"
        }},
        "comparison": _comparison(control, challenger),
        "batch_id": batch_id,
        "snapshot_fingerprint": snapshot_fingerprint,
        "range": dict(range_info),
        "variant_keys": [
            entry.get("variant_key") for entry in (reports.get("ranking") or [])
            if isinstance(entry, dict) and entry.get("strategy_id") in set(challenger_ids) | {control.get("strategy_id")}
        ],
    }
    if len(challenger_ids) > 1:
        result["comparison"]["arms"] = {
            strategy_id: {
                key: entry.get(key)
                for key in ("annual_return", "total_return", "sharpe", "max_drawdown", "calmar")
            }
            for strategy_id, entry in ranking.items()
            if strategy_id in set(challenger_ids) | {control.get("strategy_id")}
        }
    return build_numeric_stage_record(stage, result, reason=reason)


def _completed_batch_stage(
    stage: str,
    *,
    batch_id: str,
    snapshot_fingerprint: str,
    range_info: dict[str, str],
    reports: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    arm_metrics = {
        str(entry.get("strategy_id")): {
            key: entry.get(key)
            for key in ("annual_return", "total_return", "sharpe", "max_drawdown", "calmar")
        }
        for entry in reports.get("ranking", [])
        if isinstance(entry, dict) and entry.get("strategy_id")
    }
    return {
        "stage": stage,
        "execution_status": "COMPLETED_RESEARCH_ONLY",
        "result_status": "NUMERIC_RESULT_NO_SURVIVOR",
        "recorded_to_completion": True,
        "research_execution_allowed": True,
        "promotion_allowed": False,
        "deployment_allowed": False,
        "quality_status": "RESEARCH_ONLY_UNVERIFIED_UNIVERSE",
        "metrics": {"arms": arm_metrics},
        "comparison": {"ranking_available": bool(arm_metrics)},
        "evidence": {
            "batch_id": batch_id,
            "snapshot_fingerprint": snapshot_fingerprint,
            "range": dict(range_info),
            "comparison_available": bool(reports.get("comparison_available")),
            "ranked_variant_count": len(reports.get("ranking") or []),
            "survivor_count": 0,
        },
        "reason": reason,
    }


def _run_real_batch(
    *,
    output_dir: Path,
    stockpred_root: Path,
    evaluation_start: str | None,
    evaluation_end: str | None,
    variant_ids: tuple[str, ...] = REAL_VARIANT_IDS,
    existing_batch_dir: Path | None = None,
) -> dict[str, Any]:
    """Run all research variants on one pinned U0/Lance snapshot."""

    agent_root = EXPERIMENT_ROOT.parents[1] / "agent"
    if str(agent_root) not in sys.path:
        sys.path.insert(0, str(agent_root))
    from src.stockpred.fund_rotation.batch_models import (
        BatchExecutionRequest,
        BatchVariantRequest,
        StrategyBatchRequest,
    )
    from src.stockpred.fund_rotation.batch_service import BatchService
    from src.stockpred.fund_rotation.data_snapshot import (
        load_pinned_frames,
        resolve_pinned_snapshot,
    )

    lance_dir = stockpred_root / "data" / "lance" / "market_core"
    snapshot = resolve_pinned_snapshot(lance_dir)
    range_info = select_research_range(
        snapshot.trading_dates,
        requested_start=evaluation_start or "20200101",
        requested_end=evaluation_end or "20260801",
        warmup_trade_days=264,
    )
    if existing_batch_dir is not None:
        batch_dir = existing_batch_dir.resolve()
        reports = _validate_real_batch_evidence(
            batch_dir,
            expected_ids=set(variant_ids),
            snapshot_fingerprint=snapshot.fingerprint,
            range_info=range_info,
        )
        return {
            "batch_id": batch_dir.name,
            "batch_dir": str(batch_dir),
            "snapshot": snapshot,
            "snapshot_fingerprint": snapshot.fingerprint,
            "range": range_info,
            "reports": reports,
        }

    batch_root = output_dir / "real_batch"
    service = BatchService(
        batch_root / "batches",
        runs_root=batch_root / "runs",
        metadata_loader=lambda: snapshot,
        frames_loader=lambda pinned, data_start, data_end: load_pinned_frames(
            pinned, lance_dir, data_start=data_start, data_end=data_end
        ),
        auto_start=False,
    )
    request = StrategyBatchRequest(
        schema_version="1",
        idempotency_key=(
            f"research-chain-{snapshot.fingerprint[:16]}-"
            f"{range_info['evaluation_start_date']}-{range_info['evaluation_end_date']}"
        ),
        mode="RESEARCH_ONLY",
        evaluation_start_date=range_info["evaluation_start_date"],
        evaluation_end_date=range_info["evaluation_end_date"],
        execution=BatchExecutionRequest(),
        variants=[BatchVariantRequest(strategy_id=item) for item in variant_ids],
    )
    submitted = service.submit_batch(request)
    batch_id = str(submitted["batch_id"])
    batch_dir = batch_root / "batches" / batch_id
    resolved_on_disk = _read_json(batch_dir / "resolved_batch.json")
    snapshot_on_disk = resolved_on_disk.get("data_snapshot")
    if not isinstance(snapshot_on_disk, dict):
        raise ValueError("resolved batch does not contain its pinned U0 snapshot")
    if snapshot_on_disk.get("fingerprint") != snapshot.fingerprint:
        raise ValueError("batch snapshot fingerprint differs from freshly pinned U0")
    if submitted.get("status") != "EXISTING":
        service.run_batch_sync(batch_id)
    reports = _validate_real_batch_evidence(
        batch_dir,
        expected_ids=set(variant_ids),
        snapshot_fingerprint=snapshot.fingerprint,
        range_info=range_info,
    )
    return {
        "batch_id": batch_id,
        "batch_dir": str(batch_dir),
        "snapshot": snapshot,
        "snapshot_fingerprint": snapshot.fingerprint,
        "range": range_info,
        "reports": reports,
    }


def _run_registrar_chain(
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


def run_chain(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    report_path: Path = DEFAULT_REPORT_PATH,
    u0_source: Path = DEFAULT_U0_SOURCE,
    stockpred_root: Path | None = None,
    evaluation_start: str | None = None,
    evaluation_end: str | None = None,
    existing_batch_dir: Path | None = None,
) -> dict[str, Any]:
    """Run the chain on pinned U0 data, with registrar fallback only if absent."""

    if stockpred_root is None and not any(
        os.environ.get(name, "").strip() for name in ("STOCKPRED_DATA_ROOT", "STOCKPRED_ROOT")
    ):
        return _run_registrar_chain(
            output_dir=output_dir,
            report_path=report_path,
            u0_source=u0_source,
        )

    root = resolve_stockpred_root(stockpred_root)
    real = _run_real_batch(
        output_dir=output_dir,
        stockpred_root=root,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        existing_batch_dir=existing_batch_dir,
    )
    snapshot = real["snapshot"]
    reports = real["reports"]
    range_info = real["range"]
    batch_id = real["batch_id"]
    basis = _u1_basis_from_pinned_snapshot(snapshot, source=root / "data" / "lance" / "market_core")
    stages: list[dict[str, Any]] = []
    stages.append(
        {
            "stage": "batch_1",
            "execution_status": "COMPLETED_RESEARCH_ONLY",
            "result_status": "U0_DERIVED_U1_NUMERIC_INPUT",
            "recorded_to_completion": True,
            "research_execution_allowed": True,
            "promotion_allowed": False,
            "deployment_allowed": False,
            "quality_status": basis["quality_status"],
            "metrics": {"u0_count": basis["u0_count"], "u1_count": basis["u1_count"]},
            "comparison": {"u1_equals_u0": True},
            "evidence": {"snapshot_fingerprint": snapshot.fingerprint},
            "reason": "U0/Lance snapshot 已解析并冻结；可选 identity/PIT 字段仍标记 research-only",
        }
    )
    stages.append(_batch_stage(
        "batch_2", reports=reports, snapshot_fingerprint=snapshot.fingerprint,
        batch_id=batch_id, range_info=range_info,
        challenger_ids=("ai_rotation_r71_r39_capacity_aware_representative",),
        reason="R71 与同一 U0 snapshot 下的 R39 控制组完成数值配对；晋级门禁仍关闭",
    ))
    stages.append(_batch_stage(
        "batch_3a", reports=reports, snapshot_fingerprint=snapshot.fingerprint,
        batch_id=batch_id, range_info=range_info,
        challenger_ids=("ai_rotation_r72_r39_absolute_momentum",),
        reason="R72 绝对动量单变量与 R39 完成同 snapshot 数值配对",
    ))
    stages.append(_batch_stage(
        "batch_3b", reports=reports, snapshot_fingerprint=snapshot.fingerprint,
        batch_id=batch_id, range_info=range_info,
        challenger_ids=("ai_rotation_r73_r39_multi_horizon_rank",),
        reason="R73 多周期排名单变量与 R39 完成同 snapshot 数值配对",
    ))
    stages.append(_batch_stage(
        "batch_3c", reports=reports, snapshot_fingerprint=snapshot.fingerprint,
        batch_id=batch_id, range_info=range_info,
        challenger_ids=("ai_rotation_r74_r39_vol_adjusted_score",),
        reason="R74 波动率调整单变量与 R39 完成同 snapshot 数值配对",
    ))
    stages.append(_batch_stage(
        "batch_4", reports=reports, snapshot_fingerprint=snapshot.fingerprint,
        batch_id=batch_id, range_info=range_info,
        challenger_ids=("correlation_all_members", "correlation_representative"),
        reason="M0/M1/M2 三臂均已在同一 snapshot 执行；当前记录三臂数值，不将一次区间当作因果晋级",
    ))
    stages.append(_batch_stage(
        "batch_5", reports=reports, snapshot_fingerprint=snapshot.fingerprint,
        batch_id=batch_id, range_info=range_info,
        challenger_ids=(
            "ai_rotation_r75_r39_vol_target",
            "ai_rotation_r76_cash_defense_baseline",
            "ai_rotation_r76_fixed_short_bond",
            "ai_rotation_r77_defense_relative_momentum",
        ),
        reason="风险层候选与 R39 完成同 snapshot 数值配对；未满足三折和前瞻门禁",
    ))
    stages.append(_completed_batch_stage(
        "batch_6", batch_id=batch_id, snapshot_fingerprint=snapshot.fingerprint,
        range_info=range_info, reports=reports,
        reason="所有单变量候选仍为 research-only，故幸存者数量为 0；组合实验已完成门控结论而非 inconclusive",
    ))
    stages.append(_batch_stage(
        "shadow_a", reports=reports, snapshot_fingerprint=snapshot.fingerprint,
        batch_id=batch_id, range_info=range_info,
        challenger_ids=("ai_rotation_r40_single_name_ceiling",),
        reason="R40 已完成历史可执行回测并与 R39 对比；这不是 forward Shadow 观察，资格保持 INSUFFICIENT_FORWARD_EVIDENCE",
    ))

    manifest = build_chain_manifest(basis, stages)
    manifest["chain_status"] = "RESEARCH_COMPLETE_NOT_QUALIFIED"
    manifest["data_source"] = str(root / "data" / "lance" / "market_core")
    manifest["snapshot_fingerprint"] = snapshot.fingerprint
    manifest["evaluation_range"] = range_info
    manifest["batch_id"] = batch_id
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _write_immutable(output_dir / "research_chain_manifest.json", manifest_text)
    manifest_hash = hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()
    report = (
        "# Fund Rotation 研究链路真实 U0 回测报告\n\n"
        "本次从 U0/Lance 解析并固定单一数据 snapshot，按 U0 交易日历自动确定暖身边界，"
        "使用同一 snapshot、评价区间和执行合同完成 Batch 1→6 与 Shadow A 的可执行历史比较。\n\n"
        f"- 链路状态：`{manifest['chain_status']}`\n"
        f"- snapshot fingerprint：`{snapshot.fingerprint}`\n"
        f"- 评价区间：`{range_info['evaluation_start_date']}..{range_info['evaluation_end_date']}`\n"
        f"- 数据起点（含暖身）：`{range_info['data_start_date']}`\n"
        f"- BatchService batch：`{batch_id}`\n"
        "- research_execution_allowed：`true`\n"
        "- promotion_allowed：`false`\n"
        "- deployment_allowed：`false`\n\n"
        "## 阶段结论\n\n"
        + "\n".join(
            f"- {stage['stage']}：`{stage['result_status']}`，执行状态 `{stage['execution_status']}`；{stage['reason']}"
            for stage in stages
        )
        + "\n\n## 证据边界\n\n"
        "数值结果可用于同 snapshot 的研究排序和差值比较，但可选 identity/PIT 字段缺失使质量保持 "
        "`RESEARCH_ONLY_UNVERIFIED_UNIVERSE`；三折、成本压力、T+1/T+2、参数邻域和真实 forward "
        "观察仍不能由一次历史区间替代。因此本报告不产生 promotion 或 deployment 结论。\n"
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
    parser.add_argument("--stockpred-root", type=Path, default=None)
    parser.add_argument("--evaluation-start", default=None)
    parser.add_argument("--evaluation-end", default=None)
    parser.add_argument("--existing-batch-dir", type=Path, default=None)
    args = parser.parse_args()
    run_chain(
        output_dir=args.output_dir,
        report_path=args.report_path,
        u0_source=args.u0_source,
        stockpred_root=args.stockpred_root,
        evaluation_start=args.evaluation_start,
        evaluation_end=args.evaluation_end,
        existing_batch_dir=args.existing_batch_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
