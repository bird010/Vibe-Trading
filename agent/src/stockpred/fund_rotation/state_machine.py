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


# ── Versioned batch/child state machines (Phase 4, §26/§30.1) ──

class BatchStage(str, Enum):
    """§26 — parent batch v2 state graph."""

    QUEUED = "QUEUED"
    VALIDATING = "VALIDATING"
    SNAPSHOTTING_DATA = "SNAPSHOTTING_DATA"
    RUNNING_STRATEGIES = "RUNNING_STRATEGIES"
    COMPARING = "COMPARING"
    WRITING_RESULTS = "WRITING_RESULTS"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL_SUCCEEDED = "PARTIAL_SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    FAILED_INTERRUPTED = "FAILED_INTERRUPTED"


class ChildStage(str, Enum):
    """§26 — sub-run v2 state graph (§13.4 generic stage names)."""

    QUEUED = "QUEUED"
    PREPARING_DATA = "PREPARING_DATA"
    GENERATING_SIGNALS = "GENERATING_SIGNALS"
    EXECUTING = "EXECUTING"
    COMPUTING_METRICS = "COMPUTING_METRICS"
    WRITING_RESULTS = "WRITING_RESULTS"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    FAILED_INTERRUPTED = "FAILED_INTERRUPTED"


def _terminal_failures(stage_cls) -> set:
    return {
        stage_cls.FAILED,
        stage_cls.CANCELED,
        stage_cls.FAILED_INTERRUPTED,
    }


_BATCH_FAILURES = _terminal_failures(BatchStage)
_CHILD_FAILURES = _terminal_failures(ChildStage)

BATCH_TRANSITIONS: dict[BatchStage, set[BatchStage]] = {
    BatchStage.QUEUED: {BatchStage.VALIDATING} | _BATCH_FAILURES,
    BatchStage.VALIDATING: {BatchStage.SNAPSHOTTING_DATA} | _BATCH_FAILURES,
    BatchStage.SNAPSHOTTING_DATA: {BatchStage.RUNNING_STRATEGIES} | _BATCH_FAILURES,
    BatchStage.RUNNING_STRATEGIES: {BatchStage.COMPARING} | _BATCH_FAILURES,
    BatchStage.COMPARING: {BatchStage.WRITING_RESULTS} | _BATCH_FAILURES,
    BatchStage.WRITING_RESULTS: (
        {BatchStage.SUCCEEDED, BatchStage.PARTIAL_SUCCEEDED} | _BATCH_FAILURES
    ),
    BatchStage.SUCCEEDED: set(),
    BatchStage.PARTIAL_SUCCEEDED: set(),
    BatchStage.FAILED: set(),
    BatchStage.CANCELED: set(),
    BatchStage.FAILED_INTERRUPTED: set(),
}

CHILD_TRANSITIONS: dict[ChildStage, set[ChildStage]] = {
    ChildStage.QUEUED: {ChildStage.PREPARING_DATA} | _CHILD_FAILURES,
    ChildStage.PREPARING_DATA: {ChildStage.GENERATING_SIGNALS} | _CHILD_FAILURES,
    ChildStage.GENERATING_SIGNALS: {ChildStage.EXECUTING} | _CHILD_FAILURES,
    ChildStage.EXECUTING: {ChildStage.COMPUTING_METRICS} | _CHILD_FAILURES,
    ChildStage.COMPUTING_METRICS: {ChildStage.WRITING_RESULTS} | _CHILD_FAILURES,
    ChildStage.WRITING_RESULTS: {ChildStage.SUCCEEDED} | _CHILD_FAILURES,
    ChildStage.SUCCEEDED: set(),
    ChildStage.FAILED: set(),
    ChildStage.CANCELED: set(),
    ChildStage.FAILED_INTERRUPTED: set(),
}

BATCH_TERMINAL_STAGES = {
    stage for stage, allowed in BATCH_TRANSITIONS.items() if not allowed
}
BATCH_RUNNING_STAGES = set(BatchStage) - BATCH_TERMINAL_STAGES
CHILD_TERMINAL_STAGES = {
    stage for stage, allowed in CHILD_TRANSITIONS.items() if not allowed
}
CHILD_RUNNING_STAGES = set(ChildStage) - CHILD_TERMINAL_STAGES


class _VersionedStateMachine:
    """Shared engine for the v2 batch/child machines."""

    transitions: dict = {}
    terminal_stages: set = set()
    running_stages: set = set()

    def __init__(self, initial=None) -> None:
        self._stage = initial if initial is not None else next(iter(self.transitions))
        self._event_seq = 0

    @property
    def stage(self):
        return self._stage

    @property
    def is_terminal(self) -> bool:
        return self._stage in self.terminal_stages

    @property
    def is_running(self) -> bool:
        return self._stage in self.running_stages

    def transition(self, target) -> int:
        allowed = self.transitions.get(self._stage, set())
        if target not in allowed:
            raise InvalidTransitionError(
                f"Cannot transition from {self._stage.value} to {target.value}"
            )
        self._stage = target
        self._event_seq += 1
        return self._event_seq

    def next_event_seq(self) -> int:
        return self._event_seq + 1


class BatchStateMachine(_VersionedStateMachine):
    transitions = BATCH_TRANSITIONS
    terminal_stages = BATCH_TERMINAL_STAGES
    running_stages = BATCH_RUNNING_STAGES

    @staticmethod
    def detect_interrupted(state_dict: dict) -> bool:
        return detect_interrupted_state(state_dict)

    @staticmethod
    def mark_interrupted(state_dict: dict) -> dict:
        return mark_state_interrupted(state_dict)


class ChildStateMachine(_VersionedStateMachine):
    transitions = CHILD_TRANSITIONS
    terminal_stages = CHILD_TERMINAL_STAGES
    running_stages = CHILD_RUNNING_STAGES

    @staticmethod
    def detect_interrupted(state_dict: dict) -> bool:
        return detect_interrupted_state(state_dict)

    @staticmethod
    def mark_interrupted(state_dict: dict) -> dict:
        return mark_state_interrupted(state_dict)


# ── Cross-version (v1/v2) read-only helpers (§26.1) ──

# v1 running stages stay parseable for read-only display; history files are
# never rewritten with v2 names.
_V1_RUNNING = {
    TaskStage.QUEUED,
    TaskStage.VALIDATING_DATA,
    TaskStage.PREPARING_RETURNS,
    TaskStage.CLUSTERING,
    TaskStage.GENERATING_TARGETS,
    TaskStage.EXECUTING,
    TaskStage.COMPUTING_BENCHMARKS,
    TaskStage.WRITING_RESULTS,
}
_V1_NAMES = {stage.value for stage in TaskStage}


def detect_interrupted_state(state_dict: dict) -> bool:
    """True when a persisted v1 OR v2 state is a non-terminal (interrupted)
    run. Unknown stage names are not treated as interrupted."""
    stage = state_dict.get("stage", "")
    if stage in _V1_NAMES:
        return TaskStage(stage) in _V1_RUNNING
    if stage in {s.value for s in BatchStage}:
        return BatchStage(stage) in BATCH_RUNNING_STAGES
    if stage in {s.value for s in ChildStage}:
        return ChildStage(stage) in CHILD_RUNNING_STAGES
    return False


def mark_state_interrupted(state_dict: dict) -> dict:
    """Return the state dict marked FAILED_INTERRUPTED (other fields kept)."""
    return {**state_dict, "stage": "FAILED_INTERRUPTED"}


def stage_display(state_dict: dict) -> str:
    """Read-only display of a persisted stage (v1 files keep v1 names)."""
    return str(state_dict.get("stage", ""))
