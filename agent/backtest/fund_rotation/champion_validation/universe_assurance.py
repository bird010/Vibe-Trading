"""Strict point-in-time Universe Assurance for Champion Validation.

The existing PIT resolver remains the source of truth.  This module only
adapts its resolution evidence into the frozen Champion Validation gates; it
never removes an exclusion or promotes a degraded PIT quality state.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
import math
from typing import Any

import pandas as pd

from backtest.fund_rotation.pit_universe import (
    ExclusionReasonCode,
    PITQualityStatus,
    UniverseResolution,
)


class UniverseAssuranceStatus(str, Enum):
    """Outcome exposed by the assurance adapter."""

    VERIFIED = "VERIFIED"
    INCONCLUSIVE_UNIVERSE = "INCONCLUSIVE_UNIVERSE"


_GATES = (
    "unresolved_cross_source_conflicts",
    "overlapping_valid_periods",
    "unordered_revisions",
    "included_before_listing",
    "included_after_delisting",
    "included_without_knowledge_time",
    "selected_when_not_tradable",
    "decision_universe_snapshot_mismatch",
)

_AUDIT_COUNT_FIELDS = (
    "overlapping_valid_range_count",
    "overlapping_knowledge_range_count",
    "ambiguous_revision_order_count",
    "included_before_listing_count",
    "included_after_delisting_count",
    "knowledge_time_unverified_count",
    "cross_source_missing_count",
    "status_conflict_count",
    "date_conflict_count",
    "classification_conflict_count",
    "selected_when_not_tradable_count",
)


@dataclass(frozen=True)
class UniverseAssuranceResult:
    """Auditable result of applying the frozen Universe Assurance gates."""

    status: UniverseAssuranceStatus
    passed: bool
    gate_counts: Mapping[str, int]
    reason_codes: tuple[str, ...] = ()
    quality_statuses: tuple[str, ...] = ()
    details: tuple[Mapping[str, Any], ...] = ()
    expected_quality: str = PITQualityStatus.VERIFIED.value

    @property
    def quality_status(self) -> UniverseAssuranceStatus:
        """Compatibility alias used by stage-gate callers."""

        return self.status

    @property
    def failure_reasons(self) -> tuple[str, ...]:
        return self.reason_codes

    @property
    def counts(self) -> Mapping[str, int]:
        return self.gate_counts

    @property
    def gates(self) -> Mapping[str, bool]:
        return {name: count == 0 for name, count in self.gate_counts.items()}


def assure_universe(
    resolutions: UniverseResolution | Mapping[str, Any] | Iterable[UniverseResolution | Mapping[str, Any]],
    expected_quality: PITQualityStatus | str = PITQualityStatus.VERIFIED,
    *,
    expected_snapshot_version: int | None = None,
) -> UniverseAssuranceResult:
    """Apply the zero-count gates to PIT resolutions or serialized evidence.

    A missing cross-source audit, an unverified quality state, and missing
    knowledge timestamps are evidence gaps, not reasons to reinterpret the
    data as valid.  All non-passing outcomes therefore remain
    ``INCONCLUSIVE_UNIVERSE`` for this validation layer.
    """

    records = _materialize(resolutions)
    counts = {name: 0 for name in _GATES}
    reasons: set[str] = set()
    quality_statuses: list[str] = []
    details: list[Mapping[str, Any]] = []
    source_snapshots: set[int] = set()
    invalid_evidence = False

    expected_quality_value = _enum_value(expected_quality)
    for record in records:
        metrics = _mapping_value(record, "audit_metrics")
        exclusions = _all_exclusions(record)
        cross_source = _mapping_value(record, "cross_source_audit")

        eligible = _as_items(_value(record, "eligible", ()))
        if not eligible or not metrics:
            invalid_evidence = True
            reasons.add("SPARSE_UNIVERSE_EVIDENCE")
        invalid_count_fields = _invalid_count_fields(metrics)
        if invalid_count_fields:
            invalid_evidence = True
            reasons.add("INVALID_AUDIT_COUNT")
        if not cross_source or "reconciliation_status" not in cross_source:
            invalid_evidence = True
            reasons.add("MISSING_CROSS_SOURCE_AUDIT")

        if not _valid_timestamp(_value(record, "signal_date"), allow_missing=False):
            invalid_evidence = True
            reasons.add("INVALID_UNIVERSE_DATE")
        if not _valid_timestamp(_value(record, "knowledge_cutoff"), allow_missing=False):
            invalid_evidence = True
            reasons.add("INVALID_UNIVERSE_DATE")
        for instrument in eligible:
            for field in ("known_from", "valid_from", "valid_to", "list_date", "delist_date"):
                if not _valid_timestamp(_value(instrument, field), allow_missing=True):
                    invalid_evidence = True
                    reasons.add("INVALID_UNIVERSE_DATE")

        counts["unresolved_cross_source_conflicts"] += _cross_source_count(metrics, cross_source)
        counts["overlapping_valid_periods"] += max(
            _int_value(metrics, "overlapping_valid_range_count"),
            _exclusion_count(exclusions, ExclusionReasonCode.OVERLAPPING_VALID_RANGE),
        )
        counts["unordered_revisions"] += max(
            _int_value(metrics, "ambiguous_revision_order_count"),
            _int_value(metrics, "overlapping_knowledge_range_count"),
            _exclusion_count(exclusions, ExclusionReasonCode.AMBIGUOUS_REVISION_ORDER),
        )
        counts["included_before_listing"] += max(
            _int_value(metrics, "included_before_listing_count"),
            _exclusion_count(exclusions, ExclusionReasonCode.NOT_YET_LISTED),
        )
        counts["included_after_delisting"] += max(
            _int_value(metrics, "included_after_delisting_count"),
            _exclusion_count(exclusions, ExclusionReasonCode.DELISTED),
        )

        lifecycle_counts, lifecycle_reasons = _included_lifecycle_violations(
            eligible, _value(record, "signal_date")
        )
        counts["included_before_listing"] += lifecycle_counts["included_before_listing"]
        counts["included_after_delisting"] += lifecycle_counts["included_after_delisting"]
        reasons.update(lifecycle_reasons)
        counts["included_without_knowledge_time"] += _int_value(
            metrics, "knowledge_time_unverified_count"
        )
        counts["included_without_knowledge_time"] += sum(
            1 for instrument in _as_items(eligible) if _value(instrument, "known_from") in (None, "")
        )
        counts["selected_when_not_tradable"] += _int_value(
            metrics, "selected_when_not_tradable_count"
        )
        counts["selected_when_not_tradable"] += _selected_not_tradable_count(record, exclusions)

        source_snapshot = _value(record, "source_snapshot_version")
        normalized_source_snapshot = _snapshot_version(source_snapshot)
        if normalized_source_snapshot is None:
            invalid_evidence = True
            reasons.add(
                "MISSING_UNIVERSE_SNAPSHOT_VERSION"
                if source_snapshot is None
                else "INVALID_UNIVERSE_SNAPSHOT_VERSION"
            )
            counts["decision_universe_snapshot_mismatch"] += 1
        else:
            source_snapshots.add(normalized_source_snapshot)
        decision_snapshot = _value(record, "decision_snapshot_version")
        if decision_snapshot is None:
            decision_snapshot = _value(record, "expected_snapshot_version")
        normalized_decision_snapshot = _snapshot_version(decision_snapshot)
        if decision_snapshot is not None and normalized_decision_snapshot is None:
            invalid_evidence = True
            reasons.add("INVALID_UNIVERSE_SNAPSHOT_VERSION")
            counts["decision_universe_snapshot_mismatch"] += 1
        if (
            normalized_decision_snapshot is not None
            and normalized_source_snapshot is not None
            and normalized_source_snapshot != normalized_decision_snapshot
        ):
            counts["decision_universe_snapshot_mismatch"] += 1

        quality = _enum_value(_value(record, "quality_status"))
        if quality:
            quality_statuses.append(quality)
            if quality != expected_quality_value:
                reasons.add("QUALITY_STATUS_NOT_VERIFIED")
        details.append(
            {
                "signal_date": str(_value(record, "signal_date", "")),
                "source_snapshot_version": normalized_source_snapshot,
                "quality_status": quality,
            }
        )

        _append_exclusion_reasons(reasons, exclusions)
        _append_metric_reasons(reasons, metrics)

    if expected_snapshot_version is not None:
        counts["decision_universe_snapshot_mismatch"] += sum(
            1 for snapshot in source_snapshots if snapshot != expected_snapshot_version
        )
    if len(source_snapshots) > 1:
        counts["decision_universe_snapshot_mismatch"] += len(source_snapshots) - 1
        reasons.add("DECISION_UNIVERSE_SNAPSHOT_MISMATCH")

    if not records:
        reasons.add("EMPTY_UNIVERSE_EVIDENCE")

    if any(counts.values()) or invalid_evidence:
        status = UniverseAssuranceStatus.INCONCLUSIVE_UNIVERSE
    elif not quality_statuses or any(status != expected_quality_value for status in quality_statuses):
        reasons.add("QUALITY_STATUS_NOT_VERIFIED")
        status = UniverseAssuranceStatus.INCONCLUSIVE_UNIVERSE
    else:
        status = UniverseAssuranceStatus.VERIFIED

    return UniverseAssuranceResult(
        status=status,
        passed=status is UniverseAssuranceStatus.VERIFIED,
        gate_counts=counts,
        reason_codes=tuple(sorted(reasons)),
        quality_statuses=tuple(quality_statuses),
        details=tuple(details),
        expected_quality=expected_quality_value,
    )


def _materialize(
    resolutions: UniverseResolution | Mapping[str, Any] | Iterable[UniverseResolution | Mapping[str, Any]],
) -> tuple[UniverseResolution | Mapping[str, Any], ...]:
    if isinstance(resolutions, UniverseResolution) or isinstance(resolutions, Mapping):
        return (resolutions,)
    return tuple(resolutions)


def _mapping_value(record: object, key: str) -> Mapping[str, Any]:
    value = _value(record, key, {})
    return value if isinstance(value, Mapping) else {}


def _value(record: object, key: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(key, default)
    return getattr(record, key, default)


def _enum_value(value: object) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return "" if value is None else str(value)


def _int_value(mapping: Mapping[str, Any], key: str) -> int:
    value = mapping.get(key, 0)
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _invalid_count_fields(metrics: Mapping[str, Any]) -> tuple[str, ...]:
    invalid: list[str] = []
    for key in _AUDIT_COUNT_FIELDS:
        if key not in metrics:
            continue
        value = metrics[key]
        try:
            number = float(value)
        except (TypeError, ValueError):
            invalid.append(key)
            continue
        if not math.isfinite(number) or number < 0 or not number.is_integer():
            invalid.append(key)
    return tuple(invalid)


def _snapshot_version(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0 or not number.is_integer():
        return None
    return int(number)


def _valid_timestamp(value: object, *, allow_missing: bool) -> bool:
    if value is None or value == "":
        return allow_missing
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return not pd.isna(timestamp)


def _as_items(value: object) -> tuple[object, ...]:
    if value is None or isinstance(value, (str, bytes)):
        return ()
    try:
        return tuple(value)  # type: ignore[arg-type]
    except TypeError:
        return ()


def _all_exclusions(record: object) -> tuple[object, ...]:
    return tuple(
        exclusion
        for layer in ("master_exclusions", "strategy_exclusions", "tradable_exclusions")
        for exclusion in _as_items(_value(record, layer, ()))
    )


def _reason_value(exclusion: object) -> str:
    reason = _value(exclusion, "reason_code", "")
    return _enum_value(reason)


def _exclusion_count(exclusions: tuple[object, ...], reason: ExclusionReasonCode) -> int:
    return sum(_reason_value(exclusion) == reason.value for exclusion in exclusions)


def _cross_source_count(metrics: Mapping[str, Any], audit: Mapping[str, Any]) -> int:
    metric_count = sum(
        _int_value(metrics, key)
        for key in (
            "cross_source_missing_count",
            "status_conflict_count",
            "date_conflict_count",
            "classification_conflict_count",
        )
    )
    audit_count = sum(
        len(_as_items(audit.get(key)))
        for key in (
            "only_in_a",
            "only_in_b",
            "status_conflicts",
            "date_conflicts",
            "classification_conflicts",
        )
    )
    if audit.get("reconciliation_status") in {"CONFLICTS", "MISSING_CODES"} and not (metric_count or audit_count):
        return 1
    if audit.get("reconciliation_status") == "NOT_PROVIDED" and not metric_count:
        return 1
    return max(metric_count, audit_count)


def _selected_not_tradable_count(record: object, exclusions: tuple[object, ...]) -> int:
    selected = set(str(code) for code in _as_items(_value(record, "selected_codes", ())))
    excluded = {
        str(_value(exclusion, "ts_code", ""))
        for exclusion in exclusions
        if _reason_value(exclusion) == ExclusionReasonCode.NOT_TRADABLE.value
    }
    return len(selected & excluded)


def _included_lifecycle_violations(
    instruments: tuple[object, ...], signal_date: object
) -> tuple[dict[str, int], set[str]]:
    counts = {"included_before_listing": 0, "included_after_delisting": 0}
    reasons: set[str] = set()
    if signal_date in (None, ""):
        return counts, reasons
    try:
        signal_ts = pd.Timestamp(signal_date)
    except (TypeError, ValueError):
        return counts, reasons
    for instrument in instruments:
        list_date = _value(instrument, "list_date")
        delist_date = _value(instrument, "delist_date")
        try:
            if list_date not in (None, "") and pd.Timestamp(list_date) > signal_ts:
                counts["included_before_listing"] += 1
                reasons.add(ExclusionReasonCode.NOT_YET_LISTED.value)
            if delist_date not in (None, "") and pd.Timestamp(delist_date) <= signal_ts:
                counts["included_after_delisting"] += 1
                reasons.add(ExclusionReasonCode.DELISTED.value)
        except (TypeError, ValueError):
            continue
    return counts, reasons


def _append_exclusion_reasons(reasons: set[str], exclusions: tuple[object, ...]) -> None:
    for exclusion in exclusions:
        reason = _reason_value(exclusion)
        if reason in {
            ExclusionReasonCode.OVERLAPPING_VALID_RANGE.value,
            ExclusionReasonCode.AMBIGUOUS_REVISION_ORDER.value,
            ExclusionReasonCode.NOT_TRADABLE.value,
        }:
            reasons.add(reason)


def _append_metric_reasons(reasons: set[str], metrics: Mapping[str, Any]) -> None:
    mappings = {
        "cross_source_missing_count": "CROSS_SOURCE_COVERAGE_GAP",
        "status_conflict_count": "STATUS_CONFLICT",
        "date_conflict_count": "DATE_CONFLICT",
        "classification_conflict_count": "CLASSIFICATION_CONFLICT",
        "overlapping_valid_range_count": "OVERLAPPING_VALID_RANGE",
        "overlapping_knowledge_range_count": "AMBIGUOUS_REVISION_ORDER",
        "ambiguous_revision_order_count": "AMBIGUOUS_REVISION_ORDER",
        "knowledge_time_unverified_count": "MISSING_KNOWN_FROM",
    }
    for key, reason in mappings.items():
        if _int_value(metrics, key):
            reasons.add(reason)
