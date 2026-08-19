"""Point-in-time wide panels for Alpha Zoo strategies on StockPred data."""

from __future__ import annotations

import pandas as pd

from src.stockpred.contracts import StockPredDataError
from src.stockpred.graph.adjustment import apply_qfq
from src.stockpred.graph.universe import build_pit_universe
from src.stockpred.strategies.contracts import StrategyDescriptor


def build_panel_from_inputs(
    gateway: object,
    *,
    eval_date: str,
    max_lookback: int,
    trade_dates: list[str],
    stock_dimension: pd.DataFrame,
    name_history: pd.DataFrame,
    industry_history: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """Build a point-in-time panel from already-loaded batch inputs."""
    if eval_date not in trade_dates:
        raise StockPredDataError("STOCKPRED_EVAL_DATE_CLOSED", f"evaluation date is not open: {eval_date}")
    universe, _ = build_pit_universe(
        stock_dimension,
        eval_date=eval_date,
        trade_dates=trade_dates,
        min_listed_trade_days=0,
        name_history=name_history,
        industry_history=industry_history,
    )
    codes = sorted(universe["ts_code"].astype(str))
    if not codes:
        return _empty_panel()
    dates = [date for date in trade_dates if date <= eval_date][-max_lookback:]
    start = dates[0]
    raw = gateway.prices(start, eval_date, codes)
    raw = raw[(raw["trade_date"].astype(str) >= start) & (raw["trade_date"].astype(str) <= eval_date)].copy()
    adjusted = apply_qfq(raw, gateway.adjustment_factors(start, eval_date, codes))
    if adjusted.empty:
        return _empty_panel()
    multiplier = adjusted["adj_close"].div(adjusted["close"].where(adjusted["close"] != 0))
    adjusted["adj_high"] = adjusted["high"] * multiplier
    adjusted["adj_low"] = adjusted["low"] * multiplier
    adjusted["volume"] = adjusted["vol"]
    adjusted["vwap"] = adjusted["amount"] * 1000.0 / (adjusted["vol"] * 100.0 + 1.0)
    return {
        "open": _pivot(adjusted, "adj_open"),
        "high": _pivot(adjusted, "adj_high"),
        "low": _pivot(adjusted, "adj_low"),
        "close": _pivot(adjusted, "adj_close"),
        "volume": _pivot(adjusted, "volume"),
        "amount": _pivot(adjusted, "amount"),
        "vwap": _pivot(adjusted, "vwap"),
    }


def _pivot(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    result = frame.pivot(index="trade_date", columns="ts_code", values=column)
    result = result.sort_index().sort_index(axis=1).rename_axis(index=None, columns=None)
    result.index = pd.to_datetime(result.index)
    return result


def _empty_panel() -> dict[str, pd.DataFrame]:
    return {name: pd.DataFrame() for name in ("open", "high", "low", "close", "volume", "amount", "vwap")}


class StockPredPanelBuilder:
    """Build only history available at one evaluation date."""

    def __init__(self, gateway: object, *, data_lookback_days: int = 180, context: object | None = None) -> None:
        self.gateway = gateway
        self.data_lookback_days = data_lookback_days
        self.context = context

    def build(self, eval_date: str, descriptor: StrategyDescriptor) -> dict[str, pd.DataFrame]:
        if self.context is not None:
            return self.context.panel_for_strategy(  # type: ignore[union-attr]
                eval_date,
                descriptor,
                data_lookback_days=self.data_lookback_days,
            )
        return self._build_uncached(eval_date, descriptor)

    def _build_uncached(self, eval_date: str, descriptor: StrategyDescriptor) -> dict[str, pd.DataFrame]:
        trade_dates = self.gateway.trade_dates("19900101", eval_date)
        return build_panel_from_inputs(
            self.gateway,
            eval_date=eval_date,
            max_lookback=max(self.data_lookback_days, descriptor.min_warmup_bars + 1),
            trade_dates=trade_dates,
            stock_dimension=self.gateway.stock_dimension(),
            name_history=self.gateway.name_history(),
            industry_history=self.gateway.industry_history(),
        )

    @staticmethod
    def _pivot(frame: pd.DataFrame, column: str) -> pd.DataFrame:
        return _pivot(frame, column)

    @staticmethod
    def _empty_panel() -> dict[str, pd.DataFrame]:
        return _empty_panel()
