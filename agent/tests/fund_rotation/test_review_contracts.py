"""Small structural guards for publication and interface migration contracts."""

import inspect
import json

import pandas as pd
import pytest
from fastapi import FastAPI

from backtest.engines.base import BaseEngine
from backtest.fund_rotation import pipeline
from backtest.fund_rotation.metrics import compute_performance_metrics
from src.stockpred.fund_rotation import artifacts, service
from src.api.fund_rotation_routes import register_fund_rotation_routes


def test_legacy_direction_rebalance_interface_is_removed():
    assert not hasattr(BaseEngine, "_rebalance")
    assert "portfolio_mode" not in inspect.getsource(BaseEngine)


def _artifact_manifest(run_dir):
    return artifacts.write_run_artifacts(
        run_dir, weekly_targets={}, cluster_history=[], exclusions=[],
        strategy_cumulative=pd.Series(dtype=float),
        equal_weight_benchmark=pd.Series(dtype=float),
        buy_hold_benchmark=pd.Series(dtype=float), cash_benchmark=pd.Series(dtype=float),
        strategy_metrics={}, benchmark_metrics={}, config_params={},
        num_weeks=0, num_reclusters=0, num_etfs_used=0,
    )


def test_equity_artifact_cannot_reintroduce_dates_before_execution_interval(tmp_path, monkeypatch):
    common = pd.Index(["20240108", "20240109"], name="date")
    executed_metric_indexes = []

    def capture_executed_metrics(series, periods_per_year=52):
        executed_metric_indexes.append((series.index.copy(), periods_per_year))
        return {"annual_return": 0.0}

    monkeypatch.setattr(
        "backtest.fund_rotation.metrics.compute_performance_metrics",
        capture_executed_metrics,
    )
    theoretical = pd.Series(
        [0.95, 1.00, 1.01], index=["20240105", *common], name="theoretical_strategy",
    )
    artifacts.write_run_artifacts(
        tmp_path, weekly_targets={}, cluster_history=[], exclusions=[],
        strategy_cumulative=theoretical,
        equal_weight_benchmark=pd.Series([1.0, 1.01], index=common),
        buy_hold_benchmark=pd.Series([1.0, 0.99], index=common),
        cash_benchmark=pd.Series([1.0, 1.0], index=common),
        strategy_metrics={}, benchmark_metrics={}, config_params={},
        num_weeks=2, num_reclusters=0, num_etfs_used=1,
        executed_equity=pd.Series([1.0, 1.02], index=common),
    )

    equity = pd.read_csv(tmp_path / "equity.csv", dtype={"date": str})
    assert equity["date"].tolist() == list(common)
    assert equity["theoretical_strategy"].tolist() == [1.0, 1.01]
    assert len(executed_metric_indexes) == 1
    assert executed_metric_indexes[0][0].equals(common)
    assert executed_metric_indexes[0][1] == 244


def test_manifest_is_published_only_after_succeeded_state(tmp_path):
    manifest = _artifact_manifest(tmp_path)
    assert not (tmp_path / "manifest.json").exists()
    (tmp_path / "state.json").write_text(json.dumps({
        "stage": "WRITING_RESULTS", "run_id": "run-1", "params_fingerprint": "fp",
    }), encoding="utf-8")
    with pytest.raises(RuntimeError, match="before SUCCEEDED"):
        artifacts.publish_manifest(tmp_path, manifest, run_id="run-1", params_fingerprint="fp")
    assert not (tmp_path / "manifest.json").exists()

    (tmp_path / "state.json").write_text(json.dumps({
        "stage": "SUCCEEDED", "run_id": "run-1", "params_fingerprint": "fp",
    }), encoding="utf-8")
    published = artifacts.publish_manifest(
        tmp_path, manifest, run_id="run-1", params_fingerprint="fp",
    )
    assert published["publication_id"]
    assert (tmp_path / "manifest.json").exists()
    assert service.FundRotationBacktestService._is_published(
        tmp_path, {"stage": "SUCCEEDED", "run_id": "run-1", "params_fingerprint": "fp"},
    )
    (tmp_path / "state.json").write_text(json.dumps({
        "stage": "FAILED", "run_id": "run-1", "params_fingerprint": "fp",
    }), encoding="utf-8")
    assert not service.FundRotationBacktestService._is_published(
        tmp_path, {"stage": "FAILED", "run_id": "run-1", "params_fingerprint": "fp"},
    )


def test_manifest_write_fault_never_exposes_success_manifest(tmp_path, monkeypatch):
    manifest = _artifact_manifest(tmp_path)
    (tmp_path / "state.json").write_text(json.dumps({
        "stage": "SUCCEEDED", "run_id": "run-1", "params_fingerprint": "fp",
    }), encoding="utf-8")
    real_write = artifacts.atomic_write_json

    def fail_manifest(path, value):
        if path.name == "manifest.json":
            raise OSError("injected publication failure")
        return real_write(path, value)

    monkeypatch.setattr(artifacts, "atomic_write_json", fail_manifest)
    with pytest.raises(OSError, match="injected"):
        artifacts.publish_manifest(tmp_path, manifest, run_id="run-1", params_fingerprint="fp")
    assert not (tmp_path / "manifest.json").exists()


@pytest.mark.asyncio
async def test_sse_synthesizes_done_when_success_event_append_was_lost(tmp_path):
    run_id = "run-sse"
    run_dir = tmp_path / "fund_rotation" / run_id
    run_dir.mkdir(parents=True)
    state = {"stage": "SUCCEEDED", "run_id": run_id, "params_fingerprint": "fp"}
    (run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    artifacts.publish_manifest(
        run_dir,
        {
            "schema_version": "v1", "status": "SUCCEEDED",
            "publication_id": "pub-1", "files": [], "file_details": {},
        },
        run_id=run_id,
        params_fingerprint="fp",
        terminal_event_seq=7,
    )
    # Deliberately no SUCCEEDED record in events.jsonl: this is the injected
    # append failure after successful manifest publication.
    app = FastAPI()
    register_fund_rotation_routes(app, tmp_path, lambda: None, lambda: None)
    endpoint = next(
        route.endpoint for route in app.routes
        if getattr(route, "path", "").endswith("/{run_id}/events")
    )

    class RequestStub:
        headers = {}

        async def is_disconnected(self):
            return False

    response = await endpoint(run_id, RequestStub())
    chunk = await anext(response.body_iterator)
    assert "event: done" in chunk
    assert '"stage": "SUCCEEDED"' in chunk
    assert '"source": "state_manifest"' in chunk


def test_invalid_state_transition_is_not_silently_swallowed():
    callback_source = inspect.getsource(service.FundRotationBacktestService._execute_run)
    assert "Skip invalid transitions" not in callback_source


def test_only_one_etf_capacity_execution_implementation_exists():
    assert inspect.getsource(pipeline).count("def _execute_with_capacity(") == 1


def _spy_pct_change(monkeypatch):
    """Record fill_method kwarg of every Series/DataFrame pct_change call."""
    calls: list = []
    orig_series = pd.Series.pct_change
    orig_df = pd.DataFrame.pct_change

    def series_spy(self, *args, **kwargs):
        calls.append(kwargs.get("fill_method", "ABSENT"))
        return orig_series(self, *args, **kwargs)

    def df_spy(self, *args, **kwargs):
        calls.append(kwargs.get("fill_method", "ABSENT"))
        return orig_df(self, *args, **kwargs)

    monkeypatch.setattr(pd.Series, "pct_change", series_spy)
    monkeypatch.setattr(pd.DataFrame, "pct_change", df_spy)
    return calls


def test_metrics_pct_change_uses_fill_method_none(monkeypatch):
    """§6/§32.1 — metrics must not forward-fill missing values before differencing."""
    calls = _spy_pct_change(monkeypatch)
    cumulative = pd.Series(
        [1.0, 1.01, 1.02, 1.015, 1.03, 1.04],
        index=["20240101", "20240102", "20240103", "20240104", "20240105", "20240108"],
    )
    compute_performance_metrics(cumulative, periods_per_year=244)
    assert calls, "pct_change was not called by compute_performance_metrics"
    assert all(fm is None for fm in calls), (
        f"metrics pct_change must pass fill_method=None, got {calls}"
    )
