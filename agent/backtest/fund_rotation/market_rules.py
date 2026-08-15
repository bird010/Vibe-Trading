"""PIT-backed market-rule resolution for fund-rotation execution."""

from __future__ import annotations

from dataclasses import dataclass
from numbers import Integral
from typing import Iterable, Mapping, Protocol

import pandas as pd

from .pit_universe import (
    PITQueryMode,
    map_fund_type_asset_class_to_instrument_type,
)


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
class ExecutionRuleProvenance:
    source_id: str
    rule_version: str
    pit_verified: bool


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
    revision_order: int
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
    revision_order: int | None = None
    valid_from: str = ""
    valid_to: str | None = None
    known_from: str = ""


@dataclass(frozen=True)
class ResearchExecutionRuleContext:
    """Explicit static execution-rule context for research-only backtests."""

    resolver: "MarketRuleResolver"
    instruments: dict[str, FundInstrumentVersion]
    rule_version: str
    source_id: str
    pit_verified: bool = False

    def __post_init__(self) -> None:
        resolver_provenance = self.resolver.provenance
        expected = ExecutionRuleProvenance(
            source_id=self.source_id,
            rule_version=self.rule_version,
            pit_verified=self.pit_verified,
        )
        if resolver_provenance != expected:
            raise ValueError(
                "execution rule context provenance must match its resolver"
            )


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

    def __init__(
        self,
        records: Iterable[Mapping[str, object]],
        *,
        provenance: ExecutionRuleProvenance | None = None,
    ):
        self._records = [dict(record) for record in records]
        self.provenance = provenance

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
        record = _record_from_row(selected)
        if self.provenance is not None and (
            record.source_id != self.provenance.source_id
            or record.rule_version != self.provenance.rule_version
        ):
            raise PITInvalidMarketRule(
                "PIT_INVALID_EXECUTION_RULE: record provenance mismatch"
            )
        return record


class MarketRuleResolver:
    """Thin resolver facade that exposes versioned market-rule context."""

    def __init__(
        self,
        source: PITMarketRuleSource,
    ):
        if source is None or not callable(getattr(source, "resolve", None)):
            raise TypeError("an explicit PIT market-rule source is required")
        self._source = source
        self.provenance = getattr(source, "provenance", None)

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
            revision_order=record.revision_order,
            valid_from=record.valid_from,
            valid_to=record.valid_to,
            known_from=record.known_from,
        )


def build_research_static_execution_rule_context(
    *,
    dim_fund: pd.DataFrame,
    universe_codes: Iterable[str],
    evaluation_start_date: str,
    evaluation_end_date: str,
    snapshot_version: int,
) -> ResearchExecutionRuleContext:
    """Build explicit, auditable static rules for ``RESEARCH_ONLY`` runs.

    This is deliberately a source-backed ``MarketRuleResolver`` context so the
    native execution engine keeps the same rule lookup and provenance path as
    formal runs.  The rules are not PIT evidence and are marked accordingly.
    """
    start = _to_timestamp(evaluation_start_date, "evaluation_start_date")
    end = _to_timestamp(evaluation_end_date, "evaluation_end_date")
    if start > end:
        raise ValueError("evaluation_start_date must be <= evaluation_end_date")
    snapshot = _coerce_int(snapshot_version)
    if snapshot < 1:
        raise ValueError("snapshot_version must be positive")
    if not isinstance(dim_fund, pd.DataFrame):
        raise TypeError("dim_fund must be a pandas DataFrame")
    required_columns = {"ts_code", "name"}
    missing_columns = required_columns.difference(dim_fund.columns)
    if missing_columns:
        raise UnknownExecutionRule(
            "missing instrument metadata columns: "
            + ", ".join(sorted(missing_columns))
        )

    codes = tuple(sorted({str(code).strip() for code in universe_codes if str(code).strip()}))
    structured_columns = [
        column
        for column in ("fund_type", "asset_class", "instrument_type")
        if column in dim_fund.columns
    ]
    metadata = {
        str(row.ts_code).strip(): (
            str(row.name).strip(),
            {
                column: getattr(row, column, None)
                for column in structured_columns
            },
        )
        for row in dim_fund[
            ["ts_code", "name"] + structured_columns
        ].itertuples(index=False)
    }
    records: list[dict[str, object]] = []
    instruments: dict[str, FundInstrumentVersion] = {}
    for code in codes:
        instrument_metadata = metadata.get(code)
        if not instrument_metadata:
            raise UnknownExecutionRule(
                f"UNKNOWN_EXECUTION_RULE: missing instrument metadata for {code}"
            )
        _name, structured_metadata = instrument_metadata
        instrument_type = _resolve_research_instrument_type(structured_metadata)
        if instrument_type != "domestic_equity_etf":
            # Leave unsupported codes unmapped.  If a strategy selects one,
            # Native Execution will fail explicitly instead of inheriting ETF
            # rules that do not apply to it.
            continue
        instruments[code] = FundInstrumentVersion(
            ts_code=code,
            instrument_type=instrument_type,
            version="research-static-v1",
        )
        records.append(
            {
                "ts_code": code,
                "instrument_type": instrument_type,
                "valid_from": start.strftime("%Y%m%d"),
                "valid_to": None,
                "known_from": start.strftime("%Y%m%dT000000"),
                "snapshot_version": snapshot,
                "revision_id": f"research-static:{code}:r1",
                "revision_order": 1,
                "settlement": "T+1",
                "lot_size": 100,
                "tick_size": 0.001,
                "price_limit_pct": 0.10,
                "price_limit_rule": "PCT:0.1",
                "short_allowed": False,
                "currency": "CNY",
                "source_record_id": f"research-static:{code}",
                "source_id": "RESEARCH_STATIC_RULES",
                "rule_version": "research-cn-etf-v1",
            }
        )

    return ResearchExecutionRuleContext(
        resolver=MarketRuleResolver(
            InMemoryPITMarketRuleSource(
                records,
                provenance=ExecutionRuleProvenance(
                    source_id="RESEARCH_STATIC_RULES",
                    rule_version="research-cn-etf-v1",
                    pit_verified=False,
                ),
            )
        ),
        instruments=instruments,
        rule_version="research-cn-etf-v1",
        source_id="RESEARCH_STATIC_RULES",
    )


def _resolve_research_instrument_type(
    structured_metadata: Mapping[str, object],
) -> str | None:
    """Reuse the canonical PIT fund metadata classification."""
    explicit = str(structured_metadata.get("instrument_type") or "").strip()
    if explicit:
        return explicit
    fund_type = str(structured_metadata.get("fund_type") or "").strip() or None
    asset_class = str(structured_metadata.get("asset_class") or "").strip() or None
    instrument_type = map_fund_type_asset_class_to_instrument_type(
        fund_type,
        asset_class,
    )
    if instrument_type is not None:
        return instrument_type
    # The current production ETF dimension uses a legacy fund_type vocabulary
    # and has no asset_class.  This compatibility is local to Research Static;
    # the formal PIT mapper remains strict about fund_type == ETF.
    if fund_type in {"股票型", "股票指数型"} and asset_class is None:
        return "domestic_equity_etf"
    return None


def _select_candidate(rows: list[dict[str, object]]) -> dict[str, object]:
    sortable = [
        (
            _known_from_timestamp(row),
            _revision_order(row),
            row,
        )
        for row in rows
    ]
    latest_known = max(item[0] for item in sortable)
    top = [item for item in sortable if item[0] == latest_known]
    if len(top) == 1:
        return top[0][2]

    revision_orders = [item[1] for item in top]
    if len(set(revision_orders)) != len(revision_orders):
        raise PITInvalidMarketRule(
            "PIT_INVALID_EXECUTION_RULE: ambiguous revision order"
        )
    top.sort(key=lambda item: item[1])
    return top[-1][2]


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
        short_allowed=_required_bool(row.get("short_allowed"), "short_allowed"),
        currency=_required_string(row.get("currency"), "currency"),
        valid_from=_format_date(_required_value(row.get("valid_from"), "valid_from")),
        valid_to=_optional_format_date(row.get("valid_to")),
        known_from=_format_datetime(_required_value(row.get("known_from"), "known_from")),
        snapshot_version=_required_positive_int(
            row.get("snapshot_version"), "snapshot_version"
        ),
        revision_id=_revision_id(row),
        revision_order=_revision_order(row),
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


def _revision_order(row: Mapping[str, object]) -> int:
    return _required_non_negative_int(row.get("revision_order"), "revision_order")


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
    number = _required_non_negative_int(value, field_name)
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


def _required_non_negative_int(value: object, field_name: str) -> int:
    required = _required_value(value, field_name)
    if isinstance(required, bool):
        raise PITInvalidMarketRule(
            f"PIT_INVALID_EXECUTION_RULE: invalid {field_name}"
        )
    if isinstance(required, Integral):
        number = int(required)
    elif isinstance(required, str) and required.strip().isdigit():
        number = int(required.strip())
    else:
        raise PITInvalidMarketRule(
            f"PIT_INVALID_EXECUTION_RULE: invalid {field_name}"
        )
    if number < 0:
        raise PITInvalidMarketRule(
            f"PIT_INVALID_EXECUTION_RULE: invalid {field_name}"
        )
    return number


def _required_bool(value: object, field_name: str) -> bool:
    required = _required_value(value, field_name)
    if isinstance(required, bool):
        return required
    if isinstance(required, Integral):
        numeric = int(required)
        if numeric in (0, 1):
            return bool(numeric)
        raise PITInvalidMarketRule(
            f"PIT_INVALID_EXECUTION_RULE: invalid {field_name}"
        )
    if isinstance(required, str):
        normalized = required.strip().lower()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0"}:
            return False
    raise PITInvalidMarketRule(
        f"PIT_INVALID_EXECUTION_RULE: invalid {field_name}"
    )


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
