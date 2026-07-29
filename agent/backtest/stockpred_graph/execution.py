"""图谱回测的可交易执行规则。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


_LIMIT_EPSILON = 1e-6

TRADE_COLUMNS = [
    "timestamp",
    "code",
    "side",
    "requested_value",
    "executed_value",
    "qty",
    "price",
    "cost_bps",
    "status",
    "reason",
    "signal_date",
    "exit_delay_days",
]


@dataclass(frozen=True)
class ExecutedTrade:
    ts_code: str
    signal_date: str
    entry_date: str | None
    exit_date: str | None
    entry_price: float | None
    exit_price: float | None
    entered: bool
    entry_block_reason: str | None
    exit_delay_days: int
    gross_return: float | None


@dataclass(frozen=True)
class CapacityResult:
    requested_value: float
    executed_value: float
    unfilled_value: float
    participation_rate: float


def estimate_one_way_cost_bps(
    *,
    trade_value: float,
    daily_amount_cny: float,
    side: str,
) -> float:
    """按成交参与率估算佣金、税费和滑点的单边成本。"""
    if side not in {"buy", "sell"}:
        raise ValueError(f"unsupported side: {side}")
    participation = (
        abs(float(trade_value)) / float(daily_amount_cny)
        if daily_amount_cny > 0
        else 1.0
    )
    slippage_bps = float(
        np.clip(5.0 + 200.0 * participation, 5.0, 30.0)
    )
    commission_bps = 15.0
    stamp_bps = 10.0 if side == "sell" else 0.0
    return commission_bps + stamp_bps + slippage_bps


def apply_capacity_limit(
    *,
    requested_value: float,
    daily_amount_cny: float,
    max_participation: float,
) -> CapacityResult:
    """将单笔成交限制在当日成交额的指定比例内。"""
    requested = max(float(requested_value), 0.0)
    capacity = max(float(daily_amount_cny), 0.0) * max(
        float(max_participation),
        0.0,
    )
    executed = min(requested, capacity)
    return CapacityResult(
        requested_value=requested,
        executed_value=executed,
        unfilled_value=requested - executed,
        participation_rate=(
            executed / float(daily_amount_cny)
            if daily_amount_cny > 0
            else 0.0
        ),
    )


def simulate_trades(
    market: pd.DataFrame,
    *,
    signals: pd.DataFrame,
    holding_days: int,
) -> pd.DataFrame:
    """批量模拟 T+1 开盘买入和目标持有期后的可交易退出。"""
    return_column = f"fwd_ret_{holding_days}d"
    output_columns = [
        "ts_code",
        "eval_date",
        "entry_date",
        "exit_date",
        "entry_price",
        "exit_price",
        "entry_daily_amount_cny",
        "exit_daily_amount_cny",
        "entered",
        "entry_block_reason",
        "exit_delay_days",
        return_column,
    ]
    if signals.empty:
        return pd.DataFrame(columns=output_columns)

    market_rows = market.copy()
    market_rows["ts_code"] = market_rows["ts_code"].astype(str)
    market_rows["trade_date"] = market_rows["trade_date"].astype(str)
    market_rows = market_rows.sort_values(
        ["ts_code", "trade_date"]
    ).drop_duplicates(["ts_code", "trade_date"], keep="last")
    trade_dates = np.array(
        sorted(market_rows["trade_date"].unique().tolist()),
        dtype=object,
    )
    if trade_dates.size == 0:
        result = signals[["ts_code", "eval_date"]].copy()
        result["entry_date"] = None
        result["exit_date"] = None
        result["entry_price"] = np.nan
        result["exit_price"] = np.nan
        result["entry_daily_amount_cny"] = np.nan
        result["exit_daily_amount_cny"] = np.nan
        result["entered"] = False
        result["entry_block_reason"] = "no_market_data"
        result["exit_delay_days"] = 0
        result[return_column] = np.nan
        return result[output_columns]

    date_positions = {
        str(trade_date): position
        for position, trade_date in enumerate(trade_dates)
    }
    market_rows["_trade_position"] = (
        market_rows["trade_date"].map(date_positions).astype(int)
    )
    execution_open = (
        market_rows["adj_open"]
        if "adj_open" in market_rows.columns
        else market_rows["open"]
    )
    market_rows["_execution_open"] = execution_open
    market_rows["_daily_amount_cny"] = (
        pd.to_numeric(
            market_rows["amount"],
            errors="coerce",
        ) * 1000.0
        if "amount" in market_rows.columns
        else np.nan
    )

    result = signals[["ts_code", "eval_date"]].copy()
    result["ts_code"] = result["ts_code"].astype(str)
    result["eval_date"] = result["eval_date"].astype(str)
    entry_positions = np.searchsorted(
        trade_dates,
        result["eval_date"].to_numpy(dtype=object),
        side="right",
    )
    result["_entry_position"] = entry_positions
    result["entry_date"] = [
        str(trade_dates[position])
        if position < len(trade_dates)
        else None
        for position in entry_positions
    ]

    entry_rows = market_rows[
        [
            "ts_code",
            "trade_date",
            "open",
            "vol",
            "up_limit",
            "_execution_open",
            "_daily_amount_cny",
        ]
    ].rename(
        columns={
            "trade_date": "entry_date",
            "open": "_entry_open",
            "vol": "_entry_vol",
            "up_limit": "_entry_up_limit",
            "_daily_amount_cny": "entry_daily_amount_cny",
        }
    )
    entry_rows["_entry_row_present"] = True
    result = result.merge(
        entry_rows,
        on=["ts_code", "entry_date"],
        how="left",
    )
    result["entry_block_reason"] = None
    missing_row = result["_entry_row_present"].isna()
    missing_open = (
        ~missing_row
        & (
            result["_entry_open"].isna()
            | result["_execution_open"].isna()
        )
    )
    suspended = (
        ~missing_row
        & ~missing_open
        & (
            result["_entry_vol"].isna()
            | (result["_entry_vol"] <= 0)
        )
    )
    limit_up = (
        ~missing_row
        & ~missing_open
        & ~suspended
        & result["_entry_up_limit"].notna()
        & (
            result["_entry_open"]
            >= result["_entry_up_limit"] - _LIMIT_EPSILON
        )
    )
    result.loc[missing_row, "entry_block_reason"] = "no_market_data"
    result.loc[missing_open, "entry_block_reason"] = "missing_open"
    result.loc[suspended, "entry_block_reason"] = "suspended"
    result.loc[limit_up, "entry_block_reason"] = "limit_up"
    result["entered"] = result["entry_block_reason"].isna()
    result["entry_price"] = result["_execution_open"].where(
        result["entered"]
    )

    result["exit_date"] = None
    result["exit_price"] = np.nan
    result["exit_daily_amount_cny"] = np.nan
    result["exit_delay_days"] = 0
    result[return_column] = np.nan
    result["_target_exit_position"] = (
        result["_entry_position"] + max(int(holding_days), 1)
    )

    entered = result[result["entered"]]
    market_groups = market_rows.groupby(
        "ts_code",
        sort=False,
    ).groups
    for ts_code, row_indices in entered.groupby("ts_code").groups.items():
        market_indices = market_groups.get(ts_code)
        if market_indices is None:
            continue
        stock_rows = market_rows.loc[market_indices]
        sellable = stock_rows[
            stock_rows["open"].notna()
            & stock_rows["_execution_open"].notna()
            & stock_rows["vol"].notna()
            & (stock_rows["vol"] > 0)
            & (
                stock_rows["down_limit"].isna()
                | (
                    stock_rows["open"]
                    > stock_rows["down_limit"] + _LIMIT_EPSILON
                )
            )
        ].sort_values("_trade_position")
        if sellable.empty:
            continue

        target_positions = result.loc[
            row_indices,
            "_target_exit_position",
        ].to_numpy(dtype=int)
        sellable_positions = sellable["_trade_position"].to_numpy(dtype=int)
        found_positions = np.searchsorted(
            sellable_positions,
            target_positions,
            side="left",
        )
        found_mask = found_positions < len(sellable)
        if not found_mask.any():
            continue

        found_indices = np.asarray(list(row_indices))[found_mask]
        chosen = sellable.iloc[found_positions[found_mask]]
        result.loc[found_indices, "exit_date"] = (
            chosen["trade_date"].to_numpy()
        )
        result.loc[found_indices, "exit_price"] = (
            chosen["_execution_open"].to_numpy(dtype=float)
        )
        result.loc[found_indices, "exit_daily_amount_cny"] = (
            chosen["_daily_amount_cny"].to_numpy(dtype=float)
        )
        result.loc[found_indices, "exit_delay_days"] = (
            chosen["_trade_position"].to_numpy(dtype=int)
            - target_positions[found_mask]
        )

    completed = result["entered"] & result["exit_price"].notna()
    result.loc[completed, return_column] = (
        result.loc[completed, "exit_price"]
        / result.loc[completed, "entry_price"]
        - 1.0
    )
    return result[output_columns]


def simulate_trade(
    market: pd.DataFrame,
    *,
    ts_code: str,
    signal_date: str,
    holding_days: int,
) -> ExecutedTrade:
    """模拟单只股票交易，主要用于规则验证和诊断。"""
    result = simulate_trades(
        market,
        signals=pd.DataFrame({
            "ts_code": [ts_code],
            "eval_date": [signal_date],
        }),
        holding_days=holding_days,
    ).iloc[0]
    gross_return = result[f"fwd_ret_{holding_days}d"]
    return ExecutedTrade(
        ts_code=ts_code,
        signal_date=signal_date,
        entry_date=result["entry_date"],
        exit_date=result["exit_date"],
        entry_price=(
            None if pd.isna(result["entry_price"])
            else float(result["entry_price"])
        ),
        exit_price=(
            None if pd.isna(result["exit_price"])
            else float(result["exit_price"])
        ),
        entered=bool(result["entered"]),
        entry_block_reason=result["entry_block_reason"],
        exit_delay_days=int(result["exit_delay_days"]),
        gross_return=(
            None if pd.isna(gross_return)
            else float(gross_return)
        ),
    )


def _iso_date(value: object) -> str:
    text = str(value or "")
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text[:10]


def _finite_amount(value: object) -> float:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return float(number) if pd.notna(number) and float(number) > 0 else 0.0


def _prepare_sellable_days(market: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Build per-stock sorted sellable-day tables for multi-day sell continuation."""
    mkt = market.copy()
    mkt["ts_code"] = mkt["ts_code"].astype(str)
    mkt["trade_date"] = mkt["trade_date"].astype(str)
    mkt = mkt.sort_values(["ts_code", "trade_date"]).drop_duplicates(
        ["ts_code", "trade_date"], keep="last"
    )
    exec_open = mkt["adj_open"] if "adj_open" in mkt.columns else mkt["open"]
    mkt["_execution_open"] = exec_open
    mkt["_daily_amount_cny"] = (
        pd.to_numeric(mkt["amount"], errors="coerce") * 1000.0
        if "amount" in mkt.columns
        else np.nan
    )
    sellable = mkt[
        mkt["open"].notna()
        & mkt["_execution_open"].notna()
        & mkt["vol"].notna()
        & (mkt["vol"] > 0)
        & (
            mkt["down_limit"].isna()
            | (mkt["open"] > mkt["down_limit"] + _LIMIT_EPSILON)
        )
    ]
    result: dict[str, pd.DataFrame] = {}
    for code, group in sellable.groupby("ts_code", sort=False):
        result[str(code)] = group.sort_values("trade_date").reset_index(drop=True)
    return result


def execute_target_portfolio(
    market: pd.DataFrame,
    targets: pd.DataFrame,
    *,
    signal_date: str,
    holding_days: int,
    capital: float,
    max_participation: float,
) -> pd.DataFrame:
    """Execute one equal-weight target batch without redistributing unfilled cash."""
    if targets.empty:
        return pd.DataFrame(columns=TRADE_COLUMNS)
    required = {"ts_code", "target_weight"}
    missing = required - set(targets.columns)
    if missing:
        raise KeyError(f"targets missing columns: {sorted(missing)}")
    simulated = simulate_trades(
        market,
        signals=targets[["ts_code"]].assign(eval_date=str(signal_date)),
        holding_days=holding_days,
    ).set_index("ts_code")
    sellable_days = _prepare_sellable_days(market)
    # Compute target exit trade-date position for exit_delay_days calculation
    trade_dates_sorted = sorted(
        market.assign(trade_date=market["trade_date"].astype(str))["trade_date"].unique()
    )
    date_position = {d: i for i, d in enumerate(trade_dates_sorted)}
    # Entry position: first trade date strictly AFTER signal_date (works for non-trading-day signals)
    import bisect
    entry_position = bisect.bisect_right(trade_dates_sorted, str(signal_date))
    events: list[dict[str, object]] = []
    for target in targets.itertuples(index=False):
        code = str(target.ts_code)
        requested = max(float(capital) * float(target.target_weight), 0.0)
        trade = simulated.loc[code]
        if not bool(trade["entered"]):
            events.append(
                {
                    "timestamp": _iso_date(trade["entry_date"] or signal_date),
                    "code": code,
                    "side": "BUY",
                    "requested_value": requested,
                    "executed_value": 0.0,
                    "qty": 0.0,
                    "price": None,
                    "cost_bps": 0.0,
                    "status": "REJECTED",
                    "reason": trade["entry_block_reason"],
                    "signal_date": _iso_date(signal_date),
                    "exit_delay_days": 0,
                }
            )
            continue
        entry_amount = _finite_amount(trade["entry_daily_amount_cny"])
        entry_capacity = apply_capacity_limit(
            requested_value=requested,
            daily_amount_cny=entry_amount,
            max_participation=max_participation,
        )
        entry_price = float(trade["entry_price"])
        quantity = (
            entry_capacity.executed_value / entry_price if entry_price > 0 else 0.0
        )
        if entry_capacity.executed_value <= 0:
            buy_status = "REJECTED"
        elif entry_capacity.unfilled_value > 1e-9:
            buy_status = "PARTIAL"
        else:
            buy_status = "FILLED"
        buy_reason = "capacity" if buy_status != "FILLED" else None
        buy_cost = (
            estimate_one_way_cost_bps(
                trade_value=entry_capacity.executed_value,
                daily_amount_cny=entry_amount,
                side="buy",
            )
            if entry_capacity.executed_value > 0
            else 0.0
        )
        events.append(
            {
                "timestamp": _iso_date(trade["entry_date"]),
                "code": code,
                "side": "BUY",
                "requested_value": requested,
                "executed_value": entry_capacity.executed_value,
                "qty": quantity,
                "price": entry_price,
                "cost_bps": buy_cost,
                "status": buy_status,
                "reason": buy_reason,
                "signal_date": _iso_date(signal_date),
                "exit_delay_days": 0,
            }
        )
        if quantity <= 0 or pd.isna(trade["exit_price"]):
            continue
        # --- Multi-day sell continuation ---
        remaining_qty = quantity
        exit_date_str = str(trade["exit_date"])
        target_exit_position = entry_position + max(int(holding_days), 1)
        stock_sellable = sellable_days.get(code)
        if stock_sellable is None or stock_sellable.empty:
            continue
        # Find sellable days starting from exit_date
        day_mask = stock_sellable["trade_date"] >= exit_date_str
        candidate_days = stock_sellable[day_mask]
        for _, day_row in candidate_days.iterrows():
            if remaining_qty <= 1e-12:
                break
            day_price = float(day_row["_execution_open"])
            day_amount = _finite_amount(day_row["_daily_amount_cny"])
            requested_exit = remaining_qty * day_price
            exit_capacity = apply_capacity_limit(
                requested_value=requested_exit,
                daily_amount_cny=day_amount,
                max_participation=max_participation,
            )
            exit_quantity = (
                exit_capacity.executed_value / day_price if day_price > 0 else 0.0
            )
            if exit_capacity.executed_value <= 0:
                continue
            remaining_qty -= exit_quantity
            if remaining_qty <= 1e-12:
                remaining_qty = 0.0
                sell_status = "FILLED"
            else:
                sell_status = "PARTIAL"
            sell_cost = estimate_one_way_cost_bps(
                trade_value=exit_capacity.executed_value,
                daily_amount_cny=day_amount,
                side="sell",
            )
            day_position = date_position.get(str(day_row["trade_date"]), 0)
            events.append(
                {
                    "timestamp": _iso_date(day_row["trade_date"]),
                    "code": code,
                    "side": "SELL",
                    "requested_value": requested_exit,
                    "executed_value": exit_capacity.executed_value,
                    "qty": exit_quantity,
                    "price": day_price,
                    "cost_bps": sell_cost,
                    "status": sell_status,
                    "reason": "capacity" if sell_status != "FILLED" else None,
                    "signal_date": _iso_date(signal_date),
                    "exit_delay_days": day_position - target_exit_position,
                }
            )
    return pd.DataFrame(events, columns=TRADE_COLUMNS)


def build_daily_ledger(
    trades: pd.DataFrame,
    market: pd.DataFrame,
    *,
    initial_capital: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Replay events into deterministic daily positions and mark-to-market equity."""
    if market.empty:
        return pd.DataFrame(), pd.DataFrame()
    prices = market.copy()
    prices["time"] = prices["trade_date"].map(_iso_date)
    mark_column = "adj_close" if "adj_close" in prices.columns else "close"
    marks = (
        prices.sort_values(["time", "ts_code"], kind="stable")
        .drop_duplicates(["time", "ts_code"], keep="last")
        .set_index(["time", "ts_code"])[mark_column]
    )
    events = trades.copy()
    if not events.empty:
        events["timestamp"] = events["timestamp"].map(_iso_date)
        events["_side_order"] = events["side"].map({"SELL": 0, "BUY": 1})
        events = events.sort_values(
            ["timestamp", "_side_order", "code"],
            kind="stable",
        )
    cash = float(initial_capital)
    holdings: dict[str, float] = {}
    positions: list[dict[str, object]] = []
    equity_rows: list[dict[str, float | str]] = []
    for time in sorted(prices["time"].unique()):
        if not events.empty:
            for event in events[events["timestamp"] == time].itertuples(index=False):
                executed = float(event.executed_value)
                quantity = float(event.qty)
                fee = executed * float(event.cost_bps) / 10_000.0
                if event.side == "SELL":
                    available = holdings.get(str(event.code), 0.0)
                    sold = min(quantity, available)
                    proceeds = sold * float(event.price)
                    cash += proceeds - proceeds * float(event.cost_bps) / 10_000.0
                    holdings[str(event.code)] = available - sold
                elif event.side == "BUY":
                    cash -= executed + fee
                    holdings[str(event.code)] = holdings.get(str(event.code), 0.0) + quantity
        market_value = 0.0
        for code, quantity in sorted(holdings.items()):
            if quantity <= 1e-12 or (time, code) not in marks.index:
                continue
            price = float(marks.loc[(time, code)])
            value = quantity * price
            market_value += value
            positions.append(
                {
                    "time": time,
                    "code": code,
                    "qty": quantity,
                    "price": price,
                    "market_value": value,
                }
            )
        equity = cash + market_value
        equity_rows.append(
            {
                "time": time,
                "cash": cash,
                "market_value": market_value,
                "equity": equity,
                "nav": equity / float(initial_capital),
            }
        )
    return pd.DataFrame(positions), pd.DataFrame(equity_rows)
