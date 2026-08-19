"""Tests for ETF universe filtering and historical eligibility — §8."""

import pandas as pd
import pytest

from backtest.fund_rotation.universe import (
    ExclusionReason,
    filter_etf_universe,
    check_historical_eligibility,
)


def _dim_fund_df(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal dim_fund DataFrame."""
    return pd.DataFrame(rows)


class TestStaticNameFilter:
    """§8.1 — name contains ETF, excludes QDII/LOF/联接."""

    def test_includes_etf_name(self):
        df = _dim_fund_df([
            {"ts_code": "510300.SH", "name": "沪深300ETF", "list_date": "20120528"},
        ])
        result = filter_etf_universe(df)
        assert "510300.SH" in result["ts_code"].values

    def test_excludes_non_etf_name(self):
        df = _dim_fund_df([
            {"ts_code": "510300.SH", "name": "沪深300指数基金", "list_date": "20120528"},
        ])
        result = filter_etf_universe(df)
        assert len(result) == 0

    def test_excludes_qdii(self):
        df = _dim_fund_df([
            {"ts_code": "513050.SH", "name": "中概互联网ETF(QDII)", "list_date": "20170104"},
        ])
        result = filter_etf_universe(df)
        assert len(result) == 0

    def test_excludes_lof(self):
        df = _dim_fund_df([
            {"ts_code": "160222.SZ", "name": "国泰深证TMT50ETF LOF", "list_date": "20141222"},
        ])
        result = filter_etf_universe(df)
        assert len(result) == 0

    def test_excludes_feeder(self):
        df = _dim_fund_df([
            {"ts_code": "007339.SZ", "name": "易方达沪深300ETF联接A", "list_date": "20190601"},
        ])
        result = filter_etf_universe(df)
        assert len(result) == 0

    def test_mixed_pool(self):
        df = _dim_fund_df([
            {"ts_code": "510300.SH", "name": "沪深300ETF", "list_date": "20120528"},
            {"ts_code": "159915.SZ", "name": "创业板ETF", "list_date": "20110920"},
            {"ts_code": "513050.SH", "name": "中概互联网ETF(QDII)", "list_date": "20170104"},
            {"ts_code": "007339.SZ", "name": "ETF联接A", "list_date": "20190601"},
            {"ts_code": "000001.SZ", "name": "平安银行", "list_date": "19910403"},
        ])
        result = filter_etf_universe(df)
        codes = set(result["ts_code"].values)
        assert codes == {"510300.SH", "159915.SZ"}


class TestHistoricalEligibility:
    """§8.2 — per-signal-date eligibility checks."""

    def test_listed_before_signal_date_passes(self):
        df = _dim_fund_df([
            {"ts_code": "510300.SH", "name": "沪深300ETF", "list_date": "20120528"},
        ])
        eligible, excluded = check_historical_eligibility(df, signal_date="20200101")
        assert "510300.SH" in eligible

    def test_listed_after_signal_date_excluded(self):
        df = _dim_fund_df([
            {"ts_code": "588000.SH", "name": "科创50ETF", "list_date": "20200928"},
        ])
        eligible, excluded = check_historical_eligibility(df, signal_date="20200101")
        assert "588000.SH" not in eligible
        assert any(e.ts_code == "588000.SH" and e.reason == ExclusionReason.NOT_YET_LISTED for e in excluded)

    def test_missing_list_date_excluded(self):
        df = _dim_fund_df([
            {"ts_code": "999999.SH", "name": "神秘ETF", "list_date": None},
        ])
        eligible, excluded = check_historical_eligibility(df, signal_date="20200101")
        assert "999999.SH" not in eligible
        assert any(e.reason == ExclusionReason.INVALID_LIST_DATE for e in excluded)

    def test_invalid_list_date_format_excluded(self):
        df = _dim_fund_df([
            {"ts_code": "999998.SH", "name": "坏日期ETF", "list_date": "not_a_date"},
        ])
        eligible, excluded = check_historical_eligibility(df, signal_date="20200101")
        assert "999998.SH" not in eligible
        assert any(e.reason == ExclusionReason.INVALID_LIST_DATE for e in excluded)

    def test_same_day_listing_passes(self):
        df = _dim_fund_df([
            {"ts_code": "510300.SH", "name": "沪深300ETF", "list_date": "20200101"},
        ])
        eligible, _ = check_historical_eligibility(df, signal_date="20200101")
        assert "510300.SH" in eligible

    def test_delisted_etf_still_eligible_before_delist(self):
        """§8.2.3 — 不以回测结束时仍存在作为历史资格条件."""
        df = _dim_fund_df([
            {"ts_code": "159001.SZ", "name": "已退市ETF", "list_date": "20150101"},
        ])
        # Even if we know it's delisted now, it's eligible for historical dates
        eligible, _ = check_historical_eligibility(df, signal_date="20180601")
        assert "159001.SZ" in eligible


class TestExclusionRecord:
    """Exclusion records carry structured reason and details."""

    def test_exclusion_has_ts_code_and_reason(self):
        df = _dim_fund_df([
            {"ts_code": "588000.SH", "name": "科创50ETF", "list_date": "20200928"},
        ])
        _, excluded = check_historical_eligibility(df, signal_date="20200101")
        assert len(excluded) == 1
        assert excluded[0].ts_code == "588000.SH"
        assert excluded[0].reason == ExclusionReason.NOT_YET_LISTED
        assert "20200928" in excluded[0].details


class TestOrderIndependence:
    """Filtering results must not depend on input row order."""

    def test_shuffled_input_same_output(self):
        rows = [
            {"ts_code": "510300.SH", "name": "沪深300ETF", "list_date": "20120528"},
            {"ts_code": "159915.SZ", "name": "创业板ETF", "list_date": "20110920"},
            {"ts_code": "513050.SH", "name": "中概互联网ETF(QDII)", "list_date": "20170104"},
        ]
        df1 = _dim_fund_df(rows)
        df2 = _dim_fund_df(list(reversed(rows)))
        r1 = filter_etf_universe(df1)
        r2 = filter_etf_universe(df2)
        assert set(r1["ts_code"].values) == set(r2["ts_code"].values)
