"""The pre-registered 45-point R11 parameter stability surface."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from statistics import median


MOMENTUM_WINDOWS: tuple[int, ...] = (3, 4, 6, 8, 12)
TOP_NS: tuple[int, ...] = (2, 3, 4)
RECLUSTER_WEEKS: tuple[int, ...] = (13, 26, 52)
REFERENCE_POINT = (4, 3, 26)
_TECHNICAL_SUCCESS_STATUSES = {"PASS", "COMPLETE", "OK"}
_TECHNICAL_TERMINAL_STATUSES = _TECHNICAL_SUCCESS_STATUSES | {"FAIL", "INCONCLUSIVE"}


@dataclass(frozen=True, order=True)
class StabilityPoint:
    momentum_window: int
    top_n: int
    recluster_weeks: int

    @property
    def parameters(self) -> dict[str, int]:
        return {
            "momentum_window": self.momentum_window,
            "top_n": self.top_n,
            "recluster_weeks": self.recluster_weeks,
        }


@dataclass(frozen=True)
class StabilityEvaluation:
    status: str
    complete_points: int
    total_points: int
    positive_excess_ratio: float | None
    neighborhood_positive_ratio: float | None
    neighborhood_sharpe_median: float | None
    reference_sharpe: float | None
    parameter_island: bool
    technical_failures: tuple[str, ...]
    reason_codes: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "complete_points": self.complete_points,
            "total_points": self.total_points,
            "positive_excess_ratio": self.positive_excess_ratio,
            "neighborhood_positive_ratio": self.neighborhood_positive_ratio,
            "neighborhood_sharpe_median": self.neighborhood_sharpe_median,
            "reference_sharpe": self.reference_sharpe,
            "parameter_island": self.parameter_island,
            "technical_failures": list(self.technical_failures),
            "reason_codes": list(self.reason_codes),
        }


def build_stability_grid() -> tuple[StabilityPoint, ...]:
    """Return exactly the frozen Cartesian product of 5 × 3 × 3 points."""

    return tuple(
        StabilityPoint(window, top_n, recluster)
        for window in MOMENTUM_WINDOWS
        for top_n in TOP_NS
        for recluster in RECLUSTER_WEEKS
    )


def _point_from_result(result: Mapping[str, object]) -> StabilityPoint:
    try:
        return StabilityPoint(
            int(result["momentum_window"]),
            int(result["top_n"]),
            int(result["recluster_weeks"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("stability result is missing its frozen point identity") from exc


def _finite_number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _is_complete(result: Mapping[str, object]) -> bool:
    status = _technical_status(result)
    return status in _TECHNICAL_SUCCESS_STATUSES and all(
        _finite_number(result.get(field)) is not None
        for field in ("annualized_excess_return", "sharpe")
    )


def _technical_status(result: Mapping[str, object]) -> str | None:
    value = result.get("technical_status")
    if value is None or not str(value).strip():
        return None
    return str(value).upper()


def _technical_failure(point: StabilityPoint, result: Mapping[str, object]) -> str | None:
    status = _technical_status(result)
    if status is None:
        return f"MISSING_TECHNICAL_STATUS:{point}"
    if status not in _TECHNICAL_TERMINAL_STATUSES:
        return f"INVALID_TECHNICAL_STATUS:{point}"
    if not _is_complete(result):
        return f"INCOMPLETE:{point}"
    return None


def _direct_neighbors(point: StabilityPoint) -> tuple[StabilityPoint, ...]:
    neighbors: list[StabilityPoint] = []
    dimensions = (
        ("momentum_window", MOMENTUM_WINDOWS),
        ("top_n", TOP_NS),
        ("recluster_weeks", RECLUSTER_WEEKS),
    )
    for field, values in dimensions:
        index = values.index(getattr(point, field))
        for neighbor_index in (index - 1, index + 1):
            if 0 <= neighbor_index < len(values):
                values_for_point = point.parameters
                values_for_point[field] = values[neighbor_index]
                neighbors.append(StabilityPoint(**values_for_point))
    return tuple(neighbors)


def evaluate_stability_surface(
    results: Iterable[Mapping[str, object]],
    *,
    positive_ratio_threshold: float = 0.60,
    neighborhood_sharpe_fraction: float = 0.80,
) -> StabilityEvaluation:
    """Apply the frozen stability gates without selecting a point."""

    expected = build_stability_grid()
    expected_set = set(expected)
    by_point: dict[StabilityPoint, Mapping[str, object]] = {}
    technical_failures: list[str] = []
    for result in results:
        point = _point_from_result(result)
        if point not in expected_set:
            technical_failures.append(f"OUT_OF_GRID:{point}")
            continue
        if point in by_point:
            technical_failures.append(f"DUPLICATE:{point}")
        by_point[point] = result
        failure = _technical_failure(point, result)
        if failure is not None:
            technical_failures.append(failure)

    missing = [point for point in expected if point not in by_point]
    technical_failures.extend(f"MISSING:{point}" for point in missing)
    complete_results = [
        by_point[point] for point in expected if point in by_point and _is_complete(by_point[point])
    ]
    complete_points = len(complete_results)
    positive_ratio = (
        sum(float(result["annualized_excess_return"]) > 0.0 for result in complete_results)
        / complete_points
        if complete_points
        else None
    )

    reference = by_point.get(StabilityPoint(*REFERENCE_POINT))
    reference_sharpe = _finite_number(reference.get("sharpe")) if reference else None
    neighbors = _direct_neighbors(StabilityPoint(*REFERENCE_POINT))
    complete_neighbors = [
        by_point[point]
        for point in neighbors
        if point in by_point and _is_complete(by_point[point])
    ]
    neighborhood_positive_ratio = (
        sum(float(result["annualized_excess_return"]) > 0.0 for result in complete_neighbors)
        / len(complete_neighbors)
        if complete_neighbors
        else None
    )
    neighbor_sharpes = [float(result["sharpe"]) for result in complete_neighbors]
    neighborhood_sharpe_median = median(neighbor_sharpes) if neighbor_sharpes else None

    center_positive = bool(
        reference
        and _is_complete(reference)
        and float(reference["annualized_excess_return"]) > 0.0
    )
    parameter_island = center_positive and (
        neighborhood_positive_ratio is None or neighborhood_positive_ratio < positive_ratio_threshold
    )

    reasons: list[str] = []
    if technical_failures:
        reasons.append("TECHNICAL_FAILURE")
    if parameter_island:
        reasons.append("PARAMETER_ISLAND")
    if positive_ratio is None or positive_ratio < positive_ratio_threshold:
        reasons.append("FULL_GRID_POSITIVE_RATIO_BELOW_THRESHOLD")
    if (
        neighborhood_positive_ratio is None
        or neighborhood_positive_ratio < positive_ratio_threshold
    ):
        reasons.append("DIRECT_NEIGHBORHOOD_POSITIVE_RATIO_BELOW_THRESHOLD")
    if (
        reference_sharpe is None
        or neighborhood_sharpe_median is None
        or neighborhood_sharpe_median < reference_sharpe * neighborhood_sharpe_fraction
    ):
        reasons.append("DIRECT_NEIGHBORHOOD_SHARPE_BELOW_THRESHOLD")

    if technical_failures or parameter_island:
        status = "FAIL"
    elif reasons:
        status = "INCONCLUSIVE"
    else:
        status = "PASS"
    return StabilityEvaluation(
        status=status,
        complete_points=complete_points,
        total_points=len(expected),
        positive_excess_ratio=positive_ratio,
        neighborhood_positive_ratio=neighborhood_positive_ratio,
        neighborhood_sharpe_median=(
            float(neighborhood_sharpe_median) if neighborhood_sharpe_median is not None else None
        ),
        reference_sharpe=reference_sharpe,
        parameter_island=parameter_island,
        technical_failures=tuple(technical_failures),
        reason_codes=tuple(dict.fromkeys(reasons)),
    )
