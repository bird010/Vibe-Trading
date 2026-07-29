from __future__ import annotations

import pandas as pd

from src.stockpred.graph.universe import build_pit_universe


EVAL_DATE = "20260331"
TRADE_DATES = pd.bdate_range("2025-01-01", EVAL_DATE).strftime("%Y%m%d").tolist()


def _stocks() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ", "000005.SZ"],
            "name": ["旧名称", "普通名称", "次新股", "已退市", "待上市"],
            "industry": ["银行", "地产", "电子", "零售", "机械"],
            "list_date": ["19910403", "19910129", "20260301", "20000101", "20260401"],
            "delist_date": ["", "", "", "20260301", ""],
            "list_status": ["L", "L", "L", "D", "P"],
            "exchange": ["SZSE"] * 5,
            "market": ["主板"] * 5,
        }
    )


def _name_history() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": [
                "000001.SZ",
                "000001.SZ",
                "000002.SZ",
                "000003.SZ",
                "000004.SZ",
                "000005.SZ",
            ],
            "security_name": ["*ST旧名", "平安银行", "ST万科", "次新股", "已退市", "待上市"],
            "effective_from": [
                "20200101",
                EVAL_DATE,
                "20200101",
                "20260301",
                "20200101",
                "20260401",
            ],
            "effective_to": [EVAL_DATE, "", "", "", "", ""],
            "ann_date": [
                "20200101",
                EVAL_DATE,
                "20200101",
                "20260301",
                "20200101",
                "20260401",
            ],
        }
    )


def _industry_history() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ", "000005.SZ"],
            "industry_code": ["801780", "801180", "801080", "801200", "801890"],
            "industry_name": ["银行", "房地产", "电子", "商贸零售", "机械设备"],
            "effective_from": ["20200101"] * 5,
            "effective_to": [""] * 5,
        }
    )


def test_universe_uses_half_open_history_and_exclusion_rules() -> None:
    selected, stats = build_pit_universe(
        _stocks(),
        eval_date=EVAL_DATE,
        trade_dates=TRADE_DATES,
        min_listed_trade_days=60,
        name_history=_name_history(),
        industry_history=_industry_history(),
        exclude_st=True,
    )

    assert selected["ts_code"].tolist() == ["000001.SZ"]
    assert selected.loc[0, "pit_name"] == "平安银行"
    assert selected.loc[0, "industry_code"] == "801780"
    assert stats.input_count == 5
    assert stats.pre_list_excluded == 1
    assert stats.post_delist_excluded == 1
    assert stats.recent_listing_excluded == 1
    assert stats.st_excluded == 1
    assert stats.name_missing == 0
    assert stats.industry_missing == 0


def test_universe_can_keep_st_when_exclusion_is_disabled() -> None:
    selected, stats = build_pit_universe(
        _stocks(),
        eval_date=EVAL_DATE,
        trade_dates=TRADE_DATES,
        min_listed_trade_days=60,
        name_history=_name_history(),
        industry_history=_industry_history(),
        exclude_st=False,
    )

    assert selected["ts_code"].tolist() == ["000001.SZ", "000002.SZ"]
    assert stats.st_excluded == 0


def test_universe_excludes_active_security_with_delisting_name() -> None:
    stocks = _stocks().iloc[[0]].copy()
    names = _name_history().iloc[[1]].copy()
    names["security_name"] = "退市卓朗"

    selected, stats = build_pit_universe(
        stocks,
        eval_date=EVAL_DATE,
        trade_dates=TRADE_DATES,
        min_listed_trade_days=60,
        name_history=names,
        industry_history=_industry_history().iloc[[0]],
        exclude_st=True,
    )

    assert selected.empty
    assert stats.st_excluded == 1


def test_universe_falls_back_to_current_name_and_industry_without_histories() -> None:
    selected, stats = build_pit_universe(
        _stocks().iloc[[0]],
        eval_date=EVAL_DATE,
        trade_dates=TRADE_DATES,
        min_listed_trade_days=60,
        name_history=None,
        industry_history=None,
        exclude_st=True,
    )

    assert selected.loc[0, "pit_name"] == "旧名称"
    assert selected.loc[0, "industry"] == "银行"
    assert stats.name_missing == 0
    assert stats.industry_missing == 0


def test_universe_reports_missing_pit_industry() -> None:
    selected, stats = build_pit_universe(
        _stocks().iloc[[0]],
        eval_date=EVAL_DATE,
        trade_dates=TRADE_DATES,
        min_listed_trade_days=60,
        name_history=_name_history(),
        industry_history=pd.DataFrame(columns=_industry_history().columns),
        exclude_st=True,
    )

    assert selected["industry"].isna().all()
    assert stats.industry_missing == 1


def test_empty_stock_dimension_returns_zero_stats() -> None:
    selected, stats = build_pit_universe(
        pd.DataFrame(),
        eval_date=EVAL_DATE,
        trade_dates=TRADE_DATES,
        min_listed_trade_days=60,
    )

    assert selected.empty
    assert stats.input_count == 0
