from __future__ import annotations

from pathlib import Path

import pandas as pd

from backtest.stockpred_strategy.artifacts import write_screening_artifacts
from backtest.stockpred_strategy.runner import StrategyBacktestResult
from src.stockpred.contracts import DataSnapshotManifest, ModelSnapshot

from agent.tests.stockpred.test_strategy_runner import _config


def test_screening_artifacts_write_snapshot_and_standard_report_without_ohlcv(tmp_path: Path) -> None:
    result = StrategyBacktestResult(
        strategy_id="alpha101_1", eval_dates=["20250102"],
        signals=pd.DataFrame({"ts_code": ["000001.SZ"], "score": [1.0]}),
        selected=pd.DataFrame({"ts_code": ["000002.SZ", "000001.SZ", "000002.SZ"]}),
        trades=pd.DataFrame(), positions=pd.DataFrame(),
        equity=pd.DataFrame({"time": ["2025-01-02"], "nav": [100.0]}),
        metrics={"sharpe": 1.0},
    )
    manifest = DataSnapshotManifest(as_of="2025-01-07T15:00:00+08:00", tables={}, model=ModelSnapshot(id="stockpred", version="v1", config_sha256="0" * 64))

    detail_manifest = write_screening_artifacts(tmp_path, result, manifest, _config())

    assert (tmp_path / "strategy_snapshot.json").is_file()
    assert (tmp_path / "strategy_source.zip").is_file()
    assert (tmp_path / "data_snapshot.json").is_file()
    assert (tmp_path / "artifacts" / "metrics.csv").is_file()
    assert (tmp_path / "artifacts" / "signals.parquet").is_file()
    assert not list((tmp_path / "artifacts").glob("ohlcv_*.csv"))
    assert (tmp_path / "detail_manifest.json").is_file()
    assert detail_manifest == {
        "version": 1,
        "run_id": tmp_path.name,
        "comparison_key": "c" * 64,
        "data_snapshot": manifest.model_dump(mode="json"),
        "codes": ["000001.SZ", "000002.SZ"],
        "market_start": "20250102",
        "market_end": "20250308",
    }
