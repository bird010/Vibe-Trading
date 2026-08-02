"""Daily ideal target-weight account with next-trading-open execution semantics."""

from __future__ import annotations

from typing import Sequence

import pandas as pd

from backtest.fund_rotation.evaluation import TargetSnapshot, schedule_targets


def run_daily_ideal_account(
    weekly_targets: dict[str, dict[str, float]],
    fund_daily: pd.DataFrame,
    fund_adj: pd.DataFrame,
    evaluation_dates: Sequence[str] | None = None,
) -> pd.Series:
    """Execute signals at the next valid open without fees, lots, or capacity limits.

    ``evaluation_dates`` (optional) is the formal evaluation trading calendar;
    when given, a pre-evaluation signal activates at the first evaluation day
    (design §24). When omitted, all market dates are used.
    """
    if not weekly_targets or fund_daily.empty or fund_adj.empty:
        return pd.Series(dtype=float, name="theoretical_strategy")

    daily = fund_daily[["trade_date", "ts_code", "open", "close"]].copy()
    adj = fund_adj[["trade_date", "ts_code", "adj_factor"]].copy()
    daily[["trade_date", "ts_code"]] = daily[["trade_date", "ts_code"]].astype(str)
    adj[["trade_date", "ts_code"]] = adj[["trade_date", "ts_code"]].astype(str)
    market = daily.merge(adj, on=["trade_date", "ts_code"], how="inner")
    for column in ("open", "close", "adj_factor"):
        market[column] = pd.to_numeric(market[column], errors="coerce")
    market = market[market["adj_factor"].gt(0)].copy()
    if market.empty:
        return pd.Series(dtype=float, name="theoretical_strategy")
    market["adj_open"] = market["open"] * market["adj_factor"]
    market["adj_close"] = market["close"] * market["adj_factor"]

    dates = sorted(market["trade_date"].unique())
    open_by_date = {
        date: group.loc[group["adj_open"].gt(0)].set_index("ts_code")["adj_open"].to_dict()
        for date, group in market.groupby("trade_date", sort=False)
    }
    close_by_date = {
        date: group.loc[group["adj_close"].gt(0)].set_index("ts_code")["adj_close"].to_dict()
        for date, group in market.groupby("trade_date", sort=False)
    }

    activations: dict[str, dict[str, float]] = {}
    # §24: unify execution scheduling — each signal activates at the first
    # trading day strictly after the signal date (a pre-evaluation signal
    # activates at the first available trading day).
    snapshots = [
        TargetSnapshot(
            pd.Timestamp(signal_date),
            {str(code): float(weight) for code, weight in raw_targets.items() if float(weight) > 0},
        )
        for signal_date, raw_targets in weekly_targets.items()
    ]
    eval_dates = [pd.Timestamp(d) for d in (evaluation_dates if evaluation_dates is not None else dates)]
    market_dates = set(dates)
    for exec_date, snap in schedule_targets(snapshots, eval_dates).items():
        # A later signal mapped to the same market day supersedes the earlier
        # one before any order is attempted (schedule_targets already resolves
        # this). Align the activation to the first actual market day >= exec_date
        # so it never lands on a day absent from the merged market frame (e.g. a
        # day with no positive adj_factor); it rolls forward to the next valid
        # market day, matching the legacy next-valid-open behaviour.
        exec_key = exec_date.strftime("%Y%m%d")
        if exec_key not in market_dates:
            exec_key = next((d for d in dates if d >= exec_key), None)
            if exec_key is None:
                continue
        activations[exec_key] = dict(snap.weights)

    cash = 1.0
    quantities: dict[str, float] = {}
    pending_notional: dict[str, float] = {}
    last_close: dict[str, float] = {}
    last_close_date: dict[str, str] = {}
    last_close_source: dict[str, str] = {}
    stale_valuations: list[dict] = []
    date_ordinal = {date: index for index, date in enumerate(dates)}
    equity: dict[str, float] = {}
    for trade_date in dates:
        opens = open_by_date[trade_date]
        closes = close_by_date[trade_date]
        open_nav = cash + sum(
            quantity * opens.get(code, last_close.get(code, 0.0))
            for code, quantity in quantities.items()
        )

        targets = activations.get(trade_date)
        if targets is not None:
            # Replacing this mapping cancels every residual from an older
            # signal.  Store desired capital at activation; successful legs
            # are not continuously rebalanced on later holding days.
            pending_notional = {
                code: open_nav * targets.get(code, 0.0)
                for code in sorted(set(quantities) | set(targets))
                if quantities.get(code, 0.0) > 0 or targets.get(code, 0.0) > 0
            }

        # Valid-open reductions fund valid-open additions.  A missing-open
        # leg remains pending and its existing holding (or cash) is retained.
        for code in sorted(list(pending_notional)):
            price = opens.get(code)
            if price is None:
                continue
            current = quantities.get(code, 0.0) * price
            desired = pending_notional[code]
            if desired < current - 1e-12:
                cash += current - desired
                quantities[code] = desired / price
                pending_notional.pop(code)
            elif abs(desired - current) <= 1e-12:
                pending_notional.pop(code)

        buy_needs = {
            code: desired - quantities.get(code, 0.0) * opens[code]
            for code, desired in pending_notional.items()
            if code in opens and desired > quantities.get(code, 0.0) * opens[code] + 1e-12
        }
        total_buy = sum(buy_needs.values())
        buy_scale = min(cash / total_buy, 1.0) if total_buy > 0 else 0.0
        for code in sorted(buy_needs):
            spend = buy_needs[code] * buy_scale
            was_empty = quantities.get(code, 0.0) <= 0
            quantities[code] = quantities.get(code, 0.0) + spend / opens[code]
            cash -= spend
            if spend > 0 and was_empty:
                # A real fill is the only confirmed valuation anchor available
                # when the opening day has no close.
                last_close[code] = opens[code]
                last_close_date[code] = trade_date
                last_close_source[code] = "execution_open"
            if buy_scale >= 1.0 - 1e-12:
                pending_notional.pop(code)

        close_nav = cash + sum(
            quantity * closes.get(code, last_close.get(code, 0.0))
            for code, quantity in quantities.items()
        )
        equity[trade_date] = close_nav
        for code, quantity in quantities.items():
            if quantity <= 0 or code in closes:
                continue
            anchor_date = last_close_date.get(code, trade_date)
            stale_valuations.append({
                "trade_date": trade_date,
                "ts_code": code,
                "mark_price": last_close.get(code, 0.0),
                "last_valid_close_date": anchor_date,
                "stale_days": date_ordinal[trade_date] - date_ordinal.get(anchor_date, date_ordinal[trade_date]),
                "anchor_source": (
                    "execution_open"
                    if last_close_source.get(code) == "execution_open"
                    else "last_valid_close"
                ),
            })
        for code, price in closes.items():
            last_close[code] = price
            last_close_date[code] = trade_date
            last_close_source[code] = "close"

    result = pd.Series(equity, name="theoretical_strategy", dtype=float)
    result.attrs["stale_valuations"] = stale_valuations
    return result
