"""Tests for persistence and state machine — §15.2, §16."""

import json
import pytest
from pathlib import Path

from src.stockpred.fund_rotation.persistence import (
    atomic_write_json,
    append_jsonl,
    deterministic_run_id,
    request_fingerprint,
    RunDirectory,
    IdempotencyGuard,
)
from src.stockpred.fund_rotation.state_machine import (
    TaskStage,
    TaskStateMachine,
    InvalidTransitionError,
)


class TestAtomicWrite:
    """§16.2 — atomic JSON write via tmp+rename."""

    def test_creates_file(self, tmp_path):
        p = tmp_path / "test.json"
        atomic_write_json(p, {"key": "value"})
        assert p.exists()
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data == {"key": "value"}

    def test_no_tmp_left(self, tmp_path):
        p = tmp_path / "test.json"
        atomic_write_json(p, {"a": 1})
        assert not (tmp_path / "test.tmp").exists()

    def test_overwrite(self, tmp_path):
        p = tmp_path / "test.json"
        atomic_write_json(p, {"v": 1})
        atomic_write_json(p, {"v": 2})
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["v"] == 2


class TestAppendJsonl:
    """§16.1 — events.jsonl is append-only."""

    def test_appends_lines(self, tmp_path):
        p = tmp_path / "events.jsonl"
        append_jsonl(p, {"seq": 1, "msg": "start"})
        append_jsonl(p, {"seq": 2, "msg": "done"})
        lines = p.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["seq"] == 1
        assert json.loads(lines[1])["seq"] == 2


class TestDeterministicRunId:
    """§16.2 — same key -> same run_id."""

    def test_same_key_same_id(self):
        assert deterministic_run_id("abc") == deterministic_run_id("abc")

    def test_different_key_different_id(self):
        assert deterministic_run_id("abc") != deterministic_run_id("def")

    def test_length(self):
        assert len(deterministic_run_id("test")) == 16


class TestRequestFingerprint:
    """§16.2 — stable fingerprint regardless of key order."""

    def test_order_independent(self):
        fp1 = request_fingerprint({"a": 1, "b": 2})
        fp2 = request_fingerprint({"b": 2, "a": 1})
        assert fp1 == fp2

    def test_different_params_different_fp(self):
        fp1 = request_fingerprint({"a": 1})
        fp2 = request_fingerprint({"a": 2})
        assert fp1 != fp2


class TestRunDirectory:
    """§16 — run directory management."""

    def test_ensure_creates_dir(self, tmp_path):
        rd = RunDirectory(tmp_path, "run123")
        rd.ensure()
        assert rd.path.exists()
        assert rd.path == tmp_path / "fund_rotation" / "run123"

    def test_write_and_read_state(self, tmp_path):
        rd = RunDirectory(tmp_path, "run123")
        rd.ensure()
        rd.write_state({"stage": "QUEUED", "progress": 0})
        state = rd.read_state()
        assert state["stage"] == "QUEUED"

    def test_manifest_lifecycle(self, tmp_path):
        rd = RunDirectory(tmp_path, "run123")
        rd.ensure()
        assert not rd.has_manifest()
        rd.write_manifest({"files": [], "status": "SUCCEEDED"})
        assert rd.has_manifest()


class TestIdempotencyGuard:
    """§16.2 — same key+params reuse; different params -> 409."""

    def test_new_key_returns_none_status(self, tmp_path):
        guard = IdempotencyGuard(tmp_path)
        run_id, status = guard.check("key1", {"a": 1})
        assert status is None
        assert len(run_id) == 16

    def test_same_key_same_params_reuses(self, tmp_path):
        guard = IdempotencyGuard(tmp_path)
        run_id, _ = guard.check("key1", {"a": 1})
        # Simulate existing run
        rd = RunDirectory(tmp_path, run_id)
        rd.ensure()
        rd.write_request({"a": 1})
        rd.write_state({"stage": "SUCCEEDED"})
        # Same key+params
        run_id2, status = guard.check("key1", {"a": 1})
        assert run_id2 == run_id
        assert status == "SUCCEEDED"

    def test_same_key_different_params_raises(self, tmp_path):
        guard = IdempotencyGuard(tmp_path)
        run_id, _ = guard.check("key1", {"a": 1})
        rd = RunDirectory(tmp_path, run_id)
        rd.ensure()
        rd.write_request({"a": 1})
        with pytest.raises(ValueError, match="[Cc]onflict"):
            guard.check("key1", {"a": 999})


class TestTaskStateMachine:
    """§15.2 — valid transitions and terminal states."""

    def test_happy_path(self):
        sm = TaskStateMachine()
        assert sm.stage == TaskStage.QUEUED
        sm.transition(TaskStage.VALIDATING_DATA)
        sm.transition(TaskStage.PREPARING_RETURNS)
        sm.transition(TaskStage.CLUSTERING)
        sm.transition(TaskStage.GENERATING_TARGETS)
        sm.transition(TaskStage.EXECUTING)
        sm.transition(TaskStage.COMPUTING_BENCHMARKS)
        sm.transition(TaskStage.WRITING_RESULTS)
        sm.transition(TaskStage.SUCCEEDED)
        assert sm.is_terminal

    def test_invalid_transition_raises(self):
        sm = TaskStateMachine()
        with pytest.raises(InvalidTransitionError):
            sm.transition(TaskStage.EXECUTING)  # Can't skip stages

    def test_fail_from_any_running_stage(self):
        sm = TaskStateMachine()
        sm.transition(TaskStage.VALIDATING_DATA)
        sm.transition(TaskStage.FAILED)
        assert sm.stage == TaskStage.FAILED
        assert sm.is_terminal

    def test_no_transition_from_terminal(self):
        sm = TaskStateMachine()
        sm.transition(TaskStage.FAILED)
        with pytest.raises(InvalidTransitionError):
            sm.transition(TaskStage.QUEUED)

    def test_event_seq_increments(self):
        sm = TaskStateMachine()
        seq1 = sm.transition(TaskStage.VALIDATING_DATA)
        seq2 = sm.transition(TaskStage.PREPARING_RETURNS)
        assert seq2 == seq1 + 1

    def test_detect_interrupted(self):
        assert TaskStateMachine.detect_interrupted({"stage": "EXECUTING"}) is True
        assert TaskStateMachine.detect_interrupted({"stage": "SUCCEEDED"}) is False
        assert TaskStateMachine.detect_interrupted({"stage": "FAILED"}) is False

    def test_mark_interrupted(self):
        result = TaskStateMachine.mark_interrupted({"stage": "CLUSTERING", "progress": 50})
        assert result["stage"] == "FAILED_INTERRUPTED"
        assert result["progress"] == 50
