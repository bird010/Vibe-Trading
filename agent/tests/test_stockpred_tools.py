"""Tests for StockPred base tool class and all 7 StockPred tools.

TDD: written before implementation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

lance = pytest.importorskip("lance")


def _build_lance(root: Path, layer: str, table: str, rows: list[dict]) -> Path:
    """Create a minimal Lance dataset under root/data/lance/{layer}/{table}.lance."""
    lance_dir = root / "data" / "lance" / layer / f"{table}.lance"
    lance_dir.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    lance.write_dataset(df, str(lance_dir), mode="create")
    return lance_dir


def _build_stockpred_root(tmp_path: Path) -> Path:
    """Build a minimal StockPred data root with key datasets."""
    # market_core: fact_fina_indicator (2 rows)
    _build_lance(
        tmp_path, "market_core", "fact_fina_indicator",
        [
            {"ts_code": "000001.SZ", "ann_date": "20260430", "end_date": "20260331",
             "eps": 1.5, "roe": 12.0, "roa": 5.0, "grossprofit_margin": 35.0,
             "netprofit_margin": 20.0, "debt_to_assets": 45.0,
             "current_ratio": 1.8, "quick_ratio": 1.2, "ocfps": 2.0,
             "dt_eps": 1.4, "roe_dt": 11.0},
        ],
    )
    # market_core: fact_macro_series (2 rows)
    _build_lance(
        tmp_path, "market_core", "fact_macro_series",
        [
            {"series_id": "rate.shibor_on", "obs_date": "2026-06-20",
             "period": "2026-06-20", "value": 1.85, "source": "Tushare",
             "available_date": "2026-06-20"},
            {"series_id": "macro.gdp_yoy", "obs_date": "2026-03-31",
             "period": "2026Q1", "value": 5.2, "source": "Tushare",
             "available_date": "2026-04-15"},
        ],
    )
    # source_raw: raw_income (1 row)
    _build_lance(
        tmp_path, "source_raw", "raw_income",
        [
            {"ts_code": "000001.SZ", "ann_date": "20260430", "f_ann_date": "20260430",
             "end_date": "20260331", "report_type": "1", "comp_type": "1",
             "basic_eps": 1.5, "diluted_eps": 1.5, "total_revenue": 100000.0,
             "revenue": 100000.0, "operate_profit": 30000.0,
             "total_profit": 28000.0, "n_income": 21000.0, "n_income_attr_p": 21000.0},
        ],
    )
    # source_raw: raw_dividend (1 row)
    _build_lance(
        tmp_path, "source_raw", "raw_dividend",
        [
            {"ts_code": "600519.SH", "end_date": "20251231", "ann_date": "20260415",
             "div_proc": "实施", "stk_div": 0.0, "stk_bo_rate": 0.0,
             "stk_co_rate": 0.0, "cash_div": 30.876, "cash_div_tax": 27.788,
             "record_date": "20260620", "ex_date": "20260621", "pay_date": "20260621"},
        ],
    )
    # graph: fact_graph_features (1 row)
    _build_lance(
        tmp_path, "graph", "fact_graph_features",
        [
            {"ts_code": "000001.SZ", "industry": "银行", "trade_date": "20260625",
             "industry_momentum_20d": 0.05, "diffusion_score": 0.7,
             "crowding_score": 0.3, "reversal_5d": -0.02,
             "neighbor_momentum": 0.03, "fundamental_roe": 12.0,
             "low_volatility_signal": 0.8, "liquidity_signal": 0.6,
             "rotation_phase": "扩散", "beta_category": "low"},
        ],
    )
    # market_core: fact_index_daily (1 row)
    _build_lance(
        tmp_path, "market_core", "fact_index_daily",
        [
            {"ts_code": "000300.SH", "trade_date": "20260625",
             "close": 3800.0, "open": 3780.0, "high": 3820.0,
             "low": 3770.0, "vol": 200000000.0, "amount": 380000000.0,
             "pre_close": 3775.0, "change": 25.0, "pct_chg": 0.66},
        ],
    )
    return tmp_path


# ---------------------------------------------------------------------------
# StockPredBaseTool
# ---------------------------------------------------------------------------


class TestStockPredBaseTool:
    def test_check_available_false_when_no_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No STOCKPRED_DATA_ROOT → tools unavailable."""
        monkeypatch.delenv("STOCKPRED_DATA_ROOT", raising=False)
        from src.tools.stockpred_financials_tool import StockpredFinancialsTool

        assert StockpredFinancialsTool.check_available() is False

    def test_check_available_true_when_root_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _build_stockpred_root(tmp_path)
        monkeypatch.setenv("STOCKPRED_DATA_ROOT", str(root))
        from src.tools.stockpred_financials_tool import StockpredFinancialsTool

        assert StockpredFinancialsTool.check_available() is True


# ---------------------------------------------------------------------------
# Financials Tool
# ---------------------------------------------------------------------------


class TestFinancialsTool:
    @pytest.fixture()
    def env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        root = _build_stockpred_root(tmp_path)
        monkeypatch.setenv("STOCKPRED_DATA_ROOT", str(root))
        return root

    def test_name(self, env: Path) -> None:
        from src.tools.stockpred_financials_tool import StockpredFinancialsTool

        assert StockpredFinancialsTool.name == "get_stockpred_financials"

    def test_execute_returns_json_with_indicators(self, env: Path) -> None:
        from src.tools.stockpred_financials_tool import StockpredFinancialsTool

        result = json.loads(StockpredFinancialsTool().execute(
            code="000001.SZ", data_type="indicators"
        ))
        assert result["ok"] is True
        assert len(result["data"]) >= 1
        assert result["data"][0]["roe"] == 12.0

    def test_execute_returns_json_with_income(self, env: Path) -> None:
        from src.tools.stockpred_financials_tool import StockpredFinancialsTool

        result = json.loads(StockpredFinancialsTool().execute(
            code="000001.SZ", data_type="income"
        ))
        assert result["ok"] is True
        assert result["data"][0]["total_revenue"] == 100000.0

    def test_execute_empty_for_missing_code(self, env: Path) -> None:
        from src.tools.stockpred_financials_tool import StockpredFinancialsTool

        result = json.loads(StockpredFinancialsTool().execute(
            code="999999.SZ", data_type="indicators"
        ))
        assert result["ok"] is True
        assert result["data"] == []


# ---------------------------------------------------------------------------
# Macro Tool
# ---------------------------------------------------------------------------


class TestMacroTool:
    @pytest.fixture()
    def env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        root = _build_stockpred_root(tmp_path)
        monkeypatch.setenv("STOCKPRED_DATA_ROOT", str(root))
        return root

    def test_name(self, env: Path) -> None:
        from src.tools.stockpred_macro_tool import StockpredMacroTool

        assert StockpredMacroTool.name == "get_stockpred_macro"

    def test_execute_returns_series(self, env: Path) -> None:
        from src.tools.stockpred_macro_tool import StockpredMacroTool

        result = json.loads(StockpredMacroTool().execute(series_id="rate.shibor_on"))
        assert result["ok"] is True
        assert len(result["data"]) >= 1
        assert result["data"][0]["value"] == 1.85


# ---------------------------------------------------------------------------
# Dividend Tool
# ---------------------------------------------------------------------------


class TestDividendTool:
    @pytest.fixture()
    def env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        root = _build_stockpred_root(tmp_path)
        monkeypatch.setenv("STOCKPRED_DATA_ROOT", str(root))
        return root

    def test_name(self, env: Path) -> None:
        from src.tools.stockpred_dividend_tool import StockpredDividendTool

        assert StockpredDividendTool.name == "get_stockpred_dividend"

    def test_execute_returns_dividends(self, env: Path) -> None:
        from src.tools.stockpred_dividend_tool import StockpredDividendTool

        result = json.loads(StockpredDividendTool().execute(code="600519.SH"))
        assert result["ok"] is True
        assert result["data"][0]["cash_div"] == 30.876


# ---------------------------------------------------------------------------
# Graph Tool
# ---------------------------------------------------------------------------


class TestGraphTool:
    @pytest.fixture()
    def env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        root = _build_stockpred_root(tmp_path)
        monkeypatch.setenv("STOCKPRED_DATA_ROOT", str(root))
        return root

    def test_name(self, env: Path) -> None:
        from src.tools.stockpred_graph_tool import StockpredGraphTool

        assert StockpredGraphTool.name == "get_stockpred_graph"

    def test_execute_returns_features(self, env: Path) -> None:
        from src.tools.stockpred_graph_tool import StockpredGraphTool

        result = json.loads(StockpredGraphTool().execute(
            code="000001.SZ", data_type="features"
        ))
        assert result["ok"] is True
        assert result["data"][0]["industry"] == "银行"
        assert result["data"][0]["diffusion_score"] == 0.7


# ---------------------------------------------------------------------------
# Index Tool
# ---------------------------------------------------------------------------


class TestIndexTool:
    @pytest.fixture()
    def env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        root = _build_stockpred_root(tmp_path)
        monkeypatch.setenv("STOCKPRED_DATA_ROOT", str(root))
        return root

    def test_name(self, env: Path) -> None:
        from src.tools.stockpred_index_tool import StockpredIndexTool

        assert StockpredIndexTool.name == "get_stockpred_index"

    def test_execute_returns_daily(self, env: Path) -> None:
        from src.tools.stockpred_index_tool import StockpredIndexTool

        result = json.loads(StockpredIndexTool().execute(
            code="000300.SH", data_type="daily"
        ))
        assert result["ok"] is True
        assert result["data"][0]["close"] == 3800.0


# ---------------------------------------------------------------------------
# Holders Tool (minimal — no fixture data, tests unavailable path)
# ---------------------------------------------------------------------------


class TestHoldersTool:
    def test_name(self) -> None:
        from src.tools.stockpred_holders_tool import StockpredHoldersTool

        assert StockpredHoldersTool.name == "get_stockpred_holders"

    def test_check_available_false_when_no_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("STOCKPRED_DATA_ROOT", raising=False)
        from src.tools.stockpred_holders_tool import StockpredHoldersTool

        assert StockpredHoldersTool.check_available() is False


# ---------------------------------------------------------------------------
# Fund Tool (minimal — no fixture data, tests unavailable path)
# ---------------------------------------------------------------------------


class TestFundTool:
    def test_name(self) -> None:
        from src.tools.stockpred_fund_tool import StockpredFundTool

        assert StockpredFundTool.name == "get_stockpred_fund"

    def test_check_available_false_when_no_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("STOCKPRED_DATA_ROOT", raising=False)
        from src.tools.stockpred_fund_tool import StockpredFundTool

        assert StockpredFundTool.check_available() is False
