"""Normalize Graph and Alpha Zoo outputs to StockPred score frames."""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.factors.registry import RegistryError, SkipAlpha
from src.stockpred.contracts import StockPredDataError
from src.stockpred.graph.service import GraphSignalConfig
from src.stockpred.strategies.contracts import StrategyDescriptor, StrategyScore


class AlphaZooStrategyAdapter:
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
    def __init__(self, signal_service: Any) -> None:
        self.signal_service = signal_service

    def evaluate(self, eval_date: str, config: GraphSignalConfig = GraphSignalConfig()) -> StrategyScore:
        signals = self.signal_service.evaluate(eval_date, config)
        if signals.empty:
            return StrategyScore(pd.DataFrame(columns=["ts_code", "score", "trade_date"]))
        scores = signals[["ts_code", "score"]].copy()
        scores["trade_date"] = eval_date
        return StrategyScore(scores=scores, diagnostics={"kind": "graph", "signals": signals})
