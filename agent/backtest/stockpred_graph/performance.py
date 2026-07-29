from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

import numpy as np
import pandas as pd

TRADING_DAYS = 252
_VALID_STATUSES = {"FILLED", "PARTIAL"}


def calculate_performance_metrics(
    equity: pd.DataFrame, trades: pd.DataFrame
) -> dict[str, float]:
    nav = pd.to_numeric(equity.get("nav"), errors="coerce").dropna().reset_index(drop=True)
    metrics: dict[str, float] = {}
    if len(nav) >= 1 and nav.iloc[0] > 0:
        metrics["total_return"] = float(nav.iloc[-1] / nav.iloc[0] - 1.0)
    returns = nav.pct_change().dropna()
    if len(nav) >= 2 and nav.iloc[0] > 0:
        if nav.iloc[-1] > 0:
            metrics["annual_return"] = float(
                (nav.iloc[-1] / nav.iloc[0]) ** (TRADING_DAYS / (len(nav) - 1)) - 1.0
            )
        metrics["max_drawdown"] = float(nav.div(nav.cummax()).sub(1.0).min())
        if metrics.get("annual_return") is not None and metrics["max_drawdown"] < 0:
            metrics["calmar"] = metrics["annual_return"] / abs(metrics["max_drawdown"])
    if len(returns) >= 2:
        std = returns.std(ddof=1)
        metrics["annual_volatility"] = float(std * np.sqrt(TRADING_DAYS))
        if std > 0:
            metrics["sharpe"] = float(returns.mean() / std * np.sqrt(TRADING_DAYS))
        downside = returns[returns < 0]
        if len(downside) >= 2 and downside.std(ddof=1) > 0:
            metrics["sortino"] = float(
                returns.mean() / downside.std(ddof=1) * np.sqrt(TRADING_DAYS)
            )
    metrics.update(_completed_trade_metrics(trades))
    return {key: value for key, value in metrics.items() if np.isfinite(value)}


def build_symbol_metrics(
    trades: pd.DataFrame, ohlcv_by_symbol: dict[str, pd.DataFrame]
) -> list[dict[str, float | str]]:
    rows: list[dict[str, float | str]] = []
    symbols = set(ohlcv_by_symbol)
    if "code" in trades.columns:
        symbols.update(trades["code"].dropna().astype(str))
    for symbol in sorted(symbols):
        prices = ohlcv_by_symbol.get(symbol, pd.DataFrame())
        symbol_trades = _symbol_trades(trades, symbol)
        equity = _build_symbol_equity(symbol_trades, prices)
        metrics = calculate_performance_metrics(equity, symbol_trades)
        if metrics.get("trade_count", 0.0) > 0:
            rows.append({"symbol": symbol, **metrics})
    return rows


def build_symbol_metrics_from_market(
    trades: pd.DataFrame, market: pd.DataFrame
) -> list[dict[str, float | str]]:
    """Build per-symbol metrics from one normalized market frame."""
    symbols: set[str] = set()
    if "code" in trades.columns:
        symbols.update(trades["code"].dropna().astype(str))
    if "ts_code" not in market.columns:
        return build_symbol_metrics(trades, {})

    normalized_market = market.copy()
    normalized_market["ts_code"] = normalized_market["ts_code"].astype(str)
    symbols.update(normalized_market["ts_code"])
    grouped = normalized_market.groupby("ts_code", sort=False)
    rows: list[dict[str, float | str]] = []
    for symbol in sorted(symbols):
        prices = grouped.get_group(symbol) if symbol in grouped.groups else pd.DataFrame()
        symbol_trades = _symbol_trades(trades, symbol)
        equity = _build_symbol_equity(symbol_trades, prices)
        metrics = calculate_performance_metrics(equity, symbol_trades)
        if metrics.get("trade_count", 0.0) > 0:
            rows.append({"symbol": symbol, **metrics})
    return rows


def _symbol_trades(trades: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if "code" not in trades.columns:
        return pd.DataFrame()
    return trades.loc[trades["code"].astype(str) == symbol].copy()


def _completed_trade_metrics(trades: pd.DataFrame) -> dict[str, float]:
    if trades.empty:
        return {}
    required = {"timestamp", "code", "side", "executed_value", "qty", "cost_bps", "status"}
    if not required.issubset(trades.columns):
        return {}

    events = trades.loc[
        trades["status"].astype(str).str.upper().isin(_VALID_STATUSES)
    ].copy()
    if events.empty:
        return {}
    events["timestamp"] = pd.to_datetime(events["timestamp"], errors="coerce")
    for column in ("executed_value", "qty", "cost_bps"):
        events[column] = pd.to_numeric(events[column], errors="coerce")
    events = events.dropna(subset=["timestamp", "code", "side", "executed_value", "qty", "cost_bps"])
    events = events.loc[(events["qty"] > 0) & (events["executed_value"] > 0)]
    events = events.sort_values("timestamp", kind="stable")

    open_buys: dict[str, deque[tuple[float, float, pd.Timestamp]]] = defaultdict(deque)
    completed: list[tuple[float, float]] = []
    for event in events.itertuples(index=False):
        code = str(event.code)
        side = str(event.side).upper()
        quantity = float(event.qty)
        value = float(event.executed_value)
        cost_bps = float(event.cost_bps)
        if side == "BUY":
            open_buys[code].append(
                (quantity, value * (1.0 + cost_bps / 10_000.0) / quantity, event.timestamp)
            )
        elif side == "SELL":
            sell_unit_value = value * (1.0 - cost_bps / 10_000.0) / quantity
            remaining = quantity
            while remaining > 0 and open_buys[code]:
                buy_qty, buy_unit_cost, buy_timestamp = open_buys[code][0]
                matched_qty = min(remaining, buy_qty)
                completed.append(
                    (
                        matched_qty * (sell_unit_value - buy_unit_cost),
                        float((event.timestamp.normalize() - buy_timestamp.normalize()).days),
                    )
                )
                remaining -= matched_qty
                if matched_qty == buy_qty:
                    open_buys[code].popleft()
                else:
                    open_buys[code][0] = (buy_qty - matched_qty, buy_unit_cost, buy_timestamp)

    if not completed:
        return {}
    profits = [pnl for pnl, _ in completed if pnl > 0]
    losses = [pnl for pnl, _ in completed if pnl < 0]
    metrics = {
        "trade_count": float(len(completed)),
        "win_rate": float(len(profits) / len(completed)),
        "avg_holding_days": float(np.mean([days for _, days in completed])),
    }
    if profits and losses:
        metrics["profit_loss_ratio"] = float(np.mean(profits) / abs(np.mean(losses)))
    return metrics


def _build_symbol_equity(trades: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    if prices.empty:
        return pd.DataFrame(columns=["time", "nav"])
    price_column = "adj_close" if "adj_close" in prices.columns else "close"
    date_column = "trade_date" if "trade_date" in prices.columns else "timestamp"
    if price_column not in prices.columns or date_column not in prices.columns:
        return pd.DataFrame(columns=["time", "nav"])

    price_rows = pd.DataFrame(
        {
            "time": pd.to_datetime(prices[date_column].astype(str), errors="coerce"),
            "price": pd.to_numeric(prices[price_column], errors="coerce"),
        }
    ).dropna()
    price_rows = price_rows.sort_values("time", kind="stable")
    if price_rows.empty:
        return pd.DataFrame(columns=["time", "nav"])

    events = _valid_events(trades)
    matched_events: list[pd.Series] = []
    available_quantity = 0.0
    for _, event in events.iterrows():
        if event["side"] == "BUY":
            available_quantity += float(event["qty"])
            matched_events.append(event)
            continue
        matched_quantity = min(float(event["qty"]), available_quantity)
        if matched_quantity <= 0:
            continue
        matched_event = event.copy()
        matched_event["executed_value"] *= matched_quantity / float(event["qty"])
        matched_event["qty"] = matched_quantity
        matched_events.append(matched_event)
        available_quantity -= matched_quantity
    events = pd.DataFrame(matched_events, columns=events.columns)
    event_cash_flows = [
        -row.executed_value * (1.0 + row.cost_bps / 10_000.0)
        if row.side == "BUY"
        else row.executed_value * (1.0 - row.cost_bps / 10_000.0)
        for row in events.itertuples(index=False)
    ]
    opening_cash = max(0.0, -min(0.0, *np.cumsum(event_cash_flows))) if event_cash_flows else 0.0
    cash = opening_cash
    quantity = 0.0
    event_index = 0
    rows: list[dict[str, Any]] = []
    event_rows = list(events.itertuples(index=False))
    if event_rows and opening_cash > 0:
        rows.append(
            {
                "time": price_rows.iloc[0]["time"] - pd.Timedelta(days=1),
                "nav": opening_cash,
            }
        )
    for price_row in price_rows.itertuples(index=False):
        while event_index < len(event_rows) and event_rows[event_index].timestamp <= price_row.time:
            event = event_rows[event_index]
            if event.side == "BUY":
                cash -= event.executed_value * (1.0 + event.cost_bps / 10_000.0)
                quantity += event.qty
            else:
                cash += event.executed_value * (1.0 - event.cost_bps / 10_000.0)
                quantity -= event.qty
            event_index += 1
        rows.append({"time": price_row.time, "nav": cash + quantity * price_row.price})
    return pd.DataFrame(rows)


def _valid_events(trades: pd.DataFrame) -> pd.DataFrame:
    required = {"timestamp", "side", "executed_value", "qty", "cost_bps", "status"}
    if trades.empty or not required.issubset(trades.columns):
        return pd.DataFrame(columns=["timestamp", "side", "executed_value", "qty", "cost_bps"])
    events = trades.loc[
        trades["status"].astype(str).str.upper().isin(_VALID_STATUSES)
    ].copy()
    events["timestamp"] = pd.to_datetime(events["timestamp"], errors="coerce")
    for column in ("executed_value", "qty", "cost_bps"):
        events[column] = pd.to_numeric(events[column], errors="coerce")
    events["side"] = events["side"].astype(str).str.upper()
    events = events.dropna(subset=["timestamp", "executed_value", "qty", "cost_bps"])
    events = events.loc[
        events["side"].isin({"BUY", "SELL"})
        & (events["qty"] > 0)
        & (events["executed_value"] > 0)
    ]
    return events.sort_values(["timestamp", "side"], key=lambda values: values.map({"SELL": 0, "BUY": 1}).fillna(values), kind="stable")
