from __future__ import annotations

from collections.abc import Iterable

from backtest.fund_rotation.market_rules import (
    FundInstrumentVersion,
    InMemoryPITMarketRuleSource,
    MarketRuleResolver,
)


def make_test_market_rule_inputs(
    codes: Iterable[str],
    *,
    snapshot_version: int = 1,
) -> tuple[MarketRuleResolver, dict[str, FundInstrumentVersion]]:
    unique_codes = tuple(sorted({str(code) for code in codes}))
    resolver = MarketRuleResolver(
        InMemoryPITMarketRuleSource(
            [
                {
                    "ts_code": code,
                    "instrument_type": "domestic_equity_etf",
                    "valid_from": "19000101",
                    "valid_to": None,
                    "known_from": "19000101T000000",
                    "snapshot_version": snapshot_version,
                    "revision_id": f"{code}-r1",
                    "revision_order": 1,
                    "settlement": "T+1",
                    "lot_size": 100,
                    "tick_size": 0.001,
                    "price_limit_pct": 0.10,
                    "short_allowed": False,
                    "currency": "CNY",
                    "source_record_id": f"{code}-src",
                    "source_id": "fund-rotation-test-rules",
                    "rule_version": f"{code}-rules-v1",
                }
                for code in unique_codes
            ]
        )
    )
    instruments = {
        code: FundInstrumentVersion(code, "domestic_equity_etf", f"{code}-src")
        for code in unique_codes
    }
    return resolver, instruments
