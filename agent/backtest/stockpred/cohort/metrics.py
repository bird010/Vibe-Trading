"""Cohort metrics: raw signal label and single-cohort return computation.

Implements design §12 and §27.12.
"""

from __future__ import annotations

import bisect
from math import isfinite
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from backtest.stockpred.cohort.contracts import CohortResult, CohortStatus, TargetSnapshot


@dataclass(frozen=True)
class RawLabelResult:
    """Result of raw signal label computation per §27.12."""

    raw_signal_return: float | None
    raw_label_coverage: float
    symbol_returns: dict[str, float | None] = field(default_factory=dict)
    status: str = "ok"  # "ok" or "insufficient_data"


def compute_raw_signal_return(
    target: TargetSnapshot,
    market: pd.DataFrame,
    trade_dates: list[str],
    *,
    holding_days: int,
    min_coverage: float = 0.0,
) -> RawLabelResult:
    """Compute open-to-open raw signal return per §27.12.

    raw_symbol_return = adj_open(target_exit_date) / adj_open(entry_date) - 1
    entry_date = first trade date after evaluation_date
    target_exit_date = entry_date position + holding_days in trade calendar

    Ignores limit-up/down, suspension, capacity, fees, and exit delays.
    Does NOT renormalize weights when labels are missing.
    """
    if not target.selected_codes:
        return RawLabelResult(raw_signal_return=None, raw_label_coverage=0.0, status="insufficient_data")

    # Find entry date: first trade date strictly after evaluation_date
    entry_idx = bisect.bisect_right(trade_dates, target.evaluation_date)
    if entry_idx >= len(trade_dates):
        return RawLabelResult(raw_signal_return=None, raw_label_coverage=0.0, status="insufficient_data")

    entry_date = trade_dates[entry_idx]

    # Target exit date: entry position + holding_days
    exit_idx = entry_idx + holding_days
    if exit_idx >= len(trade_dates):
        return RawLabelResult(raw_signal_return=None, raw_label_coverage=0.0, status="insufficient_data")

    target_exit_date = trade_dates[exit_idx]

    # Build price lookup
    mkt = market.copy()
    mkt["ts_code"] = mkt["ts_code"].astype(str)
    mkt["trade_date"] = mkt["trade_date"].astype(str)
    if "adj_open" not in mkt.columns:
        return RawLabelResult(
            raw_signal_return=None,
            raw_label_coverage=0.0,
            symbol_returns={code: None for code in target.selected_codes},
            status="insufficient_data",
        )

    # Compute per-symbol returns
    symbol_returns: dict[str, float | None] = {}
    weighted_return = 0.0
    complete_weight = 0.0

    for code in target.selected_codes:
        weight = target.target_weights.get(code, 0.0)

        # Get entry price
        entry_row = mkt[(mkt["ts_code"] == code) & (mkt["trade_date"] == entry_date)]
        if entry_row.empty:
            symbol_returns[code] = None
            continue

        entry_price = pd.to_numeric(entry_row.iloc[0]["adj_open"], errors="coerce")
        if pd.isna(entry_price) or not isfinite(float(entry_price)) or entry_price <= 0:
            symbol_returns[code] = None
            continue

        # Get exit price
        exit_row = mkt[(mkt["ts_code"] == code) & (mkt["trade_date"] == target_exit_date)]
        if exit_row.empty:
            symbol_returns[code] = None
            continue

        exit_price = pd.to_numeric(exit_row.iloc[0]["adj_open"], errors="coerce")
        if pd.isna(exit_price) or not isfinite(float(exit_price)) or exit_price <= 0:
            symbol_returns[code] = None
            continue

        ret = float(exit_price) / float(entry_price) - 1.0
        symbol_returns[code] = ret
        weighted_return += weight * ret
        complete_weight += weight

    # Coverage = weight of complete labels / total weight
    total_weight = sum(target.target_weights.get(c, 0.0) for c in target.selected_codes)
    coverage = complete_weight / total_weight if total_weight > 0 else 0.0

    # Status
    status = "ok" if coverage >= min_coverage else "insufficient_data"

    # Return None if no complete labels
    raw_return = weighted_return if complete_weight > 0 else None

    return RawLabelResult(
        raw_signal_return=raw_return,
        raw_label_coverage=coverage,
        symbol_returns=symbol_returns,
        status=status,
    )


def compute_cohort_result(
    *,
    ledger: Any,
    raw_signal_return: float | None,
    horizon_mark_return: float,
    target_horizon_benchmark_return: float,
    liquidation_benchmark_return: float,
    exit_delay_days: int,
    unliquidated_ratio: float,
    terminal_value: float | None = None,
    data_quality: dict[str, Any] | None = None,
    raw_label_coverage: float = 0.0,
    raw_label_status: str = "insufficient_data",
    uses_stale_valuation: bool = False,
    max_stale_days: int = 0,
) -> CohortResult:
    """Compute single-cohort return metrics per §12.

    committed_capital_return = (final_cash + residual_value - C) / C
    """
    committed = ledger.committed_capital
    status = ledger.status

    # Failed cohorts enter coverage denominator without invented returns.
    if status in (CohortStatus.FAILED_DATA, CohortStatus.FAILED_EXECUTION):
        return CohortResult(
            cohort_id=ledger.cohort_id,
            committed_capital_return=None,
            executed_capital_return=None,
            raw_signal_return=None,
            horizon_mark_return=None,
            liquidation_return=None,
            benchmark_return=None,
            target_horizon_excess_return=None,
            liquidation_policy_excess_return=None,
            fill_rate=0.0,
            idle_cash_ratio=1.0,
            cost_ratio=0.0,
            exit_delay_days=0,
            unliquidated_ratio=0.0,
            status=status,
            data_quality=data_quality or {},
            evaluation_date=ledger.evaluation_date,
            raw_label_coverage=raw_label_coverage,
            raw_label_status=raw_label_status,
            uses_stale_valuation=uses_stale_valuation,
            max_stale_days=max_stale_days,
        )

    # committed_capital_return: (final_cash + residual_terminal_value - C) / C.
    # A caller that has attempted exits must provide the policy-valued residual.
    residual_value = ledger.position_cost_basis if terminal_value is None else terminal_value
    final_value = ledger.available_cash + residual_value
    committed_return = (final_value - committed) / committed if committed > 0 else 0.0

    # executed_capital_return: pnl / actual_invested (diagnostic)
    invested = ledger._entry_executed_value
    if invested > 0:
        pnl = ledger.total_exit_proceeds - invested + residual_value
        executed_return = pnl / invested
    else:
        executed_return = 0.0

    # liquidation_return = committed_capital_return for LIQUIDATED/UNLIQUIDATED
    liquidation_return = committed_return

    # Excess returns
    target_excess = horizon_mark_return - target_horizon_benchmark_return
    liquidation_excess = liquidation_return - liquidation_benchmark_return

    return CohortResult(
        cohort_id=ledger.cohort_id,
        committed_capital_return=committed_return,
        executed_capital_return=executed_return,
        raw_signal_return=raw_signal_return,
        horizon_mark_return=horizon_mark_return,
        liquidation_return=liquidation_return,
        benchmark_return=target_horizon_benchmark_return,
        target_horizon_excess_return=target_excess,
        liquidation_policy_excess_return=liquidation_excess,
        fill_rate=ledger.fill_rate,
        idle_cash_ratio=ledger.idle_cash_ratio,
        cost_ratio=ledger.cost_ratio,
        exit_delay_days=exit_delay_days,
        unliquidated_ratio=unliquidated_ratio,
        status=status,
        data_quality=data_quality or {},
        evaluation_date=ledger.evaluation_date,
        raw_label_coverage=raw_label_coverage,
        raw_label_status=raw_label_status,
        uses_stale_valuation=uses_stale_valuation,
        max_stale_days=max_stale_days,
    )
