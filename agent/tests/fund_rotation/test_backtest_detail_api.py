"""Checksum-gated child-run detail API contracts."""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backtest.fund_rotation.contracts import StrategyArtifact
from src.api.fund_rotation_routes import register_fund_rotation_routes
from src.stockpred.fund_rotation.artifact_publisher import ArtifactPublisher
from src.stockpred.fund_rotation.artifacts import compute_file_checksum


def _app(tmp_path):
    app = FastAPI()
    register_fund_rotation_routes(
        app,
        tmp_path,
        lambda: None,
        lambda: None,
    )
    return app


def _publish_v2_run(tmp_path, *, run_id: str = "run-detail"):
    run_dir = tmp_path / "fund_rotation" / run_id
    run_dir.mkdir(parents=True)
    state = {
        "schema_version": "2",
        "stage": "SUCCEEDED",
        "batch_id": "batch-1",
        "run_id": run_id,
        "variant_key": "variant-1",
        "strategy_id": "correlation_representative",
        "mode": "RESEARCH_ONLY",
        "quality_status": "VALID",
        "params_fingerprint": "config-hash",
    }
    (run_dir / "state.json").write_text(
        json.dumps(state),
        encoding="utf-8",
    )
    (run_dir / "events.jsonl").write_text(
        json.dumps(
            {
                "seq": 1,
                "event_type": "TERMINAL",
                "scope": "VARIANT",
                "stage": "SUCCEEDED",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    publisher = ArtifactPublisher(run_dir)
    publisher.publish(
        StrategyArtifact(
            role="resolved_spec",
            media_type="application/json",
            payload={
                "run_id": run_id,
                "batch_id": "batch-1",
                "variant_key": "variant-1",
                "strategy_id": "correlation_representative",
                "label": "基准参数",
                "mode": "RESEARCH_ONLY",
                "data_start": "20230101",
                "decision_start_date": "20231229",
                "anchor_decision_date": "20231229",
                "evaluation_start_date": "20240102",
                "evaluation_end_date": "20241231",
                "resolved_config": {"top_n": 3},
                "resolved_config_hash": "config-hash",
                "resolved_requirements_hash": "requirements-hash",
                "strategy_implementation_hash": "strategy-hash",
                "framework_implementation_hash": "framework-hash",
                "data_snapshot_fingerprint": "snapshot-hash",
                "run_identity_hash": "run-identity-hash",
            },
        )
    )
    publisher.publish(
        StrategyArtifact(
            role="summary",
            media_type="application/json",
            payload={
                "status": "SUCCEEDED",
                "quality_status": "VALID",
                "partial": False,
                "publishable_for_comparison": True,
            },
        )
    )
    publisher.publish(
        StrategyArtifact(
            role="metrics",
            media_type="application/json",
            payload={
                "strategy": {
                    "total_return": 0.12,
                    "annual_return": 0.1,
                    "sharpe": 1.2,
                }
            },
        )
    )
    publisher.publish(
        StrategyArtifact(
            role="targets",
            media_type="text/csv",
            payload=[
                {
                    "week_ending": "20240105",
                    "ts_code": "510300.SH",
                    "weight": 0.5,
                },
                {
                    "week_ending": "20240105",
                    "ts_code": "159915.SZ",
                    "weight": 0.5,
                },
            ],
        )
    )
    publisher.publish(
        StrategyArtifact(
            role="orders",
            media_type="text/csv",
            payload=[
                {
                    "trade_date": "20240108",
                    "ts_code": "510300.SH",
                    "direction": "BUY",
                }
            ],
        )
    )
    publisher.publish(
        StrategyArtifact(
            role="fills",
            media_type="text/csv",
            payload=[
                {
                    "trade_date": "20240108",
                    "ts_code": "510300.SH",
                    "action": "BUY",
                    "filled": 100,
                    "price": 3.5,
                }
            ],
        )
    )
    publisher.publish(
        StrategyArtifact(
            role="positions",
            media_type="text/csv",
            payload=[
                {
                    "trade_date": "20240108",
                    "ts_code": "510300.SH",
                    "quantity": 100,
                    "market_value": 350.0,
                }
            ],
        )
    )
    publisher.publish(
        StrategyArtifact(
            role="equity",
            media_type="text/csv",
            payload=[
                {"date": "20240108", "strategy": 1.0},
                {"date": "20240109", "strategy": 1.01},
            ],
        )
    )
    publisher.publish(
        StrategyArtifact(
            role="exclusions",
            media_type="application/json",
            payload=[],
        ),
        producer="correlation_representative",
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
            "partial": False,
            "publishable_for_comparison": True,
            "params_fingerprint": "config-hash",
            "framework_implementation_hash": "framework-hash",
            "strategy_implementation_hash": "strategy-hash",
            "data_snapshot_fingerprint": "snapshot-hash",
            "run_identity_hash": "run-identity-hash",
            "state_checksum": compute_file_checksum(run_dir / "state.json"),
            "file_details": file_details,
        },
    )
    return run_dir


def test_v2_detail_returns_published_metrics_identity_and_instruments(tmp_path):
    _publish_v2_run(tmp_path)
    client = TestClient(_app(tmp_path))

    response = client.get("/stockpred/fund-rotation/backtests/run-detail")

    assert response.status_code == 200
    body = response.json()
    assert body["result_published"] is True
    assert body["variant_key"] == "variant-1"
    assert body["period"]["evaluation_start_date"] == "20240102"
    assert body["identity"]["run_identity_hash"] == "run-identity-hash"
    assert body["metrics"]["sharpe"] == 1.2
    assert body["resolved_config"] == {"top_n": 3}
    assert [item["ts_code"] for item in body["instruments"]] == [
        "510300.SH",
        "159915.SZ",
    ]
    first = body["instruments"][0]
    assert first == {
        "ts_code": "510300.SH",
        "has_signal": True,
        "has_order": True,
        "has_trade": True,
        "has_position": True,
    }
    strategy_artifact = next(
        item for item in body["artifacts"] if item["role"] == "exclusions"
    )
    assert strategy_artifact["producer"] == "correlation_representative"


def test_v2_detail_without_manifest_exposes_only_lifecycle_facts(tmp_path):
    run_id = "run-interrupted"
    run_dir = tmp_path / "fund_rotation" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(
        json.dumps(
            {
                "schema_version": "2",
                "stage": "FAILED_INTERRUPTED",
                "batch_id": "batch-1",
                "run_id": run_id,
                "variant_key": "variant-2",
                "strategy_id": "correlation_all_members",
                "mode": "RESEARCH_ONLY",
                "error": "publication failed",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "events.jsonl").write_text(
        json.dumps(
            {
                "seq": 9,
                "event_type": "TERMINAL",
                "scope": "VARIANT",
                "stage": "FAILED_INTERRUPTED",
                "error": "publication failed",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    # An unvalidated file must never leak through the state-only response.
    (run_dir / "metrics.json").write_text(
        json.dumps({"strategy": {"sharpe": 99}}),
        encoding="utf-8",
    )

    client = TestClient(_app(tmp_path))
    response = client.get(f"/stockpred/fund-rotation/backtests/{run_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "FAILED_INTERRUPTED"
    assert body["result_published"] is False
    assert body["error"] == "publication failed"
    assert body["metrics"] == {}
    assert body["artifacts"] == []
    assert body["events"][0]["seq"] == 9
