"""Atomic standard and audit artifacts for StockPred Graph runs."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

import pandas as pd

from backtest.run_card import write_run_card
from backtest.stockpred_graph.runner import GraphBacktestResult
from src.stockpred.contracts import DataSnapshotManifest
from src.stockpred.graph.backtest_config import GraphBacktestConfig
from src.stockpred.parity import ParityReport
from src.stockpred.run_store import atomic_json


_SAFE_CODE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _atomic_csv(path: Path, frame: pd.DataFrame) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    frame.to_csv(temporary, index=False)
    temporary.replace(path)


def _atomic_parquet(path: Path, frame: pd.DataFrame) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    frame.to_parquet(temporary, index=False)
    temporary.replace(path)


def _config_sha256(config: GraphBacktestConfig) -> str:
    payload = config.model_dump(mode="json")
    payload.pop("parity_reference", None)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_parity_report(path: Path, report: ParityReport) -> None:
    atomic_json(path, json.loads(report.to_json()))


def write_graph_artifacts(
    run_dir: Path,
    result: GraphBacktestResult,
    manifest: DataSnapshotManifest,
    config: GraphBacktestConfig,
    parity_report: ParityReport | None = None,
    *,
    config_path_written: bool = True,
) -> None:
    root = Path(run_dir)
    root.mkdir(parents=True, exist_ok=True)
    if not config_path_written or not (root / "config.json").is_file():
        atomic_json(root / "config.json", config.model_dump(mode="json"))
    if not (root / "req.json").is_file():
        atomic_json(
            root / "req.json",
            {
                "prompt": "StockPred Graph backtest",
                "context": {
                    "strategy_type": "stockpred_graph",
                    "start_date": config.start,
                    "end_date": config.end,
                    "mode": config.mode,
                    "benchmark_code": config.benchmark_code,
                    "codes": sorted(result.ohlcv),
                },
            },
        )
    model_manifest = manifest.model.model_dump(mode="json")
    model_manifest["config_sha256"] = _config_sha256(config)
    atomic_json(root / "model_manifest.json", model_manifest)

    staging = root / ".artifacts.staging"
    artifacts = root / "artifacts"
    if staging.exists():
        shutil.rmtree(staging)
    if artifacts.exists():
        raise FileExistsError(f"artifacts already published: {artifacts}")
    staging.mkdir()
    try:
        _atomic_csv(staging / "metrics.csv", pd.DataFrame([result.metrics]))
        _atomic_csv(staging / "equity.csv", result.equity)
        _atomic_csv(staging / "positions.csv", result.positions)
        _atomic_csv(staging / "trades.csv", result.trades)
        symbol_metrics = pd.DataFrame(result.symbol_metrics)
        if symbol_metrics.empty:
            symbol_metrics = pd.DataFrame(columns=["symbol"])
        _atomic_csv(staging / "symbol_metrics.csv", symbol_metrics)
        _atomic_parquet(staging / "signals.parquet", result.signals)
        _atomic_csv(staging / "selected_signals.csv", result.selected)
        for code, frame in sorted(result.ohlcv.items()):
            if not _SAFE_CODE.fullmatch(str(code)):
                raise ValueError(f"unsafe OHLCV code: {code!r}")
            _atomic_csv(staging / f"ohlcv_{code}.csv", frame)
        staging.replace(artifacts)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    if parity_report is not None:
        write_parity_report(root / "parity.json", parity_report)
    card_config = {
        **config.model_dump(mode="json"),
        "start_date": config.start,
        "end_date": config.end,
        "engine": "stockpred_graph",
        "initial_cash": config.portfolio_capital,
        "source": "stockpred",
    }
    write_run_card(
        root,
        card_config,
        result.metrics,
        data_sources=["stockpred"],
    )

