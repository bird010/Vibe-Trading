"""Generate auditable U0/U1 PIT identity snapshots.

This script deliberately does not manufacture R39 backtest evidence.  It can
resolve snapshots when an explicit PIT master, rebalance-date list and
decision-date tradability file are supplied; otherwise it writes an explicit
``unavailable`` manifest and Chinese report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

AGENT_ROOT = Path(__file__).resolve().parents[2] / "agent"
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from backtest.fund_rotation.pit_universe import (  # noqa: E402
    PITFundMaster,
    PITQueryMode,
    UniversePolicy,
    UniverseResolver,
)


EXPERIMENT_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = EXPERIMENT_ROOT / "batch_1"
DEFAULT_REPORT_PATH = EXPERIMENT_ROOT / "batch_1_report.md"


class StrictTradabilityView:
    """Decision-date tradability source that fails closed when a row is absent."""

    def __init__(self, records: list[dict[str, Any]]):
        self._records: dict[tuple[str, str], bool | None] = {}
        self._conflicts: set[tuple[str, str]] = set()
        for row in records:
            if row.get("signal_date") is None or row.get("ts_code") is None:
                continue
            key = (self._normalize_date(row["signal_date"]), str(row["ts_code"]))
            value = self._parse_tradable(row.get("tradable"))
            if key in self._records and self._records[key] != value:
                self._conflicts.add(key)
            else:
                self._records[key] = value

    def tradable_status(self, ts_code: str, signal_date: str) -> tuple[bool, str]:
        key = (self._normalize_date(signal_date), str(ts_code))
        if key in self._conflicts:
            return False, "TRADABILITY_CONFLICT"
        value = self._records.get(key)
        if value is None:
            return False, "TRADABILITY_UNAVAILABLE"
        if value:
            return True, ""
        return False, "NOT_TRADABLE"

    @staticmethod
    def _parse_tradable(value: object) -> bool | None:
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "y", "tradable"}:
            return True
        if normalized in {"0", "false", "no", "n", "not_tradable"}:
            return False
        return None

    @staticmethod
    def _normalize_date(value: object) -> str:
        try:
            return pd.Timestamp(value).strftime("%Y-%m-%d")
        except (TypeError, ValueError, OverflowError):
            return str(value)


def _read_records(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        return json.loads(pd.read_csv(path).to_json(orient="records", force_ascii=False))
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("records", payload.get("dates", []))
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        raise ValueError(f"expected a JSON list of objects: {path}")
    return payload


def _read_dates(path: Path) -> list[str]:
    if path.suffix.lower() == ".csv":
        frame = pd.read_csv(path)
        column = "signal_date" if "signal_date" in frame else "date"
        return sorted({str(value) for value in frame[column].dropna()})
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("dates", payload.get("records", []))
    if not isinstance(payload, list):
        raise ValueError(f"expected a JSON list of dates or objects: {path}")
    dates = []
    for value in payload:
        if isinstance(value, dict):
            value = value.get("signal_date", value.get("date"))
        if value is not None:
            dates.append(str(value))
    return sorted(set(dates))


def _write_immutable(path: Path, content: str) -> None:
    """Write an artifact once, while allowing an identical rerun."""

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(content.encode("utf-8"))
        return
    except FileExistsError:
        existing = path.read_text(encoding="utf-8")
        if existing == content:
            return
        raise FileExistsError(f"refusing to overwrite immutable artifact: {path}") from None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_immutable(path: Path, content: str) -> None:
    if path.exists() and path.read_text(encoding="utf-8") != content:
        raise FileExistsError(f"refusing to overwrite immutable artifact: {path}")


def _snapshot_payload(snapshot: Any) -> dict[str, Any]:
    return {
        "layer": snapshot.layer,
        "signal_date": snapshot.signal_date,
        "knowledge_cutoff": snapshot.knowledge_cutoff,
        "source_snapshot_version": snapshot.source_snapshot_version,
        "eligible_codes": list(snapshot.eligible_codes),
        "membership": [
            {
                "ts_code": item.ts_code,
                "included": item.included,
                "reason_code": item.reason_code,
                "identity_key": item.identity_key,
                "layer": item.layer,
            }
            for item in snapshot.membership
        ],
        "identity_mapping": dict(snapshot.identity_mapping),
        "identity_hash": snapshot.identity_hash,
        "snapshot_fingerprint": snapshot.snapshot_fingerprint,
        "coverage_diagnostics": dict(snapshot.coverage_diagnostics),
        "quality_status": snapshot.quality_status.value,
    }


def _unavailable(reason: str) -> dict[str, Any]:
    return {"status": "unavailable", "reason": reason}


def generate(
    *,
    master_path: Path | None,
    dates_path: Path | None,
    tradability_path: Path | None,
    output_dir: Path,
    report_path: Path,
    snapshot_version: int,
    cutoff_time: str,
) -> dict[str, Any]:
    if cutoff_time != "15:00:00":
        raise ValueError("Task 2 requires cutoff_time='15:00:00'")
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "schema_version": "fund_rotation_pit_identity_v1",
        "task": "Task 2",
        "mode": PITQueryMode.AS_WAS_KNOWN.value,
        "knowledge_cutoff_time": cutoff_time,
        "snapshot_version": snapshot_version,
        "inputs": {
            "master": str(master_path) if master_path else None,
            "rebalance_dates": str(dates_path) if dates_path else None,
            "tradability": str(tradability_path) if tradability_path else None,
        },
        "snapshots": [],
        "r39_three_fold_experiment": _unavailable(
            "当前仓库未提供可验证的 PIT master、R39 manifest、三折回测入口及 T+1/T+2 延迟证据"
        ),
    }

    if not master_path or not dates_path or not tradability_path:
        missing = [
            name
            for name, path in (
                ("PIT master", master_path),
                ("rebalance dates", dates_path),
                ("decision-date tradability", tradability_path),
            )
            if path is None
        ]
        manifest["snapshot_status"] = _unavailable("缺少输入：" + ", ".join(missing))
    else:
        master_records = _read_records(master_path)
        dates = _read_dates(dates_path)
        tradability = StrictTradabilityView(_read_records(tradability_path))
        resolver = UniverseResolver(PITFundMaster(master_records))
        invalid_dates: list[str] = []
        research_only_dates: list[str] = []
        for signal_date in dates:
            layers = resolver.resolve_identity_layers(
                signal_date=signal_date,
                knowledge_cutoff=f"{signal_date}T{cutoff_time}",
                strategy_policy=UniversePolicy(),
                causal_view=tradability,
                snapshot_version=snapshot_version,
                mode=PITQueryMode.AS_WAS_KNOWN,
            )
            u0 = _snapshot_payload(layers.u0)
            u1 = _snapshot_payload(layers.u1)
            for payload in (u0, u1):
                payload["coverage_diagnostics"].update(
                    {
                        "momentum_coverage": "unavailable",
                        "max_cluster_share": "unavailable",
                        "effective_cluster_count": "unavailable",
                        "tradable_representative_ratio": "unavailable",
                    }
                )
            if any(
                payload["quality_status"] == "PIT_INVALID"
                for payload in (u0, u1)
            ):
                invalid_dates.append(signal_date)
            elif u1["quality_status"] == "RESEARCH_ONLY_UNVERIFIED_UNIVERSE":
                research_only_dates.append(signal_date)
            manifest["snapshots"].append({"signal_date": signal_date, "u0": u0, "u1": u1})
        if not dates:
            manifest["snapshot_status"] = _unavailable("rebalance date list is empty")
        else:
            manifest["snapshot_status"] = {
                "status": (
                    "invalid"
                    if invalid_dates
                    else "available_research_only"
                    if research_only_dates
                    else "available"
                ),
                "date_count": len(dates),
                "invalid_dates": invalid_dates,
                "research_only_dates": research_only_dates,
            }

    manifest_path = output_dir / "manifest.json"
    manifest_text = json.dumps(
        manifest, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
    ) + "\n"
    manifest_hash = (
        _sha256(manifest_path)
        if manifest_path.exists()
        else hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()
    )
    status = manifest.get("snapshot_status", {})
    report = (
        "# Batch 1：PIT U0/U1 身份快照报告\n\n"
        f"- 快照状态：`{status.get('status', 'unavailable')}`\n"
        f"- 快照日期数：`{status.get('date_count', 0)}`\n"
        f"- manifest：`{manifest_path}`\n"
        f"- manifest SHA-256：`{manifest_hash}`\n\n"
        "## 证据边界\n\n"
        "U0 复用既有 `UniverseResolver` 的 AS_WAS_KNOWN、上市/退市边界、三层 exclusion "
        "和旧 diagnostics；U1 从冻结 U0 派生并保持相同 eligible 集合；身份完整且无冲突时记录 "
        "`u1_equals_u0=true`；身份缺失或冲突时仍保留相同 `eligible_codes` 作为研究对照，相关成员"
        "保持 `included`，U1 标记为 `RESEARCH_ONLY_UNVERIFIED_UNIVERSE`，允许研究运行但禁止 "
        "promotion/deployment。PIT 可选证据缺失或部分时同样允许研究运行但禁止 promotion/deployment。"
        "未来已知和边界不确定的成员不作为 U1 有效成员。\n\n"
        "## 研究诊断\n\n"
        "available count、duplicate identity ratio、identity hash 和 snapshot fingerprint 在可用"
        "输入时逐日期写入 manifest。momentum coverage、max cluster share、effective cluster count "
        "和 tradable representative ratio 需要额外的决策日行情/聚类证据；本次没有输入时明确标记"
        "为 `unavailable`，没有用零值替代。\n\n"
        "## R39 三折成本/延迟实验\n\n"
        "`unavailable`：当前仓库没有可验证的 PIT master、冻结 R39 manifest、三折回测入口及 "
        "T+1/T+2 延迟证据，因此本报告不伪造 U0/U1 收益、成本或策略结论。U1 也未被调参。\n"
    )
    _check_immutable(manifest_path, manifest_text)
    _check_immutable(report_path, report)
    _write_immutable(manifest_path, manifest_text)
    _write_immutable(report_path, report)
    manifest["manifest_sha256"] = manifest_hash
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 PIT U0/U1 identity snapshots")
    parser.add_argument("--master-path", type=Path)
    parser.add_argument("--dates-path", "--rebalance-dates", dest="dates_path", type=Path)
    parser.add_argument("--tradability-path", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--snapshot-version", type=int, default=1)
    parser.add_argument("--cutoff-time", default="15:00:00")
    args = parser.parse_args()
    generate(
        master_path=args.master_path,
        dates_path=args.dates_path,
        tradability_path=args.tradability_path,
        output_dir=args.output_dir,
        report_path=args.report_path,
        snapshot_version=args.snapshot_version,
        cutoff_time=args.cutoff_time,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
