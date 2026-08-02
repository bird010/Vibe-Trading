"""Artifacts writer — §16. Serializes pipeline results to CSV/JSON files."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path

import pandas as pd

from src.stockpred.fund_rotation.persistence import atomic_write_json, atomic_write_text


def write_csv_atomic(path: Path, df: pd.DataFrame) -> None:
    """Write CSV atomically with UTF-8 encoding."""
    tmp = path.with_suffix(".csv.tmp")
    df.to_csv(tmp, index=True, encoding="utf-8")
    tmp.replace(path)


def compute_file_checksum(path: Path) -> str:
    """SHA-256 checksum of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def write_run_artifacts(
    run_dir: Path,
    *,
    weekly_targets: dict[str, dict[str, float]],
    cluster_history: list[dict],
    exclusions: list[dict],
    strategy_cumulative: pd.Series,
    equal_weight_benchmark: pd.Series,
    buy_hold_benchmark: pd.Series,
    cash_benchmark: pd.Series,
    strategy_metrics: dict[str, float],
    benchmark_metrics: dict[str, dict[str, float]],
    config_params: dict,
    num_weeks: int,
    num_reclusters: int,
    num_etfs_used: int,
    trade_events: list[dict] | None = None,
    positions_history: list[dict] | None = None,
    executed_equity: pd.Series | None = None,
    robustness: dict | None = None,
    orders: list[dict] | None = None,
    data_snapshot: dict | None = None,
) -> dict:
    """Write all §16 artifacts and return manifest dict.

    Returns:
        manifest dict with files, checksums, and metadata.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    files_written: list[str] = []

    # targets.csv — week_ending, ts_code, weight
    if weekly_targets:
        rows = []
        previous: dict[str, float] = {}
        for week, targets in sorted(weekly_targets.items()):
            for code in sorted(set(previous) | set(targets)):
                weight = float(targets.get(code, 0.0))
                old_weight = float(previous.get(code, 0.0))
                if weight == 0.0 and old_weight == 0.0:
                    continue
                action = "EXIT" if weight == 0.0 else ("ENTRY" if old_weight == 0.0 else "REBALANCE")
                rows.append({
                    "week_ending": week, "ts_code": code, "weight": weight,
                    "previous_weight": old_weight, "signal_action": action,
                })
            previous = dict(targets)
        targets_df = pd.DataFrame(rows)
        write_csv_atomic(run_dir / "targets.csv", targets_df)
        files_written.append("targets.csv")

    # clusters.csv — week, ts_code, cluster_id
    if cluster_history:
        rows = []
        for entry in cluster_history:
            week = entry.get("week", "")
            for code, cid in sorted(entry.get("clusters", {}).items()):
                rows.append({"week": week, "ts_code": code, "cluster_id": cid})
        clusters_df = pd.DataFrame(rows)
        write_csv_atomic(run_dir / "clusters.csv", clusters_df)
        files_written.append("clusters.csv")

    # universe.csv — exclusions
    if exclusions:
        excl_df = pd.DataFrame(exclusions)
        write_csv_atomic(run_dir / "universe.csv", excl_df)
        files_written.append("universe.csv")

    # equity.csv — PRIMARY result is executed_equity (daily, with fees)
    # Theoretical strategy is kept as a secondary column for comparison.
    equity_data = {}
    equity_index = (
        executed_equity.index
        if executed_equity is not None and not executed_equity.empty
        else None
    )

    def on_equity_interval(series: pd.Series) -> pd.Series:
        return series.reindex(equity_index) if equity_index is not None else series

    if executed_equity is not None and not executed_equity.empty:
        equity_data["strategy"] = executed_equity
    if not strategy_cumulative.empty:
        equity_data["theoretical_strategy"] = on_equity_interval(strategy_cumulative)
    if not equal_weight_benchmark.empty:
        equity_data["equal_weight_etf"] = on_equity_interval(equal_weight_benchmark)
    if not buy_hold_benchmark.empty:
        equity_data["buy_hold_510300"] = on_equity_interval(buy_hold_benchmark)
    if not cash_benchmark.empty:
        equity_data["cash"] = on_equity_interval(cash_benchmark)

    if equity_data:
        equity_df = pd.DataFrame(equity_data)
        equity_df.index.name = "date"
        write_csv_atomic(run_dir / "equity.csv", equity_df)
        files_written.append("equity.csv")

    # metrics.json — primary metrics from executed equity (daily, 244 periods/year)
    executed_metrics = {}
    if executed_equity is not None and not executed_equity.empty and len(executed_equity) > 1:
        from backtest.fund_rotation.metrics import compute_performance_metrics as _cpm
        executed_metrics = _cpm(executed_equity, periods_per_year=244)

    metrics = {
        "strategy": executed_metrics if executed_metrics else strategy_metrics,
        "theoretical_strategy": strategy_metrics,
        "benchmarks": benchmark_metrics,
        "robustness": robustness or {},
        "metadata": {
            "num_weeks": num_weeks,
            "num_reclusters": num_reclusters,
            "num_etfs_used": num_etfs_used,
            "equity_basis": "executed" if executed_metrics else "theoretical",
        },
    }
    atomic_write_json(run_dir / "metrics.json", metrics)
    files_written.append("metrics.json")

    # trade_events.csv — §16 execution records
    if trade_events:
        te_df = pd.DataFrame(trade_events)
        write_csv_atomic(run_dir / "trade_events.csv", te_df)
        files_written.append("trade_events.csv")

    # orders.csv — §16 order lifecycle records
    if orders:
        orders_df = pd.DataFrame(orders)
        write_csv_atomic(run_dir / "orders.csv", orders_df)
        files_written.append("orders.csv")

    # positions.csv — §16 holdings snapshots
    if positions_history:
        pos_rows = []
        for snap in positions_history:
            td = snap.get("trade_date", "")
            for holding in snap.get("holdings", []):
                pos_rows.append({"trade_date": td, **holding,
                                 "cash": snap.get("cash", 0.0),
                                 "signal_cash": snap.get("signal_cash", 0.0),
                                 "execution_failure_cash": snap.get("execution_failure_cash", 0.0)})
            pos_rows.append({
                "trade_date": td, "ts_code": "_CASH", "quantity": 0,
                "mark_price": 1.0, "market_value": snap.get("cash", 0.0),
                "target_weight": 0.0,
                "actual_weight": snap.get("cash", 0.0) / snap.get("equity", 1.0)
                if snap.get("equity", 0.0) else 0.0,
                "adj_factor": 1.0, "stale_days": 0,
                "cash": snap.get("cash", 0.0),
                "signal_cash": snap.get("signal_cash", 0.0),
                "execution_failure_cash": snap.get("execution_failure_cash", 0.0),
            })
        if pos_rows:
            pos_df = pd.DataFrame(pos_rows)
            write_csv_atomic(run_dir / "positions.csv", pos_df)
            files_written.append("positions.csv")

    # summary.json — lightweight for list view (uses executed metrics as primary)
    primary_metrics = executed_metrics if executed_metrics else strategy_metrics
    summary = {
        "mode": "RESEARCH_ONLY",
        "num_weeks": num_weeks,
        "num_reclusters": num_reclusters,
        "num_etfs_used": num_etfs_used,
        "annual_return": primary_metrics.get("annual_return", 0.0),
        "max_drawdown": primary_metrics.get("max_drawdown", 0.0),
        "sharpe": primary_metrics.get("sharpe", 0.0),
        "total_return": primary_metrics.get("total_return", 0.0),
        "equity_basis": "executed" if executed_metrics else "theoretical",
    }
    atomic_write_json(run_dir / "summary.json", summary)
    files_written.append("summary.json")

    # data_snapshot.json is created immutably during validation; publish it in
    # the same final manifest instead of reopening the manifest afterwards.
    snapshot_path = run_dir / "data_snapshot.json"
    if data_snapshot is not None and not snapshot_path.exists():
        atomic_write_json(snapshot_path, data_snapshot)
    if snapshot_path.exists():
        files_written.append("data_snapshot.json")

    # Build, but do not publish, the success manifest.  The service first
    # persists SUCCEEDED state and then calls publish_manifest as the final
    # visibility boundary.
    manifest_files = {}
    for fname in files_written:
        fpath = run_dir / fname
        if fpath.exists():
            manifest_files[fname] = {
                "checksum": compute_file_checksum(fpath),
                "rows": _count_rows(fpath),
                "schema_version": "v1",
                "encoding": "utf-8",
                "columns": _csv_columns(fpath),
            }

    manifest = {
        "schema_version": "v1",
        "status": "SUCCEEDED",
        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "files": files_written,
        "file_details": manifest_files,
        "config": config_params,
        "publication_id": uuid.uuid4().hex,
    }

    return manifest


def publish_manifest(
    run_dir: Path,
    manifest: dict,
    *,
    run_id: str,
    params_fingerprint: str,
    terminal_event_seq: int | None = None,
) -> dict:
    """Atomically publish a unique manifest after SUCCEEDED state is durable."""
    state_path = run_dir / "state.json"
    if not state_path.exists():
        raise RuntimeError("cannot publish results without state.json")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("stage") != "SUCCEEDED":
        raise RuntimeError("cannot publish results before SUCCEEDED state")
    if state.get("run_id") != run_id or state.get("params_fingerprint") != params_fingerprint:
        raise RuntimeError("state identity does not match publication")
    published = {
        **manifest,
        "run_id": run_id,
        "params_fingerprint": params_fingerprint,
        "state_checksum": compute_file_checksum(state_path),
        "terminal_event_seq": terminal_event_seq,
    }
    atomic_write_json(run_dir / "manifest.json", published)
    return published


def write_debug_json(run_dir: Path, error: dict, stage: str) -> None:
    """§18.1 — Write debug.json on failure (local diagnostics only)."""
    import traceback as tb
    debug = {
        "stage": stage,
        "error": error,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "traceback": tb.format_exc(),
    }
    atomic_write_json(run_dir / "debug.json", debug)


def _count_rows(path: Path) -> int:
    """Count data rows in a CSV file."""
    if path.suffix != ".csv":
        return 0
    try:
        with open(path, encoding="utf-8") as f:
            return max(0, sum(1 for _ in f) - 1)  # minus header
    except Exception:
        return 0


def _csv_columns(path: Path) -> list[str]:
    """Return the fixed CSV header recorded by the publication manifest."""
    if path.suffix != ".csv":
        return []
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.readline().strip().split(",")
    except OSError:
        return []
