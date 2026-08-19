"""Cluster quality gates — design §9 (strategy-internal, Phase 3 Task 2).

After each reclustering, the gates score cluster member share
``p_c = n_c / N`` and the effective cluster count
``N_effective = exp(-Σ p_c ln p_c)`` against the warn/reject thresholds from
the frozen strategy config (§4: no hidden constants). Thresholds are fixed
before the backtest starts and never tuned to the test interval.

Gate outcomes are diagnostics only. Portfolio construction and decision
quality mapping remain in the strategy session; this module never creates
portfolio decisions.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from backtest.fund_rotation.strategies.correlation_representative.config import (
    CorrelationRepresentativeConfig,
)


class GateStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    REJECT = "REJECT"


@dataclass(frozen=True)
class GateResult:
    """One gate outcome with stable code, thresholds, actual value and the
    codes affected (for diagnostics, §9/§12)."""

    code: str
    status: GateStatus
    actual: float
    warn_threshold: float
    reject_threshold: float
    affected_codes: tuple[str, ...]


@dataclass(frozen=True)
class GateEvaluation:
    overall: GateStatus
    results: tuple[GateResult, ...]

    @property
    def rejected(self) -> bool:
        return self.overall is GateStatus.REJECT


def evaluate_cluster_gates(
    clusters: Mapping[str, int],
    config: CorrelationRepresentativeConfig,
) -> GateEvaluation:
    """§9 — score one clustering against the configured warn/reject gates."""
    members: dict[int, list[str]] = {}
    for code, cluster_id in clusters.items():
        members.setdefault(cluster_id, []).append(code)
    total = sum(len(m) for m in members.values())

    # Largest cluster (deterministic tie-break: smallest member code).
    top_members = sorted(members.values(), key=lambda m: (-len(m), min(m)))[0]
    max_share = len(top_members) / total
    if max_share > config.max_cluster_share_reject:
        share_status = GateStatus.REJECT
    elif max_share > config.max_cluster_share_warn:
        share_status = GateStatus.WARN
    else:
        share_status = GateStatus.PASS
    share_gate = GateResult(
        code="MAX_CLUSTER_SHARE",
        status=share_status,
        actual=max_share,
        warn_threshold=config.max_cluster_share_warn,
        reject_threshold=config.max_cluster_share_reject,
        affected_codes=tuple(sorted(top_members)),
    )

    probabilities = [len(m) / total for m in members.values()]
    entropy = -sum(p * math.log(p) for p in probabilities if p > 0)
    effective_count = math.exp(entropy)
    if effective_count < config.min_effective_cluster_count_reject:
        eff_status = GateStatus.REJECT
    elif effective_count < config.min_effective_cluster_count_warn:
        eff_status = GateStatus.WARN
    else:
        eff_status = GateStatus.PASS
    effective_gate = GateResult(
        code="EFFECTIVE_CLUSTER_COUNT",
        status=eff_status,
        actual=effective_count,
        warn_threshold=config.min_effective_cluster_count_warn,
        reject_threshold=config.min_effective_cluster_count_reject,
        affected_codes=tuple(sorted(clusters)),
    )

    results = (share_gate, effective_gate)
    if any(r.status is GateStatus.REJECT for r in results):
        overall = GateStatus.REJECT
    elif any(r.status is GateStatus.WARN for r in results):
        overall = GateStatus.WARN
    else:
        overall = GateStatus.PASS
    return GateEvaluation(overall=overall, results=results)
