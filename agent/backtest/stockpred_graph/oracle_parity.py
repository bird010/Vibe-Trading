"""Oracle-compatible comparable layers for StockPred Graph parity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from backtest.stockpred_graph.execution import (
    apply_capacity_limit,
    estimate_one_way_cost_bps,
    simulate_trades,
)
from src.stockpred.graph.backtest_config import GraphBacktestConfig
from src.stockpred.graph.portfolio import select_buffered_portfolio


TRADE_COLUMNS = [
    "timestamp",
    "code",
    "side",
    "price",
    "status",
    "reason",
    "signal_date",
    "exit_delay_days",
]

_BULLISH_ACTIONS = frozenset({"买入", "增持"})
_BEARISH_ACTIONS = frozenset({"减持", "卖出"})
_BULLISH_DIRECTIONS = frozenset({"强", "偏强"})
_CROWDING_ALERT_THRESHOLD = 0.80
_TRANSACTION_COST_BPS = 15.0
_STAMP_DUTY_BPS = 10.0


@dataclass(frozen=True)
class OracleParityView:
    signals: pd.DataFrame
    selected: pd.DataFrame
    trades: pd.DataFrame
    equity: pd.DataFrame
    metrics: dict[str, float | int]


@dataclass(frozen=True)
class _ReturnMetrics:
    annualized_return: float
    sharpe_ratio: float
    max_drawdown: float
    cumulative_returns: list[float]


@dataclass(frozen=True)
class _ICStats:
    mean_ic: float
    periods: int


@dataclass(frozen=True)
class _CandidateMetrics:
    fixed_cost_net_return: float = 0.0
    liquidity_cost_net_return: float = 0.0
    capacity_fill_rate: float = 1.0
    avg_participation_rate: float = 0.0


def _iso_date(value: Any) -> str:
    text = str(value or "")
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text[:10]


def _optional_number(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _select_top(details: pd.DataFrame, top_n: int) -> pd.DataFrame:
    ordered = details.copy()
    if ordered.empty:
        return ordered
    required = {"trade_date", "score", "ts_code"}
    missing = required - set(ordered.columns)
    if missing:
        raise ValueError(f"details missing selection columns: {sorted(missing)}")
    eligible = ordered.groupby("trade_date")["ts_code"].transform("size") >= int(top_n)
    ordered = ordered.loc[eligible]
    ordered = ordered.sort_values(
        ["trade_date", "score", "ts_code"],
        ascending=[True, False, True],
        kind="stable",
    )
    return ordered.groupby("trade_date", sort=True).head(int(top_n)).reset_index(drop=True)


def _eligible_eval_dates(details: pd.DataFrame, *, top_n: int) -> list[str]:
    selected = _select_top(details, top_n)
    if selected.empty:
        return []
    return sorted(str(value) for value in selected["trade_date"].unique())


def _normalize_trades(details: pd.DataFrame, *, top_n: int) -> pd.DataFrame:
    if details.empty:
        return pd.DataFrame(columns=TRADE_COLUMNS)
    selected = _select_top(details, top_n)
    events: list[dict[str, Any]] = []
    for row in selected.to_dict(orient="records"):
        code = str(row["ts_code"])
        signal_date = _iso_date(row.get("trade_date"))
        entered = bool(row.get("entered", False))
        if not entered:
            events.append(
                {
                    "timestamp": _iso_date(row.get("entry_date") or row.get("trade_date")),
                    "code": code,
                    "side": "BUY",
                    "price": _optional_number(row.get("entry_price")),
                    "status": "REJECTED",
                    "reason": row.get("entry_block_reason"),
                    "signal_date": signal_date,
                    "exit_delay_days": 0,
                }
            )
            continue
        events.append(
            {
                "timestamp": _iso_date(row.get("entry_date")),
                "code": code,
                "side": "BUY",
                "price": _optional_number(row.get("entry_price")),
                "status": "FILLED",
                "reason": None,
                "signal_date": signal_date,
                "exit_delay_days": 0,
            }
        )
        if row.get("exit_date") is not None and not pd.isna(row.get("exit_date")):
            delay = int(row.get("exit_delay_days") or 0)
            events.append(
                {
                    "timestamp": _iso_date(row.get("exit_date")),
                    "code": code,
                    "side": "SELL",
                    "price": _optional_number(row.get("exit_price")),
                    "status": "FILLED",
                    "reason": "exit_delayed" if delay > 0 else None,
                    "signal_date": signal_date,
                    "exit_delay_days": delay,
                }
            )
    frame = pd.DataFrame(events, columns=TRADE_COLUMNS)
    frame["reason"] = pd.Series([event["reason"] for event in events], dtype=object)
    return frame


def _normalize_equity(
    eval_dates: list[str],
    cumulative_returns: list[float],
) -> pd.DataFrame:
    if len(eval_dates) != len(cumulative_returns):
        raise ValueError("eval_dates and cumulative_returns must have equal length")
    return pd.DataFrame(
        {
            "time": [_iso_date(value) for value in eval_dates],
            "equity": [1.0 + float(value) for value in cumulative_returns],
        }
    )


def _compute_return_metrics(net_returns: np.ndarray, eval_step: int) -> _ReturnMetrics:
    returns = np.asarray(net_returns, dtype=float)
    avg_net = float(np.mean(returns)) if len(returns) > 0 else 0.0
    periods_per_year = 244.0 / max(eval_step, 1)
    annualized = float((1.0 + avg_net) ** periods_per_year - 1.0) if avg_net > -1.0 else -1.0
    sharpe = (
        float((avg_net / np.std(returns)) * np.sqrt(periods_per_year))
        if len(returns) > 1 and np.std(returns) > 0
        else 0.0
    )
    equity = np.cumprod(1.0 + returns) if len(returns) > 0 else np.array([1.0])
    cumulative_returns = equity - 1.0
    peak = np.maximum.accumulate(equity)
    drawdown = equity / peak - 1.0
    max_drawdown = float(np.min(drawdown)) if len(drawdown) > 0 else 0.0
    return _ReturnMetrics(
        annualized_return=annualized,
        sharpe_ratio=sharpe,
        max_drawdown=max_drawdown,
        cumulative_returns=cumulative_returns.tolist(),
    )


def _compute_cross_sectional_ic(
    rows: pd.DataFrame,
    *,
    factor_col: str,
    return_col: str,
    min_cross_section: int = 30,
) -> _ICStats:
    daily: list[float] = []
    for _, group in rows.groupby("trade_date", sort=True):
        valid = group[[factor_col, return_col]].dropna()
        if len(valid) < min_cross_section:
            continue
        if valid[factor_col].nunique() < 2 or valid[return_col].nunique() < 2:
            continue
        ic = valid[factor_col].corr(valid[return_col], method="spearman")
        if pd.isna(ic):
            continue
        daily.append(float(ic))
    if not daily:
        return _ICStats(mean_ic=0.0, periods=0)
    return _ICStats(mean_ic=float(np.mean(daily)), periods=len(daily))


def _candidate_score(details: pd.DataFrame) -> np.ndarray:
    base_score = details["base_score"].to_numpy(float)
    return np.maximum(0.0, base_score)


def _evaluate_baseline_candidate(
    details: pd.DataFrame,
    forward_return_col: str,
    *,
    top_n: int,
    eval_step: int,
    retain_rank: int,
    portfolio_capital: float,
    max_participation: float,
) -> _CandidateMetrics:
    required = {
        "ts_code",
        "trade_date",
        "base_score",
        "stage",
        "rotation_phase",
        "retreat_severity",
        "industry_turning_severity",
        forward_return_col,
    }
    if not required.issubset(details.columns):
        return _CandidateMetrics()
    valid = details[details[forward_return_col].notna()].copy()
    if valid.empty:
        return _CandidateMetrics()

    valid["_candidate_score"] = _candidate_score(valid)
    fixed_cost_rates: list[float] = []
    liquidity_cost_rates: list[float] = []
    requested_trade_value = 0.0
    executed_trade_value = 0.0
    participation_rates: list[float] = []
    previous_holdings: set[str] = set()
    previous_amounts: dict[str, float] = {}
    position_value = float(portfolio_capital) / top_n if top_n > 0 else 0.0
    fixed_round_trip_cost = (_TRANSACTION_COST_BPS * 2.0 + _STAMP_DUTY_BPS) / 10000.0

    for _, group in valid.groupby("trade_date", sort=True):
        if len(group) < top_n:
            continue
        ranked_group = group.sort_values(
            ["_candidate_score", "ts_code"],
            ascending=[False, True],
        )
        ranked_codes = ranked_group["ts_code"].astype(str).tolist()
        selected_codes = select_buffered_portfolio(
            ranked_codes,
            previous_holdings=previous_holdings,
            target_size=top_n,
            retain_rank=retain_rank,
        )
        selected_set = set(selected_codes)
        top = ranked_group[ranked_group["ts_code"].astype(str).isin(selected_set)]
        if previous_holdings:
            overlap = len(previous_holdings & selected_set)
            period_turnover = 1.0 - overlap / top_n
            fixed_cost_rates.append(period_turnover * fixed_round_trip_cost)

            amount_by_code = (
                group.set_index(group["ts_code"].astype(str))["entry_daily_amount_cny"].to_dict()
                if "entry_daily_amount_cny" in group.columns
                else {}
            )
            liquidity_cost_value = 0.0
            buys = selected_set - previous_holdings
            sells = previous_holdings - selected_set
            for ts_code in buys:
                daily_amount = float(amount_by_code.get(ts_code, 0.0) or 0.0)
                capacity = apply_capacity_limit(
                    requested_value=position_value,
                    daily_amount_cny=daily_amount,
                    max_participation=max_participation,
                )
                requested_trade_value += position_value
                executed_trade_value += capacity.executed_value
                if capacity.executed_value > 0:
                    participation_rates.append(capacity.participation_rate)
                    liquidity_cost_value += (
                        capacity.executed_value
                        * estimate_one_way_cost_bps(
                            trade_value=capacity.executed_value,
                            daily_amount_cny=daily_amount,
                            side="buy",
                        )
                        / 10000.0
                    )
            for ts_code in sells:
                daily_amount = float(
                    amount_by_code.get(ts_code, previous_amounts.get(ts_code, 0.0)) or 0.0
                )
                capacity = apply_capacity_limit(
                    requested_value=position_value,
                    daily_amount_cny=daily_amount,
                    max_participation=max_participation,
                )
                if capacity.executed_value > 0:
                    participation_rates.append(capacity.participation_rate)
                    liquidity_cost_value += (
                        capacity.executed_value
                        * estimate_one_way_cost_bps(
                            trade_value=capacity.executed_value,
                            daily_amount_cny=daily_amount,
                            side="sell",
                        )
                        / 10000.0
                    )
            liquidity_cost_rates.append(
                liquidity_cost_value / portfolio_capital if portfolio_capital > 0 else 0.0
            )
        else:
            fixed_cost_rates.append(0.0)
            liquidity_cost_rates.append(0.0)

        previous_holdings = selected_set
        previous_amounts = (
            top.set_index(top["ts_code"].astype(str))["entry_daily_amount_cny"].to_dict()
            if "entry_daily_amount_cny" in top.columns
            else {}
        )

    valid_returns: list[float] = []
    for _, group in valid.groupby("trade_date", sort=True):
        if len(group) < top_n:
            continue
        ranked = group.sort_values(
            ["_candidate_score", "ts_code"],
            ascending=[False, True],
        ).head(top_n)
        valid_returns.append(float(ranked[forward_return_col].mean()))
    gross = np.asarray(valid_returns, dtype=float)
    fixed_net = gross - np.asarray(fixed_cost_rates, dtype=float)
    liquidity_net = gross - np.asarray(liquidity_cost_rates, dtype=float)
    return _CandidateMetrics(
        fixed_cost_net_return=float(fixed_net.mean()) if fixed_net.size else 0.0,
        liquidity_cost_net_return=float(liquidity_net.mean()) if liquidity_net.size else 0.0,
        capacity_fill_rate=(
            executed_trade_value / requested_trade_value
            if requested_trade_value > 0
            else 1.0
        ),
        avg_participation_rate=(
            float(np.mean(participation_rates)) if participation_rates else 0.0
        ),
    )


def _evaluate_advisor_metrics(
    details: pd.DataFrame,
    fwd_col: str,
) -> dict[str, float]:
    required = {"confidence", "stop_loss_pct", "take_profit_pct", "action", "position_weight"}
    if not required.issubset(details.columns):
        return {}
    result: dict[str, float] = {}
    fwd = details[fwd_col].to_numpy(dtype=float)
    buy_mask = details["action"].isin(_BULLISH_ACTIONS).to_numpy()
    if buy_mask.any():
        stop_loss = details.loc[buy_mask, "stop_loss_pct"].to_numpy(dtype=float) / 100.0
        take_profit = details.loc[buy_mask, "take_profit_pct"].to_numpy(dtype=float) / 100.0
        fwd_buy = fwd[buy_mask]
        result["stop_loss_hit_rate"] = float((fwd_buy < stop_loss).sum() / buy_mask.sum())
        result["take_profit_hit_rate"] = float((fwd_buy > take_profit).sum() / buy_mask.sum())
    else:
        result["stop_loss_hit_rate"] = 0.0
        result["take_profit_hit_rate"] = 0.0

    correct = 0
    total = 0
    for action, forward_return in zip(details["action"].to_numpy(), fwd, strict=True):
        if action in _BULLISH_ACTIONS:
            total += 1
            correct += int(forward_return > 0)
        elif action in _BEARISH_ACTIONS:
            total += 1
            correct += int(forward_return < 0)
    result["action_accuracy"] = float(correct / total) if total > 0 else 0.0

    weights = details["position_weight"].to_numpy(dtype=float)
    buy_weight_mask = buy_mask & (weights > 0)
    if buy_weight_mask.any():
        w = weights[buy_weight_mask]
        r = fwd[buy_weight_mask]
        total_w = w.sum()
        result["position_weighted_return"] = float((w * r).sum() / total_w) if total_w > 0 else 0.0
    else:
        result["position_weighted_return"] = 0.0
    return result


def _benchmark_metrics(
    gateway: Any,
    config: GraphBacktestConfig,
    *,
    eval_dates: list[str],
    top_n_dates: list[str],
    gross_returns: np.ndarray,
) -> tuple[float, float, float]:
    if (
        not config.benchmark_code
        or not eval_dates
        or not top_n_dates
        or gross_returns.size == 0
        or not hasattr(gateway, "index_daily")
    ):
        return 0.0, 0.0, 0.0
    bench_start = eval_dates[0]
    bench_end = (
        pd.Timestamp(eval_dates[-1]) + pd.Timedelta(days=int(config.forward_days * 2))
    ).strftime("%Y%m%d")
    bench_df = gateway.index_daily(config.benchmark_code, bench_start, bench_end)
    if bench_df.empty:
        return 0.0, 0.0, 0.0
    bench_df = bench_df.sort_values("trade_date").reset_index(drop=True)
    bench_dates = bench_df["trade_date"].astype(str).tolist()
    bench_set = set(bench_dates)
    closes = bench_df["close"].astype(float).tolist()
    bench_by_date: dict[str, float] = {}
    for eval_date in eval_dates:
        if eval_date not in bench_set:
            continue
        idx = bench_dates.index(eval_date)
        if idx + config.forward_days < len(closes):
            bench_by_date[eval_date] = closes[idx + config.forward_days] / closes[idx] - 1
    if not bench_by_date:
        return 0.0, 0.0, 0.0
    strategy_by_date = dict(zip(top_n_dates, gross_returns.tolist(), strict=False))
    matched_bench: list[float] = []
    matched_strategy: list[float] = []
    for eval_date in top_n_dates:
        if eval_date in bench_by_date and eval_date in strategy_by_date:
            matched_bench.append(bench_by_date[eval_date])
            matched_strategy.append(strategy_by_date[eval_date])
    if not matched_bench:
        return 0.0, 0.0, 0.0
    bench = np.asarray(matched_bench, dtype=float)
    strategy = np.asarray(matched_strategy, dtype=float)
    benchmark_return = float(np.mean(bench))
    if len(bench) <= 1:
        return benchmark_return, 0.0, 0.0
    excess = strategy - bench
    tracking_error = float(np.std(excess))
    return (
        benchmark_return,
        float(np.mean(excess)),
        float(np.mean(excess) / tracking_error) if tracking_error > 0 else 0.0,
    )


def _compute_metrics(
    details_before_filter: pd.DataFrame,
    details: pd.DataFrame,
    *,
    config: GraphBacktestConfig,
    gateway: Any,
    eval_dates: list[str],
) -> tuple[dict[str, float | int], list[float], list[str]]:
    fwd_col = f"fwd_ret_{config.forward_days}d"
    entry_block_rate = (
        float((~details_before_filter["entered"].astype(bool)).mean())
        if "entered" in details_before_filter.columns and not details_before_filter.empty
        else 0.0
    )
    entered_rows = (
        details_before_filter[details_before_filter["entered"].astype(bool)]
        if "entered" in details_before_filter.columns
        else pd.DataFrame()
    )
    exit_delay_rate = (
        float((entered_rows["exit_delay_days"] > 0).mean())
        if not entered_rows.empty and "exit_delay_days" in entered_rows.columns
        else 0.0
    )
    metrics: dict[str, float | int] = {
        "total_evaluated": int(len(details)),
        "direction_accuracy": 0.0,
        "top_n_excess_return": 0.0,
        "top_n_actual_return": 0.0,
        "median_actual_return": 0.0,
        "industry_momentum_strategy_return": 0.0,
        "crowding_alert_hit_rate": 0.0,
        "crowding_alert_count": 0,
        "score_return_corr": 0.0,
        "avg_turnover_per_period": 0.0,
        "avg_transaction_cost": 0.0,
        "net_top_n_return": 0.0,
        "annualized_return": 0.0,
        "sharpe_ratio": 0.0,
        "max_drawdown": 0.0,
        "benchmark_return": 0.0,
        "benchmark_excess_return": 0.0,
        "information_ratio": 0.0,
        "stop_loss_hit_rate": 0.0,
        "take_profit_hit_rate": 0.0,
        "action_accuracy": 0.0,
        "position_weighted_return": 0.0,
        "fixed_cost_net_return": 0.0,
        "liquidity_cost_net_return": 0.0,
        "entry_block_rate": entry_block_rate,
        "exit_delay_rate": exit_delay_rate,
        "capacity_fill_rate": 1.0,
        "avg_participation_rate": 0.0,
    }
    if details.empty:
        return metrics, [], []

    bullish = (
        details[details["direction"].isin(_BULLISH_DIRECTIONS)]
        if "direction" in details.columns
        else pd.DataFrame()
    )
    if not bullish.empty:
        metrics["direction_accuracy"] = float((bullish[fwd_col] > 0).mean())

    top_n_returns: list[float] = []
    median_returns: list[float] = []
    top_n_sets: list[set[str]] = []
    top_n_dates: list[str] = []
    for date, group in details.groupby("trade_date"):
        if len(group) < config.top_n:
            continue
        top_n = group.nlargest(config.top_n, "score")
        top_n_returns.append(float(top_n[fwd_col].mean()))
        median_returns.append(float(group[fwd_col].median()))
        top_n_sets.append(set(top_n["ts_code"].astype(str).tolist()))
        top_n_dates.append(str(date))

    turnovers = []
    for previous, current in zip(top_n_sets, top_n_sets[1:], strict=False):
        overlap = len(previous & current)
        turnovers.append(1.0 - overlap / config.top_n)
    avg_turnover = float(np.mean(turnovers)) if turnovers else 0.0
    round_trip_cost = (_TRANSACTION_COST_BPS * 2 + _STAMP_DUTY_BPS) / 10000.0
    avg_tx_cost = avg_turnover * round_trip_cost
    gross_returns = np.asarray(top_n_returns, dtype=float)
    median = np.asarray(median_returns, dtype=float)
    net_returns = gross_returns - avg_tx_cost
    return_metrics = _compute_return_metrics(net_returns, config.eval_step)

    metrics["top_n_excess_return"] = float(np.mean(gross_returns - median)) if gross_returns.size else 0.0
    metrics["top_n_actual_return"] = float(np.mean(gross_returns)) if gross_returns.size else 0.0
    metrics["median_actual_return"] = float(np.mean(median)) if median.size else 0.0
    metrics["avg_turnover_per_period"] = avg_turnover
    metrics["avg_transaction_cost"] = avg_tx_cost
    metrics["net_top_n_return"] = float(np.mean(net_returns)) if net_returns.size else 0.0
    metrics["annualized_return"] = return_metrics.annualized_return
    metrics["sharpe_ratio"] = return_metrics.sharpe_ratio
    metrics["max_drawdown"] = return_metrics.max_drawdown

    if "score" in details.columns:
        score_stats = _compute_cross_sectional_ic(
            details,
            factor_col="score",
            return_col=fwd_col,
            min_cross_section=30,
        )
        if score_stats.periods > 0:
            metrics["score_return_corr"] = score_stats.mean_ic

    crowding_alerts = (
        details[details["crowding_score"] >= _CROWDING_ALERT_THRESHOLD]
        if "crowding_score" in details.columns
        else pd.DataFrame()
    )
    metrics["crowding_alert_count"] = int(len(crowding_alerts))
    if not crowding_alerts.empty:
        metrics["crowding_alert_hit_rate"] = float((crowding_alerts[fwd_col] < 0).mean())

    industry_rank_returns: list[float] = []
    for _, group in details.groupby("trade_date"):
        if not {"industry_momentum_rank", "industry"}.issubset(group.columns):
            continue
        top_industries = group.nsmallest(10, "industry_momentum_rank")["industry"].unique()
        top_industry_predictions = group[group["industry"].isin(top_industries)]
        if not top_industry_predictions.empty:
            industry_rank_returns.append(float(top_industry_predictions[fwd_col].mean()))
    metrics["industry_momentum_strategy_return"] = (
        float(np.mean(industry_rank_returns)) if industry_rank_returns else 0.0
    )

    (
        metrics["benchmark_return"],
        metrics["benchmark_excess_return"],
        metrics["information_ratio"],
    ) = _benchmark_metrics(
        gateway,
        config,
        eval_dates=eval_dates,
        top_n_dates=top_n_dates,
        gross_returns=gross_returns,
    )

    metrics.update(_evaluate_advisor_metrics(details, fwd_col))
    portfolio_metrics = _evaluate_baseline_candidate(
        details,
        fwd_col,
        top_n=config.top_n,
        eval_step=config.eval_step,
        retain_rank=config.buffer_retain_rank,
        portfolio_capital=config.portfolio_capital,
        max_participation=config.max_participation,
    )
    metrics["fixed_cost_net_return"] = portfolio_metrics.fixed_cost_net_return
    metrics["liquidity_cost_net_return"] = portfolio_metrics.liquidity_cost_net_return
    metrics["capacity_fill_rate"] = portfolio_metrics.capacity_fill_rate
    metrics["avg_participation_rate"] = portfolio_metrics.avg_participation_rate
    return metrics, return_metrics.cumulative_returns if top_n_dates else [], top_n_dates


def build_oracle_parity_view(
    signals: pd.DataFrame,
    *,
    market: pd.DataFrame,
    config: GraphBacktestConfig,
    gateway: Any,
) -> OracleParityView:
    """Build the five frozen-Oracle comparable layers from Vibe signals."""
    if signals.empty or market.empty:
        metrics, cumulative_returns, _ = _compute_metrics(
            pd.DataFrame(),
            pd.DataFrame(),
            config=config,
            gateway=gateway,
            eval_dates=[],
        )
        return OracleParityView(
            signals=pd.DataFrame(),
            selected=pd.DataFrame(),
            trades=pd.DataFrame(columns=TRADE_COLUMNS),
            equity=_normalize_equity([], cumulative_returns),
            metrics=metrics,
        )

    predictions = signals.copy()
    if "trade_date" not in predictions.columns:
        predictions["trade_date"] = predictions["eval_date"]
    predictions["trade_date"] = predictions["trade_date"].astype(str)
    predictions["ts_code"] = predictions["ts_code"].astype(str)
    predictions = predictions.drop(columns=["eval_date"], errors="ignore")
    eval_dates = sorted(predictions["trade_date"].unique().tolist())

    market_rows = market.copy()
    market_rows["trade_date"] = market_rows["trade_date"].astype(str)
    market_rows["ts_code"] = market_rows["ts_code"].astype(str)
    forward_inputs = (
        market_rows[market_rows["trade_date"].isin(eval_dates)][["ts_code", "trade_date"]]
        .drop_duplicates()
        .rename(columns={"trade_date": "eval_date"})
    )
    forward_returns = simulate_trades(
        market_rows,
        signals=forward_inputs,
        holding_days=config.forward_days,
    )
    details_before_filter = predictions.merge(
        forward_returns,
        left_on=["ts_code", "trade_date"],
        right_on=["ts_code", "eval_date"],
        how="inner",
    )
    fwd_col = f"fwd_ret_{config.forward_days}d"
    details = details_before_filter[details_before_filter[fwd_col].notna()].copy()
    metrics, cumulative_returns, _ = _compute_metrics(
        details_before_filter,
        details,
        config=config,
        gateway=gateway,
        eval_dates=eval_dates,
    )
    return OracleParityView(
        signals=details.reset_index(drop=True),
        selected=_select_top(details, config.top_n),
        trades=_normalize_trades(details, top_n=config.top_n),
        equity=_normalize_equity(
            _eligible_eval_dates(details, top_n=config.top_n),
            cumulative_returns,
        ),
        metrics=metrics,
    )
