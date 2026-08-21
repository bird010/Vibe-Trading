"""Frozen B0-B5 benchmark specifications and comparability checks."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BenchmarkSpec:
    """Declarative benchmark behavior; construction stays in public modules."""

    benchmark_id: str
    name: str
    implementation: str
    selection: str
    universe: str
    rebalance: str
    cost_policy: str
    theoretical: bool = False
    comparable: bool = True
    comparability_reason: str = "EXECUTION_COMPARABLE"
    instrument_code: str | None = None

    @property
    def id(self) -> str:
        return self.benchmark_id

    @property
    def is_theoretical(self) -> bool:
        return self.theoretical

    @property
    def is_execution_comparable(self) -> bool:
        return self.comparable

    @property
    def definition(self) -> Mapping[str, Any]:
        return self.behavior

    @property
    def behavior(self) -> Mapping[str, Any]:
        return {
            "implementation": self.implementation,
            "selection": self.selection,
            "universe": self.universe,
            "rebalance": self.rebalance,
            "cost_policy": self.cost_policy,
        }


@dataclass(frozen=True)
class ExecutionIdentityComparison:
    """Result of comparing identities required for an execution benchmark."""

    comparable: bool
    mismatches: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()

    @property
    def is_comparable(self) -> bool:
        return self.comparable

    def __bool__(self) -> bool:
        return self.comparable

    def require_comparable(self) -> None:
        if not self.comparable:
            raise ValueError("benchmark identities are not comparable: " + ", ".join(self.mismatches))


def build_benchmark_specs(*, theoretical_equal_weight: bool = False) -> tuple[BenchmarkSpec, ...]:
    """Return the pre-registered B0-B5 definitions in stable order."""

    b1_theoretical = bool(theoretical_equal_weight)
    b1_comparable = not b1_theoretical
    b1_cost = "no_cost" if b1_theoretical else "shared_public_execution_cost"
    b1_reason = "THEORETICAL_NO_COST" if b1_theoretical else "EXECUTION_COMPARABLE"
    return (
        BenchmarkSpec(
            "B0", "100% Cash", "cash", "cash", "whole_evaluation_interval", "none",
            "no_cost", comparable=True,
        ),
        BenchmarkSpec(
            "B1", "Dynamic PIT Universe Equal Weight", "dynamic_pit_equal_weight",
            "all_current_pit_eligible", "dynamic_pit", "weekly", b1_cost,
            theoretical=b1_theoretical, comparable=b1_comparable,
            comparability_reason=b1_reason,
        ),
        BenchmarkSpec(
            "B2", "510300.SH Buy And Hold", "buy_and_hold", "single_instrument",
            "shared_pit_evidence", "initial_entry_only", "shared_public_execution_cost",
            instrument_code="510300.SH",
        ),
        BenchmarkSpec(
            "B3", "Non-cluster Top-3 4W Momentum", "non_cluster_top3_momentum",
            "top_3_m0_positive_non_cluster", "dynamic_pit_non_cluster", "weekly",
            "shared_public_execution_cost",
        ),
        BenchmarkSpec(
            "B4", "Non-cluster Persistent Momentum", "non_cluster_persistent_momentum",
            "top_3_m0_m1_positive_non_cluster", "dynamic_pit_non_cluster", "weekly",
            "shared_public_execution_cost",
        ),
        BenchmarkSpec(
            "B5", "Dynamic PIT Universe 13W Inverse Volatility", "dynamic_pit_inverse_vol_13w",
            "all_valid_inverse_vol_13w", "dynamic_pit", "weekly", "shared_public_execution_cost",
        ),
    )


_IDENTITY_FIELDS = (
    "data_snapshot",
    "universe_id",
    "evaluation_calendar",
    "cost_policy",
    "execution_identity",
)
_ALIASES = {
    "data_snapshot": ("data_snapshot", "data_snapshot_fingerprint", "snapshot", "snapshot_version"),
    "universe_id": ("universe_id", "universe", "universe_identity"),
    "evaluation_calendar": ("evaluation_calendar", "calendar", "evaluation_calendar_hash"),
    "cost_policy": ("cost_policy", "cost_identity", "cost_policy_hash"),
    "execution_identity": ("execution_identity", "execution_contract", "execution_contract_version"),
}


def compare_execution_identity(
    reference: Mapping[str, Any] | object,
    candidate: Mapping[str, Any] | object,
) -> ExecutionIdentityComparison:
    """Reject snapshot, universe, calendar, cost, or execution identity drift."""

    mismatches: list[str] = []
    missing_required_identity = False
    for field in _IDENTITY_FIELDS:
        reference_value = _lookup_identity(reference, field)
        candidate_value = _lookup_identity(candidate, field)
        if _is_missing_identity(reference_value) or _is_missing_identity(candidate_value):
            missing_required_identity = True
            mismatches.append(field)
            continue
        if reference_value != candidate_value:
            mismatches.append(field)
    reasons: list[str] = []
    if mismatches:
        reasons.append("IDENTITY_MISMATCH")
    if missing_required_identity:
        reasons.append("MISSING_REQUIRED_IDENTITY")
    return ExecutionIdentityComparison(
        comparable=not mismatches,
        mismatches=tuple(mismatches),
        reason_codes=tuple(reasons),
    )


def _lookup_identity(record: Mapping[str, Any] | object, field: str) -> Any:
    for key in _ALIASES[field]:
        if isinstance(record, Mapping):
            if key in record:
                return record[key]
        elif hasattr(record, key):
            return getattr(record, key)
    return None


def _is_missing_identity(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())
