from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import api_server


def test_graph_run_detail_returns_symbol_metrics(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "graph_123"
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True)
    (run_dir / "req.json").write_text(
        json.dumps({"context": {"strategy_type": "stockpred_graph"}}),
        encoding="utf-8",
    )
    (artifacts / "symbol_metrics.csv").write_text(
        "symbol,total_return,trade_count,profit_loss_ratio\n"
        "000001.SZ,0.12,3,\n",
        encoding="utf-8",
    )
    (artifacts / "metrics.csv").write_text(
        "total_return,annual_return,max_drawdown,trade_count\n"
        "0.12,0.18,-0.04,3\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(api_server, "RUNS_DIR", runs_dir)

    response = TestClient(api_server.app).get("/runs/graph_123?chart_payload=summary")

    assert response.status_code == 200
    assert response.json()["symbol_metrics"] == [
        {"symbol": "000001.SZ", "total_return": 0.12, "trade_count": 3.0}
    ]
    assert response.json()["metrics"] == {
        "total_return": 0.12,
        "annual_return": 0.18,
        "max_drawdown": -0.04,
        "trade_count": 3,
    }


def test_run_detail_omits_full_trade_artifact_but_keeps_preview(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "plain_trades_123"
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True)
    (run_dir / "req.json").write_text(
        json.dumps({"context": {"strategy_type": "generated_strategy"}}),
        encoding="utf-8",
    )
    (artifacts / "trades.csv").write_text(
        "timestamp,code,side\n2025-01-02,000001.SZ,BUY\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(api_server, "RUNS_DIR", runs_dir)

    response = TestClient(api_server.app).get("/runs/plain_trades_123?chart_payload=summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["trade_log"] == [{"timestamp": "2025-01-02", "code": "000001.SZ", "side": "BUY"}]
    assert "artifacts_trades_csv" not in payload

def test_non_graph_run_detail_omits_only_unset_symbol_metrics(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "plain_123"
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True)
    (run_dir / "req.json").write_text(
        json.dumps({"context": {"strategy_type": "generated_strategy"}}),
        encoding="utf-8",
    )
    (artifacts / "metrics.csv").write_text(
        "final_value,total_return,annual_return,max_drawdown,sharpe,win_rate,trade_count\n"
        "110000,0.1,0.2,-0.05,1.5,0.6,4\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(api_server, "RUNS_DIR", runs_dir)

    client = TestClient(api_server.app)
    responses = [
        client.get("/runs/plain_123"),
        client.get("/runs/plain_123?chart_payload=summary"),
    ]

    for response in responses:
        assert response.status_code == 200
        payload = response.json()
        assert "symbol_metrics" not in payload
        assert "planner_output" in payload
        assert payload["planner_output"] is None
        assert payload["artifacts"] == [
            {
                "name": "metrics.csv",
                "path": str(artifacts / "metrics.csv"),
                "type": "csv",
                "size": (artifacts / "metrics.csv").stat().st_size,
                "exists": True,
            }
        ]
        assert payload["metrics"]["total_return"] == 0.1


def test_graph_run_detail_keeps_empty_persisted_symbol_metrics(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "graph_empty_123"
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True)
    (run_dir / "req.json").write_text(
        json.dumps({"context": {"strategy_type": "stockpred_graph"}}),
        encoding="utf-8",
    )
    (artifacts / "symbol_metrics.csv").write_text(
        "symbol,total_return,trade_count\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(api_server, "RUNS_DIR", runs_dir)

    response = TestClient(api_server.app).get("/runs/graph_empty_123")

    assert response.status_code == 200
    assert response.json()["symbol_metrics"] == []


def test_strategy_run_detail_returns_snapshot_and_symbol_metrics(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "runs" / "strategy_123"
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True)
    (run_dir / "req.json").write_text(json.dumps({"context": {"strategy_type": "stockpred_strategy"}}), encoding="utf-8")
    (run_dir / "strategy_snapshot.json").write_text(json.dumps({"strategy_version": "a" * 64, "descriptor": {"id": "alpha101_1"}}), encoding="utf-8")
    (artifacts / "symbol_metrics.csv").write_text("symbol,total_return\n000001.SZ,0.12\n", encoding="utf-8")
    monkeypatch.setattr(api_server, "RUNS_DIR", tmp_path / "runs")

    response = TestClient(api_server.app).get("/runs/strategy_123?chart_payload=summary")

    assert response.json()["strategy_snapshot"]["strategy_version"] == "a" * 64
    assert response.json()["symbol_metrics"] == [{"symbol": "000001.SZ", "total_return": 0.12}]


def test_batch_strategy_run_falls_back_to_reports_json_metrics(tmp_path: Path, monkeypatch) -> None:
    """Batch strategy runs may lack artifacts/; metrics fall back to reports.json."""
    runs_dir = tmp_path / "runs"
    batch_dir = runs_dir / "strategy_batches" / "batch_001"
    run_dir = batch_dir / "strategy_20260725T182240_5957d05e"
    run_dir.mkdir(parents=True)

    # Minimal state/req/config — no artifacts directory
    (run_dir / "state.json").write_text(json.dumps({"status": "success", "phase": "SUCCEEDED"}), encoding="utf-8")
    (run_dir / "req.json").write_text(
        json.dumps({"prompt": "StockPred strategy backtest", "context": {"strategy_type": "stockpred_strategy", "strategy_id": "academic_retskew"}}),
        encoding="utf-8",
    )
    snapshot = {"descriptor": {"id": "academic_retskew", "name": "RetSkew", "kind": "alpha_zoo"}, "strategy_version": "b" * 64}
    (run_dir / "config.json").write_text(json.dumps({"strategy_snapshot": snapshot}), encoding="utf-8")

    # Batch-level reports.json carries the computed metrics
    (batch_dir / "reports.json").write_text(
        json.dumps({"reports": [
            {"run_id": "strategy_20260725T182240_5957d05e", "status": "success", "metrics": {
                "annual_return": -0.52, "sharpe": -3.47, "total_return": -0.14,
                "max_drawdown": -0.17, "win_rate": 0.31, "trade_count": 527.0,
            }},
        ]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(api_server, "RUNS_DIR", runs_dir)

    response = TestClient(api_server.app).get("/runs/strategy_20260725T182240_5957d05e?chart_payload=summary")

    assert response.status_code == 200
    payload = response.json()
    # Metrics populated from reports.json fallback
    assert payload["metrics"]["sharpe"] == -3.47
    assert payload["metrics"]["trade_count"] == 527
    assert payload["metrics"]["total_return"] == -0.14
    # Strategy snapshot populated from config.json fallback
    assert payload["strategy_snapshot"]["descriptor"]["id"] == "academic_retskew"
    assert payload["strategy_snapshot"]["strategy_version"] == "b" * 64
    assert payload["status"] == "success"
