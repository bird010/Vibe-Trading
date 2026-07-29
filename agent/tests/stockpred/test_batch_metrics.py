from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from backtest.stockpred_strategy.artifacts import write_screening_artifacts
from backtest.stockpred_strategy.runner import StrategyBacktestResult
from src.stockpred.batch_metrics import PhaseTimer, write_phase_metrics
from src.stockpred.batch_data import BatchDataContext
from src.stockpred.contracts import DataSnapshotManifest, ModelSnapshot
from src.stockpred.run_store import atomic_json
from agent.tests.stockpred.test_strategy_runner import _config


def test_phase_timer_serializes_durations_and_artifact_budget(tmp_path) -> None:
    timer = PhaseTimer(clock=iter((1.0, 1.25)).__next__)
    with timer.phase("execution"):
        pass
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    for index in range(10):
        (artifacts / f"report_{index}.json").write_text("{}", encoding="utf-8")

    payload = write_phase_metrics(tmp_path, timer)

    assert payload["timings"]["execution"] == 0.25
    assert payload["artifacts"] == {"files": 10, "bytes": 20}
    assert json.loads((tmp_path / "phase_metrics.json").read_text(encoding="utf-8")) == payload


def test_phase_metrics_keep_batch_shared_reads_out_of_strategy_values(tmp_path) -> None:
    payload = write_phase_metrics(
        tmp_path,
        PhaseTimer(),
        batch_shared={
            "timings": {"data_load": 0.5, "execution": 1.5},
            "cache": {"hits": 3, "misses": 2},
            "rows_read": 42,
        },
    )

    assert payload["cache"] == {"hits": 0, "misses": 0}
    assert payload["rows_read"] == 0
    assert payload["batch_shared"] == {
        "timings": {"data_load": 0.5, "execution": 1.5},
        "cache": {"hits": 3, "misses": 2},
        "rows_read": 42,
    }


def test_batch_data_context_counts_static_and_panel_cache_hits(monkeypatch) -> None:
    context = BatchDataContext(object(), "snapshot")
    monkeypatch.setattr(context, "_static_inputs_unlocked", lambda: SimpleNamespace(trade_dates=[], stock_dimension=None, name_history=None, industry_history=None))
    monkeypatch.setattr("src.stockpred.batch_data.build_panel_from_inputs", lambda *_args, **_kwargs: {})

    context.panel("20250102", 20)
    context.panel("20250102", 20)

    assert context.cache_metrics() == {"cache_hits": 1, "cache_misses": 1}


def test_offline_benchmark_writes_ten_real_screening_artifact_shapes(tmp_path) -> None:
    manifest = DataSnapshotManifest(as_of="2025-01-07T15:00:00+08:00", tables={}, model=ModelSnapshot(id="stockpred", version="v1", config_sha256="0" * 64))
    reports = []
    phase_metrics_by_report = []
    for index in range(10):
        run_dir = tmp_path / f"strategy_{index}"
        result = StrategyBacktestResult(
            strategy_id=f"alpha101_{index}", eval_dates=["20250102"],
            signals=pd.DataFrame({"ts_code": ["000001.SZ"], "score": [1.0]}),
            selected=pd.DataFrame({"ts_code": ["000001.SZ"]}), trades=pd.DataFrame(), positions=pd.DataFrame(),
            equity=pd.DataFrame({"time": ["2025-01-02"], "nav": [100.0]}), metrics={"sharpe": 1.0},
        )
        write_screening_artifacts(run_dir, result, manifest, _config())
        artifacts = list((run_dir / "artifacts").iterdir())
        assert len(artifacts) <= 20
        assert not list((run_dir / "artifacts").glob("ohlcv_*.csv"))
        phase_metrics = write_phase_metrics(run_dir, PhaseTimer())
        reports.append({"strategy_id": result.strategy_id, **phase_metrics["artifacts"]})
        phase_metrics_by_report.append(phase_metrics)

    benchmark = {
        "reports": reports,
        "timings": {
            name: sum(item["timings"][name] for item in phase_metrics_by_report)
            for name in ("data_load", "panel_build", "factor_compute", "execution", "artifact_write")
        },
        "cache": {
            "hits": sum(item["cache"]["hits"] for item in phase_metrics_by_report),
            "misses": sum(item["cache"]["misses"] for item in phase_metrics_by_report),
        },
        "screening_artifacts": {
            "files": sum(item["files"] for item in reports),
            "bytes": sum(item["bytes"] for item in reports),
        },
        "equivalent": all(
            item["artifacts"]["files"] <= 20
            for item in phase_metrics_by_report
        ),
    }
    benchmark_path = tmp_path / "benchmark.json"
    atomic_json(benchmark_path, benchmark)
    fixture = json.loads((Path(__file__).parent / "fixtures" / "strategy_batch_benchmark.json").read_text(encoding="utf-8"))

    assert len(benchmark["reports"]) == 10
    assert benchmark["screening_artifacts"]["files"] == sum(
        len(list((tmp_path / f"strategy_{index}" / "artifacts").iterdir())) for index in range(10)
    )
    assert benchmark["screening_artifacts"]["bytes"] == sum(
        path.stat().st_size for path in tmp_path.glob("strategy_*/artifacts/*") if path.is_file()
    )
    assert json.loads(benchmark_path.read_text(encoding="utf-8")) == benchmark
    assert benchmark == fixture
