"""Fund Rotation V2 evidence API contracts."""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.fund_rotation_routes import register_fund_rotation_routes
from src.stockpred.fund_rotation.artifact_publisher import ArtifactPublisher
from src.stockpred.fund_rotation.artifacts import compute_file_checksum
from backtest.fund_rotation.contracts import StrategyArtifact


def _app(tmp_path):
    app = FastAPI()
    register_fund_rotation_routes(
        app,
        tmp_path,
        lambda: None,
        lambda: None,
    )
    return app


def _publish_rotation_run(tmp_path, run_id: str = "run-v2") -> None:
    run_dir = tmp_path / "fund_rotation" / run_id
    run_dir.mkdir(parents=True)
    state_path = run_dir / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": "2",
                "stage": "SUCCEEDED",
                "run_id": run_id,
                "batch_id": "batch-1",
                "variant_key": "variant-1",
                "strategy_id": "correlation_representative",
                "mode": "RESEARCH_ONLY",
                "quality_status": "VALID",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "events.jsonl").write_text("{}\n", encoding="utf-8")
    publisher = ArtifactPublisher(run_dir)
    publisher.publish(
        StrategyArtifact(
            role="resolved_spec",
            media_type="application/json",
            payload={"run_id": run_id, "strategy_id": "correlation_representative"},
        )
    )
    publisher.publish(
        StrategyArtifact(
            role="summary",
            media_type="application/json",
            payload={"quality_status": "VALID", "partial": False},
        )
    )
    publisher.publish(
        StrategyArtifact(
            role="holdings_timeline",
            media_type="application/json",
            payload={
                "schema_version": "1",
                "run_id": run_id,
                "start_date": "20240102",
                "end_date": "20240103",
                "instruments": [{"ts_code": "510300.SH", "name": "沪深300ETF"}],
                "intervals": [{
                    "ts_code": "510300.SH",
                    "start_date": "20240102",
                    "end_date": "20240103",
                    "actual_weight": 0.5,
                    "target_weight": 1.0,
                }],
                "rebalance_markers": [],
            },
        )
    )
    publisher.publish(
        StrategyArtifact(
            role="rebalance_index",
            media_type="application/json",
            payload={
                "schema_version": "1",
                "run_id": run_id,
                "items": [{
                    "signal_date": "20240103",
                    "sequence": 1,
                    "quality_status": "VALID",
                    "changed_positions": 1,
                    "target_count": 1,
                    "turnover": 1.0,
                    "cash_target_weight": 0.0,
                    "cluster_snapshot_date": "20240103",
                    "has_execution": True,
                }],
            },
        )
    )
    publisher.publish(
        StrategyArtifact(
            role="rebalance_decisions",
            media_type="application/json",
            payload={
                "schema_version": "1",
                "run_id": run_id,
                "items": {
                    "20240103": {
                        "schema_version": "1",
                        "run_id": run_id,
                        "signal_date": "20240103",
                        "sequence": 1,
                        "quality": {"decision_status": "VALID", "reasons": []},
                        "before": {"as_of_date": "20240102", "weights": {}},
                        "after_target": {
                            "as_of_signal_date": "20240103",
                            "weights": {"510300.SH": 1.0},
                        },
                        "decision": {
                            "strategy": {"ranking_metric": "momentum"},
                            "candidates": [],
                        },
                        "execution": {
                            "orders": [],
                            "summary": {
                                "filled": 0,
                                "partial": 0,
                                "blocked": 0,
                                "commission": 0.0,
                                "turnover": 1.0,
                            },
                        },
                    }
                },
            },
        )
    )
    publisher.index_external("state")
    publisher.index_external("events")
    file_details = {
        entry["file"]: {
            key: value
            for key, value in entry.items()
            if key in {"checksum", "rows", "columns", "encoding"}
        }
        for entry in publisher.artifact_index().values()
    }
    publisher.finalize(
        status="SUCCEEDED",
        identity={
            "run_id": run_id,
            "batch_id": "batch-1",
            "variant_key": "variant-1",
            "strategy_id": "correlation_representative",
            "mode": "RESEARCH_ONLY",
            "quality_status": "VALID",
            "params_fingerprint": "config-hash",
            "data_snapshot_fingerprint": "snapshot-hash",
            "state_checksum": compute_file_checksum(state_path),
            "file_details": file_details,
        },
    )


def test_v2_evidence_endpoints_read_published_artifacts(tmp_path):
    _publish_rotation_run(tmp_path)
    client = TestClient(_app(tmp_path))

    timeline = client.get(
        "/stockpred/fund-rotation/backtests/run-v2/holdings-timeline"
    )
    index = client.get("/stockpred/fund-rotation/backtests/run-v2/rebalances")
    bundle = client.get(
        "/stockpred/fund-rotation/backtests/run-v2/rebalances/20240103"
    )

    assert timeline.status_code == 200
    assert timeline.json()["run_id"] == "run-v2"
    assert index.status_code == 200
    assert index.json()["items"][0]["signal_date"] == "20240103"
    assert bundle.status_code == 200
    assert bundle.json()["signal_date"] == "20240103"


def test_v2_evidence_endpoint_rejects_unknown_signal_date(tmp_path):
    _publish_rotation_run(tmp_path)
    response = TestClient(_app(tmp_path)).get(
        "/stockpred/fund-rotation/backtests/run-v2/rebalances/20240104"
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "REBALANCE_NOT_FOUND"


def test_legacy_run_gets_explicit_rotation_artifact_error(tmp_path):
    run_dir = tmp_path / "fund_rotation" / "legacy"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(
        json.dumps({"stage": "SUCCEEDED", "run_id": "legacy"}),
        encoding="utf-8",
    )
    response = TestClient(_app(tmp_path)).get(
        "/stockpred/fund-rotation/backtests/legacy/holdings-timeline"
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "ROTATION_TIMELINE_UNAVAILABLE"

