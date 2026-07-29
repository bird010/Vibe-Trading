"""Tests for the StockPred Lance-backed local data loader.

Follows TDD: these tests are written BEFORE the loader implementation.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest

lance = pytest.importorskip("lance")


def _build_lance_stock_dataset(root: Path, rows: list[dict]) -> Path:
    """Create a minimal stock.lance dataset under root/data/lance/market_core/."""
    lance_dir = root / "data" / "lance" / "market_core" / "stock.lance"
    lance_dir.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    lance.write_dataset(df, str(lance_dir), mode="create")
    return lance_dir


# ---------------------------------------------------------------------------
# is_available()
# ---------------------------------------------------------------------------


class TestIsAvailable:
    def test_false_when_env_not_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """STOCKPRED_DATA_ROOT unset → is_available() = False."""
        monkeypatch.delenv("STOCKPRED_DATA_ROOT", raising=False)
        from backtest.loaders.stockpred_loader import DataLoader

        assert DataLoader().is_available() is False

    def test_false_when_dir_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """STOCKPRED_DATA_ROOT points to nonexistent path → False."""
        monkeypatch.setenv("STOCKPRED_DATA_ROOT", "/nonexistent/path/xyz")
        from backtest.loaders.stockpred_loader import DataLoader

        assert DataLoader().is_available() is False

    def test_false_when_lance_dir_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Root exists but no stock.lance → False."""
        monkeypatch.setenv("STOCKPRED_DATA_ROOT", str(tmp_path))
        from backtest.loaders.stockpred_loader import DataLoader

        assert DataLoader().is_available() is False

    def test_true_when_lance_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """stock.lance exists under market_core → True."""
        _build_lance_stock_dataset(
            tmp_path,
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20260101",
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": 10.5,
                    "vol": 1000.0,
                }
            ],
        )
        monkeypatch.setenv("STOCKPRED_DATA_ROOT", str(tmp_path))
        from backtest.loaders.stockpred_loader import DataLoader

        assert DataLoader().is_available() is True


# ---------------------------------------------------------------------------
# fetch()
# ---------------------------------------------------------------------------


class TestFetch:
    @pytest.fixture()
    def stockpred_root(self, tmp_path: Path) -> Path:
        """Build a StockPred-shaped temp dir with 3 stocks × 2 days."""
        _build_lance_stock_dataset(
            tmp_path,
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20260102",
                    "pre_close": 10.0,
                    "open": 10.2,
                    "high": 11.0,
                    "low": 10.0,
                    "close": 10.8,
                    "change": 0.8,
                    "pct_chg": 8.0,
                    "vol": 5000.0,
                    "amount": 54000.0,
                },
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20260103",
                    "pre_close": 10.8,
                    "open": 10.9,
                    "high": 11.5,
                    "low": 10.7,
                    "close": 11.2,
                    "change": 0.4,
                    "pct_chg": 3.7,
                    "vol": 6000.0,
                    "amount": 66000.0,
                },
                {
                    "ts_code": "600519.SH",
                    "trade_date": "20260102",
                    "pre_close": 1800.0,
                    "open": 1810.0,
                    "high": 1850.0,
                    "low": 1800.0,
                    "close": 1830.0,
                    "change": 30.0,
                    "pct_chg": 1.67,
                    "vol": 2000.0,
                    "amount": 3660000.0,
                },
                {
                    "ts_code": "600519.SH",
                    "trade_date": "20260103",
                    "pre_close": 1830.0,
                    "open": 1835.0,
                    "high": 1860.0,
                    "low": 1820.0,
                    "close": 1845.0,
                    "change": 15.0,
                    "pct_chg": 0.82,
                    "vol": 1800.0,
                    "amount": 3312000.0,
                },
            ],
        )
        return tmp_path

    def test_fetch_returns_dict_of_dataframes(
        self,
        stockpred_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """fetch() returns {symbol: DataFrame} with OHLCV columns."""
        monkeypatch.setenv("STOCKPRED_DATA_ROOT", str(stockpred_root))
        from backtest.loaders.stockpred_loader import DataLoader

        result = DataLoader().fetch(
            ["000001.SZ", "600519.SH"], "2026-01-02", "2026-01-03"
        )

        assert set(result) == {"000001.SZ", "600519.SH"}
        for sym in result:
            df = result[sym]
            assert list(df.columns) == [
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]
            assert isinstance(df.index, pd.DatetimeIndex)

    def test_fetch_renames_vol_to_volume(
        self,
        stockpred_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """StockPred 'vol' column must be renamed to 'volume'."""
        monkeypatch.setenv("STOCKPRED_DATA_ROOT", str(stockpred_root))
        from backtest.loaders.stockpred_loader import DataLoader

        result = DataLoader().fetch(["000001.SZ"], "2026-01-02", "2026-01-02")
        df = result["000001.SZ"]

        assert "volume" in df.columns
        assert "vol" not in df.columns
        assert df["volume"].iloc[0] == 5000.0

    def test_fetch_drops_non_ohlcv_columns(
        self,
        stockpred_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """pre_close, change, pct_chg, amount should NOT appear in output."""
        monkeypatch.setenv("STOCKPRED_DATA_ROOT", str(stockpred_root))
        from backtest.loaders.stockpred_loader import DataLoader

        result = DataLoader().fetch(["000001.SZ"], "2026-01-02", "2026-01-02")
        df = result["000001.SZ"]

        for col in ("pre_close", "change", "pct_chg", "amount", "ts_code"):
            assert col not in df.columns

    def test_fetch_date_range_filter(
        self,
        stockpred_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Only rows within [start_date, end_date] should be returned."""
        monkeypatch.setenv("STOCKPRED_DATA_ROOT", str(stockpred_root))
        from backtest.loaders.stockpred_loader import DataLoader

        result = DataLoader().fetch(["000001.SZ"], "2026-01-03", "2026-01-03")
        df = result["000001.SZ"]

        assert len(df) == 1
        assert df.index[0] == pd.Timestamp("2026-01-03")

    def test_fetch_missing_code_returns_empty(
        self,
        stockpred_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A code not in the dataset should produce no entry (fall-through)."""
        monkeypatch.setenv("STOCKPRED_DATA_ROOT", str(stockpred_root))
        from backtest.loaders.stockpred_loader import DataLoader

        result = DataLoader().fetch(["999999.SZ"], "2026-01-02", "2026-01-03")

        assert result == {}

    def test_fetch_trade_date_is_datetime_index(
        self,
        stockpred_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """trade_date string (YYYYMMDD) → DatetimeIndex."""
        monkeypatch.setenv("STOCKPRED_DATA_ROOT", str(stockpred_root))
        from backtest.loaders.stockpred_loader import DataLoader

        result = DataLoader().fetch(["000001.SZ"], "2026-01-02", "2026-01-03")
        df = result["000001.SZ"]

        assert df.index.name == "trade_date"
        assert all(isinstance(ts, pd.Timestamp) for ts in df.index)

    @pytest.mark.parametrize("interval", ["1m", "1H", "4H"])
    def test_fetch_rejects_non_daily_interval(
        self,
        stockpred_root: Path,
        monkeypatch: pytest.MonkeyPatch,
        interval: str,
    ) -> None:
        """Non-1D intervals must raise ValueError, not silently return daily data."""
        monkeypatch.setenv("STOCKPRED_DATA_ROOT", str(stockpred_root))
        from backtest.loaders.stockpred_loader import DataLoader

        with pytest.raises(ValueError, match="only interval='1D'"):
            DataLoader().fetch(["000001.SZ"], "2026-01-02", "2026-01-03", interval=interval)

    @pytest.mark.parametrize("interval", ["1D", "1d"])
    def test_fetch_accepts_daily_interval_case_insensitive(
        self,
        stockpred_root: Path,
        monkeypatch: pytest.MonkeyPatch,
        interval: str,
    ) -> None:
        """1D and 1d should both work and return daily data."""
        monkeypatch.setenv("STOCKPRED_DATA_ROOT", str(stockpred_root))
        from backtest.loaders.stockpred_loader import DataLoader

        result = DataLoader().fetch(["000001.SZ"], "2026-01-02", "2026-01-03", interval=interval)
        assert "000001.SZ" in result
        assert len(result["000001.SZ"]) == 2


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


class TestProtocol:
    def test_name(self) -> None:
        from backtest.loaders.stockpred_loader import DataLoader

        assert DataLoader.name == "stockpred"

    def test_markets(self) -> None:
        from backtest.loaders.stockpred_loader import DataLoader

        assert "a_share" in DataLoader.markets

    def test_requires_auth_is_false(self) -> None:
        from backtest.loaders.stockpred_loader import DataLoader

        assert DataLoader.requires_auth is False
