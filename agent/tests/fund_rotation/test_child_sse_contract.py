"""Child-run SSE must replay persisted lifecycle facts without synthesis."""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.fund_rotation_routes import register_fund_rotation_routes
from src.stockpred.fund_rotation.persistence import BatchEventLog, atomic_write_json


def _client(tmp_path, *, run_id: str, stage: str):
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "fund_rotation" / run_id
    run_dir.mkdir(parents=True)
    atomic_write_json(
        run_dir / "state.json",
        {
            "schema_version": "2",
            "stage": stage,
            "batch_id": "batch-1",
            "run_id": run_id,
            "variant_key": "fake@one",
            "strategy_id": "fake",
            "mode": "RESEARCH_ONLY",
        },
    )
    app = FastAPI()
    register_fund_rotation_routes(
        app,
        runs_dir,
        lambda: None,
        lambda: None,
    )
    return TestClient(app), run_dir


def _data_rows(response_text: str) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in response_text.splitlines()
        if line.startswith("data: ")
    ]


def test_child_sse_never_synthesizes_a_missing_terminal_event(tmp_path):
    client, run_dir = _client(
        tmp_path,
        run_id="missing-terminal",
        stage="SUCCEEDED",
    )
    persisted = BatchEventLog(run_dir, batch_id="batch-1").append(
        event_type="VARIANT_STAGE",
        scope="VARIANT",
        run_id="missing-terminal",
        variant_key="fake@one",
        strategy_id="fake",
        stage="WRITING_RESULTS",
    )

    response = client.get(
        "/stockpred/fund-rotation/backtests/missing-terminal/events"
    )

    assert response.status_code == 200
    assert response.text.count("event: done") == 0
    assert _data_rows(response.text) == [persisted]


def test_child_sse_emits_persisted_terminal_once_as_done(tmp_path):
    client, run_dir = _client(
        tmp_path,
        run_id="success-terminal",
        stage="SUCCEEDED",
    )
    persisted = BatchEventLog(run_dir, batch_id="batch-1").append(
        event_type="TERMINAL",
        scope="VARIANT",
        run_id="success-terminal",
        variant_key="fake@one",
        strategy_id="fake",
        message="SUCCEEDED",
    )

    response = client.get(
        "/stockpred/fund-rotation/backtests/success-terminal/events"
    )

    assert response.status_code == 200
    assert response.text.count("event: done") == 1
    assert _data_rows(response.text) == [persisted]


def test_child_sse_emits_persisted_canceled_terminal_as_done(tmp_path):
    client, run_dir = _client(
        tmp_path,
        run_id="canceled-terminal",
        stage="CANCELED",
    )
    persisted = BatchEventLog(run_dir, batch_id="batch-1").append(
        event_type="TERMINAL",
        scope="VARIANT",
        run_id="canceled-terminal",
        variant_key="fake@one",
        strategy_id="fake",
        message="CANCELED",
    )

    response = client.get(
        "/stockpred/fund-rotation/backtests/canceled-terminal/events"
    )

    assert response.status_code == 200
    assert response.text.count("event: done") == 1
    assert _data_rows(response.text) == [persisted]


def test_child_sse_query_cursor_deduplicates_terminal_replay(tmp_path):
    client, run_dir = _client(
        tmp_path,
        run_id="cursor-terminal",
        stage="CANCELED",
    )
    persisted = BatchEventLog(run_dir, batch_id="batch-1").append(
        event_type="TERMINAL",
        scope="VARIANT",
        run_id="cursor-terminal",
        variant_key="fake@one",
        strategy_id="fake",
        stage="CANCELED",
        message="CANCELED",
    )

    response = client.get(
        "/stockpred/fund-rotation/backtests/cursor-terminal/events",
        params={"last_event_id": persisted["seq"]},
    )

    assert response.status_code == 200
    assert _data_rows(response.text) == []
    assert "event: done" not in response.text
