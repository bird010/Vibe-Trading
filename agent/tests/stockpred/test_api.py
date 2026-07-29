from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import stockpred_routes


async def _auth() -> None:
    return None


@pytest.fixture
def api(tmp_path: Path) -> tuple[TestClient, Path]:
    app = FastAPI()
    stockpred_routes.register_stockpred_routes(
        app,
        runs_dir=tmp_path,
        require_auth=_auth,
        require_event_stream_auth=_auth,
    )
    return TestClient(app), tmp_path


def _seed_run(
    root: Path,
    run_id: str,
    *,
    strategy_type: str = "stockpred_graph",
    status: str = "success",
) -> Path:
    run_dir = root / run_id
    run_dir.mkdir()
    (run_dir / "req.json").write_text(
        json.dumps(
            {
                "context": {
                    "strategy_type": strategy_type,
                    "start_date": "20250101",
                    "end_date": "20250331",
                    "mode": "parity",
                }
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "state.json").write_text(
        json.dumps(
            {
                "status": status,
                "phase": "SUCCEEDED" if status == "success" else "FAILED",
                "created_at": "2025-04-01T00:00:00Z",
                "updated_at": "2025-04-01T00:01:00Z",
                "progress": {"done": 12, "total": 12, "eval_date": "20250328"},
            }
        ),
        encoding="utf-8",
    )
    return run_dir


def _seed_cohort_artifacts(root: Path, run_id: str, *, with_chart: bool = False) -> Path:
    run_dir = _seed_run(root, run_id)
    version_dir = run_dir / "artifacts_versions" / "staging"
    version_dir.mkdir(parents=True)
    (version_dir / "aggregate_metrics.json").write_text(json.dumps({"value": float("nan")}), encoding="utf-8")
    (version_dir / "quality_report.json").write_text(json.dumps({"nested": {"value": float("inf")}}), encoding="utf-8")
    (version_dir / "cohort_returns.csv").write_text("cohort_id,committed_capital_return\nc1,nan\n", encoding="utf-8")
    (version_dir / "period_breakdown.csv").write_text("period,count,mean_return,win_rate\n2025,1,0.1,1\n2025Q1,1,0.1,1\n", encoding="utf-8")
    entries: list[dict[str, object]] = []
    if with_chart:
        import pandas as pd

        chart_path = version_dir / "chart.parquet"
        pd.DataFrame(
            [{"trade_date": "20250101", "open": 1, "high": 1, "low": 1, "close": 1, "vol": 1}]
        ).to_parquet(chart_path, index=False)
        chart_payload = chart_path.read_bytes()
        (version_dir / "cohort_orders.csv").write_text(
            "code,trade_date,side\n000001.SZ,20250101,buy\n", encoding="utf-8"
        )
        entries.append(
            {
                "code": "000001.SZ",
                "relative_path": "chart.parquet",
                "sha256": hashlib.sha256(chart_payload).hexdigest(),
                "byte_size": len(chart_payload),
                "row_count": 1,
                "columns": ["trade_date", "open", "high", "low", "close", "vol"],
                "start_date": "20250101",
                "end_date": "20250101",
            }
        )
    manifest_path = version_dir / "chart_bundle_manifest.json"
    manifest_path.write_text(json.dumps({"version": 1, "entries": entries}), encoding="utf-8")
    files = []
    for path in sorted(version_dir.rglob("*")):
        if path.is_file() and path != manifest_path:
            payload = path.read_bytes()
            files.append({"relative_path": path.relative_to(version_dir).as_posix(), "sha256": hashlib.sha256(payload).hexdigest(), "byte_size": len(payload)})
    manifest_path.write_text(json.dumps({"version": 1, "entries": entries, "files": files}), encoding="utf-8")
    hasher = hashlib.sha256()
    for path in sorted(version_dir.rglob("*")):
        if path.is_file():
            hasher.update(str(path.relative_to(version_dir)).encode("utf-8"))
            hasher.update(path.read_bytes())
    version_id = hasher.hexdigest()[:32]
    final_dir = version_dir.with_name(version_id)
    version_dir.rename(final_dir)
    manifest_path = final_dir / "chart_bundle_manifest.json"
    (run_dir / "artifacts_current.json").write_text(
        json.dumps(
            {
                "version_id": version_id,
                "schema_version": "signal_cohort_v1",
                "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    return final_dir


def test_chart_json_sanitizes_nested_non_finite_values(api: tuple[TestClient, Path]) -> None:
    client, runs = api
    _seed_cohort_artifacts(runs, "cohort_nan")

    response = client.get("/stockpred/runs/cohort_nan/cohort/quality")

    assert response.status_code == 200
    assert response.json() == {"nested": {"value": None}}


def test_symbols_and_period_breakdown_endpoints(api: tuple[TestClient, Path]) -> None:
    client, runs = api
    _seed_cohort_artifacts(runs, "cohort_symbols")

    symbols = client.get("/stockpred/runs/cohort_symbols/cohort/symbols")
    periods = client.get("/stockpred/runs/cohort_symbols/cohort/period-breakdown")

    assert symbols.status_code == 200
    assert symbols.json() == {"symbols": []}
    assert periods.status_code == 200
    assert periods.json()[1]["period"] == "2025Q1"


def test_chart_endpoint_rejects_path_outside_version_dir(api: tuple[TestClient, Path]) -> None:
    client, runs = api
    version_dir = _seed_cohort_artifacts(runs, "cohort_escape")
    run_dir = runs / "cohort_escape"
    manifest_path = version_dir / "chart_bundle_manifest.json"
    manifest_path.write_text(
        json.dumps({"version": 1, "entries": [{"code": "000001.SZ", "relative_path": "../secret.parquet", "sha256": "a" * 64, "byte_size": 1, "row_count": 1, "columns": [], "start_date": "20250101", "end_date": "20250101"}]}),
        encoding="utf-8",
    )
    pointer_path = run_dir / "artifacts_current.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    pointer["manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")

    assert client.get("/stockpred/runs/cohort_escape/cohort/chart/000001.SZ").status_code == 404


def test_chart_endpoint_rejects_sha256_mismatch(api: tuple[TestClient, Path]) -> None:
    import pandas as pd

    client, runs = api
    version_dir = _seed_cohort_artifacts(runs, "cohort_hash")
    run_dir = runs / "cohort_hash"
    chart_path = version_dir / "chart.parquet"
    pd.DataFrame([{"trade_date": "20250101", "open": 1, "high": 1, "low": 1, "close": 1, "vol": 1}]).to_parquet(chart_path, index=False)
    manifest_path = version_dir / "chart_bundle_manifest.json"
    manifest_path.write_text(
        json.dumps({"version": 1, "entries": [{"code": "000001.SZ", "relative_path": "chart.parquet", "sha256": "a" * 64, "byte_size": chart_path.stat().st_size, "row_count": 1, "columns": ["trade_date"], "start_date": "20250101", "end_date": "20250101"}]}),
        encoding="utf-8",
    )
    pointer_path = run_dir / "artifacts_current.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    pointer["manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")

    assert client.get("/stockpred/runs/cohort_hash/cohort/chart/000001.SZ").status_code == 404


@pytest.mark.parametrize(
    ("filename", "endpoint"),
    [
        ("aggregate_metrics.json", "/cohort/metrics"),
        ("cohort_returns.csv", "/cohort/returns"),
    ],
)
def test_cohort_endpoint_rejects_tampered_version_snapshot(
    api: tuple[TestClient, Path], filename: str, endpoint: str
) -> None:
    client, runs = api
    version_dir = _seed_cohort_artifacts(runs, "cohort_snapshot")
    (version_dir / filename).write_bytes(b"tampered")

    response = client.get(f"/stockpred/runs/cohort_snapshot{endpoint}")

    assert response.status_code == 404


def test_chart_endpoint_rejects_tampered_orders_from_valid_chart(api: tuple[TestClient, Path]) -> None:
    client, runs = api
    version_dir = _seed_cohort_artifacts(runs, "cohort_orders", with_chart=True)

    assert client.get("/stockpred/runs/cohort_orders/cohort/chart/000001.SZ").status_code == 200
    (version_dir / "cohort_orders.csv").write_bytes(b"tampered")

    assert client.get("/stockpred/runs/cohort_orders/cohort/chart/000001.SZ").status_code == 404


def test_status_reports_contract_failure_without_starting_job(
    api: tuple[TestClient, Path],
    monkeypatch,
) -> None:
    client, _ = api
    monkeypatch.setattr(
        stockpred_routes,
        "probe_stockpred_status",
        lambda: {
            "ready": False,
            "contract": "stockpred-data/v1",
            "tables": [],
            "error_code": "STOCKPRED_ROOT_MISSING",
            "message": "data root is not configured",
        },
    )

    response = client.get("/stockpred/status")

    assert response.status_code == 200
    assert response.json()["ready"] is False


def test_defaults_lock_parity_fields(api: tuple[TestClient, Path]) -> None:
    client, _ = api

    body = client.get("/stockpred/graph/defaults").json()

    assert body["top_n"] == 50
    assert set(body["locked_fields"]) >= {
        "top_n",
        "eval_step",
        "forward_days",
        "benchmark_code",
    }


def test_create_backtest_returns_run_and_event_url(
    api: tuple[TestClient, Path],
    monkeypatch,
) -> None:
    client, _ = api

    class FakeService:
        def reserve(self, config) -> str:  # noqa: ANN001
            assert config.start == "20250101"
            return "graph_123"

        def execute(self, run_id: str) -> str:
            return run_id

    monkeypatch.setattr(stockpred_routes, "build_service", lambda *_: FakeService())

    response = client.post(
        "/stockpred/graph/backtests",
        json={"start": "2025-01-01", "end": "2025-03-31", "mode": "parity"},
    )

    assert response.status_code == 202
    assert response.json() == {
        "run_id": "graph_123",
        "events_url": "/stockpred/graph/backtests/graph_123/events",
    }


def test_event_stream_reads_terminal_state_from_disk(
    api: tuple[TestClient, Path],
) -> None:
    client, runs = api
    _seed_run(runs, "graph_123")

    response = client.get("/stockpred/graph/backtests/graph_123/events")

    assert response.status_code == 200
    assert "event: progress" in response.text
    assert "event: done" in response.text


def test_list_backtests_returns_only_graph_runs(
    api: tuple[TestClient, Path],
) -> None:
    client, runs = api
    _seed_run(runs, "graph_123")
    _seed_run(runs, "normal_123", strategy_type="generated_strategy")

    response = client.get("/stockpred/graph/backtests?limit=20")

    assert response.status_code == 200
    assert [row["run_id"] for row in response.json()] == ["graph_123"]
