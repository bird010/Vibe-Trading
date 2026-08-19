"""Tests for PIT assurance classifier."""

from __future__ import annotations

from backtest.stockpred.cohort.pit_assurance import (
    REVISABLE_TABLES,
    STRICT_TABLES,
    classify_pit_assurance,
)


def test_all_strict_tables_returns_strict():
    deps = ["stock", "fact_adj_factor", "fact_stock_limit"]
    result = classify_pit_assurance(deps)
    assert result.level == "strict"
    assert result.snapshot_only_tables == []
    assert result.warning == ""


def test_any_revisable_returns_snapshot_only():
    deps = ["stock", "fact_fina_indicator"]
    result = classify_pit_assurance(deps)
    assert result.level == "snapshot_only"
    assert "fact_fina_indicator" in result.snapshot_only_tables
    assert result.warning != ""


def test_multiple_revisable():
    deps = ["fact_fina_indicator", "dim_stock_name_history", "bridge_stock_industry"]
    result = classify_pit_assurance(deps)
    assert result.level == "snapshot_only"
    assert len(result.snapshot_only_tables) == 3


def test_empty_deps_returns_strict():
    result = classify_pit_assurance([])
    assert result.level == "strict"
    assert result.strict_tables == []
    assert result.snapshot_only_tables == []


def test_unknown_pit_dependency_is_snapshot_only():
    deps = ["stock", "some_unknown_table"]
    result = classify_pit_assurance(deps)
    assert result.level == "snapshot_only"
    assert result.snapshot_only_tables == ["some_unknown_table"]


def test_strict_tables_identified():
    deps = ["stock", "fact_adj_factor", "fact_fina_indicator"]
    result = classify_pit_assurance(deps)
    assert "stock" in result.strict_tables
    assert "fact_adj_factor" in result.strict_tables
    assert "fact_fina_indicator" in result.snapshot_only_tables


def test_warning_mentions_revisable_tables():
    deps = ["fact_moneyflow"]
    result = classify_pit_assurance(deps)
    assert "fact_moneyflow" in result.warning
    assert "snapshot_only" in result.warning


def test_all_revisable_tables_defined():
    expected = {
        "fact_fina_indicator",
        "dim_stock_name_history",
        "bridge_stock_industry",
        "fact_stock_daily_basic",
        "fact_moneyflow",
    }
    assert REVISABLE_TABLES == expected


def test_all_strict_tables_defined():
    expected = {
        "stock",
        "dim_stock",
        "fact_adj_factor",
        "fact_stock_limit",
        "dim_trade_cal",
        "fact_index_daily",
        "fact_index_weight",
    }
    assert STRICT_TABLES == expected
