"""Behavior-level comparison for frozen strategies and diagnostic variants."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from typing import Any


_BEHAVIOR_FIELDS = ("eligibility", "ranking", "selection", "weights", "trades", "cash")


@dataclass(frozen=True)
class BehaviorComparison:
    """Aligned behavior differences and the frozen equivalence marker."""

    difference_ratios: Mapping[str, float]
    behaviorally_equivalent: bool
    incremental_excess_return: float | None = None
    incremental_turnover: float | None = None
    tolerance: float = 1e-9

    @property
    def equivalence_marker(self) -> str:
        return "BEHAVIORALLY_EQUIVALENT" if self.behaviorally_equivalent else "BEHAVIORALLY_DIFFERENT"

    @property
    def behavioral_equivalent(self) -> bool:
        """Compatibility alias for callers using the adjective-less spelling."""

        return self.behaviorally_equivalent

    @property
    def equivalent(self) -> bool:
        return self.behaviorally_equivalent

    @property
    def behaviorally_equivalent_marker(self) -> bool:
        return self.behaviorally_equivalent

    @property
    def eligibility_difference_ratio(self) -> float:
        return self.difference_ratios["eligibility"]

    @property
    def ranking_difference_ratio(self) -> float:
        return self.difference_ratios["ranking"]

    @property
    def selection_difference_ratio(self) -> float:
        return self.difference_ratios["selection"]

    @property
    def weight_difference_ratio(self) -> float:
        return self.difference_ratios["weights"]

    @property
    def weights_difference_ratio(self) -> float:
        return self.weight_difference_ratio

    @property
    def trade_difference_ratio(self) -> float:
        return self.difference_ratios["trades"]

    @property
    def trades_difference_ratio(self) -> float:
        return self.trade_difference_ratio

    @property
    def cash_difference_ratio(self) -> float:
        return self.difference_ratios["cash"]


def compare_behavior(
    reference: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    candidate: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    tolerance: float = 1e-9,
) -> BehaviorComparison:
    """Compare eligibility through cash paths on a common event timeline.

    The denominator is the union of reference and candidate event keys.  A
    missing event is therefore a difference rather than an implicit zero or
    empty event.  Behavioral equivalence follows the frozen rule: selection
    and trade differences must both be strictly below one percent.
    """

    if tolerance < 0 or not math.isfinite(float(tolerance)):
        raise ValueError("tolerance must be a finite non-negative number")
    reference_path = _normalize_path(reference)
    candidate_path = _normalize_path(candidate)
    ratios = {
        field: _difference_ratio(reference_path.get(field, {}), candidate_path.get(field, {}), tolerance)
        for field in _BEHAVIOR_FIELDS
    }
    return BehaviorComparison(
        difference_ratios=ratios,
        behaviorally_equivalent=(ratios["selection"] < 0.01 and ratios["trades"] < 0.01),
        incremental_excess_return=_incremental_value(reference, candidate, "excess_return"),
        incremental_turnover=_incremental_value(reference, candidate, "turnover"),
        tolerance=float(tolerance),
    )


def _normalize_path(path: Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> dict[str, Mapping[Any, Any]]:
    if isinstance(path, Mapping) and any(field in path for field in _BEHAVIOR_FIELDS):
        result: dict[str, Mapping[Any, Any]] = {}
        for field in _BEHAVIOR_FIELDS:
            value = path.get(field, {})
            result[field] = value if isinstance(value, Mapping) else _sequence_to_mapping(value)
        return result

    result = {field: {} for field in _BEHAVIOR_FIELDS}
    for index, event in enumerate(path):
        if not isinstance(event, Mapping):
            raise TypeError("behavior events must be mappings")
        event_key = event.get("date", event.get("week", event.get("timestamp", index)))
        for field in _BEHAVIOR_FIELDS:
            if field in event:
                result[field][event_key] = event[field]
    return result


def _sequence_to_mapping(value: object) -> Mapping[Any, Any]:
    if value is None or isinstance(value, (str, bytes)):
        return {}
    return {index: item for index, item in enumerate(value)}  # type: ignore[arg-type]


def _difference_ratio(reference: Mapping[Any, Any], candidate: Mapping[Any, Any], tolerance: float) -> float:
    keys = set(reference) | set(candidate)
    if not keys:
        return 0.0
    different = sum(
        key not in reference
        or key not in candidate
        or not _equivalent(reference[key], candidate[key], tolerance)
        for key in keys
    )
    return float(different / len(keys))


def _equivalent(left: object, right: object, tolerance: float) -> bool:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        if set(left) != set(right):
            return False
        return all(_equivalent(left[key], right[key], tolerance) for key in left)
    if isinstance(left, (set, frozenset)) and isinstance(right, (set, frozenset)):
        return left == right
    if _is_number(left) and _is_number(right):
        return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=tolerance)
    if isinstance(left, Sequence) and not isinstance(left, (str, bytes)) and isinstance(right, Sequence) and not isinstance(right, (str, bytes)):
        return len(left) == len(right) and all(
            _equivalent(a, b, tolerance) for a, b in zip(left, right, strict=True)
        )
    return left == right


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _incremental_value(reference: object, candidate: object, key: str) -> float | None:
    reference_value = _metric_value(reference, key)
    candidate_value = _metric_value(candidate, key)
    if reference_value is None or candidate_value is None:
        return None
    return float(candidate_value - reference_value)


def _metric_value(path: object, key: str) -> float | None:
    if not isinstance(path, Mapping):
        return None
    metrics = path.get("metrics", path)
    if not isinstance(metrics, Mapping):
        return None
    aliases = {
        "excess_return": ("excess_return", "annualized_excess_return", "incremental_excess_return"),
        "turnover": ("turnover", "incremental_turnover"),
    }
    for alias in aliases[key]:
        value = metrics.get(alias)
        if _is_number(value):
            return float(value)
    return None
