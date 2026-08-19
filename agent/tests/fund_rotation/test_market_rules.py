from __future__ import annotations

import pandas as pd
import pytest

from backtest.fund_rotation.market_rules import (
    ExecutionRuleProvenance,
    FundInstrumentVersion,
    InMemoryPITMarketRuleSource,
    MarketRuleResolver,
    PITInvalidMarketRule,
    ResearchExecutionRuleContext,
    UnknownExecutionRule,
    build_research_static_execution_rule_context,
)
from backtest.fund_rotation.pit_universe import (
    PITQueryMode,
    map_fund_type_asset_class_to_instrument_type,
)


def test_build_research_static_context_for_supported_domestic_etf() -> None:
    context = build_research_static_execution_rule_context(
        dim_fund=pd.DataFrame(
            [
                {
                    "ts_code": "510300.SH",
                    "name": "沪深300ETF",
                    "fund_type": "股票型",
                    "list_date": "20120101",
                },
            ]
        ),
        universe_codes=["510300.SH"],
        evaluation_start_date="20230101",
        evaluation_end_date="20231229",
        snapshot_version=55,
    )

    assert context.source_id == "RESEARCH_STATIC_RULES"
    assert context.rule_version == "research-cn-etf-v1"
    assert context.pit_verified is False
    assert context.instruments["510300.SH"].instrument_type == "domestic_equity_etf"

    rules = context.resolver.resolve(
        context.instruments["510300.SH"],
        trade_date="20230103",
        knowledge_cutoff="2023-01-03T15:00:00",
        snapshot_version=55,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert rules.settlement == "T+1"
    assert rules.lot_size == 100
    assert rules.tick_size == 0.001
    assert rules.price_limit_pct == 0.10
    assert rules.short_allowed is False
    assert rules.currency == "CNY"
    assert rules.source_id == "RESEARCH_STATIC_RULES"
    assert rules.source_record_id == "research-static:510300.SH"
    assert rules.rule_version == "research-cn-etf-v1"


def test_research_static_context_supports_bond_etf() -> None:
    context = build_research_static_execution_rule_context(
        dim_fund=pd.DataFrame(
            [
                {
                    "ts_code": "511010.SH",
                    "name": "国债ETF",
                    "fund_type": "ETF",
                    "asset_class": "bond",
                    "list_date": "20130101",
                },
            ]
        ),
        universe_codes=["511010.SH"],
        evaluation_start_date="20230101",
        evaluation_end_date="20231229",
        snapshot_version=55,
    )

    assert set(context.instruments) == {"511010.SH"}
    assert context.instruments["511010.SH"].instrument_type == "bond_etf"


def test_research_static_context_supports_gold_etf() -> None:
    context = build_research_static_execution_rule_context(
        dim_fund=pd.DataFrame(
            [
                {
                    "ts_code": "518880.SH",
                    "name": "黄金ETF",
                    "fund_type": "ETF",
                    "asset_class": "gold",
                },
            ]
        ),
        universe_codes=["518880.SH"],
        evaluation_start_date="20230101",
        evaluation_end_date="20231229",
        snapshot_version=55,
    )

    assert set(context.instruments) == {"518880.SH"}
    assert context.instruments["518880.SH"].instrument_type == "commodity_etf"
    rules = context.resolver.resolve(
        context.instruments["518880.SH"],
        trade_date="20230103",
        knowledge_cutoff="2023-01-03T15:00:00",
        snapshot_version=55,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )
    assert rules.settlement == "T+0"


def test_research_static_context_supports_money_market_etf_without_price_limit() -> None:
    context = build_research_static_execution_rule_context(
        dim_fund=pd.DataFrame(
            [
                {
                    "ts_code": "511880.SH",
                    "name": "货币ETF",
                    "fund_type": "ETF",
                    "asset_class": "money_market",
                },
            ]
        ),
        universe_codes=["511880.SH"],
        evaluation_start_date="20230101",
        evaluation_end_date="20231229",
        snapshot_version=55,
    )

    rules = context.resolver.resolve(
        context.instruments["511880.SH"],
        trade_date="20230103",
        knowledge_cutoff="2023-01-03T15:00:00",
        snapshot_version=55,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )
    assert rules.price_limit_pct is None
    assert rules.price_limit_rule == "NONE"


def test_research_static_context_compatibly_maps_legacy_money_fund_etf() -> None:
    context = build_research_static_execution_rule_context(
        dim_fund=pd.DataFrame(
            [
                {
                    "ts_code": "159001.SZ",
                    "name": "货币ETF",
                    "fund_type": "货币型",
                    "list_date": "20141020",
                },
            ]
        ),
        universe_codes=["159001.SZ"],
        evaluation_start_date="20230101",
        evaluation_end_date="20231229",
        snapshot_version=55,
    )

    assert context.instruments["159001.SZ"].instrument_type == "money_market_etf"


def test_research_static_context_rejects_unknown_instrument_type() -> None:
    with pytest.raises(UnknownExecutionRule, match="unsupported research-static instrument"):
        build_research_static_execution_rule_context(
            dim_fund=pd.DataFrame(
                [
                    {
                        "ts_code": "511010.SH",
                        "name": "国债ETF",
                        "fund_type": "ETF",
                        "asset_class": "unsupported",
                    },
                ]
            ),
            universe_codes=["511010.SH"],
            evaluation_start_date="20230101",
            evaluation_end_date="20231229",
            snapshot_version=55,
        )


@pytest.mark.parametrize(
    ("fund_type", "expected"),
    [
        ("股票型", "domestic_equity_etf"),
        ("股票指数型", "domestic_equity_etf"),
        ("债券型", "bond_etf"),
        ("货币型", "money_market_etf"),
        ("其他", "commodity_etf"),
    ],
)
def test_research_static_context_supports_legacy_fund_types(
    fund_type: str,
    expected: str,
) -> None:
    context = build_research_static_execution_rule_context(
        dim_fund=pd.DataFrame(
            [{"ts_code": "TEST.ETF", "name": "测试ETF", "fund_type": fund_type}]
        ),
        universe_codes=["TEST.ETF"],
        evaluation_start_date="20230101",
        evaluation_end_date="20231229",
        snapshot_version=55,
    )

    assert context.instruments["TEST.ETF"].instrument_type == expected


def test_research_static_context_requires_structured_instrument_type() -> None:
    with pytest.raises(UnknownExecutionRule, match="unsupported research-static instrument"):
        build_research_static_execution_rule_context(
            dim_fund=pd.DataFrame(
                [{"ts_code": "510300.SH", "name": "沪深300ETF"}]
            ),
            universe_codes=["510300.SH"],
            evaluation_start_date="20230101",
            evaluation_end_date="20231229",
            snapshot_version=55,
        )


def test_research_static_context_uses_canonical_etf_asset_class() -> None:
    context = build_research_static_execution_rule_context(
        dim_fund=pd.DataFrame(
            [
                {
                    "ts_code": "510300.SH",
                    "name": "沪深300ETF",
                    "fund_type": "ETF",
                    "asset_class": "equity",
                }
            ]
        ),
        universe_codes=["510300.SH"],
        evaluation_start_date="20230101",
        evaluation_end_date="20231229",
        snapshot_version=55,
    )

    assert context.instruments["510300.SH"].instrument_type == "domestic_equity_etf"


def test_formal_canonical_mapper_does_not_treat_stock_fund_as_etf() -> None:
    assert map_fund_type_asset_class_to_instrument_type("股票型", None) is None


def test_context_rejects_provenance_not_backed_by_resolver() -> None:
    resolver = MarketRuleResolver(
        InMemoryPITMarketRuleSource(
            [],
            provenance=ExecutionRuleProvenance(
                source_id="RESEARCH_STATIC_RULES",
                rule_version="research-cn-etf-v1",
                pit_verified=False,
            ),
        )
    )
    with pytest.raises(ValueError, match="provenance"):
        ResearchExecutionRuleContext(
            resolver=resolver,
            instruments={"510300.SH": FundInstrumentVersion(
                "510300.SH", "domestic_equity_etf", "research-static-v1"
            )},
            rule_version="pit-r1",
            source_id="PIT",
            pit_verified=True,
        )


def test_resolver_rejects_record_provenance_mismatch() -> None:
    resolver = MarketRuleResolver(
        InMemoryPITMarketRuleSource(
            [
                {
                    "ts_code": "510300.SH",
                    "instrument_type": "domestic_equity_etf",
                    "valid_from": "20230101",
                    "valid_to": None,
                    "known_from": "20230101T000000",
                    "snapshot_version": 55,
                    "revision_id": "r1",
                    "revision_order": 1,
                    "settlement": "T+1",
                    "lot_size": 100,
                    "tick_size": 0.001,
                    "price_limit_pct": 0.10,
                    "price_limit_rule": "PCT:0.1",
                    "short_allowed": False,
                    "currency": "CNY",
                    "source_record_id": "research-static:510300.SH",
                    "source_id": "RESEARCH_STATIC_RULES",
                    "rule_version": "research-cn-etf-v1",
                }
            ],
            provenance=ExecutionRuleProvenance(
                source_id="PIT_FIXTURE",
                rule_version="pit-r1",
                pit_verified=True,
            ),
        )
    )
    with pytest.raises(PITInvalidMarketRule, match="provenance mismatch"):
        resolver.resolve(
            FundInstrumentVersion("510300.SH", "domestic_equity_etf", "v1"),
            trade_date="20230103",
            knowledge_cutoff="2023-01-03T15:00:00",
            snapshot_version=55,
            mode=PITQueryMode.AS_WAS_KNOWN,
        )


def test_research_static_context_rejects_missing_instrument_metadata() -> None:
    with pytest.raises(UnknownExecutionRule, match="missing instrument metadata"):
        build_research_static_execution_rule_context(
            dim_fund=pd.DataFrame(columns=["ts_code", "name", "list_date"]),
            universe_codes=["510300.SH"],
            evaluation_start_date="20230101",
            evaluation_end_date="20231229",
            snapshot_version=55,
        )
