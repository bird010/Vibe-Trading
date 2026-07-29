"""Historical execution shared by Graph and Alpha Zoo strategies."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from backtest.stockpred_graph.execution import build_daily_ledger, execute_target_portfolio
from backtest.stockpred_graph.performance import build_symbol_metrics_from_market, calculate_performance_metrics
from src.stockpred.contracts import StockPredDataError
from src.stockpred.graph.adjustment import apply_qfq
from src.stockpred.graph.portfolio import build_equal_weight_targets
from src.stockpred.strategies.contracts import StrategyBacktestConfig


@dataclass
class StrategyBacktestResult:
    strategy_id: str
    eval_dates: list[str]
    signals: pd.DataFrame
    selected: pd.DataFrame
    trades: pd.DataFrame
    positions: pd.DataFrame
    equity: pd.DataFrame
    metrics: dict[str, float]
    ohlcv: dict[str, pd.DataFrame] = field(default_factory=dict)
    symbol_metrics: list[dict[str, float | str]] = field(default_factory=list)


def _concat(frames: list[pd.DataFrame]) -> pd.DataFrame:
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


class StockPredStrategyBacktestRunner:
    def __init__(self, gateway: Any, strategy: Any) -> None:
        self.gateway = gateway
        self.strategy = strategy

    def run(self, config: StrategyBacktestConfig, on_progress: Any = None) -> StrategyBacktestResult:
        session = StrategyScreeningSession(self, config)
        for done, eval_date in enumerate(session.scheduled_dates, start=1):
            session.evaluate(eval_date)
            if on_progress is not None:
                on_progress(done, len(session.scheduled_dates), eval_date)
        return session.finalize()

    def _execute(self, config: StrategyBacktestConfig, dates: list[str], selections: list[pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        if not selections:
            return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
        end = (datetime.strptime(config.end, "%Y%m%d") + timedelta(days=60)).strftime("%Y%m%d")
        codes = sorted(set().union(*(set(frame["ts_code"].astype(str)) for frame in selections)))
        market = apply_qfq(self.gateway.prices(config.start, end, codes), self.gateway.adjustment_factors(config.start, end, codes))
        limits = self.gateway.stock_limits(config.start, end, codes)
        market = market.merge(limits[["ts_code", "trade_date", "up_limit", "down_limit"]], on=["ts_code", "trade_date"], how="left", validate="one_to_one")
        trades = _concat([execute_target_portfolio(market, targets, signal_date=date, holding_days=config.forward_days, capital=config.portfolio_capital, max_participation=config.max_participation) for date, targets in zip(dates, selections, strict=True)])
        positions, equity = build_daily_ledger(trades, market, initial_capital=config.portfolio_capital)
        return trades, positions, equity, market


class StrategyScreeningSession:
    """Keep one strategy's cross-evaluation selection state."""

    def __init__(self, runner: StockPredStrategyBacktestRunner, config: StrategyBacktestConfig, *, scheduled_dates: list[str] | None = None, phase_timer: Any = None) -> None:
        self.runner = runner
        self.config = config
        self.scheduled_dates = list(scheduled_dates) if scheduled_dates is not None else runner.gateway.trade_dates(config.start, config.end)[::config.eval_step]
        self.signals: list[pd.DataFrame] = []
        self.selected: list[pd.DataFrame] = []
        self.valid_dates: list[str] = []
        self.previous_holdings: set[str] = set()
        self.phase_timer = phase_timer

    def evaluate(self, eval_date: str, panel: dict[str, pd.DataFrame] | None = None) -> None:
        if self.phase_timer is None:
            score = self.runner.strategy.evaluate(eval_date) if panel is None else self.runner.strategy.evaluate(eval_date, panel)
        else:
            with self.phase_timer.phase("factor_compute"):
                score = self.runner.strategy.evaluate(eval_date) if panel is None else self.runner.strategy.evaluate(eval_date, panel)
        if not score.scores.empty:
            current = score.scores.copy()
            current["trade_date"] = eval_date
            targets = build_equal_weight_targets(current, top_n=self.config.top_n, previous_holdings=self.previous_holdings, retain_rank=self.config.buffer_retain_rank)
            self.previous_holdings = set(targets["ts_code"].astype(str))
            self.signals.append(current)
            self.selected.append(targets)
            self.valid_dates.append(eval_date)

    def finalize(self) -> StrategyBacktestResult:
        ratio = len(self.valid_dates) / len(self.scheduled_dates) if self.scheduled_dates else 0.0
        if ratio < self.config.min_valid_eval_ratio:
            raise StockPredDataError("STOCKPRED_VALID_EVAL_RATIO", f"valid evaluation ratio {ratio:.2%} is below required {self.config.min_valid_eval_ratio:.2%}")
        trades, positions, equity, market = self.runner._execute(self.config, self.valid_dates, self.selected)
        metrics = {"scheduled_evaluations": float(len(self.scheduled_dates)), "valid_evaluations": float(len(self.valid_dates)), "valid_eval_ratio": float(ratio)}
        if not equity.empty:
            metrics.update(calculate_performance_metrics(equity, trades))
        return StrategyBacktestResult(self.config.strategy_snapshot.descriptor.id, self.valid_dates, _concat(self.signals), _concat(self.selected), trades, positions, equity, metrics, {}, build_symbol_metrics_from_market(trades, market))
