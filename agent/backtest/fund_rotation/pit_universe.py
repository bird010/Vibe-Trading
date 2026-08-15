"""Point-in-time fund master and universe resolver vertical slice.

This module intentionally avoids database dependencies.  The first adapter
boundary accepts either a pandas DataFrame or an iterable of record dicts so a
future Lance/SQLite reader can materialize rows without changing resolver code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Mapping, Protocol

import pandas as pd


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
        signal_ts = _to_timestamp(signal_date)
        cutoff_ts = _to_timestamp(knowledge_cutoff)
        rows = self._rows_for_snapshot(snapshot_version)
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
        return UniverseResolution(
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
    return pd.Timestamp(value)


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

    revision_values = [_optional_string(value) for value in top.get("revision_id", pd.Series())]
    if any(value is None for value in revision_values) or len(set(revision_values)) != len(revision_values):
        return None, True

    top["_revision_sort"] = top["revision_id"].astype(str)
    top = top.sort_values(["_revision_sort", "ts_code"])
    return top.iloc[-1], False


def _instrument_from_row(row: pd.Series) -> FundInstrumentVersion:
    quality = _row_quality(row)
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
    )


def _row_quality(row: pd.Series) -> PITQualityStatus:
    has_revision_chain = (
        _has_value(row.get("known_from"))
        and _has_value(row.get("revision_id"))
        and _has_value(row.get("source_record_id"))
        and bool(row.get("revision_chain_verified", False))
    )
    if not has_revision_chain:
        return PITQualityStatus.KNOWLEDGE_TIME_UNVERIFIED
    if not bool(row.get("independent_source_verified", False)):
        return PITQualityStatus.PIT_UNVERIFIED
    return PITQualityStatus.VERIFIED


def _snapshot_integrity_exclusions(
    rows: pd.DataFrame, signal_date: str
) -> tuple[UniverseExclusion, ...]:
    exclusions: list[UniverseExclusion] = []
    if "ts_code" not in rows.columns:
        return ()

    for ts_code in sorted(str(code) for code in rows["ts_code"].dropna().unique()):
        code_rows = rows[rows["ts_code"].astype(str) == ts_code]
        if _has_unsortable_same_knowledge_tie(code_rows):
            exclusions.append(
                _exclusion(
                    ExclusionLayer.FUND_MASTER,
                    ts_code,
                    ExclusionReasonCode.AMBIGUOUS_REVISION_ORDER,
                    signal_date,
                )
            )
        if _has_valid_range_overlap(code_rows):
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
        return True, ""
    result = causal_view.tradable_status(ts_code, signal_date)
    if isinstance(result, tuple):
        return bool(result[0]), str(result[1]) if len(result) > 1 else ""
    return bool(result), ""


def _resolution_quality(
    eligible: tuple[FundInstrumentVersion, ...],
    master: _MasterSelection,
    strategy_exclusions: list[UniverseExclusion],
    tradable_exclusions: list[UniverseExclusion],
) -> PITQualityStatus:
    del strategy_exclusions, tradable_exclusions
    if master.fatal:
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
    ) -> None:
        self._resolver = resolver
        self._strategy_policy = strategy_policy
        self._causal_view_factory = causal_view_factory
        self._snapshot_version = snapshot_version
        self._mode = mode

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
        return self._resolver.resolve(
            signal_date=signal_date,
            knowledge_cutoff=knowledge_cutoff,
            strategy_policy=self._strategy_policy,
            causal_view=causal_view,
            snapshot_version=snapshot_version,
            mode=self._mode,
        )
