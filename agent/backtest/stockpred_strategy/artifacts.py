"""Atomic standard artifacts for one versioned StockPred strategy report."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from backtest.run_card import write_run_card
from backtest.stockpred_strategy.runner import StrategyBacktestResult
from src.stockpred.contracts import DataSnapshotManifest
from src.stockpred.run_store import atomic_json
from src.stockpred.strategies.contracts import StrategyBacktestConfig
from src.stockpred.strategies.snapshot import write_strategy_archive


def _csv(path: Path, frame: pd.DataFrame) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    frame.to_csv(temporary, index=False)
    temporary.replace(path)


def _parquet(path: Path, frame: pd.DataFrame) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    frame.to_parquet(temporary, index=False)
    temporary.replace(path)


def write_screening_artifacts(
    run_dir: Path,
    result: StrategyBacktestResult,
    manifest: DataSnapshotManifest,
    config: StrategyBacktestConfig,
) -> dict[str, object]:
    root = Path(run_dir)
    root.mkdir(parents=True, exist_ok=True)
    atomic_json(root / "data_snapshot.json", manifest.model_dump(mode="json"))
    write_strategy_archive(root, config.strategy_snapshot)
    artifacts = root / "artifacts"
    staging = root / ".artifacts.staging"
    if staging.exists() or artifacts.exists():
        raise FileExistsError("strategy artifacts already published")
    staging.mkdir()
    try:
        _csv(staging / "metrics.csv", pd.DataFrame([result.metrics]))
        _csv(staging / "equity.csv", result.equity)
        _csv(staging / "positions.csv", result.positions)
        _csv(staging / "trades.csv", result.trades)
        _csv(staging / "selected_signals.csv", result.selected)
        _csv(staging / "symbol_metrics.csv", pd.DataFrame(result.symbol_metrics))
        _parquet(staging / "signals.parquet", result.signals)
        staging.replace(artifacts)
    except Exception:
        if staging.exists():
            for path in staging.iterdir():
                path.unlink()
            staging.rmdir()
        raise
    write_run_card(root, {**config.model_dump(mode="json"), "engine": "stockpred_strategy", "initial_cash": config.portfolio_capital}, result.metrics, data_sources=["stockpred"])
    detail_manifest = {
        "version": 1,
        "run_id": root.name,
        "comparison_key": config.comparison_key,
        "data_snapshot": manifest.model_dump(mode="json"),
        "codes": _selected_codes(result.selected),
        "market_start": config.start,
        "market_end": (datetime.strptime(config.end, "%Y%m%d") + timedelta(days=60)).strftime("%Y%m%d"),
    }
    atomic_json(root / "detail_manifest.json", detail_manifest)
    return detail_manifest


def write_strategy_artifacts(
    run_dir: Path,
    result: StrategyBacktestResult,
    manifest: DataSnapshotManifest,
    config: StrategyBacktestConfig,
) -> None:
    """Compatibility wrapper for callers that have not adopted screening naming."""
    write_screening_artifacts(run_dir, result, manifest, config)


def _selected_codes(selected: pd.DataFrame) -> list[str]:
    if "ts_code" not in selected.columns:
        return []
    return sorted(selected["ts_code"].dropna().astype(str).unique())
