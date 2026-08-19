from __future__ import annotations

import pytest

from backtest.fund_rotation.market_rules import (
    FundInstrumentVersion,
    InMemoryPITMarketRuleSource,
    MarketRuleResolver,
    PITInvalidMarketRule,
    PITQueryMode,
    UnknownExecutionRule,
)


def _record(
    ts_code: str,
    instrument_type: str,
    *,
    valid_from: str = "2024-01-01",
    valid_to: str | None = None,
    known_from: str = "2024-01-02T00:00:00",
    snapshot_version: int = 7,
    revision_id: str = "r1",
    revision_order: int = 1,
    source_record_id: str | None = None,
    settlement: str = "T+1",
    lot_size: int = 100,
    tick_size: float = 0.001,
    price_limit_pct: float | None = 0.10,
    short_allowed: bool = False,
    currency: str = "CNY",
    rule_version: str = "pit-r1",
) -> dict[str, object]:
    return {
        "ts_code": ts_code,
        "instrument_type": instrument_type,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "known_from": known_from,
        "snapshot_version": snapshot_version,
        "revision_id": revision_id,
        "revision_order": revision_order,
        "source_record_id": source_record_id or f"{ts_code}-{revision_id}",
        "settlement": settlement,
        "lot_size": lot_size,
        "tick_size": tick_size,
        "price_limit_pct": price_limit_pct,
        "short_allowed": short_allowed,
        "currency": currency,
        "rule_version": rule_version,
    }


def _instrument(
    ts_code: str = "510300.SH",
    instrument_type: str = "domestic_equity_etf",
) -> FundInstrumentVersion:
    return FundInstrumentVersion(
        ts_code=ts_code,
        instrument_type=instrument_type,
        version="instrument-v1",
    )


def test_resolver_selects_rule_known_before_cutoff_and_valid_on_trade_date() -> None:
    resolver = MarketRuleResolver(
        InMemoryPITMarketRuleSource(
            [
                _record(
                    "510300.SH",
                    "domestic_equity_etf",
                    source_record_id="src-510300-r1",
                    rule_version="pit-r1",
                )
            ]
        )
    )

    rules = resolver.resolve(
        _instrument(),
        trade_date="20240103",
        knowledge_cutoff="2024-01-03T15:00:00",
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert rules.instrument_type == "domestic_equity_etf"
    assert rules.trade_date == "2024-01-03"
    assert rules.knowledge_cutoff == "2024-01-03T15:00:00"
    assert rules.snapshot_version == 7
    assert rules.currency == "CNY"
    assert rules.source_record_id == "src-510300-r1"
    assert rules.revision_id == "r1"
    assert rules.rule_version == "pit-r1"


def test_as_was_known_excludes_future_known_row() -> None:
    resolver = MarketRuleResolver(
        InMemoryPITMarketRuleSource(
            [
                _record(
                    "510300.SH",
                    "domestic_equity_etf",
                    known_from="2024-01-02T00:00:00",
                    revision_id="r1",
                    source_record_id="src-510300-r1",
                    rule_version="pit-r1",
                ),
                _record(
                    "510300.SH",
                    "domestic_equity_etf",
                    known_from="2024-01-05T00:00:00",
                    revision_id="r2",
                    source_record_id="src-510300-r2",
                    rule_version="pit-r2",
                ),
            ]
        )
    )

    rules = resolver.resolve(
        _instrument(),
        trade_date="20240103",
        knowledge_cutoff="2024-01-03T15:00:00",
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert rules.source_record_id == "src-510300-r1"
    assert rules.rule_version == "pit-r1"


def test_latest_restated_selects_latest_revision_from_fixed_snapshot() -> None:
    resolver = MarketRuleResolver(
        InMemoryPITMarketRuleSource(
            [
                _record(
                    "510300.SH",
                    "domestic_equity_etf",
                    known_from="2024-01-02T00:00:00",
                    revision_id="r1",
                    revision_order=1,
                    source_record_id="src-510300-r1",
                    rule_version="pit-r1",
                ),
                _record(
                    "510300.SH",
                    "domestic_equity_etf",
                    known_from="2024-01-05T00:00:00",
                    revision_id="r2",
                    revision_order=2,
                    source_record_id="src-510300-r2",
                    rule_version="pit-r2",
                ),
            ]
        )
    )

    rules = resolver.resolve(
        _instrument(),
        trade_date="20240103",
        knowledge_cutoff="2024-01-03T15:00:00",
        snapshot_version=7,
        mode=PITQueryMode.LATEST_RESTATED,
    )

    assert rules.source_record_id == "src-510300-r2"
    assert rules.rule_version == "pit-r2"


def test_revision_order_is_explicit_and_not_revision_id_lexicographic() -> None:
    resolver = MarketRuleResolver(
        InMemoryPITMarketRuleSource(
            [
                _record(
                    "510300.SH",
                    "domestic_equity_etf",
                    known_from="2024-01-02T00:00:00",
                    revision_id="r2",
                    revision_order=2,
                    source_record_id="src-510300-r2",
                    rule_version="pit-r2",
                ),
                _record(
                    "510300.SH",
                    "domestic_equity_etf",
                    known_from="2024-01-02T00:00:00",
                    revision_id="r10",
                    revision_order=10,
                    source_record_id="src-510300-r10",
                    rule_version="pit-r10",
                ),
            ]
        )
    )

    rules = resolver.resolve(
        _instrument(),
        trade_date="20240103",
        knowledge_cutoff="2024-01-03T15:00:00",
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert rules.source_record_id == "src-510300-r10"
    assert rules.rule_version == "pit-r10"


def test_missing_revision_order_fails_closed_when_multiple_candidates_exist() -> None:
    resolver = MarketRuleResolver(
        InMemoryPITMarketRuleSource(
            [
                _record(
                    "510300.SH",
                    "domestic_equity_etf",
                    known_from="2024-01-02T00:00:00",
                    revision_id="r1",
                    revision_order=1,
                    source_record_id="src-510300-r1",
                ),
                {
                    **_record(
                        "510300.SH",
                        "domestic_equity_etf",
                        known_from="2024-01-02T00:00:00",
                        revision_id="r2",
                        source_record_id="src-510300-r2",
                    ),
                    "revision_order": None,
                },
            ]
        )
    )

    with pytest.raises(PITInvalidMarketRule, match="PIT_INVALID_EXECUTION_RULE"):
        resolver.resolve(
            _instrument(),
            trade_date="20240103",
            knowledge_cutoff="2024-01-03T15:00:00",
            snapshot_version=7,
            mode=PITQueryMode.AS_WAS_KNOWN,
        )


def test_duplicate_revision_order_fails_closed_when_multiple_candidates_exist() -> None:
    resolver = MarketRuleResolver(
        InMemoryPITMarketRuleSource(
            [
                _record(
                    "510300.SH",
                    "domestic_equity_etf",
                    known_from="2024-01-02T00:00:00",
                    revision_id="r1",
                    revision_order=2,
                    source_record_id="src-510300-r1",
                ),
                _record(
                    "510300.SH",
                    "domestic_equity_etf",
                    known_from="2024-01-02T00:00:00",
                    revision_id="r2",
                    revision_order=2,
                    source_record_id="src-510300-r2",
                ),
            ]
        )
    )

    with pytest.raises(PITInvalidMarketRule, match="PIT_INVALID_EXECUTION_RULE"):
        resolver.resolve(
            _instrument(),
            trade_date="20240103",
            knowledge_cutoff="2024-01-03T15:00:00",
            snapshot_version=7,
            mode=PITQueryMode.LATEST_RESTATED,
        )


def test_snapshot_mismatch_fails_closed() -> None:
    resolver = MarketRuleResolver(
        InMemoryPITMarketRuleSource([_record("510300.SH", "domestic_equity_etf", snapshot_version=6)])
    )

    with pytest.raises(PITInvalidMarketRule, match="PIT_INVALID_EXECUTION_RULE"):
        resolver.resolve(
            _instrument(),
            trade_date="20240103",
            knowledge_cutoff="2024-01-03T15:00:00",
            snapshot_version=7,
            mode=PITQueryMode.AS_WAS_KNOWN,
        )


def test_ambiguous_overlap_fails_closed() -> None:
    resolver = MarketRuleResolver(
        InMemoryPITMarketRuleSource(
            [
                _record(
                    "510300.SH",
                    "domestic_equity_etf",
                    valid_from="2024-01-01",
                    valid_to="2024-01-10",
                    known_from="2024-01-02T00:00:00",
                    revision_id="",
                    source_record_id="src-510300-a",
                ),
                _record(
                    "510300.SH",
                    "domestic_equity_etf",
                    valid_from="2024-01-01",
                    valid_to="2024-01-10",
                    known_from="2024-01-02T00:00:00",
                    revision_id="",
                    source_record_id="src-510300-b",
                ),
            ]
        )
    )

    with pytest.raises(PITInvalidMarketRule, match="PIT_INVALID_EXECUTION_RULE"):
        resolver.resolve(
            _instrument(),
            trade_date="20240103",
            knowledge_cutoff="2024-01-03T15:00:00",
            snapshot_version=7,
            mode=PITQueryMode.AS_WAS_KNOWN,
        )


def test_missing_or_unknown_rule_raises_without_static_fallback() -> None:
    resolver = MarketRuleResolver(InMemoryPITMarketRuleSource([]))

    with pytest.raises(UnknownExecutionRule, match="UNKNOWN_EXECUTION_RULE"):
        resolver.resolve(
            _instrument(),
            trade_date="20240103",
            knowledge_cutoff="2024-01-03T15:00:00",
            snapshot_version=7,
            mode=PITQueryMode.AS_WAS_KNOWN,
        )

    with pytest.raises(UnknownExecutionRule, match="UNKNOWN_EXECUTION_RULE"):
        resolver.resolve(
            _instrument(ts_code="LEVERED.SH", instrument_type="levered_crypto_etf"),
            trade_date="20240103",
            knowledge_cutoff="2024-01-03T15:00:00",
            snapshot_version=7,
            mode=PITQueryMode.AS_WAS_KNOWN,
        )


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("False", False),
        ("0", False),
        ("true", True),
        (1, True),
    ],
)
def test_short_allowed_parses_only_explicit_boolean_values(
    raw_value: object,
    expected: bool,
) -> None:
    resolver = MarketRuleResolver(
        InMemoryPITMarketRuleSource(
            [
                _record(
                    "510300.SH",
                    "domestic_equity_etf",
                    short_allowed=raw_value,  # type: ignore[arg-type]
                )
            ]
        )
    )

    rules = resolver.resolve(
        _instrument(),
        trade_date="20240103",
        knowledge_cutoff="2024-01-03T15:00:00",
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert rules.short_allowed is expected


@pytest.mark.parametrize("raw_value", ["maybe", "", None, 2])
def test_short_allowed_invalid_values_fail_closed(raw_value: object) -> None:
    record = _record("510300.SH", "domestic_equity_etf")
    record["short_allowed"] = raw_value
    resolver = MarketRuleResolver(InMemoryPITMarketRuleSource([record]))

    with pytest.raises(PITInvalidMarketRule, match="PIT_INVALID_EXECUTION_RULE"):
        resolver.resolve(
            _instrument(),
            trade_date="20240103",
            knowledge_cutoff="2024-01-03T15:00:00",
            snapshot_version=7,
            mode=PITQueryMode.AS_WAS_KNOWN,
        )
