"""Deterministic historical evaluation loop for StockPred Graph signals."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from datetime import datetime, timedelta

import pandas as pd

from src.stockpred.contracts import StockPredDataError
from src.stockpred.graph.backtest_config import GraphBacktestConfig
from src.stockpred.graph.adjustment import apply_qfq
from src.stockpred.graph.portfolio import build_equal_weight_targets
from src.stockpred.graph.service import GraphSignalConfig

from backtest.stockpred_graph.execution import (
    build_daily_ledger,
    execute_target_portfolio,
)
from backtest.stockpred_graph.oracle_parity import (
    OracleParityView,
    build_oracle_parity_view,
)
from backtest.stockpred_graph.performance import (
    build_symbol_metrics,
    calculate_performance_metrics,
)


ProgressCallback = Callable[[int, int, str], None]


@dataclass
class GraphBacktestResult:
    eval_dates: list[str]
    signals: pd.DataFrame
    selected: pd.DataFrame
    trades: pd.DataFrame
    positions: pd.DataFrame
    equity: pd.DataFrame
    metrics: dict[str, float]
    ohlcv: dict[str, pd.DataFrame] = field(default_factory=dict)
    symbol_metrics: list[dict[str, float | str]] = field(default_factory=list)
    parity_signals: pd.DataFrame | None = None
    parity_selected: pd.DataFrame | None = None
    parity_trades: pd.DataFrame | None = None
    parity_equity: pd.DataFrame | None = None
    parity_metrics: dict[str, float | int] | None = None


def _concat(frames: list[pd.DataFrame]) -> pd.DataFrame:
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


class GraphBacktestRunner:
    def __init__(self, gateway: Any, signal_service: Any) -> None:
        self.gateway = gateway
        self.signal_service = signal_service

    def run(
        self,
        config: GraphBacktestConfig,
        on_progress: ProgressCallback | None = None,
    ) -> GraphBacktestResult:
        open_dates = self.gateway.trade_dates(config.start, config.end)
        scheduled_dates = open_dates[:: config.eval_step]
        signal_config = GraphSignalConfig(
            data_lookback_days=config.data_lookback_days,
            min_listed_trade_days=config.min_listed_trade_days,
            min_adj_coverage=config.min_adj_coverage,
            benchmark_code=config.benchmark_code,
            exclude_st=config.exclude_st,
            require_pit_industry=config.require_pit_industry,
            allowed_exchanges=config.allowed_exchanges,
        )
        valid_dates: list[str] = []
        signal_frames: list[pd.DataFrame] = []
        selected_frames: list[pd.DataFrame] = []
        previous_holdings: set[str] = set()
        total = len(scheduled_dates)
        for done, eval_date in enumerate(scheduled_dates, start=1):
            signals = self.signal_service.evaluate(eval_date, signal_config)
            if not signals.empty:
                signals = signals.copy()
                if "trade_date" not in signals.columns:
                    signals["trade_date"] = eval_date
                selected = build_equal_weight_targets(
                    signals,
                    top_n=config.top_n,
                    previous_holdings=previous_holdings,
                    retain_rank=config.buffer_retain_rank,
                )
                previous_holdings = set(selected["ts_code"].astype(str))
                valid_dates.append(eval_date)
                signal_frames.append(signals)
                selected_frames.append(selected)
            if on_progress is not None:
                on_progress(done, total, eval_date)

        valid_ratio = len(valid_dates) / total if total else 0.0
        if valid_ratio < config.min_valid_eval_ratio:
            raise StockPredDataError(
                "STOCKPRED_VALID_EVAL_RATIO",
                (
                    f"valid evaluation ratio {valid_ratio:.2%} is below "
                    f"required {config.min_valid_eval_ratio:.2%}"
                ),
            )
        all_signals = _concat(signal_frames)
        trade_frames: list[pd.DataFrame] = []
        positions = pd.DataFrame()
        equity = pd.DataFrame()
        ohlcv: dict[str, pd.DataFrame] = {}
        parity_view: OracleParityView | None = None
        if selected_frames:
            execution_end = (
                datetime.strptime(config.end, "%Y%m%d") + timedelta(days=60)
            ).strftime("%Y%m%d")
            selected_codes = sorted(
                set().union(
                    *(set(frame["ts_code"].astype(str)) for frame in selected_frames)
                )
            )
            raw_market = self.gateway.prices(
                config.start,
                execution_end,
                selected_codes,
            )
            factors = self.gateway.adjustment_factors(
                config.start,
                execution_end,
                selected_codes,
            )
            market = apply_qfq(raw_market, factors)
            limits = self.gateway.stock_limits(
                config.start,
                execution_end,
                selected_codes,
            )
            market = market.merge(
                limits[["ts_code", "trade_date", "up_limit", "down_limit"]],
                on=["ts_code", "trade_date"],
                how="left",
                validate="one_to_one",
            )
            ohlcv = {
                code: market[market["ts_code"].astype(str) == code]
                .sort_values("trade_date", kind="stable")
                .reset_index(drop=True)
                for code in selected_codes
            }
            for eval_date, targets in zip(valid_dates, selected_frames, strict=True):
                trade_frames.append(
                    execute_target_portfolio(
                        market,
                        targets,
                        signal_date=eval_date,
                        holding_days=config.forward_days,
                        capital=config.portfolio_capital,
                        max_participation=config.max_participation,
                    )
                )
            trades = _concat(trade_frames)
            positions, equity = build_daily_ledger(
                trades,
                market,
                initial_capital=config.portfolio_capital,
            )
            if (config.mode == "parity" or config.parity_reference) and not all_signals.empty:
                parity_end = (
                    datetime.strptime(valid_dates[-1], "%Y%m%d")
                    + timedelta(days=int(config.forward_days * 2))
                ).strftime("%Y%m%d")
                all_signal_codes = sorted(set(all_signals["ts_code"].astype(str)))
                parity_raw_market = self.gateway.prices(
                    config.start,
                    parity_end,
                    all_signal_codes,
                )
                parity_factors = self.gateway.adjustment_factors(
                    config.start,
                    parity_end,
                    all_signal_codes,
                )
                parity_market = apply_qfq(parity_raw_market, parity_factors)
                parity_limits = self.gateway.stock_limits(
                    config.start,
                    parity_end,
                    all_signal_codes,
                )
                parity_market = parity_market.merge(
                    parity_limits[["ts_code", "trade_date", "up_limit", "down_limit"]],
                    on=["ts_code", "trade_date"],
                    how="left",
                    validate="one_to_one",
                )
                parity_view = build_oracle_parity_view(
                    all_signals,
                    market=parity_market,
                    config=config,
                    gateway=self.gateway,
                )
        else:
            trades = pd.DataFrame()
        metrics = {
            "scheduled_evaluations": float(total),
            "valid_evaluations": float(len(valid_dates)),
            "valid_eval_ratio": float(valid_ratio),
        }
        if not equity.empty:
            metrics.update(calculate_performance_metrics(equity, trades))
        symbol_metrics = build_symbol_metrics(trades, ohlcv)
        return GraphBacktestResult(
            eval_dates=valid_dates,
            signals=all_signals,
            selected=_concat(selected_frames),
            trades=trades,
            positions=positions,
            equity=equity,
            metrics=metrics,
            ohlcv=ohlcv,
            symbol_metrics=symbol_metrics,
            parity_signals=parity_view.signals if parity_view is not None else None,
            parity_selected=parity_view.selected if parity_view is not None else None,
            parity_trades=parity_view.trades if parity_view is not None else None,
            parity_equity=parity_view.equity if parity_view is not None else None,
            parity_metrics=parity_view.metrics if parity_view is not None else None,
        )
