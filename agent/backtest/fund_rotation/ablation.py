"""Fixed Momentum/Cluster/Carry ablation adapters for research-only comparisons."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from backtest.fund_rotation.strategies.ai_rotation_r34_staged_reentry.strategy import (
    apply_staged_reentry,
)
from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import (
    apply_incumbent_carry,
)


@dataclass(frozen=True)
class AblationArm:
    arm_id: str
    momentum: bool
    cluster: bool
    carry: bool
    identity_dedup: bool = True

    def __post_init__(self) -> None:
        expected = {
            "M0": (True, False, False),
            "M1": (True, True, False),
            "M2": (True, True, True),
        }
        if self.arm_id not in expected:
            raise ValueError(f"unknown ablation arm: {self.arm_id}")
        if (self.momentum, self.cluster, self.carry) != expected[self.arm_id]:
            raise ValueError(f"mechanism flags do not match {self.arm_id}")
        if not self.identity_dedup:
            raise ValueError("identity_dedup must remain enabled for every ablation arm")


@dataclass(frozen=True)
class AblationResult:
    selected_codes: tuple[str, ...]
    target_weights: dict[str, float]
    cash_weight: float
    diagnostics: dict[str, object]


def fixed_ablation_arms() -> tuple[AblationArm, ...]:
    return (
        AblationArm("M0", momentum=True, cluster=False, carry=False),
        AblationArm("M1", momentum=True, cluster=True, carry=False),
        AblationArm("M2", momentum=True, cluster=True, carry=True),
    )


def _finite(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _identity_deduped_codes(
    momentum_scores: Mapping[str, object],
    identity_by_code: Mapping[str, object],
) -> list[str]:
    representatives = _identity_representatives(identity_by_code)

    candidates = []
    for identity, code in representatives.items():
        raw_score = momentum_scores.get(code)
        score = _finite(raw_score)
        if score is None or score <= 0.0:
            continue
        candidates.append((code, score, identity))
    candidates.sort(key=lambda item: (-item[1], item[0]))
    return [code for code, _, _ in candidates]


def _identity_representatives(identity_by_code: Mapping[str, object]) -> dict[str, str]:
    representatives: dict[str, str] = {}
    for raw_code, raw_identity in identity_by_code.items():
        code = str(raw_code)
        if isinstance(raw_identity, str) and raw_identity:
            representatives[raw_identity] = min(
                code,
                representatives.get(raw_identity, code),
            )
    return representatives


def apply_ablation_arm(
    arm: AblationArm,
    *,
    momentum_scores: Mapping[str, object],
    cluster_by_code: Mapping[str, int],
    identity_by_code: Mapping[str, object],
    representatives: Mapping[int, str | None],
    top_n: int,
    previous_weights: Mapping[str, object],
) -> AblationResult:
    """Apply one fixed arm without changing the shared identity layer."""
    if not isinstance(arm, AblationArm):
        raise TypeError("arm must be an AblationArm")
    deduped = _identity_deduped_codes(momentum_scores, identity_by_code)
    try:
        slots = int(top_n)
    except (TypeError, ValueError, OverflowError):
        slots = 0
    if slots <= 0:
        return AblationResult(
            selected_codes=(),
            target_weights={},
            cash_weight=1.0,
            diagnostics={
                "arm_id": arm.arm_id,
                "momentum": arm.momentum,
                "cluster": arm.cluster,
                "carry": arm.carry,
                "identity_dedup": arm.identity_dedup,
            },
        )

    selected: list[str]
    selected_clusters: list[int] = []
    if not arm.cluster:
        selected = deduped[:slots]
    else:
        cluster_candidates: list[tuple[int, float, str, str]] = []
        u1_codes = set(_identity_representatives(identity_by_code).values())
        cluster_ids = {
            cluster_by_code[code]
            for code in deduped
            if cluster_by_code.get(code) is not None
        }
        for cluster_id in sorted(cluster_ids):
            members = [code for code in deduped if cluster_by_code.get(code) == cluster_id]
            all_members = [
                code for code in u1_codes if cluster_by_code.get(code) == cluster_id
            ]
            representative = representatives.get(cluster_id)
            if (
                not isinstance(representative, str)
                or representative not in members
                or not all_members
            ):
                continue
            best_score = max(float(momentum_scores[code]) for code in members)
            cluster_candidates.append((cluster_id, best_score, min(all_members), representative))
        cluster_candidates.sort(key=lambda item: (-item[1], item[2], item[0]))
        selected_clusters = [cluster_id for cluster_id, _, _, _ in cluster_candidates[:slots]]
        selected = [
            representative
            for _, _, _, representative in cluster_candidates[:slots]
        ]

    base_weights = {code: 1.0 / slots for code in selected}
    base_cash = max(0.0, 1.0 - sum(base_weights.values()))
    target_weights = base_weights
    cash_weight = base_cash
    if arm.carry:
        staged_weights, _, _ = apply_staged_reentry(previous_weights, base_weights)
        target_weights, cash_weight, _, _ = apply_incumbent_carry(
            previous_weights, staged_weights
        )
    return AblationResult(
        selected_codes=tuple(selected),
        target_weights=target_weights,
        cash_weight=cash_weight,
        diagnostics={
            "arm_id": arm.arm_id,
            "momentum": arm.momentum,
            "cluster": arm.cluster,
            "carry": arm.carry,
            "identity_dedup": arm.identity_dedup,
            "identity_count": len({identity_by_code[code] for code in deduped}),
            "selected_clusters": selected_clusters,
        },
    )
