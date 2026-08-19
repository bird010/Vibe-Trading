from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from agent.backtest.stockpred_graph.artifacts import write_graph_artifacts
from agent.backtest.stockpred_graph.runner import GraphBacktestResult
from src.stockpred.contracts import DataSnapshotManifest, ModelSnapshot
from src.stockpred.graph.backtest_config import GraphBacktestConfig


def _manifest() -> DataSnapshotManifest:
    return DataSnapshotManifest(
        as_of="2025-01-31T15:00:00+08:00",
        tables={},
        model=ModelSnapshot(id="stockpred-graph", version="graph-v1", config_sha256="old"),
    )


def _result() -> GraphBacktestResult:
    return GraphBacktestResult(
        eval_dates=["20250102"],
        signals=pd.DataFrame(
            {"trade_date": ["20250102"], "ts_code": ["A"], "score": [1.0]}
        ),
        selected=pd.DataFrame(
            {"trade_date": ["20250102"], "ts_code": ["A"], "score": [1.0]}
        ),
        trades=pd.DataFrame(
            {"timestamp": ["2025-01-03"], "code": ["A"], "side": ["BUY"]}
        ),
        positions=pd.DataFrame(
            {"time": ["2025-01-03"], "code": ["A"], "qty": [100.0]}
        ),
        equity=pd.DataFrame({"time": ["2025-01-03"], "equity": [10_000_000.0]}),
        metrics={"total_return": 0.0},
        ohlcv={
            "A": pd.DataFrame(
                {
                    "trade_date": ["20250103"],
                    "open": [10.0],
                    "high": [10.5],
                    "low": [9.8],
                    "close": [10.2],
                    "vol": [1000.0],
                }
            )
        },
    )


def test_graph_artifacts_publish_standard_and_audit_files(tmp_path: Path) -> None:
    config = GraphBacktestConfig(start="2025-01-01", end="2025-01-31")

    write_graph_artifacts(tmp_path, _result(), _manifest(), config)

    expected = {
        "metrics.csv",
        "equity.csv",
        "positions.csv",
        "trades.csv",
        "signals.parquet",
        "selected_signals.csv",
        "symbol_metrics.csv",
        "ohlcv_A.csv",
    }
    assert {path.name for path in (tmp_path / "artifacts").iterdir()} == expected
    model = json.loads((tmp_path / "model_manifest.json").read_text(encoding="utf-8"))
    assert model["id"] == "stockpred-graph"
    assert len(model["config_sha256"]) == 64
    assert (tmp_path / "run_card.json").is_file()
    assert not (tmp_path / ".artifacts.staging").exists()

def test_graph_artifacts_publish_symbol_metrics_csv(tmp_path: Path) -> None:
    write_graph_artifacts(
        tmp_path,
        _result(),
        _manifest(),
        GraphBacktestConfig(start="2025-01-01", end="2025-01-31"),
    )

    assert (tmp_path / "artifacts" / "symbol_metrics.csv").is_file()


def test_model_hash_ignores_local_parity_reference(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    base = GraphBacktestConfig(start="2025-01-01", end="2025-01-31")

    write_graph_artifacts(first, _result(), _manifest(), base, config_path_written=False)
    write_graph_artifacts(
        second,
        _result(),
        _manifest(),
        base.model_copy(update={"parity_reference": "C:/local/golden"}),
        config_path_written=False,
    )

    first_model = json.loads((first / "model_manifest.json").read_text())
    second_model = json.loads((second / "model_manifest.json").read_text())
    assert first_model["config_sha256"] == second_model["config_sha256"]
