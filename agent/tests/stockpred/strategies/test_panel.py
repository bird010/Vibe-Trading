from __future__ import annotations

import pandas as pd

from src.stockpred.strategies.contracts import StrategyDescriptor
from src.stockpred.strategies.panel import StockPredPanelBuilder


class _Gateway:
    def trade_dates(self, start: str, end: str) -> list[str]:
        return ["20250102", "20250103", "20250106"]

    def stock_dimension(self) -> pd.DataFrame:
        return pd.DataFrame(
            [{"ts_code": "000001.SZ", "name": "Ping An", "industry": "Bank", "list_date": "20200101", "delist_date": "", "list_status": "L", "exchange": "SZSE", "market": "main"}]
        )

    def name_history(self) -> pd.DataFrame:
        return pd.DataFrame(columns=["ts_code", "security_name", "effective_from", "effective_to", "ann_date", "change_reason"])

    def industry_history(self) -> pd.DataFrame:
        return pd.DataFrame(columns=["ts_code", "industry_code", "industry_name", "level", "effective_from", "effective_to", "source"])

    def prices(self, start: str, end: str, codes: list[str]) -> pd.DataFrame:
        return pd.DataFrame(
            [{"ts_code": "000001.SZ", "trade_date": date, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0, "pct_chg": 0.0, "vol": 1000.0, "amount": 10000.0} for date in self.trade_dates(start, end)]
        )

    def adjustment_factors(self, start: str, end: str, codes: list[str]) -> pd.DataFrame:
        return self.prices(start, end, codes)[["ts_code", "trade_date"]].assign(adj_factor=1.0)


def test_panel_only_contains_observations_visible_on_eval_date() -> None:
    descriptor = StrategyDescriptor(id="alpha101_1", name="Alpha", kind="alpha_zoo", zoo="alpha101", columns_required=("close",), min_warmup_bars=2)

    panel = StockPredPanelBuilder(_Gateway()).build("20250103", descriptor)

    assert panel["close"].index.max() == pd.Timestamp("2025-01-03")
    assert set(panel) >= {"open", "high", "low", "close", "volume", "amount", "vwap"}
    assert panel["vwap"].iloc[-1, 0] == 10_000.0 * 1000.0 / (1000.0 * 100.0 + 1.0)


def test_panel_builder_uses_batch_context_when_provided() -> None:
    descriptor = StrategyDescriptor(id="alpha101_1", name="Alpha", kind="alpha_zoo", zoo="alpha101", columns_required=("close",), min_warmup_bars=2)
    expected = {"close": pd.DataFrame({"000001.SZ": [10.0]})}

    class _Context:
        def __init__(self) -> None:
            self.calls: list[tuple[str, StrategyDescriptor]] = []

        def panel_for_strategy(
            self,
            eval_date: str,
            value: StrategyDescriptor,
            *,
            data_lookback_days: int | None = None,
        ) -> dict[str, pd.DataFrame]:
            self.calls.append((eval_date, value, data_lookback_days))
            return expected

    context = _Context()

    panel = StockPredPanelBuilder(_Gateway(), context=context).build("20250103", descriptor)

    assert panel is expected
    assert context.calls == [("20250103", descriptor, 180)]


def test_panel_builder_passes_non_default_lookback_to_batch_context() -> None:
    class _Context:
        def __init__(self) -> None:
            self.data_lookback_days: int | None = None

        def panel_for_strategy(
            self,
            eval_date: str,
            descriptor: StrategyDescriptor,
            *,
            data_lookback_days: int | None = None,
        ) -> dict[str, pd.DataFrame]:
            self.data_lookback_days = data_lookback_days
            return {"close": pd.DataFrame()}

    context = _Context()
    descriptor = StrategyDescriptor(id="alpha101_1", name="Alpha", kind="alpha_zoo", zoo="alpha101")

    StockPredPanelBuilder(_Gateway(), data_lookback_days=30, context=context).build("20250103", descriptor)

    assert context.data_lookback_days == 30
