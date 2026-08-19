"""Normalize Graph and Alpha Zoo outputs to StockPred score frames."""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.factors.registry import RegistryError, SkipAlpha
from src.stockpred.contracts import StockPredDataError
from src.stockpred.graph.service import GraphSignalConfig
from src.stockpred.strategies.contracts import StrategyDescriptor, StrategyScore

# Tables read by the cohort engine itself (benchmark computation),
# merged into strategy deps before PIT classification.
ENGINE_COMMON_DEPENDENCIES: tuple[str, ...] = (
    "fact_index_daily",
    "fact_stock_limit",
)


class AlphaZooStrategyAdapter:
    """Adapter for Alpha Zoo factor strategies.

    Factor computation uses only non-revisable market data (OHLCV, adjustment
    factors, price limits, trade calendar).  However, the panel builder's
    universe construction reads dim_stock_name_history for ST exclusion,
    which is a revisable table — hence the snapshot_only PIT classification.
    """

    dependencies: tuple[str, ...] = (
        "stock",
        "dim_stock",
        "fact_adj_factor",
        "fact_stock_limit",
        "dim_trade_cal",
        "dim_stock_name_history",  # panel builder: ST filtering
        "bridge_stock_industry",  # panel builder: universe construction
    )

    def __init__(self, registry: Any, panel_builder: Any, descriptor: StrategyDescriptor) -> None:
        self.registry = registry
        self.panel_builder = panel_builder
        self.descriptor = descriptor

    def evaluate(self, eval_date: str, panel: dict[str, pd.DataFrame] | None = None) -> StrategyScore:
        if panel is None:
            panel = self.panel_builder.build(eval_date, self.descriptor)
        try:
            values = self.registry.compute(self.descriptor.id, panel)
        except SkipAlpha as exc:
            raise StockPredDataError("STOCKPRED_STRATEGY_INPUT_MISSING", str(exc)) from exc
        except (RegistryError, KeyError, ValueError) as exc:
            raise StockPredDataError("STOCKPRED_STRATEGY_EVALUATION", str(exc)) from exc
        if values.empty:
            return StrategyScore(pd.DataFrame(columns=["ts_code", "score", "trade_date"]))
        scores = values.iloc[-1].rename_axis("ts_code").rename("score").dropna().reset_index()
        scores["trade_date"] = eval_date
        return StrategyScore(scores=scores, diagnostics={"kind": "alpha_zoo"})


class GraphStrategyAdapter:
    """Adapter for Graph (LLM/knowledge-graph) strategies.

    Uses revisable data (financials, name history, industry, daily basic,
    money flow) in addition to market data.
    """

    dependencies: tuple[str, ...] = (
        "stock",
        "dim_stock",
        "fact_adj_factor",
        "dim_trade_cal",
        "fact_index_weight",  # graph service: index membership edges
        "fact_fina_indicator",
        "dim_stock_name_history",
        "bridge_stock_industry",
        "fact_stock_daily_basic",
        "fact_moneyflow",
    )

    def __init__(self, signal_service: Any) -> None:
        self.signal_service = signal_service

    def evaluate(self, eval_date: str, config: GraphSignalConfig = GraphSignalConfig()) -> StrategyScore:
        signals = self.signal_service.evaluate(eval_date, config)
        if signals.empty:
            return StrategyScore(pd.DataFrame(columns=["ts_code", "score", "trade_date"]))
        scores = signals[["ts_code", "score"]].copy()
        scores["trade_date"] = eval_date
        return StrategyScore(scores=scores, diagnostics={"kind": "graph", "signals": signals})
