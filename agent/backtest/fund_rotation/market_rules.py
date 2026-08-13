"""PIT-backed market-rule resolution for fund-rotation execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Protocol

import pandas as pd

from .pit_universe import PITQueryMode


class UnknownExecutionRule(ValueError):
    """Raised when no knowable execution rule exists for the requested view."""


class PITInvalidMarketRule(ValueError):
    """Raised when PIT rule facts exist but cannot be verified deterministically."""


@dataclass(frozen=True)
class FundInstrumentVersion:
    ts_code: str
    instrument_type: str
    version: str


@dataclass(frozen=True)
class MarketRuleRecord:
    ts_code: str
    instrument_type: str
    settlement: str
    lot_size: int
    tick_size: float
    price_limit_pct: float | None
    price_limit_rule: str
    short_allowed: bool
    currency: str
    valid_from: str
    valid_to: str | None
    known_from: str
    snapshot_version: int
    revision_id: str
    source_record_id: str
    source_id: str | None
    rule_version: str


@dataclass(frozen=True)
class MarketRules:
    instrument_type: str
    settlement: str
    lot_size: int
    tick_size: float
    price_limit_pct: float | None
    price_limit_rule: str
    short_allowed: bool
    currency: str
    rule_version: str
    trade_date: str = ""
    knowledge_cutoff: str = ""
    snapshot_version: int | None = None
    source_record_id: str = ""
    source_id: str | None = None
    revision_id: str = ""
    valid_from: str = ""
    valid_to: str | None = None
    known_from: str = ""


class PITMarketRuleSource(Protocol):
    def resolve(
        self,
        *,
        ts_code: str,
        instrument_type: str,
        trade_date: str,
        knowledge_cutoff: str,
        snapshot_version: int,
        mode: PITQueryMode,
    ) -> MarketRuleRecord:
        ...


class InMemoryPITMarketRuleSource:
    """Minimal in-memory PIT rule source for tests and research fixtures."""

    def __init__(self, records: Iterable[Mapping[str, object]]):
        self._records = [dict(record) for record in records]

    def resolve(
        self,
        *,
        ts_code: str,
        instrument_type: str,
        trade_date: str,
        knowledge_cutoff: str,
        snapshot_version: int,
        mode: PITQueryMode,
    ) -> MarketRuleRecord:
        trade_ts = _to_timestamp(trade_date, "trade_date")
        cutoff_ts = _to_timestamp(knowledge_cutoff, "knowledge_cutoff")
        matching_rows = [
            row
            for row in self._records
            if str(row.get("ts_code", "")) == ts_code
            and str(row.get("instrument_type", "")) == instrument_type
        ]
        if not matching_rows:
            raise UnknownExecutionRule(
                f"UNKNOWN_EXECUTION_RULE: {instrument_type}:{ts_code}"
            )

        snapshot_rows = [
            row for row in matching_rows if _coerce_int(row.get("snapshot_version")) == snapshot_version
        ]
        if not snapshot_rows:
            raise PITInvalidMarketRule(
                "PIT_INVALID_EXECUTION_RULE: snapshot_version mismatch"
            )

        valid_rows = [
            row for row in snapshot_rows if _is_valid_on(row, trade_ts)
        ]
        if not valid_rows:
            raise UnknownExecutionRule(
                f"UNKNOWN_EXECUTION_RULE: no rule valid on {trade_ts.strftime('%Y-%m-%d')}"
            )

        if mode is PITQueryMode.AS_WAS_KNOWN:
            candidate_rows = [
                row
                for row in valid_rows
                if _known_from_timestamp(row) <= cutoff_ts
            ]
        else:
            candidate_rows = list(valid_rows)

        if not candidate_rows:
            raise UnknownExecutionRule(
                f"UNKNOWN_EXECUTION_RULE: no rule knowable for {ts_code} at {cutoff_ts.isoformat()}"
            )

        selected = _select_candidate(candidate_rows)
        return _record_from_row(selected)


class MarketRuleResolver:
    """Thin resolver facade that exposes versioned market-rule context."""

    def __init__(self, source: PITMarketRuleSource):
        self._source = source

    def resolve(
        self,
        instrument: FundInstrumentVersion,
        trade_date: str,
        knowledge_cutoff: str,
        snapshot_version: int,
        mode: PITQueryMode,
    ) -> MarketRules:
        if not trade_date or not knowledge_cutoff:
            raise ValueError(
                "trade_date and knowledge_cutoff are required for PIT rules"
            )
        if snapshot_version is None:
            raise ValueError("snapshot_version is required for PIT rules")
        if mode is None:
            raise TypeError("mode is required for PIT rules")

        record = self._source.resolve(
            ts_code=instrument.ts_code,
            instrument_type=instrument.instrument_type,
            trade_date=trade_date,
            knowledge_cutoff=knowledge_cutoff,
            snapshot_version=snapshot_version,
            mode=mode,
        )
        return MarketRules(
            instrument_type=record.instrument_type,
            settlement=record.settlement,
            lot_size=record.lot_size,
            tick_size=record.tick_size,
            price_limit_pct=record.price_limit_pct,
            price_limit_rule=record.price_limit_rule,
            short_allowed=record.short_allowed,
            currency=record.currency,
            rule_version=record.rule_version,
            trade_date=_format_date(trade_date),
            knowledge_cutoff=_format_datetime(knowledge_cutoff),
            snapshot_version=record.snapshot_version,
            source_record_id=record.source_record_id,
            source_id=record.source_id,
            revision_id=record.revision_id,
            valid_from=record.valid_from,
            valid_to=record.valid_to,
            known_from=record.known_from,
        )


def _select_candidate(rows: list[dict[str, object]]) -> dict[str, object]:
    sortable = [
        (
            _known_from_timestamp(row),
            _revision_id(row),
            _source_record_id(row),
            row,
        )
        for row in rows
    ]
    latest_known = max(item[0] for item in sortable)
    top = [item for item in sortable if item[0] == latest_known]
    if len(top) == 1:
        return top[0][3]

    revision_ids = [item[1] for item in top]
    if len(set(revision_ids)) != len(revision_ids):
        raise PITInvalidMarketRule(
            "PIT_INVALID_EXECUTION_RULE: ambiguous revision order"
        )
    top.sort(key=lambda item: (item[1], item[2]))
    return top[-1][3]


def _record_from_row(row: Mapping[str, object]) -> MarketRuleRecord:
    price_limit_pct = _optional_float(row.get("price_limit_pct"))
    return MarketRuleRecord(
        ts_code=_required_string(row.get("ts_code"), "ts_code"),
        instrument_type=_required_string(row.get("instrument_type"), "instrument_type"),
        settlement=_required_string(row.get("settlement"), "settlement"),
        lot_size=_required_positive_int(row.get("lot_size"), "lot_size"),
        tick_size=_required_positive_float(row.get("tick_size"), "tick_size"),
        price_limit_pct=price_limit_pct,
        price_limit_rule=_normalize_price_limit_rule(row.get("price_limit_rule"), price_limit_pct),
        short_allowed=bool(row.get("short_allowed", False)),
        currency=_required_string(row.get("currency"), "currency"),
        valid_from=_format_date(_required_value(row.get("valid_from"), "valid_from")),
        valid_to=_optional_format_date(row.get("valid_to")),
        known_from=_format_datetime(_required_value(row.get("known_from"), "known_from")),
        snapshot_version=_required_positive_int(
            row.get("snapshot_version"), "snapshot_version"
        ),
        revision_id=_revision_id(row),
        source_record_id=_source_record_id(row),
        source_id=_optional_string(row.get("source_id")),
        rule_version=_required_string(row.get("rule_version"), "rule_version"),
    )


def _is_valid_on(row: Mapping[str, object], trade_ts: pd.Timestamp) -> bool:
    valid_from = _to_timestamp(_required_value(row.get("valid_from"), "valid_from"), "valid_from")
    valid_to = _optional_timestamp(row.get("valid_to"))
    if valid_from > trade_ts:
        return False
    if valid_to is not None and trade_ts >= valid_to:
        return False
    return True


def _known_from_timestamp(row: Mapping[str, object]) -> pd.Timestamp:
    return _to_timestamp(_required_value(row.get("known_from"), "known_from"), "known_from")


def _revision_id(row: Mapping[str, object]) -> str:
    return _required_string(row.get("revision_id"), "revision_id")


def _source_record_id(row: Mapping[str, object]) -> str:
    return _required_string(row.get("source_record_id"), "source_record_id")


def _normalize_price_limit_rule(
    raw_rule: object,
    price_limit_pct: float | None,
) -> str:
    explicit = _optional_string(raw_rule)
    if explicit is not None:
        return explicit
    if price_limit_pct is None:
        return "NONE"
    return f"PCT:{price_limit_pct:.6g}"


def _required_value(value: object, field_name: str) -> object:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise PITInvalidMarketRule(
            f"PIT_INVALID_EXECUTION_RULE: missing {field_name}"
        )
    return value


def _required_string(value: object, field_name: str) -> str:
    text = _optional_string(_required_value(value, field_name))
    if text is None:
        raise PITInvalidMarketRule(
            f"PIT_INVALID_EXECUTION_RULE: missing {field_name}"
        )
    return text


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required_positive_int(value: object, field_name: str) -> int:
    number = _coerce_int(_required_value(value, field_name))
    if number < 1:
        raise PITInvalidMarketRule(
            f"PIT_INVALID_EXECUTION_RULE: invalid {field_name}"
        )
    return number


def _required_positive_float(value: object, field_name: str) -> float:
    number = float(_required_value(value, field_name))
    if number <= 0:
        raise PITInvalidMarketRule(
            f"PIT_INVALID_EXECUTION_RULE: invalid {field_name}"
        )
    return number


def _optional_float(value: object) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return float(value)


def _coerce_int(value: object) -> int:
    return int(value)  # lets ValueError/TypeError surface if malformed


def _to_timestamp(value: object, field_name: str) -> pd.Timestamp:
    try:
        return pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise PITInvalidMarketRule(
            f"PIT_INVALID_EXECUTION_RULE: invalid {field_name}"
        ) from exc


def _optional_timestamp(value: object) -> pd.Timestamp | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return pd.Timestamp(value)


def _format_date(value: object) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _optional_format_date(value: object) -> str | None:
    timestamp = _optional_timestamp(value)
    return None if timestamp is None else timestamp.strftime("%Y-%m-%d")


def _format_datetime(value: object) -> str:
    return pd.Timestamp(value).isoformat()
