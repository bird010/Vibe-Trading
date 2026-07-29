"""Versioned artifacts publishing for cohort evaluation results.

Implements design §16.4 atomic publish protocol:
1. Write to artifacts_versions/.staging.<uuid>/
2. Validate completeness
3. Rename to artifacts_versions/<version_id>/
4. Atomic publish artifacts_current.json via temp + os.replace
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any

import pandas as pd

from backtest.stockpred.cohort.contracts import CohortResult


def publish_cohort_artifacts(
    *,
    run_dir: Path,
    cohort_results: list[CohortResult],
    agg_result: Any,
    config: dict[str, Any],
    chart_market: Any = None,
    chart_codes: list[str] | None = None,
    chart_orders: Any = None,
    chart_start_date: str = "",
    chart_end_date: str = "",
    signals_frames: list[Any] | None = None,
    targets_frames: list[Any] | None = None,
    benchmark_frames: list[Any] | None = None,
    period_breakdown: Any = None,
) -> str:
    """Publish cohort artifacts with versioned atomic protocol.

    Returns the version_id string.
    """
    run_dir = Path(run_dir)
    versions_dir = run_dir / "artifacts_versions"
    versions_dir.mkdir(parents=True, exist_ok=True)

    # 1. Write to staging
    token = uuid.uuid4().hex
    staging = versions_dir / f".staging.{token}"
    staging.mkdir()

    try:
        _write_cohort_returns(staging, cohort_results)
        _write_aggregate_metrics(staging, agg_result)
        _write_quality_report(staging, agg_result)
        _write_config(staging, config)
        _write_optional_frames(staging, "signals.csv", signals_frames)
        _write_optional_frames(staging, "cohort_targets.csv", targets_frames)
        _write_optional_frames(staging, "benchmark_returns.csv", benchmark_frames)
        if period_breakdown is not None:
            _atomic_csv(staging / "period_breakdown.csv", period_breakdown)

        # Every cohort version has a manifest, including an auditable empty run.
        from backtest.stockpred.cohort.chart_bundle import publish_chart_bundle

        orders_df = chart_orders if chart_orders is not None else pd.DataFrame(
            columns=["cohort_id", "code", "trade_date", "side", "price", "quantity"]
        )
        market_df = chart_market if chart_market is not None else pd.DataFrame(
            columns=["ts_code", "trade_date"]
        )
        publish_chart_bundle(
            staging_dir=staging,
            market=market_df,
            codes=chart_codes or [],
            orders=orders_df,
            start_date=chart_start_date,
            end_date=chart_end_date,
        )
        _index_artifact_files(staging)

        # 2. Compute version_id from content hash
        version_id = _compute_version_id(staging)

        # 3. Rename staging to immutable version directory
        version_dir = versions_dir / version_id
        if version_dir.exists():
            # Idempotent: same content already published
            shutil.rmtree(staging)
        else:
            staging.rename(version_dir)

        # 4. Atomic publish artifacts_current.json
        manifest_sha256 = hashlib.sha256(
            (version_dir / "chart_bundle_manifest.json").read_bytes()
        ).hexdigest()
        pointer = {
            "version_id": version_id,
            "schema_version": "signal_cohort_v1",
            "manifest_sha256": manifest_sha256,
        }
        _atomic_json(run_dir / "artifacts_current.json", pointer)

        return version_id

    except Exception:
        # Cleanup staging on failure
        if staging.exists():
            shutil.rmtree(staging)
        raise


def _write_cohort_returns(staging: Path, results: list[CohortResult]) -> None:
    """Write cohort_returns.csv."""
    if results:
        rows = [asdict(r) for r in results]
        # Preserve structured failure details in a deterministic, readable CSV form.
        for row in rows:
            row["data_quality"] = json.dumps(
                row.get("data_quality", {}), ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            )
        df = pd.DataFrame(rows)
    else:
        df = pd.DataFrame(columns=[field.name for field in fields(CohortResult)])
    _atomic_csv(staging / "cohort_returns.csv", df)


def _write_aggregate_metrics(staging: Path, agg_result: Any) -> None:
    """Write aggregate_metrics.json."""
    metrics = agg_result.metrics
    payload = {
        "mean_return": metrics.mean_return,
        "median_return": metrics.median_return,
        "std_return": metrics.std_return,
        "win_rate": metrics.win_rate,
        "p5": metrics.p5,
        "p25": metrics.p25,
        "p75": metrics.p75,
        "p95": metrics.p95,
        "mean_excess_return": metrics.mean_excess_return,
        "positive_excess_ratio": metrics.positive_excess_ratio,
        "mean_fill_rate": metrics.mean_fill_rate,
        "mean_idle_cash_ratio": metrics.mean_idle_cash_ratio,
        "mean_cost_ratio": metrics.mean_cost_ratio,
        "mean_unliquidated_ratio": metrics.mean_unliquidated_ratio,
        "valid_cohort_count": metrics.valid_cohort_count,
        "total_cohort_count": metrics.total_cohort_count,
        "hac_se": metrics.hac_se,
        "bootstrap_ci": (
            {"lower": metrics.bootstrap_ci.lower, "upper": metrics.bootstrap_ci.upper, "mean": metrics.bootstrap_ci.mean}
            if metrics.bootstrap_ci
            else None
        ),
    }
    _atomic_json(staging / "aggregate_metrics.json", payload)


def _write_quality_report(staging: Path, agg_result: Any) -> None:
    """Write quality_report.json."""
    quality = agg_result.quality
    payload = {
        "ranking_eligible": quality.ranking_eligible,
        "valid_eval_ratio": quality.valid_eval_ratio,
        "failures": quality.failures,
    }
    _atomic_json(staging / "quality_report.json", payload)


def _write_config(staging: Path, config: dict[str, Any]) -> None:
    """Write config.json."""
    _atomic_json(staging / "config.json", config)


def _compute_version_id(staging: Path) -> str:
    """Compute deterministic version_id from staging content (recursive)."""
    hasher = hashlib.sha256()
    for path in sorted(staging.rglob("*")):
        if path.is_file():
            # Use relative path for determinism across different staging dirs
            hasher.update(str(path.relative_to(staging)).encode("utf-8"))
            hasher.update(path.read_bytes())
    return hasher.hexdigest()[:32]


def _index_artifact_files(staging: Path) -> None:
    """Record hashes for lazy, verified reads of all version files except itself."""
    manifest_path = staging / "chart_bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = []
    for path in sorted(staging.rglob("*")):
        if path.is_file() and path != manifest_path:
            payload = path.read_bytes()
            files.append(
                {
                    "relative_path": path.relative_to(staging).as_posix(),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "byte_size": len(payload),
                }
            )
    manifest["files"] = files
    _atomic_json(manifest_path, manifest)


def _atomic_json(path: Path, data: Any) -> None:
    """Write JSON atomically via temp file + os.replace."""
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(str(tmp), str(path))


def _atomic_csv(path: Path, df: pd.DataFrame) -> None:
    """Write CSV atomically via temp file + os.replace."""
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    df.to_csv(tmp, index=False)
    os.replace(str(tmp), str(path))


def _write_optional_frames(staging: Path, filename: str, frames: list[Any] | None) -> None:
    """Concatenate and write optional DataFrame list."""
    if frames:
        import pandas as _pd
        valid = [f for f in frames if f is not None and not f.empty]
        if valid:
            combined = _pd.concat(valid, ignore_index=True)
            _atomic_csv(staging / filename, combined)
        else:
            _atomic_csv(staging / filename, _pd.DataFrame())
    else:
        _atomic_csv(staging / filename, pd.DataFrame())
