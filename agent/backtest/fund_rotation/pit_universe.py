"""Point-in-time fund master and universe resolver vertical slice.

This module intentionally avoids database dependencies.  The first adapter
boundary accepts either a pandas DataFrame or an iterable of record dicts so a
future Lance/SQLite reader can materialize rows without changing resolver code.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from enum import Enum
from types import MappingProxyType
from typing import Iterable, Mapping, Protocol

import pandas as pd
import numpy as np


class PITQueryMode(str, Enum):
    """How PIT rows are selected from the snapshot."""

    AS_WAS_KNOWN = "AS_WAS_KNOWN"
    LATEST_RESTATED = "LATEST_RESTATED"


class PITQualityStatus(str, Enum):
    """Quality status for PIT inputs and resolved universes."""

    VERIFIED = "VERIFIED"
    KNOWLEDGE_TIME_UNVERIFIED = "KNOWLEDGE_TIME_UNVERIFIED"
    PIT_UNVERIFIED = "PIT_UNVERIFIED"
    RESEARCH_ONLY_UNVERIFIED_UNIVERSE = "RESEARCH_ONLY_UNVERIFIED_UNIVERSE"
    PIT_INVALID = "PIT_INVALID"


class ExclusionLayer(str, Enum):
    """Universe layer that produced an exclusion."""

    FUND_MASTER = "FUND_MASTER"
    STRATEGY = "STRATEGY"
    TRADABLE = "TRADABLE"


class ExclusionReasonCode(str, Enum):
    """Stable reason codes for audit output."""

    MISSING_SNAPSHOT_VERSION = "MISSING_SNAPSHOT_VERSION"
    OVERLAPPING_VALID_RANGE = "OVERLAPPING_VALID_RANGE"
    INVALID_LIFECYCLE_DATES = "INVALID_LIFECYCLE_DATES"
    NOT_VALID_ON_SIGNAL_DATE = "NOT_VALID_ON_SIGNAL_DATE"
    KNOWN_AFTER_CUTOFF = "KNOWN_AFTER_CUTOFF"
    MISSING_KNOWN_FROM = "MISSING_KNOWN_FROM"
    AMBIGUOUS_REVISION_ORDER = "AMBIGUOUS_REVISION_ORDER"
    INVALID_LIST_DATE = "INVALID_LIST_DATE"
    NOT_YET_LISTED = "NOT_YET_LISTED"
    MISSING_DELIST_DATE = "MISSING_DELIST_DATE"
    DELISTED = "DELISTED"
    UNKNOWN_STATUS = "UNKNOWN_STATUS"
    STATUS_NOT_ALLOWED = "STATUS_NOT_ALLOWED"
    UNKNOWN_TYPE = "UNKNOWN_TYPE"
    ASSET_CLASS_NOT_ALLOWED = "ASSET_CLASS_NOT_ALLOWED"
    FUND_TYPE_NOT_ALLOWED = "FUND_TYPE_NOT_ALLOWED"
    EXCHANGE_NOT_ALLOWED = "EXCHANGE_NOT_ALLOWED"
    NOT_INCLUDED_BY_POLICY = "NOT_INCLUDED_BY_POLICY"
    EXCLUDED_BY_POLICY = "EXCLUDED_BY_POLICY"
    NOT_TRADABLE = "NOT_TRADABLE"
    MISSING_IDENTITY = "MISSING_IDENTITY"
    DUPLICATE_IDENTITY = "DUPLICATE_IDENTITY"
    INVALID_QUERY_TIMEZONE = "INVALID_QUERY_TIMEZONE"


@dataclass(frozen=True)
class FundInstrumentVersion:
    """One selected fund master version visible to the requested query."""

    ts_code: str
    valid_from: str
    valid_to: str | None
    known_from: str | None
    revision_id: str | None
    source_id: str | None
    source_record_id: str | None
    source_published_at: str | None
    ingested_at: str | None
    list_date: str | None
    delist_date: str | None
    fund_status: str | None
    name: str | None
    fund_type: str | None
    asset_class: str | None
    tracking_index: str | None
    exchange: str | None
    quality_status: PITQualityStatus
    instrument_type: str | None = None
    underlying_index: str | None = None
    region: str | None = None
    currency: str | None = None
    leveraged_or_inverse: object | None = None
    share_class_or_feeder_relationship: str | None = None


@dataclass(frozen=True)
class UniverseExclusion:
    """One stable universe exclusion event."""

    layer: ExclusionLayer
    ts_code: str
    reason_code: ExclusionReasonCode
    details: str = ""
    signal_date: str = ""


@dataclass(frozen=True)
class UniversePolicy:
    """Strategy-layer universe constraints.

    Empty sets mean "no restriction" for the corresponding policy dimension.
    """

    asset_classes: frozenset[str] = field(default_factory=frozenset)
    fund_types: frozenset[str] = field(default_factory=frozenset)
    exchanges: frozenset[str] = field(default_factory=frozenset)
    include_ts_codes: frozenset[str] = field(default_factory=frozenset)
    exclude_ts_codes: frozenset[str] = field(default_factory=frozenset)
    allowed_statuses: frozenset[str] = field(
        default_factory=lambda: frozenset({"ACTIVE", "LISTED", "L"})
    )


@dataclass(frozen=True)
class UniverseResolution:
    """Resolved three-layer universe plus quality and audit evidence."""

    eligible: tuple[FundInstrumentVersion, ...]
    master_exclusions: tuple[UniverseExclusion, ...]
    strategy_exclusions: tuple[UniverseExclusion, ...]
    tradable_exclusions: tuple[UniverseExclusion, ...]
    source_snapshot_version: int | None
    signal_date: str
    knowledge_cutoff: str
    query_mode: PITQueryMode
    audit_metrics: Mapping[str, int]
    quality_status: PITQualityStatus
    cross_source_audit: Mapping[str, object] = field(default_factory=dict)
    identity_mapping: Mapping[str, str | None] = field(default_factory=dict)
    identity_hash: str = ""
    snapshot_fingerprint: str = ""
    coverage_diagnostics: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class UniverseMembership:
    """One immutable per-date membership decision for U0 or U1."""

    ts_code: str
    included: bool
    reason_code: str
    identity_key: str | None = None
    layer: str = ""


@dataclass(frozen=True)
class PITUniverseSnapshot:
    """Immutable, reproducible U0 or U1 snapshot for one decision date."""

    layer: str
    signal_date: str
    knowledge_cutoff: str
    source_snapshot_version: int | None
    eligible_codes: tuple[str, ...]
    membership: tuple[UniverseMembership, ...]
    identity_mapping: Mapping[str, str | None]
    identity_hash: str
    snapshot_fingerprint: str
    coverage_diagnostics: Mapping[str, object]
    quality_status: PITQualityStatus

    @property
    def members(self) -> tuple[str, ...]:
        return self.eligible_codes

    @property
    def membership_reasons(self) -> Mapping[str, str]:
        return {item.ts_code: item.reason_code for item in self.membership}


@dataclass(frozen=True)
class PITIdentityLayers:
    """The U0 base snapshot and its fail-closed U1 identity projection."""

    u0: PITUniverseSnapshot
    u1: PITUniverseSnapshot
    resolution: UniverseResolution

    @property
    def quality_status(self) -> PITQualityStatus:
        if self.u1.quality_status == PITQualityStatus.PIT_INVALID:
            return self.u1.quality_status
        return self.resolution.quality_status


class CausalDataView(Protocol):
    """Minimal tradability interface for future market-data adapters."""

    def tradable_status(self, ts_code: str, signal_date: str) -> bool | tuple[bool, str]:
        ...


@dataclass(frozen=True)
class _MasterSelection:
    instruments: tuple[FundInstrumentVersion, ...]
    exclusions: tuple[UniverseExclusion, ...]
    audit_metrics: dict[str, int]
    fatal: bool = False
    research_only: bool = False


def _invalid_master_selection(signal_date: str, details: str) -> _MasterSelection:
    metrics = _empty_metrics()
    metrics["master_excluded_count"] = 1
    return _MasterSelection(
        instruments=(),
        exclusions=(
            _exclusion(
                ExclusionLayer.FUND_MASTER,
                "__QUERY__",
                ExclusionReasonCode.INVALID_QUERY_TIMEZONE,
                signal_date,
                details=details,
            ),
        ),
        audit_metrics=metrics,
        fatal=True,
    )


class PITFundMaster:
    """In-memory PIT fund master backed by DataFrame-like records."""

    def __init__(self, records: pd.DataFrame | Iterable[Mapping[str, object]]):
        if isinstance(records, pd.DataFrame):
            self._records = records.copy()
        else:
            self._records = pd.DataFrame(list(records))

    def instruments_at(
        self,
        signal_date: str,
        knowledge_cutoff: str,
        snapshot_version: int,
        mode: PITQueryMode = PITQueryMode.AS_WAS_KNOWN,
    ) -> tuple[FundInstrumentVersion, ...]:
        """Return fund instruments valid and knowable for the requested view."""

        return self._resolve_master(signal_date, knowledge_cutoff, snapshot_version, mode).instruments

    def _resolve_master(
        self,
        signal_date: str,
        knowledge_cutoff: str,
        snapshot_version: int,
        mode: PITQueryMode,
    ) -> _MasterSelection:
        try:
            signal_ts = _to_timestamp(signal_date)
            cutoff_ts = _to_timestamp(knowledge_cutoff)
        except (TypeError, ValueError, OverflowError) as exc:
            return _invalid_master_selection(signal_date, f"invalid query datetime: {exc}")
        if (signal_ts.tzinfo is None) != (cutoff_ts.tzinfo is None):
            return _invalid_master_selection(
                signal_date,
                "signal_date and knowledge_cutoff must use compatible timezone forms",
            )
        rows = self._rows_for_snapshot(snapshot_version)
        if _has_query_timezone_mismatch(rows, signal_ts, cutoff_ts):
            return _invalid_master_selection(
                signal_date,
                "query datetime timezone form does not match PIT source timestamps",
            )
        metrics = _empty_metrics()
        exclusions: list[UniverseExclusion] = []
        instruments: list[FundInstrumentVersion] = []

        if rows.empty or "ts_code" not in rows.columns:
            metrics["eligible_count"] = 0
            return _MasterSelection((), (), metrics)

        metrics["master_codes"] = int(rows["ts_code"].dropna().astype(str).nunique())
        metrics["overlapping_valid_range_count"] = _count_overlapping_ranges(
            rows, "valid_from", "valid_to"
        )
        metrics["overlapping_knowledge_range_count"] = _count_same_knowledge_ties(rows)
        fatal = False
        research_only = False

        integrity_exclusions = _snapshot_integrity_exclusions(rows, signal_date)
        if integrity_exclusions:
            exclusions.extend(integrity_exclusions)
            fatal = True
            exclusions.sort(key=_exclusion_sort_key)
            metrics["master_excluded_count"] = len(exclusions)
            metrics["eligible_count"] = 0
            return _MasterSelection((), tuple(exclusions), metrics, fatal=True)

        for ts_code in sorted(str(code) for code in rows["ts_code"].dropna().unique()):
            code_rows = rows[rows["ts_code"].astype(str) == ts_code].copy()
            valid_rows = code_rows[code_rows.apply(lambda row: _is_valid_on(row, signal_ts), axis=1)]
            if valid_rows.empty:
                exclusions.append(
                    _exclusion(
                        ExclusionLayer.FUND_MASTER,
                        ts_code,
                        ExclusionReasonCode.NOT_VALID_ON_SIGNAL_DATE,
                        signal_date,
                    )
                )
                continue

            if mode == PITQueryMode.AS_WAS_KNOWN:
                known_mask = valid_rows.apply(
                    lambda row: _known_from(row) is not None and _known_from(row) <= cutoff_ts,
                    axis=1,
                )
                candidate_rows = valid_rows[known_mask]
                if candidate_rows.empty:
                    reason = (
                        ExclusionReasonCode.KNOWN_AFTER_CUTOFF
                        if valid_rows["known_from"].apply(_has_value).any()
                        else ExclusionReasonCode.MISSING_KNOWN_FROM
                    )
                    exclusions.append(
                        _exclusion(ExclusionLayer.FUND_MASTER, ts_code, reason, signal_date)
                    )
                    continue
            else:
                candidate_rows = valid_rows

            selected, ambiguous = _select_latest_known(candidate_rows)
            if ambiguous or selected is None:
                exclusions.append(
                    _exclusion(
                        ExclusionLayer.FUND_MASTER,
                        ts_code,
                        ExclusionReasonCode.AMBIGUOUS_REVISION_ORDER,
                        signal_date,
                    )
                )
                fatal = True
                continue

            instrument = _instrument_from_row(selected)
            exclusion = _master_eligibility_exclusion(instrument, signal_ts, signal_date)
            if exclusion is not None:
                exclusions.append(exclusion)
                if exclusion.reason_code == ExclusionReasonCode.MISSING_DELIST_DATE:
                    metrics["missing_delist_date_count"] += 1
                    research_only = True
                if exclusion.reason_code == ExclusionReasonCode.INVALID_LIST_DATE:
                    fatal = True
                if exclusion.reason_code == ExclusionReasonCode.UNKNOWN_STATUS:
                    metrics["unknown_status_count"] += 1
                if exclusion.reason_code == ExclusionReasonCode.UNKNOWN_TYPE:
                    metrics["unknown_type_count"] += 1
                if exclusion.reason_code == ExclusionReasonCode.DELISTED:
                    metrics["delisted_count"] += 1
                continue

            instruments.append(instrument)
            metrics["listed_count"] += 1
            if instrument.fund_status is not None and instrument.fund_status.upper() in {
                "ACTIVE",
                "LISTED",
                "L",
            }:
                metrics["active_count"] += 1
            if instrument.delist_date is None:
                metrics["missing_delist_date_count"] += 1
            if instrument.fund_type is None:
                metrics["unknown_type_count"] += 1
            if instrument.quality_status == PITQualityStatus.KNOWLEDGE_TIME_UNVERIFIED:
                metrics["knowledge_time_unverified_count"] += 1

        instruments.sort(key=lambda item: item.ts_code)
        exclusions.sort(key=_exclusion_sort_key)
        if fatal:
            instruments = []
        metrics["eligible_count"] = len(instruments)
        metrics["master_excluded_count"] = len(exclusions)
        return _MasterSelection(
            tuple(instruments),
            tuple(exclusions),
            metrics,
            fatal=fatal,
            research_only=research_only,
        )

    def _rows_for_snapshot(self, snapshot_version: int) -> pd.DataFrame:
        if self._records.empty or "snapshot_version" not in self._records.columns:
            return self._records.copy()
        return self._records[self._records["snapshot_version"] <= snapshot_version].copy()


class UniverseResolver:
    """Resolve fund-master, strategy, and tradable universes."""

    def __init__(self, fund_master: PITFundMaster):
        self._fund_master = fund_master

    def resolve(
        self,
        signal_date: str,
        knowledge_cutoff: str,
        strategy_policy: UniversePolicy,
        causal_view: CausalDataView,
        snapshot_version: int,
        mode: PITQueryMode,
        cross_source_audit: Mapping[str, object] | None = None,
    ) -> UniverseResolution:
        if mode is None:
            raise TypeError("mode is required for PIT resolution")
        if snapshot_version is None:
            metrics = _empty_metrics()
            metrics["master_excluded_count"] = 1
            return UniverseResolution(
                eligible=(),
                master_exclusions=(
                    _exclusion(
                        ExclusionLayer.FUND_MASTER,
                        "__SNAPSHOT__",
                        ExclusionReasonCode.MISSING_SNAPSHOT_VERSION,
                        signal_date,
                        details="snapshot_version is required for PIT resolution",
                    ),
                ),
                strategy_exclusions=(),
                tradable_exclusions=(),
                source_snapshot_version=None,
                signal_date=_format_date(signal_date),
                knowledge_cutoff=_format_datetime(knowledge_cutoff),
                query_mode=mode,
                audit_metrics=metrics,
                quality_status=PITQualityStatus.PIT_INVALID,
                cross_source_audit=_cross_source_audit_output(cross_source_audit)[0],
            )

        master = self._fund_master._resolve_master(
            signal_date=signal_date,
            knowledge_cutoff=knowledge_cutoff,
            snapshot_version=snapshot_version,
            mode=mode,
        )

        strategy_pass: list[FundInstrumentVersion] = []
        strategy_exclusions: list[UniverseExclusion] = []
        for instrument in master.instruments:
            reason = _strategy_exclusion_reason(instrument, strategy_policy)
            if reason is None:
                strategy_pass.append(instrument)
            else:
                strategy_exclusions.append(
                    _exclusion(
                        ExclusionLayer.STRATEGY,
                        instrument.ts_code,
                        reason,
                        signal_date,
                    )
                )

        eligible: list[FundInstrumentVersion] = []
        tradable_exclusions: list[UniverseExclusion] = []
        for instrument in strategy_pass:
            tradable, details = _tradable_status(causal_view, instrument.ts_code, signal_date)
            if tradable:
                eligible.append(instrument)
            else:
                tradable_exclusions.append(
                    _exclusion(
                        ExclusionLayer.TRADABLE,
                        instrument.ts_code,
                        ExclusionReasonCode.NOT_TRADABLE,
                        signal_date,
                        details=details,
                    )
                )

        eligible.sort(key=lambda item: item.ts_code)
        strategy_exclusions.sort(key=_exclusion_sort_key)
        tradable_exclusions.sort(key=_exclusion_sort_key)

        metrics = dict(master.audit_metrics)
        metrics["eligible_count"] = len(eligible)
        metrics["strategy_excluded_count"] = len(strategy_exclusions)
        metrics["tradable_excluded_count"] = len(tradable_exclusions)
        cross_audit_output, cross_audit_status = _cross_source_audit_output(cross_source_audit)
        metrics["cross_source_missing_count"] = int(
            cross_audit_output.get("cross_source_missing_count", 0)
        )
        metrics["status_conflict_count"] = int(cross_audit_output.get("status_conflict_count", 0))
        metrics["date_conflict_count"] = int(cross_audit_output.get("date_conflict_count", 0))

        quality_status = _resolution_quality(tuple(eligible), master, strategy_exclusions, tradable_exclusions)
        quality_status = _combine_quality(quality_status, cross_audit_status)
        resolution = UniverseResolution(
            eligible=tuple(eligible),
            master_exclusions=master.exclusions,
            strategy_exclusions=tuple(strategy_exclusions),
            tradable_exclusions=tuple(tradable_exclusions),
            source_snapshot_version=snapshot_version,
            signal_date=_format_date(signal_date),
            knowledge_cutoff=_format_datetime(knowledge_cutoff),
            query_mode=mode,
            audit_metrics=metrics,
            quality_status=quality_status,
            cross_source_audit=cross_audit_output,
        )
        return _attach_u0_metadata(resolution)

    def resolve_identity_layers(
        self,
        signal_date: str,
        knowledge_cutoff: str,
        strategy_policy: UniversePolicy,
        causal_view: CausalDataView,
        snapshot_version: int,
        mode: PITQueryMode,
        cross_source_audit: Mapping[str, object] | None = None,
    ) -> PITIdentityLayers:
        """Resolve the existing PIT universe, then derive immutable U0/U1 layers.

        The base resolver remains the source of truth for AS_WAS_KNOWN and its
        legacy diagnostics. U1 is only a deterministic, fail-closed projection
        of U0 and never changes the base eligible universe.
        """

        resolution = self.resolve(
            signal_date=signal_date,
            knowledge_cutoff=knowledge_cutoff,
            strategy_policy=strategy_policy,
            causal_view=causal_view,
            snapshot_version=snapshot_version,
            mode=mode,
            cross_source_audit=cross_source_audit,
        )
        u0 = _snapshot_from_resolution(resolution, "U0")
        u1 = _u1_snapshot(u0)
        return PITIdentityLayers(u0=u0, u1=u1, resolution=resolution)


def _attach_u0_metadata(resolution: UniverseResolution) -> UniverseResolution:
    """Add U0 identity metadata without changing legacy resolution fields."""

    snapshot = _snapshot_from_resolution(resolution, "U0")
    return replace(
        resolution,
        identity_mapping=MappingProxyType(dict(snapshot.identity_mapping)),
        identity_hash=snapshot.identity_hash,
        snapshot_fingerprint=snapshot.snapshot_fingerprint,
        coverage_diagnostics=snapshot.coverage_diagnostics,
    )


def _snapshot_from_resolution(
    resolution: UniverseResolution,
    layer: str,
) -> PITUniverseSnapshot:
    all_codes = sorted(
        {
            instrument.ts_code
            for instrument in resolution.eligible
        }
        | {
            exclusion.ts_code
            for exclusion in (
                *resolution.master_exclusions,
                *resolution.strategy_exclusions,
                *resolution.tradable_exclusions,
            )
        }
    )
    instruments = {instrument.ts_code: instrument for instrument in resolution.eligible}
    exclusions: dict[str, UniverseExclusion] = {}
    for exclusion in (
        *resolution.master_exclusions,
        *resolution.strategy_exclusions,
        *resolution.tradable_exclusions,
    ):
        exclusions.setdefault(exclusion.ts_code, exclusion)

    membership = []
    identity_mapping: dict[str, str | None] = {}
    for code in all_codes:
        instrument = instruments.get(code)
        identity = _identity_key(instrument) if instrument is not None else None
        identity_mapping[code] = identity
        if instrument is not None:
            membership.append(
                UniverseMembership(
                    ts_code=code,
                    included=True,
                    reason_code=f"{layer}_ELIGIBLE",
                    identity_key=identity,
                    layer=layer,
                )
            )
        else:
            exclusion = exclusions[code]
            membership.append(
                UniverseMembership(
                    ts_code=code,
                    included=False,
                    reason_code=exclusion.reason_code.value,
                    identity_key=None,
                    layer=layer,
                )
            )

    coverage = dict(resolution.audit_metrics)
    coverage.update(
        {
            "momentum_coverage": "unavailable",
            "max_cluster_share": "unavailable",
            "effective_cluster_count": "unavailable",
            "tradable_representative_ratio": "unavailable",
        }
    )
    known_before_cutoff_count = sum(
        1
        for instrument in resolution.eligible
        if instrument.known_from is not None
        and _to_timestamp(instrument.known_from) <= _to_timestamp(resolution.knowledge_cutoff)
    )
    coverage.update(
        {
            "available_count": len(resolution.eligible),
            "known_before_cutoff_count": known_before_cutoff_count,
            "identity_coverage_count": sum(value is not None for value in identity_mapping.values()),
            "identity_coverage_ratio": (
                sum(value is not None for value in identity_mapping.values()) / len(identity_mapping)
                if identity_mapping
                else 0.0
            ),
            "future_known_excluded_count": sum(
                exclusion.reason_code == ExclusionReasonCode.KNOWN_AFTER_CUTOFF
                for exclusion in resolution.master_exclusions
            ),
            "boundary_uncertain_count": sum(
                exclusion.reason_code
                in {
                    ExclusionReasonCode.MISSING_KNOWN_FROM,
                    ExclusionReasonCode.AMBIGUOUS_REVISION_ORDER,
                }
                for exclusion in resolution.master_exclusions
            ),
        }
    )
    identity_hash = _stable_hash(
        {
            "layer": layer,
            "identity_mapping": sorted(identity_mapping.items()),
        }
    )
    fingerprint = _stable_hash(
        {
            "layer": layer,
            "signal_date": resolution.signal_date,
            "knowledge_cutoff": resolution.knowledge_cutoff,
            "source_snapshot_version": resolution.source_snapshot_version,
            "eligible_codes": sorted(instrument.ts_code for instrument in resolution.eligible),
            "membership": [_membership_payload(item) for item in membership],
            "identity_hash": identity_hash,
            "quality_status": resolution.quality_status.value,
            "coverage_diagnostics": dict(sorted(coverage.items())),
        }
    )
    return PITUniverseSnapshot(
        layer=layer,
        signal_date=resolution.signal_date,
        knowledge_cutoff=resolution.knowledge_cutoff,
        source_snapshot_version=resolution.source_snapshot_version,
        eligible_codes=tuple(sorted(instrument.ts_code for instrument in resolution.eligible)),
        membership=tuple(membership),
        identity_mapping=MappingProxyType(dict(sorted(identity_mapping.items()))),
        identity_hash=identity_hash,
        snapshot_fingerprint=fingerprint,
        coverage_diagnostics=MappingProxyType(dict(sorted(coverage.items()))),
        quality_status=resolution.quality_status,
    )


def _u1_snapshot(u0: PITUniverseSnapshot) -> PITUniverseSnapshot:
    grouped: dict[str, list[str]] = {}
    missing_identity: set[str] = set()
    for code in u0.eligible_codes:
        identity = u0.identity_mapping.get(code)
        if identity is None:
            missing_identity.add(code)
        else:
            grouped.setdefault(identity, []).append(code)

    representatives = {
        identity: min(codes)
        for identity, codes in grouped.items()
    }
    membership = []
    eligible_codes = set(u0.eligible_codes)
    for item in u0.membership:
        code = item.ts_code
        if code not in eligible_codes:
            membership.append(
                UniverseMembership(
                    ts_code=code,
                    included=False,
                    reason_code=item.reason_code,
                    identity_key=item.identity_key,
                    layer="U1",
                )
            )
            continue
        identity = u0.identity_mapping.get(code)
        if identity is None:
            reason = ExclusionReasonCode.MISSING_IDENTITY.value
            included = False
        elif representatives[identity] == code:
            reason = "U1_REPRESENTATIVE"
            included = True
        else:
            reason = ExclusionReasonCode.DUPLICATE_IDENTITY.value
            included = False
        membership.append(
            UniverseMembership(
                ts_code=code,
                included=included,
                reason_code=reason,
                identity_key=identity,
                layer="U1",
            )
        )

    duplicate_count = sum(max(len(codes) - 1, 0) for codes in grouped.values())
    coverage = dict(u0.coverage_diagnostics)
    coverage.update(
        {
            "available_count": len(representatives) if not missing_identity else 0,
            "u0_available_count": len(u0.eligible_codes),
            "u1_available_count": len(representatives) if not missing_identity else 0,
            "duplicate_identity_count": duplicate_count,
            "duplicate_identity_ratio": (
                duplicate_count / len(u0.eligible_codes)
                if u0.eligible_codes
                else 0.0
            ),
            "missing_identity_count": len(missing_identity),
        }
    )
    identity_mapping = dict(sorted(u0.identity_mapping.items()))
    identity_hash = _stable_hash(
        {
            "layer": "U1",
            "identity_mapping": sorted(identity_mapping.items()),
            "representatives": sorted(representatives.items()),
        }
    )
    quality = (
        PITQualityStatus.PIT_INVALID
        if missing_identity
        else u0.quality_status
    )
    if missing_identity:
        membership = [
            replace(
                item,
                included=False,
                reason_code=(
                    "U1_DISABLED_MISSING_IDENTITY"
                    if item.included
                    else item.reason_code
                ),
            )
            for item in membership
        ]
    fingerprint = _stable_hash(
        {
            "layer": "U1",
            "signal_date": u0.signal_date,
            "knowledge_cutoff": u0.knowledge_cutoff,
            "source_snapshot_version": u0.source_snapshot_version,
            "eligible_codes": sorted(representatives.values()),
            "membership": [_membership_payload(item) for item in membership],
            "identity_hash": identity_hash,
            "quality_status": quality.value,
            "coverage_diagnostics": dict(sorted(coverage.items())),
        }
    )
    return PITUniverseSnapshot(
        layer="U1",
        signal_date=u0.signal_date,
        knowledge_cutoff=u0.knowledge_cutoff,
        source_snapshot_version=u0.source_snapshot_version,
        eligible_codes=(
            ()
            if missing_identity
            else tuple(sorted(representatives.values()))
        ),
        membership=tuple(membership),
        identity_mapping=MappingProxyType(identity_mapping),
        identity_hash=identity_hash,
        snapshot_fingerprint=fingerprint,
        coverage_diagnostics=MappingProxyType(dict(sorted(coverage.items()))),
        quality_status=quality,
    )


def _project_resolution_to_snapshot(
    layers: PITIdentityLayers,
) -> UniverseResolution:
    """Project the base resolution onto U1 while retaining legacy exclusions."""

    selected = set(layers.u1.eligible_codes)
    eligible = tuple(
        instrument
        for instrument in layers.resolution.eligible
        if instrument.ts_code in selected
    )
    metrics = dict(layers.resolution.audit_metrics)
    metrics["eligible_count"] = len(eligible)
    return replace(
        layers.resolution,
        eligible=eligible,
        audit_metrics=metrics,
        quality_status=layers.u1.quality_status,
        identity_mapping=layers.u1.identity_mapping,
        identity_hash=layers.u1.identity_hash,
        snapshot_fingerprint=layers.u1.snapshot_fingerprint,
        coverage_diagnostics=layers.u1.coverage_diagnostics,
    )


def identity_key_for_instrument(
    instrument: FundInstrumentVersion,
) -> str | None:
    """Return the canonical U1 identity key, or ``None`` when incomplete."""

    return _identity_key(instrument)


def _identity_key(instrument: FundInstrumentVersion | None) -> str | None:
    if instrument is None:
        return None
    values = {
        "underlying_index": _normalize_identity_value(
            instrument.underlying_index or instrument.tracking_index,
            uppercase=True,
        ),
        "asset_class": _normalize_identity_value(instrument.asset_class),
        "region": _normalize_identity_value(instrument.region),
        "currency": _normalize_identity_value(instrument.currency, uppercase=True),
        "leveraged_or_inverse": _normalize_leverage(instrument.leveraged_or_inverse),
        "share_class_or_feeder_relationship": _normalize_identity_value(
            instrument.share_class_or_feeder_relationship
        ),
    }
    if any(value is None for value in values.values()):
        return None
    return json.dumps(values, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _normalize_identity_value(value: object, *, uppercase: bool = False) -> str | None:
    if not _has_value(value):
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    return normalized.upper() if uppercase else normalized.lower()


def _normalize_leverage(value: object) -> str | None:
    if not _has_value(value):
        return None
    if isinstance(value, (bool, np.bool_)):
        return "true" if value else "false"
    if isinstance(value, (int, float, np.integer, np.floating)):
        if not np.isfinite(value):
            return None
        if value == 0:
            return "false"
        if value == 1:
            return "true"
        return None
    normalized = str(value).strip().lower()
    if normalized in {"", "unknown", "unk", "na", "n/a", "none"}:
        return None
    try:
        numeric = float(normalized)
    except ValueError:
        return normalized
    if not np.isfinite(numeric):
        return None
    if numeric == 0:
        return "false"
    if numeric == 1:
        return "true"
    return None


def _optional_identity_value(value: object) -> object | None:
    return value if _has_value(value) else None


def _membership_payload(item: UniverseMembership) -> dict[str, object]:
    return {
        "ts_code": item.ts_code,
        "included": item.included,
        "reason_code": item.reason_code,
        "identity_key": item.identity_key,
        "layer": item.layer,
    }


def _stable_hash(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _empty_metrics() -> dict[str, int]:
    return {
        "historical_market_codes": 0,
        "master_codes": 0,
        "missing_master_codes": 0,
        "unexpected_master_codes": 0,
        "listed_count": 0,
        "delisted_count": 0,
        "active_count": 0,
        "eligible_count": 0,
        "missing_delist_date_count": 0,
        "unknown_status_count": 0,
        "unknown_type_count": 0,
        "overlapping_valid_range_count": 0,
        "overlapping_knowledge_range_count": 0,
        "knowledge_time_unverified_count": 0,
        "cross_source_missing_count": 0,
        "status_conflict_count": 0,
        "date_conflict_count": 0,
        "master_excluded_count": 0,
        "strategy_excluded_count": 0,
        "tradable_excluded_count": 0,
    }


def _to_timestamp(value: object) -> pd.Timestamp:
    if not _has_value(value):
        raise ValueError("date/time value is required")
    return pd.Timestamp(value)


def _optional_timestamp(value: object) -> pd.Timestamp | None:
    if not _has_value(value):
        return None
    try:
        return pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _has_query_timezone_mismatch(
    rows: pd.DataFrame,
    signal_ts: pd.Timestamp,
    cutoff_ts: pd.Timestamp,
) -> bool:
    signal_aware = signal_ts.tzinfo is not None
    cutoff_aware = cutoff_ts.tzinfo is not None
    for field, expected_aware in (
        (("valid_from", "valid_to", "list_date", "delist_date"), signal_aware),
        (("known_from",), cutoff_aware),
    ):
        for column in field:
            if column not in rows.columns:
                continue
            for value in rows[column]:
                timestamp = _optional_timestamp(value)
                if timestamp is not None and (timestamp.tzinfo is not None) != expected_aware:
                    return True
    return False


def _known_from(row: pd.Series) -> pd.Timestamp | None:
    return _optional_timestamp(row.get("known_from"))


def _is_valid_on(row: pd.Series, signal_ts: pd.Timestamp) -> bool:
    valid_from = _optional_timestamp(row.get("valid_from"))
    valid_to = _optional_timestamp(row.get("valid_to"))
    if valid_from is not None and valid_from > signal_ts:
        return False
    if valid_to is not None and signal_ts >= valid_to:
        return False
    return True


def _select_latest_known(rows: pd.DataFrame) -> tuple[pd.Series | None, bool]:
    if rows.empty:
        return None, False

    sortable = rows.copy()
    sortable["_known_sort"] = sortable["known_from"].apply(
        lambda value: _optional_timestamp(value) or pd.Timestamp.min
    )
    latest_known = sortable["_known_sort"].max()
    top = sortable[sortable["_known_sort"] == latest_known].copy()
    if len(top) == 1:
        return top.iloc[0], False

    return None, True


def _instrument_from_row(row: pd.Series) -> FundInstrumentVersion:
    quality = _row_quality(row)
    underlying_index = _optional_string(
        row.get("underlying_index", row.get("tracking_index"))
    )
    return FundInstrumentVersion(
        ts_code=str(row.get("ts_code")),
        valid_from=_format_date(row.get("valid_from")),
        valid_to=_optional_format_date(row.get("valid_to")),
        known_from=_optional_format_datetime(row.get("known_from")),
        revision_id=_optional_string(row.get("revision_id")),
        source_id=_optional_string(row.get("source_id")),
        source_record_id=_optional_string(row.get("source_record_id")),
        source_published_at=_optional_format_datetime(row.get("source_published_at")),
        ingested_at=_optional_format_datetime(row.get("ingested_at")),
        list_date=_optional_format_date(row.get("list_date")),
        delist_date=_optional_format_date(row.get("delist_date")),
        fund_status=_optional_string(row.get("fund_status")),
        name=_optional_string(row.get("name")),
        fund_type=_optional_string(row.get("fund_type")),
        asset_class=_optional_string(row.get("asset_class")),
        tracking_index=_optional_string(row.get("tracking_index")),
        exchange=_optional_string(row.get("exchange")),
        quality_status=quality,
        instrument_type=_resolve_instrument_type(row),
        underlying_index=underlying_index,
        region=_optional_string(row.get("region")),
        currency=_optional_string(row.get("currency")),
        leveraged_or_inverse=_optional_identity_value(
            row.get("leveraged_or_inverse", row.get("is_leveraged_or_inverse"))
        ),
        share_class_or_feeder_relationship=_optional_string(
            row.get(
                "share_class_or_feeder_relationship",
                row.get("share_class", row.get("feeder_relationship")),
            )
        ),
    )


def _row_quality(row: pd.Series) -> PITQualityStatus:
    has_revision_chain = (
        _has_value(row.get("known_from"))
        and _has_value(row.get("revision_id"))
        and _has_value(row.get("source_record_id"))
        and _strict_true(row.get("revision_chain_verified", False))
    )
    if not has_revision_chain:
        return PITQualityStatus.KNOWLEDGE_TIME_UNVERIFIED
    if not _strict_true(row.get("independent_source_verified", False)):
        return PITQualityStatus.PIT_UNVERIFIED
    return PITQualityStatus.VERIFIED


def _strict_true(value: object) -> bool:
    return isinstance(value, (bool, np.bool_)) and bool(value)


def _invalid_timestamp(value: object) -> bool:
    if not _has_value(value):
        return False
    try:
        pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError):
        return True
    return False


def _snapshot_integrity_exclusions(
    rows: pd.DataFrame, signal_date: str
) -> tuple[UniverseExclusion, ...]:
    exclusions: list[UniverseExclusion] = []
    if "ts_code" not in rows.columns:
        return ()

    for ts_code in sorted(str(code) for code in rows["ts_code"].dropna().unique()):
        code_rows = rows[rows["ts_code"].astype(str) == ts_code]
        invalid_date_fields = []
        for field in ("valid_from", "valid_to", "known_from", "list_date", "delist_date"):
            if field not in code_rows.columns:
                if field == "valid_from":
                    invalid_date_fields.append(field)
                continue
            values = code_rows[field]
            if any(_invalid_timestamp(value) for value in values):
                invalid_date_fields.append(field)
            elif field == "valid_from" and any(not _has_value(value) for value in values):
                invalid_date_fields.append(field)
        if {"valid_from", "valid_to"}.issubset(code_rows.columns):
            for _, row in code_rows.iterrows():
                valid_from = _optional_timestamp(row.get("valid_from"))
                valid_to = _optional_timestamp(row.get("valid_to"))
                if valid_from is not None and valid_to is not None and valid_from > valid_to:
                    invalid_date_fields.append("valid_from/valid_to")
                    break
        if invalid_date_fields:
            exclusions.append(
                _exclusion(
                    ExclusionLayer.FUND_MASTER,
                    ts_code,
                    ExclusionReasonCode.INVALID_LIFECYCLE_DATES,
                    signal_date,
                    details="invalid date fields: " + ", ".join(invalid_date_fields),
                )
            )
            continue
        has_overlap = _has_valid_range_overlap(code_rows)
        unsortable_tie = _has_unsortable_same_knowledge_tie(code_rows)
        if unsortable_tie:
            exclusions.append(
                _exclusion(
                    ExclusionLayer.FUND_MASTER,
                    ts_code,
                    ExclusionReasonCode.AMBIGUOUS_REVISION_ORDER,
                    signal_date,
                )
            )
        if has_overlap and not unsortable_tie:
            exclusions.append(
                _exclusion(
                    ExclusionLayer.FUND_MASTER,
                    ts_code,
                    ExclusionReasonCode.OVERLAPPING_VALID_RANGE,
                    signal_date,
                )
            )
        for _, row in code_rows.iterrows():
            list_date = _optional_timestamp(row.get("list_date"))
            delist_date = _optional_timestamp(row.get("delist_date"))
            if list_date is not None and delist_date is not None and list_date > delist_date:
                exclusions.append(
                    _exclusion(
                        ExclusionLayer.FUND_MASTER,
                        ts_code,
                        ExclusionReasonCode.INVALID_LIFECYCLE_DATES,
                        signal_date,
                        details=f"list_date={_format_date(list_date)} delist_date={_format_date(delist_date)}",
                    )
                )
                break
    exclusions.sort(key=_exclusion_sort_key)
    return tuple(exclusions)


def _has_unsortable_same_knowledge_tie(rows: pd.DataFrame) -> bool:
    if not {"known_from", "revision_id"}.issubset(rows.columns):
        return False
    for _, group in rows.groupby(rows["known_from"].apply(_optional_format_datetime)):
        if len(group) <= 1:
            continue
        revision_values = [_optional_string(value) for value in group["revision_id"]]
        if any(value is None for value in revision_values):
            return True
        if len(set(revision_values)) != len(revision_values):
            return True
    return False


def _has_valid_range_overlap(rows: pd.DataFrame) -> bool:
    if not {"valid_from", "valid_to"}.issubset(rows.columns):
        return False
    intervals = []
    for _, row in rows.iterrows():
        start = _optional_timestamp(row.get("valid_from")) or pd.Timestamp.min
        end = _optional_timestamp(row.get("valid_to")) or pd.Timestamp.max
        intervals.append((start, end))
    intervals.sort()
    for (_, previous_end), (current_start, _) in zip(intervals, intervals[1:]):
        if current_start < previous_end:
            return True
    return False


def _master_eligibility_exclusion(
    instrument: FundInstrumentVersion, signal_ts: pd.Timestamp, signal_date: str
) -> UniverseExclusion | None:
    list_date = _optional_timestamp(instrument.list_date)
    if list_date is None:
        return _exclusion(
            ExclusionLayer.FUND_MASTER,
            instrument.ts_code,
            ExclusionReasonCode.INVALID_LIST_DATE,
            signal_date,
            details="list_date is missing or invalid",
        )
    if list_date > signal_ts:
        return _exclusion(
            ExclusionLayer.FUND_MASTER,
            instrument.ts_code,
            ExclusionReasonCode.NOT_YET_LISTED,
            signal_date,
            details=f"list_date={instrument.list_date}",
        )

    delist_date = _optional_timestamp(instrument.delist_date)
    if delist_date is not None and signal_ts >= delist_date:
        return _exclusion(
            ExclusionLayer.FUND_MASTER,
            instrument.ts_code,
            ExclusionReasonCode.DELISTED,
            signal_date,
            details=f"delist_date={instrument.delist_date}",
        )
    if delist_date is None:
        return _exclusion(
            ExclusionLayer.FUND_MASTER,
            instrument.ts_code,
            ExclusionReasonCode.MISSING_DELIST_DATE,
            signal_date,
            details="delist_date is missing; survival cannot be assumed",
        )

    status = (instrument.fund_status or "").upper()
    if status in {"", "UNKNOWN", "UNK", "NA", "N/A"}:
        return _exclusion(
            ExclusionLayer.FUND_MASTER,
            instrument.ts_code,
            ExclusionReasonCode.UNKNOWN_STATUS,
            signal_date,
        )
    if status not in {"ACTIVE", "LISTED", "L"}:
        return _exclusion(
            ExclusionLayer.FUND_MASTER,
            instrument.ts_code,
            ExclusionReasonCode.STATUS_NOT_ALLOWED,
            signal_date,
            details=f"fund_status={instrument.fund_status}",
        )
    fund_type = (instrument.fund_type or "").upper()
    if fund_type in {"", "UNKNOWN", "UNK", "NA", "N/A"}:
        return _exclusion(
            ExclusionLayer.FUND_MASTER,
            instrument.ts_code,
            ExclusionReasonCode.UNKNOWN_TYPE,
            signal_date,
        )
    return None


def _strategy_exclusion_reason(
    instrument: FundInstrumentVersion, policy: UniversePolicy
) -> ExclusionReasonCode | None:
    if policy.include_ts_codes and instrument.ts_code not in policy.include_ts_codes:
        return ExclusionReasonCode.NOT_INCLUDED_BY_POLICY
    if instrument.ts_code in policy.exclude_ts_codes:
        return ExclusionReasonCode.EXCLUDED_BY_POLICY
    if policy.asset_classes and instrument.asset_class not in policy.asset_classes:
        return ExclusionReasonCode.ASSET_CLASS_NOT_ALLOWED
    if policy.fund_types and instrument.fund_type not in policy.fund_types:
        return ExclusionReasonCode.FUND_TYPE_NOT_ALLOWED
    if policy.exchanges and instrument.exchange not in policy.exchanges:
        return ExclusionReasonCode.EXCHANGE_NOT_ALLOWED
    return None


def _tradable_status(causal_view: CausalDataView, ts_code: str, signal_date: str) -> tuple[bool, str]:
    if not hasattr(causal_view, "tradable_status"):
        return False, "TRADABILITY_UNAVAILABLE"
    try:
        result = causal_view.tradable_status(ts_code, signal_date)
    except Exception:
        return False, "TRADABILITY_UNAVAILABLE"
    details = ""
    value = result
    if isinstance(result, tuple):
        if len(result) not in {1, 2}:
            return False, "TRADABILITY_UNAVAILABLE"
        value = result[0]
        if len(result) > 1:
            if not isinstance(result[1], str):
                return False, "TRADABILITY_UNAVAILABLE"
            details = result[1]
    if not isinstance(value, (bool, np.bool_)):
        return False, "TRADABILITY_UNAVAILABLE"
    return bool(value), details


def _resolution_quality(
    eligible: tuple[FundInstrumentVersion, ...],
    master: _MasterSelection,
    strategy_exclusions: list[UniverseExclusion],
    tradable_exclusions: list[UniverseExclusion],
) -> PITQualityStatus:
    del strategy_exclusions
    if master.fatal:
        return PITQualityStatus.PIT_INVALID
    if any(
        exclusion.details in {"TRADABILITY_UNAVAILABLE", "TRADABILITY_CONFLICT"}
        for exclusion in tradable_exclusions
    ):
        return PITQualityStatus.PIT_INVALID
    if master.research_only:
        return PITQualityStatus.RESEARCH_ONLY_UNVERIFIED_UNIVERSE
    if master.audit_metrics.get("knowledge_time_unverified_count", 0) > 0:
        return PITQualityStatus.PIT_UNVERIFIED
    if any(instrument.quality_status != PITQualityStatus.VERIFIED for instrument in eligible):
        return PITQualityStatus.PIT_UNVERIFIED
    return PITQualityStatus.VERIFIED


def _combine_quality(
    current: PITQualityStatus, cross_source_status: PITQualityStatus
) -> PITQualityStatus:
    if current == PITQualityStatus.PIT_INVALID or cross_source_status == PITQualityStatus.PIT_INVALID:
        return PITQualityStatus.PIT_INVALID
    if current == PITQualityStatus.PIT_UNVERIFIED:
        return PITQualityStatus.PIT_UNVERIFIED
    if cross_source_status == PITQualityStatus.RESEARCH_ONLY_UNVERIFIED_UNIVERSE:
        return PITQualityStatus.RESEARCH_ONLY_UNVERIFIED_UNIVERSE
    if current == PITQualityStatus.RESEARCH_ONLY_UNVERIFIED_UNIVERSE:
        return PITQualityStatus.RESEARCH_ONLY_UNVERIFIED_UNIVERSE
    if cross_source_status == PITQualityStatus.PIT_UNVERIFIED:
        return PITQualityStatus.PIT_UNVERIFIED
    return current


def _cross_source_audit_output(
    audit: Mapping[str, object] | None,
) -> tuple[dict[str, object], PITQualityStatus]:
    if audit is None:
        return (
            {
                "source_a_codes": frozenset(),
                "source_b_codes": frozenset(),
                "only_in_a": frozenset(),
                "only_in_b": frozenset(),
                "status_conflicts": frozenset(),
                "date_conflicts": frozenset(),
                "classification_conflicts": frozenset(),
                "cross_source_missing_count": 0,
                "status_conflict_count": 0,
                "date_conflict_count": 0,
                "classification_conflict_count": 0,
                "reconciliation_status": "NOT_PROVIDED",
                "resolution_reason": "cross_source_audit was not provided",
            },
            PITQualityStatus.RESEARCH_ONLY_UNVERIFIED_UNIVERSE,
        )

    source_a_codes = _string_set(audit.get("source_a_codes"))
    source_b_codes = _string_set(audit.get("source_b_codes"))
    only_in_a = _string_set(audit.get("only_in_a")) or (source_a_codes - source_b_codes)
    only_in_b = _string_set(audit.get("only_in_b")) or (source_b_codes - source_a_codes)
    status_conflicts = _string_set(audit.get("status_conflicts"))
    date_conflicts = _string_set(audit.get("date_conflicts"))
    classification_conflicts = _string_set(audit.get("classification_conflicts"))
    missing_count = len(only_in_a) + len(only_in_b)
    conflict_count = len(status_conflicts) + len(date_conflicts) + len(classification_conflicts)

    if conflict_count:
        reconciliation_status = "CONFLICTS"
        quality = PITQualityStatus.PIT_INVALID
        reason = "cross_source_audit has unresolved conflicts"
    elif missing_count:
        reconciliation_status = "MISSING_CODES"
        quality = PITQualityStatus.RESEARCH_ONLY_UNVERIFIED_UNIVERSE
        reason = "cross_source_audit coverage gate failed"
    else:
        reconciliation_status = "PASSED"
        quality = PITQualityStatus.VERIFIED
        reason = "cross_source_audit coverage gate passed"

    return (
        {
            "source_a_codes": source_a_codes,
            "source_b_codes": source_b_codes,
            "only_in_a": only_in_a,
            "only_in_b": only_in_b,
            "status_conflicts": status_conflicts,
            "date_conflicts": date_conflicts,
            "classification_conflicts": classification_conflicts,
            "cross_source_missing_count": missing_count,
            "status_conflict_count": len(status_conflicts),
            "date_conflict_count": len(date_conflicts),
            "classification_conflict_count": len(classification_conflicts),
            "reconciliation_status": reconciliation_status,
            "resolution_reason": reason,
        },
        quality,
    )


def _string_set(value: object) -> frozenset[str]:
    if not _has_value(value):
        return frozenset()
    if isinstance(value, str):
        return frozenset({value})
    try:
        return frozenset(str(item) for item in value if _has_value(item))  # type: ignore[union-attr]
    except TypeError:
        return frozenset({str(value)})


def _exclusion(
    layer: ExclusionLayer,
    ts_code: str,
    reason: ExclusionReasonCode,
    signal_date: str,
    details: str = "",
) -> UniverseExclusion:
    return UniverseExclusion(
        layer=layer,
        ts_code=ts_code,
        reason_code=reason,
        details=details,
        signal_date=_format_date(signal_date),
    )


def _exclusion_sort_key(exclusion: UniverseExclusion) -> tuple[str, str, str]:
    return (exclusion.ts_code, exclusion.layer.value, exclusion.reason_code.value)


def _count_overlapping_ranges(rows: pd.DataFrame, start_col: str, end_col: str) -> int:
    if start_col not in rows.columns or end_col not in rows.columns or "ts_code" not in rows.columns:
        return 0
    overlaps = 0
    for _, group in rows.groupby("ts_code"):
        intervals = []
        for _, row in group.iterrows():
            start = _optional_timestamp(row.get(start_col)) or pd.Timestamp.min
            end = _optional_timestamp(row.get(end_col)) or pd.Timestamp.max
            intervals.append((start, end))
        intervals.sort()
        for (_, previous_end), (current_start, _) in zip(intervals, intervals[1:]):
            if current_start < previous_end:
                overlaps += 1
    return overlaps


def _count_same_knowledge_ties(rows: pd.DataFrame) -> int:
    if "known_from" not in rows.columns or "ts_code" not in rows.columns:
        return 0
    count = 0
    for _, group in rows.groupby("ts_code"):
        known_counts = group["known_from"].apply(_optional_format_datetime).value_counts()
        count += int(known_counts[known_counts > 1].sum())
    return count


def _has_value(value: object) -> bool:
    if value is None:
        return False
    try:
        return not bool(pd.isna(value))
    except (TypeError, ValueError):
        return True


def _optional_string(value: object) -> str | None:
    if not _has_value(value):
        return None
    return str(value)


def _resolve_instrument_type(row: pd.Series) -> str | None:
    explicit = _optional_string(row.get("instrument_type"))
    if explicit is not None:
        return explicit

    return _map_fund_type_asset_class_to_instrument_type(
        _optional_string(row.get("fund_type")),
        _optional_string(row.get("asset_class")),
    )


def map_fund_type_asset_class_to_instrument_type(
    fund_type: str | None,
    asset_class: str | None,
) -> str | None:
    normalized_fund_type = (fund_type or "").strip().upper()
    normalized_asset_class = (asset_class or "").strip().lower()
    if normalized_fund_type != "ETF":
        return None

    asset_class_mapping = {
        "equity": "domestic_equity_etf",
        "bond": "bond_etf",
        "commodity": "commodity_etf",
        "gold": "commodity_etf",
        "cross_border": "cross_border_etf",
        "qdii": "cross_border_etf",
        "money_market": "money_market_etf",
        "cash": "money_market_etf",
    }
    return asset_class_mapping.get(normalized_asset_class)


_map_fund_type_asset_class_to_instrument_type = (
    map_fund_type_asset_class_to_instrument_type
)


def _format_date(value: object) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _optional_format_date(value: object) -> str | None:
    if not _has_value(value):
        return None
    return _format_date(value)


def _format_datetime(value: object) -> str:
    return pd.Timestamp(value).isoformat()


def _optional_format_datetime(value: object) -> str | None:
    if not _has_value(value):
        return None
    return _format_datetime(value)


def to_market_rule_instrument_version(
    instrument: FundInstrumentVersion,
):
    instrument_type = instrument.instrument_type or map_fund_type_asset_class_to_instrument_type(
        instrument.fund_type,
        instrument.asset_class,
    )
    if instrument_type is None:
        return None
    version = instrument.source_record_id or instrument.revision_id or instrument.ts_code
    from .market_rules import FundInstrumentVersion as MarketRuleInstrumentVersion

    return MarketRuleInstrumentVersion(
        ts_code=instrument.ts_code,
        instrument_type=instrument_type,
        version=version,
    )


class FundRotationPITUniverseAdapter:
    """Production adapter from the Runner boundary to ``UniverseResolver``."""

    def __init__(
        self,
        resolver: UniverseResolver,
        *,
        strategy_policy: UniversePolicy,
        causal_view_factory,
        snapshot_version: int | None = None,
        mode: PITQueryMode = PITQueryMode.AS_WAS_KNOWN,
        identity_layer: str = "U0",
    ) -> None:
        self._resolver = resolver
        self._strategy_policy = strategy_policy
        self._causal_view_factory = causal_view_factory
        self._snapshot_version = snapshot_version
        self._mode = mode
        if identity_layer not in {"U0", "U1"}:
            raise ValueError("identity_layer must be 'U0' or 'U1'")
        self._identity_layer = identity_layer

    def resolve_universe(
        self,
        *,
        snapshot: object,
        signal_date: str,
        knowledge_cutoff: str,
        fallback_universe: frozenset[str],
    ) -> UniverseResolution:
        snapshot_version = getattr(snapshot, "snapshot_version", self._snapshot_version)
        candidate_universe = frozenset(
            str(code)
            for code in (
                getattr(snapshot, "historical_candidate_codes", ())
                or fallback_universe
            )
        )
        causal_view = self._causal_view_factory(
            snapshot=snapshot,
            signal_date=signal_date,
            universe=candidate_universe,
        )
        if self._identity_layer == "U0":
            return self._resolver.resolve(
                signal_date=signal_date,
                knowledge_cutoff=knowledge_cutoff,
                strategy_policy=self._strategy_policy,
                causal_view=causal_view,
                snapshot_version=snapshot_version,
                mode=self._mode,
            )
        return _project_resolution_to_snapshot(
            self._resolver.resolve_identity_layers(
                signal_date=signal_date,
                knowledge_cutoff=knowledge_cutoff,
                strategy_policy=self._strategy_policy,
                causal_view=causal_view,
                snapshot_version=snapshot_version,
                mode=self._mode,
            )
        )

    def resolve_identity_layers(
        self,
        *,
        snapshot: object,
        signal_date: str,
        knowledge_cutoff: str,
        fallback_universe: frozenset[str],
    ) -> PITIdentityLayers:
        """Expose U0/U1 through the existing production adapter boundary."""

        if not hasattr(self._resolver, "resolve_identity_layers"):
            raise TypeError("resolver must support resolve_identity_layers")
        snapshot_version = getattr(snapshot, "snapshot_version", self._snapshot_version)
        candidate_universe = frozenset(
            str(code)
            for code in (
                getattr(snapshot, "historical_candidate_codes", ())
                or fallback_universe
            )
        )
        causal_view = self._causal_view_factory(
            snapshot=snapshot,
            signal_date=signal_date,
            universe=candidate_universe,
        )
        return self._resolver.resolve_identity_layers(
            signal_date=signal_date,
            knowledge_cutoff=knowledge_cutoff,
            strategy_policy=self._strategy_policy,
            causal_view=causal_view,
            snapshot_version=snapshot_version,
            mode=self._mode,
        )
