"""Phase 4 Task 3 — versioned batch/child state machines (design §26/§30.1).

Batch v2 and child v2 state graphs, v1 read-only compatibility (history files
are parsed and displayed, never rewritten), and the rule that unknown
strategy substages never drive the public state machine.
"""

from __future__ import annotations

import pytest

from src.stockpred.fund_rotation.state_machine import (
    BatchStage,
    BatchStateMachine,
    ChildStage,
    ChildStateMachine,
    InvalidTransitionError,
    TaskStage,
    detect_interrupted_state,
    mark_state_interrupted,
    stage_display,
)


class TestBatchStateMachine:
    def test_happy_path_to_succeeded(self):
        sm = BatchStateMachine()
        assert sm.stage == BatchStage.QUEUED
        for target in (
            BatchStage.VALIDATING,
            BatchStage.SNAPSHOTTING_DATA,
            BatchStage.RUNNING_STRATEGIES,
            BatchStage.COMPARING,
            BatchStage.WRITING_RESULTS,
            BatchStage.SUCCEEDED,
        ):
            sm.transition(target)
        assert sm.is_terminal

    def test_partial_succeeded_terminal(self):
        sm = BatchStateMachine()
        sm.transition(BatchStage.VALIDATING)
        sm.transition(BatchStage.SNAPSHOTTING_DATA)
        sm.transition(BatchStage.RUNNING_STRATEGIES)
        sm.transition(BatchStage.COMPARING)
        sm.transition(BatchStage.WRITING_RESULTS)
        sm.transition(BatchStage.PARTIAL_SUCCEEDED)
        assert sm.is_terminal

    def test_skip_transition_rejected(self):
        sm = BatchStateMachine()
        with pytest.raises(InvalidTransitionError):
            sm.transition(BatchStage.RUNNING_STRATEGIES)

    def test_cancel_from_running_and_no_exit_from_terminal(self):
        sm = BatchStateMachine()
        sm.transition(BatchStage.VALIDATING)
        sm.transition(BatchStage.CANCELED)
        assert sm.stage == BatchStage.CANCELED
        with pytest.raises(InvalidTransitionError):
            sm.transition(BatchStage.SUCCEEDED)

    def test_failed_interrupted_terminal(self):
        sm = BatchStateMachine()
        sm.transition(BatchStage.VALIDATING)
        sm.transition(BatchStage.FAILED_INTERRUPTED)
        assert sm.is_terminal

    def test_partial_succeeded_not_a_child_stage(self):
        # PARTIAL_SUCCEEDED belongs to batches only.
        assert not hasattr(ChildStage, "PARTIAL_SUCCEEDED")


class TestChildStateMachine:
    def test_happy_path(self):
        sm = ChildStateMachine()
        assert sm.stage == ChildStage.QUEUED
        for target in (
            ChildStage.PREPARING_DATA,
            ChildStage.GENERATING_SIGNALS,
            ChildStage.EXECUTING,
            ChildStage.COMPUTING_METRICS,
            ChildStage.WRITING_RESULTS,
            ChildStage.SUCCEEDED,
        ):
            sm.transition(target)
        assert sm.is_terminal

    def test_fail_and_cancel_from_running(self):
        sm = ChildStateMachine()
        sm.transition(ChildStage.PREPARING_DATA)
        sm.transition(ChildStage.FAILED)
        assert sm.is_terminal

        sm2 = ChildStateMachine()
        sm2.transition(ChildStage.PREPARING_DATA)
        sm2.transition(ChildStage.CANCELED)
        assert sm2.is_terminal

    def test_child_has_no_v1_stage_names(self):
        # v2 child stages converge to generic names (§13.4); the old
        # CLUSTERING/GENERATING_TARGETS tokens must not reappear.
        names = {s.value for s in ChildStage}
        assert "CLUSTERING" not in names
        assert "GENERATING_TARGETS" not in names
        assert "PREPARING_RETURNS" not in names


class TestV1Compatibility:
    """v1 state names stay parseable for read-only display; history files are
    never rewritten."""

    def test_v1_running_states_detected_as_interrupted(self):
        for v1_stage in ("PREPARING_RETURNS", "CLUSTERING", "GENERATING_TARGETS",
                         "EXECUTING", "COMPUTING_BENCHMARKS", "WRITING_RESULTS"):
            assert detect_interrupted_state({"stage": v1_stage}) is True

    def test_v1_terminal_states_not_interrupted(self):
        for stage in ("SUCCEEDED", "FAILED", "FAILED_INTERRUPTED"):
            assert detect_interrupted_state({"stage": stage}) is False

    def test_v2_states_detected_too(self):
        assert detect_interrupted_state({"stage": "SNAPSHOTTING_DATA"}) is True
        assert detect_interrupted_state({"stage": "PARTIAL_SUCCEEDED"}) is False

    def test_display_preserves_original_names(self):
        # Read-only display: a v1 file shows its v1 stage untouched.
        assert stage_display({"stage": "CLUSTERING"}) == "CLUSTERING"
        assert stage_display({"stage": "SNAPSHOTTING_DATA"}) == "SNAPSHOTTING_DATA"

    def test_mark_interrupted_keeps_other_fields(self):
        marked = mark_state_interrupted({"stage": "CLUSTERING", "progress": 40})
        assert marked["stage"] == "FAILED_INTERRUPTED"
        assert marked["progress"] == 40

    def test_v1_task_stage_machine_still_works(self):
        # The v1 machine remains for the legacy single-run service.
        from src.stockpred.fund_rotation.state_machine import TaskStateMachine

        sm = TaskStateMachine()
        sm.transition(TaskStage.VALIDATING_DATA)
        assert sm.stage == TaskStage.VALIDATING_DATA


class TestSubstageNeverDrivesStateMachine:
    def test_unknown_substage_does_not_change_stage(self, tmp_path):
        """§30.1 — strategy_substage is a display-only namespaced string."""
        from src.stockpred.fund_rotation.persistence import BatchEventLog

        log = BatchEventLog(tmp_path, batch_id="b1")
        sm = ChildStateMachine()
        sm.transition(ChildStage.PREPARING_DATA)
        before = sm.stage
        log.append(
            event_type="VARIANT_PROGRESS", scope="VARIANT",
            run_id="r1", variant_key="s@abc", strategy_id="s",
            stage=sm.stage.value, strategy_substage="TOTALLY_UNKNOWN_STAGE",
            progress={"completed": 1, "total": 10, "unit": "decision_dates"},
        )
        # The substage string never drives a transition.
        assert sm.stage == before
