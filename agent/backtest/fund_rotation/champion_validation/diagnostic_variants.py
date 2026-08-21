"""Pre-registered R11 mechanism diagnostics.

The objects in this module are controller metadata.  They deliberately do
not implement or register strategies: the validation controller may use the
declarations to call an existing signal/execution path, while the strategy
catalog remains unchanged.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ParityMetadata:
    """Frozen identity and comparison rules for the E/R11 control."""

    strategy_id: str = "ai_rotation_r11_persist_geom"
    reference_label: str = "Frozen R11 reference"
    momentum_window: int = 4
    top_n: int = 3
    recluster_weeks: int = 26
    required: bool = True
    numeric_tolerance: float = 1e-9
    comparison_fields: tuple[str, ...] = (
        "target_weights",
        "cash_weight",
        "trades",
        "nav",
    )

    def as_dict(self) -> dict[str, object]:
        return {
            "strategy_id": self.strategy_id,
            "reference_label": self.reference_label,
            "momentum_window": self.momentum_window,
            "top_n": self.top_n,
            "recluster_weeks": self.recluster_weeks,
            "required": self.required,
            "numeric_tolerance": self.numeric_tolerance,
            "comparison_fields": list(self.comparison_fields),
        }


@dataclass(frozen=True)
class DiagnosticVariant:
    """One intentionally isolated A–E mechanism change."""

    variant_id: str
    eligibility_rule: str
    ranking_rule: str
    declared_difference: str
    controller_diagnostic: bool = True
    catalog_registered: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "variant_id": self.variant_id,
            "eligibility_rule": self.eligibility_rule,
            "ranking_rule": self.ranking_rule,
            "declared_difference": self.declared_difference,
            "controller_diagnostic": self.controller_diagnostic,
            "catalog_registered": self.catalog_registered,
        }


@dataclass(frozen=True)
class AblationMatrix(Sequence[DiagnosticVariant]):
    """A sequence-compatible matrix carrying its frozen parity metadata."""

    variants: tuple[DiagnosticVariant, ...]
    parity: ParityMetadata

    def __iter__(self) -> Iterator[DiagnosticVariant]:
        return iter(self.variants)

    def __len__(self) -> int:
        return len(self.variants)

    def __getitem__(self, index: int) -> DiagnosticVariant:
        return self.variants[index]

    def as_dict(self) -> dict[str, object]:
        return {
            "variants": [variant.as_dict() for variant in self.variants],
            "parity": self.parity.as_dict(),
        }


@dataclass(frozen=True)
class ParityResult:
    """Serializable result of comparing the E diagnostic with frozen R11."""

    passed: bool
    mismatched_fields: tuple[str, ...] = ()
    tolerance: float = 1e-9

    def as_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "mismatched_fields": list(self.mismatched_fields),
            "tolerance": self.tolerance,
        }


def build_ablation_matrix() -> AblationMatrix:
    """Return the immutable A–E diagnostic matrix in declaration order."""

    variants = (
        DiagnosticVariant(
            variant_id="A",
            eligibility_rule="none",
            ranking_rule="M0",
            declared_difference="baseline_m0_ranking",
        ),
        DiagnosticVariant(
            variant_id="B",
            eligibility_rule="M0 > 0",
            ranking_rule="M0",
            declared_difference="add_m0_positive_eligibility",
        ),
        DiagnosticVariant(
            variant_id="C",
            eligibility_rule="M0 > 0 and M1 > 0",
            ranking_rule="M0",
            declared_difference="add_m1_positive_eligibility",
        ),
        DiagnosticVariant(
            variant_id="D",
            eligibility_rule="M0 > 0 and M1 > 0",
            ranking_rule="(M0 + M1) / 2",
            declared_difference="replace_m0_with_arithmetic_mean_ranking",
        ),
        DiagnosticVariant(
            variant_id="E",
            eligibility_rule="M0 > 0 and M1 > 0",
            ranking_rule="sqrt((1 + M0) * (1 + M1)) - 1",
            declared_difference="replace_arithmetic_mean_with_geometric_ranking",
        ),
    )
    return AblationMatrix(variants=variants, parity=ParityMetadata())


def _values_equal(left: Any, right: Any, tolerance: float) -> bool:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return (
            set(left) == set(right)
            and all(_values_equal(left[key], right[key], tolerance) for key in left)
        )
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(
            _values_equal(a, b, tolerance) for a, b in zip(left, right)
        )
    if isinstance(left, (int, float)) and not isinstance(left, bool):
        if isinstance(right, (int, float)) and not isinstance(right, bool):
            left_float = float(left)
            right_float = float(right)
            return math.isfinite(left_float) and math.isfinite(right_float) and math.isclose(
                left_float, right_float, rel_tol=0.0, abs_tol=tolerance
            )
    return left == right


def check_r11_parity(
    e_observation: Mapping[str, object],
    r11_observation: Mapping[str, object],
    *,
    metadata: ParityMetadata | None = None,
) -> ParityResult:
    """Compare E and R11 only on the frozen, auditable parity fields."""

    frozen = metadata or ParityMetadata()
    mismatches = tuple(
        field
        for field in frozen.comparison_fields
        if field not in e_observation
        or field not in r11_observation
        or not _values_equal(
            e_observation[field], r11_observation[field], frozen.numeric_tolerance
        )
    )
    return ParityResult(
        passed=not mismatches,
        mismatched_fields=mismatches,
        tolerance=frozen.numeric_tolerance,
    )


def validate_ablation_matrix(matrix: AblationMatrix) -> None:
    """Raise when a matrix violates the single-difference safety contract."""

    expected = ("A", "B", "C", "D", "E")
    if tuple(variant.variant_id for variant in matrix) != expected:
        raise ValueError("ablation matrix must contain A, B, C, D, E in order")
    if any(not variant.controller_diagnostic or variant.catalog_registered for variant in matrix):
        raise ValueError("diagnostic variants must remain outside the strategy catalog")
    if len({variant.declared_difference for variant in matrix}) != len(matrix):
        raise ValueError("each ablation stage must declare one distinct difference")

