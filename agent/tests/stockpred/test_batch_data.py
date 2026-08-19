from __future__ import annotations

from contextlib import contextmanager

import pandas as pd

from src.stockpred.batch_data import BatchDataContext
from src.stockpred.strategies.contracts import StrategyDescriptor
from src.stockpred.strategies.panel import StockPredPanelBuilder


class CountingGateway:
    def __init__(self, dates: list[str] | None = None) -> None:
        self._dates = dates or ["20250102", "20250103", "20250106", "20250107", "20250108", "20250109"]
        self.trade_dates_calls = 0
        self.stock_dimension_calls = 0
        self.name_history_calls = 0
        self.industry_history_calls = 0
        self.prices_calls = 0
        self.adjustment_factors_calls = 0

    def trade_dates(self, start: str, end: str) -> list[str]:
        self.trade_dates_calls += 1
        return self._dates

    def stock_dimension(self) -> pd.DataFrame:
        self.stock_dimension_calls += 1
        return pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "name": "Ping An",
                    "industry": "Bank",
                    "list_date": "20200101",
                    "delist_date": "",
                    "list_status": "L",
                    "exchange": "SZSE",
                    "market": "main",
                }
            ]
        )

    def name_history(self) -> pd.DataFrame:
        self.name_history_calls += 1
        return pd.DataFrame(
            columns=["ts_code", "security_name", "effective_from", "effective_to", "ann_date", "change_reason"]
        )

    def industry_history(self) -> pd.DataFrame:
        self.industry_history_calls += 1
        return pd.DataFrame(
            columns=["ts_code", "industry_code", "industry_name", "level", "effective_from", "effective_to", "source"]
        )

    def prices(self, start: str, end: str, codes: list[str]) -> pd.DataFrame:
        self.prices_calls += 1
        dates = [date for date in self._dates if start <= date <= end]
        return pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": date,
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": 10.0,
                    "pct_chg": 0.0,
                    "vol": 1000.0,
                    "amount": 10000.0,
                }
                for date in dates
            ]
        )

    def adjustment_factors(self, start: str, end: str, codes: list[str]) -> pd.DataFrame:
        self.adjustment_factors_calls += 1
        dates = [date for date in self._dates if start <= date <= end]
        return pd.DataFrame(
            [{"ts_code": "000001.SZ", "trade_date": date, "adj_factor": 1.0} for date in dates]
        )


class RecordingTimer:
    def __init__(self) -> None:
        self.phases: list[str] = []

    @contextmanager
    def phase(self, name: str):
        self.phases.append(name)
        yield


def _descriptor(*, warmup: int) -> StrategyDescriptor:
    return StrategyDescriptor(
        id=f"alpha101_{warmup}",
        name="Alpha",
        kind="alpha_zoo",
        zoo="alpha101",
        columns_required=("close",),
        min_warmup_bars=warmup,
    )


def test_context_caches_same_snapshot_and_preserves_strategy_window() -> None:
    gateway = CountingGateway()
    context = BatchDataContext(gateway, snapshot_digest="a" * 64, data_lookback_days=3)

    first = context.panel_for_strategy("20250108", _descriptor(warmup=4))
    second = context.panel_for_strategy("20250108", _descriptor(warmup=4))

    assert gateway.trade_dates_calls == 1
    assert gateway.stock_dimension_calls == 1
    assert gateway.name_history_calls == 1
    assert gateway.industry_history_calls == 1
    assert gateway.prices_calls == 1
    assert first["close"].index.max() <= pd.Timestamp("2025-01-08")
    assert len(first["close"]) == 5
    pd.testing.assert_frame_equal(first["close"], second["close"])


def test_context_times_only_the_shared_panel_build() -> None:
    timer = RecordingTimer()
    context = BatchDataContext(CountingGateway(), snapshot_digest="a" * 64, data_lookback_days=3, phase_timer=timer)

    context.panel_for_strategy("20250108", _descriptor(warmup=4))
    context.panel_for_strategy("20250108", _descriptor(warmup=4))

    assert timer.phases == ["panel_build"]


def test_context_does_not_reuse_dynamic_data_across_snapshots() -> None:
    gateway = CountingGateway()
    first = BatchDataContext(gateway, snapshot_digest="a" * 64, data_lookback_days=3)
    second = BatchDataContext(gateway, snapshot_digest="b" * 64, data_lookback_days=3)

    first.panel_for_strategy("20250108", _descriptor(warmup=2))
    second.panel_for_strategy("20250108", _descriptor(warmup=2))

    assert first.snapshot_digest != second.snapshot_digest
    assert gateway.prices_calls == 2


def test_release_eval_date_discards_dynamic_panels() -> None:
    gateway = CountingGateway()
    context = BatchDataContext(gateway, snapshot_digest="a" * 64, data_lookback_days=3)

    context.panel_for_strategy("20250108", _descriptor(warmup=2))
    context.release_eval_date()
    context.panel_for_strategy("20250108", _descriptor(warmup=2))

    assert gateway.prices_calls == 2


def test_context_slices_different_strategy_windows_from_one_batch_max_panel() -> None:
    dates = pd.bdate_range("20240102", periods=260).strftime("%Y%m%d").tolist()
    gateway = CountingGateway(dates)
    context = BatchDataContext(
        gateway,
        snapshot_digest="a" * 64,
        data_lookback_days=30,
        batch_max_lookback=251,
    )

    short = context.panel_for_strategy(dates[-1], _descriptor(warmup=2))
    long = context.panel_for_strategy(dates[-1], _descriptor(warmup=250))

    assert gateway.prices_calls == 1
    assert len(short["close"]) == 30
    assert len(long["close"]) == 251


def test_context_releases_previous_date_panel_when_evaluation_date_changes() -> None:
    gateway = CountingGateway()
    context = BatchDataContext(
        gateway,
        snapshot_digest="a" * 64,
        data_lookback_days=3,
        batch_max_lookback=3,
    )

    context.panel_for_strategy("20250108", _descriptor(warmup=2))
    context.panel_for_strategy("20250109", _descriptor(warmup=2))

    assert gateway.prices_calls == 2
    assert list(context._panels) == [("a" * 64, "20250109", 3)]


def test_context_panel_builder_uses_its_non_default_lookback() -> None:
    dates = pd.bdate_range("20240102", periods=60).strftime("%Y%m%d").tolist()
    descriptor = _descriptor(warmup=2)
    uncached = StockPredPanelBuilder(
        CountingGateway(dates),
        data_lookback_days=30,
    ).build(dates[-1], descriptor)
    context = BatchDataContext(
        CountingGateway(dates),
        snapshot_digest="a" * 64,
        batch_max_lookback=30,
    )

    shared = StockPredPanelBuilder(
        context.gateway,
        data_lookback_days=30,
        context=context,
    ).build(dates[-1], descriptor)

    assert len(shared["close"]) == len(uncached["close"]) == 30
