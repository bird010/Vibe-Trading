"""Task state machine — §15.2.

QUEUED -> VALIDATING_DATA -> PREPARING_RETURNS -> CLUSTERING ->
GENERATING_TARGETS -> EXECUTING -> COMPUTING_BENCHMARKS ->
WRITING_RESULTS -> SUCCEEDED | FAILED | FAILED_INTERRUPTED
"""

from __future__ import annotations

from enum import Enum


class TaskStage(str, Enum):
    QUEUED = "QUEUED"
    VALIDATING_DATA = "VALIDATING_DATA"
    PREPARING_RETURNS = "PREPARING_RETURNS"
    CLUSTERING = "CLUSTERING"
    GENERATING_TARGETS = "GENERATING_TARGETS"
    EXECUTING = "EXECUTING"
    COMPUTING_BENCHMARKS = "COMPUTING_BENCHMARKS"
    WRITING_RESULTS = "WRITING_RESULTS"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    FAILED_INTERRUPTED = "FAILED_INTERRUPTED"


# Valid transitions
_TRANSITIONS: dict[TaskStage, set[TaskStage]] = {
    TaskStage.QUEUED: {TaskStage.VALIDATING_DATA, TaskStage.FAILED, TaskStage.FAILED_INTERRUPTED},
    TaskStage.VALIDATING_DATA: {TaskStage.PREPARING_RETURNS, TaskStage.FAILED, TaskStage.FAILED_INTERRUPTED},
    TaskStage.PREPARING_RETURNS: {TaskStage.CLUSTERING, TaskStage.FAILED, TaskStage.FAILED_INTERRUPTED},
    TaskStage.CLUSTERING: {TaskStage.GENERATING_TARGETS, TaskStage.FAILED, TaskStage.FAILED_INTERRUPTED},
    TaskStage.GENERATING_TARGETS: {TaskStage.EXECUTING, TaskStage.FAILED, TaskStage.FAILED_INTERRUPTED},
    TaskStage.EXECUTING: {TaskStage.COMPUTING_BENCHMARKS, TaskStage.FAILED, TaskStage.FAILED_INTERRUPTED},
    TaskStage.COMPUTING_BENCHMARKS: {TaskStage.WRITING_RESULTS, TaskStage.FAILED, TaskStage.FAILED_INTERRUPTED},
    TaskStage.WRITING_RESULTS: {TaskStage.SUCCEEDED, TaskStage.FAILED, TaskStage.FAILED_INTERRUPTED},
    TaskStage.SUCCEEDED: set(),
    TaskStage.FAILED: set(),
    TaskStage.FAILED_INTERRUPTED: set(),
}

# Stages considered "running" (for interrupted detection)
RUNNING_STAGES = {
    TaskStage.QUEUED,
    TaskStage.VALIDATING_DATA,
    TaskStage.PREPARING_RETURNS,
    TaskStage.CLUSTERING,
    TaskStage.GENERATING_TARGETS,
    TaskStage.EXECUTING,
    TaskStage.COMPUTING_BENCHMARKS,
    TaskStage.WRITING_RESULTS,
}

TERMINAL_STAGES = {TaskStage.SUCCEEDED, TaskStage.FAILED, TaskStage.FAILED_INTERRUPTED}


class InvalidTransitionError(Exception):
    """Raised when an illegal state transition is attempted."""


class TaskStateMachine:
    """§15.2 — Enforces valid stage transitions."""

    def __init__(self, initial: TaskStage = TaskStage.QUEUED) -> None:
        self._stage = initial
        self._event_seq = 0

    @property
    def stage(self) -> TaskStage:
        return self._stage

    @property
    def is_terminal(self) -> bool:
        return self._stage in TERMINAL_STAGES

    @property
    def is_running(self) -> bool:
        return self._stage in RUNNING_STAGES

    def transition(self, target: TaskStage) -> int:
        """Transition to target stage.

        Returns:
            Event sequence number for this transition.

        Raises:
            InvalidTransitionError: If transition is not allowed.
        """
        allowed = _TRANSITIONS.get(self._stage, set())
        if target not in allowed:
            raise InvalidTransitionError(
                f"Cannot transition from {self._stage.value} to {target.value}"
            )
        self._stage = target
        self._event_seq += 1
        return self._event_seq

    def next_event_seq(self) -> int:
        """Get next event sequence number without transitioning."""
        return self._event_seq + 1

    @staticmethod
    def detect_interrupted(state_dict: dict) -> bool:
        """Check if a persisted state represents an interrupted run."""
        status = state_dict.get("stage", "")
        try:
            stage = TaskStage(status)
        except ValueError:
            return False
        return stage in RUNNING_STAGES

    @staticmethod
    def mark_interrupted(state_dict: dict) -> dict:
        """Return updated state dict marked as FAILED_INTERRUPTED."""
        return {**state_dict, "stage": TaskStage.FAILED_INTERRUPTED.value}
