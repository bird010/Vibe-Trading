"""Phase 4 Task 7 — batch performance baseline tests (§25.1).

Verifies bounded single-worker execution and throughput with multiple
strategies. Full Lance-data benchmarks are deferred to manual profiling.
"""

from __future__ import annotations

import json
import time

import pytest

from src.stockpred.fund_rotation.batch_service import BatchService

from tests.fund_rotation.test_batch_service import (
    CALENDAR,
    FakeBatchStrategy,
    _calendar_metadata,
    _frames_loader,
    _request,
    _resolved,
    _state,
)


def _service(tmp_path):
    from backtest.fund_rotation.catalog import FundRotationStrategyCatalog

    catalog = FundRotationStrategyCatalog([FakeBatchStrategy])
    return BatchService(
        tmp_path,
        catalog=catalog,
        metadata_loader=_calendar_metadata,
        frames_loader=_frames_loader,
        auto_start=False,
    )


class TestBoundedParallelism:
    def test_max_workers_is_one(self, tmp_path):
        service = _service(tmp_path)
        assert service.executor._max_workers == 1

    def test_single_variant_executes(self, tmp_path):
        service = _service(tmp_path)
        request = _request(
            [{"strategy_id": "fake_batch", "params": {"lookback_days": 30}}],
        )
        outcome = service.submit_batch(request)
        service.run_batch_sync(outcome["batch_id"])
        assert _state(service, outcome["batch_id"])["stage"] == "SUCCEEDED"

    def test_three_variants_execute_sequentially(self, tmp_path):
        service = _service(tmp_path)
        request = _request([
            {"strategy_id": "fake_batch", "params": {"lookback_days": 30}},
            {"strategy_id": "fake_batch", "params": {"lookback_days": 35}},
            {"strategy_id": "fake_batch", "params": {"lookback_days": 40}},
        ])
        outcome = service.submit_batch(request)
        t0 = time.perf_counter()
        service.run_batch_sync(outcome["batch_id"])
        elapsed = time.perf_counter() - t0

        state = _state(service, outcome["batch_id"])
        assert state["stage"] == "SUCCEEDED"
        resolved = _resolved(service, outcome["batch_id"])
        assert len(resolved["executed_order"]) == 3
        # Sequential execution: each variant should have its own run dir.
        batch_dir = service.persistence.batch_dir(outcome["batch_id"])
        runs_dir = batch_dir / "runs"
        assert runs_dir.exists()
        assert len(list(runs_dir.iterdir())) == 3

        # Performance baseline: 3 synthetic variants should complete quickly.
        assert elapsed < 30, f"3 variants took {elapsed:.1f}s, expected < 30s"

    def test_ten_variants_complete_without_errors(self, tmp_path):
        service = _service(tmp_path)
        request = _request([
            {"strategy_id": "fake_batch", "params": {"lookback_days": 25 + i % 10}}
            for i in range(10)
        ])
        outcome = service.submit_batch(request)
        t0 = time.perf_counter()
        service.run_batch_sync(outcome["batch_id"])
        elapsed = time.perf_counter() - t0

        state = _state(service, outcome["batch_id"])
        assert state["stage"] == "SUCCEEDED"
        resolved = _resolved(service, outcome["batch_id"])
        assert len(resolved["executed_order"]) == 10

        # Each variant has an independent run directory.
        batch_dir = service.persistence.batch_dir(outcome["batch_id"])
        runs_dir = batch_dir / "runs"
        assert len(list(runs_dir.iterdir())) == 10

        # Comparison artifacts for 10 variants.
        reports = json.loads(
            (batch_dir / "reports.json").read_text(encoding="utf-8"),
        )
        assert len(reports["ranking"]) == 10

        # Performance baseline: 10 synthetic variants should be well under 2 min.
        assert elapsed < 120, f"10 variants took {elapsed:.1f}s, expected < 120s"
