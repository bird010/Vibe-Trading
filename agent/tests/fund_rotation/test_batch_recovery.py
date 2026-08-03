"""Phase 4 Task 7 — batch recovery tests (§26.1).

Covers FAILED_INTERRUPTED marking for batch and child runs, completed
artifacts remaining readable, and no automatic resume on restart.
"""

from __future__ import annotations

import json

import pytest

from src.stockpred.fund_rotation.batch_models import StrategyBatchRequest
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


def _service(tmp_path, auto_start=False):
    from backtest.fund_rotation.catalog import FundRotationStrategyCatalog

    catalog = FundRotationStrategyCatalog([FakeBatchStrategy])
    return BatchService(
        tmp_path,
        catalog=catalog,
        metadata_loader=_calendar_metadata,
        frames_loader=_frames_loader,
        auto_start=auto_start,
    )


class TestRecovery:
    def test_simulated_crash_marks_batch_failed_interrupted(self, tmp_path):
        service = _service(tmp_path)
        request = _request(
            [{"strategy_id": "fake_batch", "params": {"lookback_days": 30}}],
        )
        outcome = service.submit_batch(request)
        service.run_batch_sync(outcome["batch_id"])
        batch_dir = service.persistence.batch_dir(outcome["batch_id"])

        # Simulate a crash by rewriting state to a running stage.
        state_path = batch_dir / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["stage"] = "RUNNING_STRATEGIES"
        state_path.write_text(json.dumps(state), encoding="utf-8")

        # Fresh service detects and marks it.
        fresh = _service(tmp_path)
        recovered = fresh.recover_interrupted()
        assert outcome["batch_id"] in recovered
        new_state = json.loads(state_path.read_text(encoding="utf-8"))
        assert new_state["stage"] == "FAILED_INTERRUPTED"

    def test_completed_batch_left_untouched_by_recovery(self, tmp_path):
        service = _service(tmp_path)
        request = _request(
            [{"strategy_id": "fake_batch", "params": {"lookback_days": 30}}],
        )
        outcome = service.submit_batch(request)
        service.run_batch_sync(outcome["batch_id"])
        fresh = _service(tmp_path)
        recovered = fresh.recover_interrupted()
        assert outcome["batch_id"] not in recovered
        assert _state(service, outcome["batch_id"])["stage"] == "SUCCEEDED"

    def test_child_run_interrupted_state(self, tmp_path):
        """Child runs with non-terminal states are also marked FAILED_INTERRUPTED."""
        service = _service(tmp_path)
        request = _request([
            {"strategy_id": "fake_batch", "params": {"lookback_days": 30}},
            {"strategy_id": "fake_batch", "params": {"lookback_days": 40}},
        ])
        outcome = service.submit_batch(request)
        service.run_batch_sync(outcome["batch_id"])
        batch_dir = service.persistence.batch_dir(outcome["batch_id"])

        # Corrupt a child run state to a running stage.
        runs_dir = batch_dir / "runs"
        children = list(runs_dir.iterdir())
        assert len(children) == 2
        child_state_path = children[0] / "state.json"
        child_state = json.loads(child_state_path.read_text(encoding="utf-8"))
        child_state["stage"] = "GENERATING_SIGNALS"
        child_state_path.write_text(json.dumps(child_state), encoding="utf-8")

        fresh = _service(tmp_path)
        fresh.recover_interrupted()
        new_child = json.loads(child_state_path.read_text(encoding="utf-8"))
        assert new_child["stage"] == "FAILED_INTERRUPTED"

        # Already terminal child stays unchanged.
        other = json.loads((children[1] / "state.json").read_text(encoding="utf-8"))
        assert other["stage"] == "SUCCEEDED"

    def test_artifacts_remain_readable_after_recovery(self, tmp_path):
        service = _service(tmp_path)
        request = _request(
            [{"strategy_id": "fake_batch", "params": {"lookback_days": 30}}],
        )
        outcome = service.submit_batch(request)
        service.run_batch_sync(outcome["batch_id"])
        batch_dir = service.persistence.batch_dir(outcome["batch_id"])

        # Corrupt to running state.
        state_path = batch_dir / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["stage"] = "RUNNING_STRATEGIES"
        state_path.write_text(json.dumps(state), encoding="utf-8")

        fresh = _service(tmp_path)
        fresh.recover_interrupted()

        # Manifest and resolved artifacts must still be readable.
        manifest_path = batch_dir / "manifest.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["status"] == "SUCCEEDED"

        resolved_path = batch_dir / "resolved_batch.json"
        assert resolved_path.exists()
        resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
        assert len(resolved["variants"]) == 1

    def test_no_auto_resume_after_recovery(self, tmp_path):
        """Recovery never re-runs a batch; it only marks states."""
        service = _service(tmp_path)
        request = _request(
            [{"strategy_id": "fake_batch", "params": {"lookback_days": 30}}],
        )
        outcome = service.submit_batch(request)
        service.run_batch_sync(outcome["batch_id"])
        batch_dir = service.persistence.batch_dir(outcome["batch_id"])

        # Corrupt to running.
        state_path = batch_dir / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["stage"] = "RUNNING_STRATEGIES"
        state_path.write_text(json.dumps(state), encoding="utf-8")

        fresh = _service(tmp_path)
        recovered = fresh.recover_interrupted()
        assert outcome["batch_id"] in recovered

        # Batch should still be FAILED_INTERRUPTED — NOT re-run.
        final_state = json.loads(state_path.read_text(encoding="utf-8"))
        assert final_state["stage"] == "FAILED_INTERRUPTED"
        # Executed order should remain as-is from original run.
        resolved = _resolved(fresh, outcome["batch_id"])
        assert len(resolved["executed_order"]) == 1
