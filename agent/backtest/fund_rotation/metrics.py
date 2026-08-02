"""Performance metrics — §14.2."""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_performance_metrics(
    cumulative: pd.Series,
    periods_per_year: int = 52,
) -> dict[str, float]:
    """§14.2 — Core return and risk metrics from a cumulative index series.

    Args:
        cumulative: Cumulative return index (starts at 1.0).
        periods_per_year: 52 for weekly data.

    Returns:
        Dict of metric_name -> value.
    """
    if cumulative.empty or len(cumulative) < 2:
        return {"annual_return": 0.0, "annual_volatility": 0.0, "sharpe": 0.0,
                "sortino": 0.0, "max_drawdown": 0.0, "calmar": 0.0}

    returns = cumulative.pct_change().dropna()
    n = len(returns)

    # Annualized return
    total_return = cumulative.iloc[-1] / cumulative.iloc[0] - 1.0
    years = n / periods_per_year
    annual_return = (1.0 + total_return) ** (1.0 / max(years, 1e-9)) - 1.0 if years > 0 else 0.0

    # Annualized volatility
    vol = float(returns.std(ddof=1)) * np.sqrt(periods_per_year) if n > 1 else 0.0

    # Sharpe (rf=0)
    sharpe = annual_return / vol if vol > 1e-12 else 0.0

    # Sortino
    downside = returns[returns < 0]
    downside_std = float(downside.std(ddof=1)) * np.sqrt(periods_per_year) if len(downside) > 1 else 0.0
    sortino = annual_return / downside_std if downside_std > 1e-12 else 0.0

    # Max drawdown
    running_max = cumulative.cummax()
    drawdown = (cumulative - running_max) / running_max
    max_dd = float(drawdown.min())

    # Calmar
    calmar = annual_return / abs(max_dd) if abs(max_dd) > 1e-12 else 0.0

    # Worst single period
    worst_period = float(returns.min()) if n > 0 else 0.0

    # Drawdown recovery time (periods from max DD trough back to prior peak)
    recovery_periods = _drawdown_recovery_periods(cumulative)

    return {
        "annual_return": annual_return,
        "annual_volatility": vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_dd,
        "calmar": calmar,
        "worst_period": worst_period,
        "total_return": total_return,
        "num_periods": n,
        "max_drawdown_recovery_periods": recovery_periods,
    }


def _drawdown_recovery_periods(cumulative: pd.Series) -> int:
    """Compute recovery time from max drawdown trough to prior peak.

    Returns number of periods, or -1 if not yet recovered.
    """
    if cumulative.empty or len(cumulative) < 2:
        return 0
    running_max = cumulative.cummax()
    drawdown = (cumulative - running_max) / running_max
    trough_idx = drawdown.idxmin()
    # Find the peak before trough
    peak_val = running_max.loc[trough_idx]
    # Find recovery point (first time cumulative >= peak_val after trough)
    after_trough = cumulative.loc[trough_idx:]
    recovered = after_trough[after_trough >= peak_val]
    if len(recovered) <= 1:
        return -1  # Not recovered
    recovery_idx = recovered.index[1] if len(recovered) > 1 else recovered.index[0]
    # Count periods between trough and recovery
    all_idx = list(cumulative.index)
    try:
        trough_pos = all_idx.index(trough_idx)
        recovery_pos = all_idx.index(recovery_idx)
        return recovery_pos - trough_pos
    except (ValueError, IndexError):
        return -1


def compute_excess_metrics(
    strategy_cumulative: pd.Series,
    benchmark_cumulative: pd.Series,
    benchmark_name: str,
    periods_per_year: int = 52,
) -> dict[str, float]:
    """§14.2 — Excess return metrics vs a single benchmark."""
    if strategy_cumulative.empty or benchmark_cumulative.empty:
        return {}

    # Align on common index
    common = strategy_cumulative.index.intersection(benchmark_cumulative.index)
    if len(common) < 2:
        return {}

    strat = strategy_cumulative.loc[common]
    bench = benchmark_cumulative.loc[common]

    strat_ret = strat.pct_change().dropna()
    bench_ret = bench.pct_change().dropna()
    excess_ret = strat_ret - bench_ret

    # Annualized excess return (arithmetic mean of periodic excess * periods_per_year)
    total_excess = float(strat.iloc[-1] / strat.iloc[0] - bench.iloc[-1] / bench.iloc[0])
    n = len(excess_ret)
    ann_excess = float(excess_ret.mean()) * periods_per_year if n > 0 else 0.0

    # Tracking error
    tracking_error = float(excess_ret.std(ddof=1)) * np.sqrt(periods_per_year) if n > 1 else 0.0

    # Information ratio
    info_ratio = ann_excess / tracking_error if tracking_error > 1e-12 else 0.0

    return {
        f"excess_vs_{benchmark_name}": total_excess,
        f"annualized_excess_vs_{benchmark_name}": ann_excess,
        f"tracking_error_vs_{benchmark_name}": tracking_error,
        f"information_ratio_vs_{benchmark_name}": info_ratio,
    }


def compute_yearly_breakdown(
    cumulative: pd.Series,
    periods_per_year: int = 52,
) -> dict[str, dict[str, float]]:
    """§14.3 — Year-by-year performance breakdown."""
    if cumulative.empty or len(cumulative) < 2:
        return {}

    returns = cumulative.pct_change().dropna()
    # Group by year (extract from index)
    try:
        years = pd.to_datetime(returns.index, format="%Y%m%d").year
    except (ValueError, TypeError):
        return {}

    result = {}
    for year, group in returns.groupby(years):
        if len(group) < 2:
            continue
        year_return = float((1 + group).prod() - 1)
        year_vol = float(group.std(ddof=1)) * np.sqrt(periods_per_year)
        result[str(year)] = {
            "return": year_return,
            "volatility": year_vol,
            "sharpe": year_return / year_vol if year_vol > 1e-12 else 0.0,
            "num_periods": len(group),
        }
    return result


def compute_execution_diagnostics(
    events: list[dict],
    initial_capital: float,
) -> dict[str, float]:
    """§14.2 — Execution diagnostics from trade events.

    Args:
        events: List of trade event dicts with keys like
            action, filled, price, commission, status.
        initial_capital: Starting capital for turnover calculation.

    Returns:
        Dict of diagnostic metrics.
    """
    total_buy_notional = 0.0
    total_sell_notional = 0.0
    total_commission = 0.0
    total_requested = 0
    total_filled = 0
    blocked_count = 0

    for e in events:
        filled = e.get("filled", 0)
        price = e.get("price", 0.0)
        commission = e.get("commission", 0.0)
        status = e.get("status", "")

        total_commission += commission
        total_requested += e.get("requested", 0)
        total_filled += filled

        if status == "BLOCKED":
            blocked_count += 1

        if e.get("action") == "BUY":
            total_buy_notional += filled * price
        elif e.get("action") == "SELL":
            total_sell_notional += filled * price

    turnover = (total_buy_notional + total_sell_notional) / max(initial_capital, 1.0)
    fill_rate = total_filled / max(total_requested, 1)

    return {
        "total_buy_notional": total_buy_notional,
        "total_sell_notional": total_sell_notional,
        "total_commission": total_commission,
        "turnover": turnover,
        "fill_rate": fill_rate,
        "blocked_count": blocked_count,
        "num_trades": len(events),
    }
