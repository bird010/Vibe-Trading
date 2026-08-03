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


# ── Phase 4 Task 3: batch event envelope and log (§30.1) ──

from src.stockpred.fund_rotation.persistence import (  # noqa: E402
    BatchEventLog,
    EventValidationError,
)


class TestEventEnvelope:
    def test_append_writes_full_envelope_persisted_first(self, tmp_path):
        log = BatchEventLog(tmp_path, batch_id="b1")
        event = log.append(
            event_type="BATCH_STAGE", scope="BATCH", stage="VALIDATING",
        )
        # Persisted before being returned (events.jsonl already contains it).
        line = (tmp_path / "events.jsonl").read_text(encoding="utf-8").strip()
        stored = json.loads(line)
        assert stored["seq"] == event["seq"] == 1
        assert stored["schema_version"] == "v2"
        assert stored["event_type"] == "BATCH_STAGE"
        assert stored["scope"] == "BATCH"
        assert stored["batch_id"] == "b1"
        assert stored["stage"] == "VALIDATING"
        assert stored["ts"]  # §30.1 time field

    def test_variant_scope_requires_identity_fields(self, tmp_path):
        log = BatchEventLog(tmp_path, batch_id="b1")
        with pytest.raises(EventValidationError):
            log.append(event_type="VARIANT_STAGE", scope="VARIANT", stage="EXECUTING")
        event = log.append(
            event_type="VARIANT_STAGE", scope="VARIANT", stage="EXECUTING",
            run_id="r1", variant_key="s@abc", strategy_id="s",
        )
        assert event["run_id"] == "r1"

    def test_variant_stage_rejects_parent_batch_stage(self, tmp_path):
        log = BatchEventLog(tmp_path, batch_id="b1")

        with pytest.raises(EventValidationError, match="stage"):
            log.append(
                event_type="VARIANT_STAGE",
                scope="VARIANT",
                stage="RUNNING_STRATEGIES",
                run_id="r1",
                variant_key="s@abc",
                strategy_id="s",
            )

    def test_unknown_event_type_or_scope_rejected(self, tmp_path):
        log = BatchEventLog(tmp_path, batch_id="b1")
        with pytest.raises(EventValidationError):
            log.append(event_type="NOPE", scope="BATCH")
        with pytest.raises(EventValidationError):
            log.append(event_type="BATCH_STAGE", scope="NOPE")

    def test_seq_globally_monotonic_across_scopes(self, tmp_path):
        """§30.1 — the batch-level seq is global; child-local seqs never leak
        into the parent event file."""
        log = BatchEventLog(tmp_path, batch_id="b1")
        seqs = []
        seqs.append(log.append(event_type="BATCH_STAGE", scope="BATCH",
                               stage="VALIDATING")["seq"])
        seqs.append(log.append(event_type="VARIANT_STAGE", scope="VARIANT",
                               stage="EXECUTING", run_id="r1",
                               variant_key="s@a", strategy_id="s")["seq"])
        seqs.append(log.append(event_type="VARIANT_STAGE", scope="VARIANT",
                               stage="EXECUTING", run_id="r2",
                               variant_key="s@b", strategy_id="s")["seq"])
        seqs.append(log.append(event_type="TERMINAL", scope="BATCH",
                               stage="SUCCEEDED")["seq"])
        assert seqs == [1, 2, 3, 4]

    def test_seq_continues_after_restart(self, tmp_path):
        first = BatchEventLog(tmp_path, batch_id="b1")
        first.append(event_type="BATCH_STAGE", scope="BATCH", stage="VALIDATING")
        first.append(event_type="BATCH_STAGE", scope="BATCH",
                     stage="SNAPSHOTTING_DATA")
        reopened = BatchEventLog(tmp_path, batch_id="b1")
        event = reopened.append(event_type="BATCH_STAGE", scope="BATCH",
                                stage="RUNNING_STRATEGIES")
        assert event["seq"] == 3

    def test_half_written_tail_isolated_and_append_survives(self, tmp_path):
        """§26.1 crash restart: a truncated last line (no newline) must not
        swallow the next appended event."""
        log = BatchEventLog(tmp_path, batch_id="b1")
        log.append(event_type="BATCH_STAGE", scope="BATCH", stage="VALIDATING")
        # Simulate a crash mid-append of seq 2.
        with open(tmp_path / "events.jsonl", "a", encoding="utf-8") as fh:
            fh.write('{"schema_version": "v2", "seq": 2, "trunca')

        reopened = BatchEventLog(tmp_path, batch_id="b1")
        event = reopened.append(event_type="BATCH_STAGE", scope="BATCH",
                                stage="SNAPSHOTTING_DATA")
        assert event["seq"] == 2  # truncated line carries no usable seq

        lines = (tmp_path / "events.jsonl").read_text(
            encoding="utf-8").strip().split("\n")
        assert len(lines) == 3
        complete = [line for line in lines if line.strip().endswith("}")]
        parsed = [json.loads(line) for line in complete]
        # The new event is intact and parseable on its own line.
        assert parsed[-1]["seq"] == 2
        assert parsed[-1]["stage"] == "SNAPSHOTTING_DATA"

    def test_non_object_json_lines_do_not_break_recovery(self, tmp_path):
        with open(tmp_path / "events.jsonl", "w", encoding="utf-8") as fh:
            fh.write("null\n[1, 2]\n")
        log = BatchEventLog(tmp_path, batch_id="b1")
        event = log.append(event_type="BATCH_STAGE", scope="BATCH",
                           stage="VALIDATING")
        assert event["seq"] == 1

    def test_ts_is_standard_parseable_iso_offset(self, tmp_path):
        from datetime import datetime

        log = BatchEventLog(tmp_path, batch_id="b1")
        event = log.append(event_type="BATCH_STAGE", scope="BATCH",
                           stage="VALIDATING")
        # ISO-8601 with colon-separated UTC offset (§30.1 example form).
        parsed = datetime.fromisoformat(event["ts"])
        assert parsed.utcoffset() is not None
        assert event["ts"][-6] in "+-" and event["ts"][-3] == ":"

    def test_stage_must_use_public_enums(self, tmp_path):
        log = BatchEventLog(tmp_path, batch_id="b1")
        with pytest.raises(EventValidationError):
            log.append(event_type="BATCH_STAGE", scope="BATCH",
                       stage="NOT_A_STAGE")
        with pytest.raises(EventValidationError):
            # Child-only stage is not a valid BATCH-stage event stage.
            log.append(event_type="BATCH_STAGE", scope="BATCH",
                       stage="GENERATING_SIGNALS")
        # TERMINAL may omit stage entirely.
        event = log.append(event_type="TERMINAL", scope="BATCH")
        assert event["stage"] is None


class TestProgressValidation:
    def _append_progress(self, log, completed, total, unit="decision_dates"):
        return log.append(
            event_type="VARIANT_PROGRESS", scope="VARIANT",
            run_id="r1", variant_key="s@a", strategy_id="s",
            progress={"completed": completed, "total": total, "unit": unit},
        )

    def test_ratio_recomputed_from_completed_and_total(self, tmp_path):
        log = BatchEventLog(tmp_path, batch_id="b1")
        event = self._append_progress(log, 12, 81)
        assert event["progress"]["ratio"] == pytest.approx(12 / 81)

    def test_zero_total_ratio_is_zero(self, tmp_path):
        log = BatchEventLog(tmp_path, batch_id="b1")
        event = self._append_progress(log, 0, 0)
        assert event["progress"]["ratio"] == 0.0

    def test_negative_or_inverted_progress_rejected(self, tmp_path):
        log = BatchEventLog(tmp_path, batch_id="b1")
        with pytest.raises(EventValidationError):
            self._append_progress(log, -1, 10)
        with pytest.raises(EventValidationError):
            self._append_progress(log, 11, 10)
        with pytest.raises(EventValidationError):
            log.append(
                event_type="VARIANT_PROGRESS", scope="VARIANT",
                run_id="r1", variant_key="s@a", strategy_id="s",
                progress={"completed": 1.5, "total": 10, "unit": "x"},
            )

    def test_same_unit_progress_cannot_go_backwards(self, tmp_path):
        log = BatchEventLog(tmp_path, batch_id="b1")
        self._append_progress(log, 10, 81)
        with pytest.raises(EventValidationError):
            self._append_progress(log, 5, 81)
        # Equal or forward is fine; a different unit is independent.
        self._append_progress(log, 10, 81)
        self._append_progress(log, 0, 3, unit="reclusters")

    def test_progress_tracks_per_variant(self, tmp_path):
        """The no-regression window is per (run_id, unit): another variant may
        start at zero."""
        log = BatchEventLog(tmp_path, batch_id="b1")
        self._append_progress(log, 10, 81)
        event = log.append(
            event_type="VARIANT_PROGRESS", scope="VARIANT",
            run_id="r2", variant_key="s@b", strategy_id="s",
            progress={"completed": 0, "total": 81, "unit": "decision_dates"},
        )
        assert event["progress"]["completed"] == 0
