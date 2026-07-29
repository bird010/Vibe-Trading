"""Catalog that presents Graph and Alpha Zoo factors as StockPred strategies."""

from __future__ import annotations

from typing import Any

from src.factors.registry import Registry
from src.stockpred.strategies.contracts import StrategyDescriptor


class StrategyCatalog:
    """Read-only catalog of strategies supported by the StockPred engine."""

    def __init__(self, registry: Registry | Any | None = None) -> None:
        self._registry = registry or Registry()

    def list(self) -> list[StrategyDescriptor]:
        return sorted(
            [self._graph_descriptor(), *self._alpha_descriptors()],
            key=lambda descriptor: descriptor.id,
        )

    def require(self, strategy_id: str) -> StrategyDescriptor:
        for descriptor in self.list():
            if descriptor.id == strategy_id:
                return descriptor
        raise KeyError(f"StockPred strategy not found: {strategy_id}")

    @staticmethod
    def _graph_descriptor() -> StrategyDescriptor:
        return StrategyDescriptor(
            id="stockpred_graph",
            name="StockPred Graph",
            kind="graph",
            zoo=None,
            columns_required=("open", "high", "low", "close", "volume", "amount"),
            min_warmup_bars=120,
            metadata={"formula_latex": "StockPred graph score", "theme": ["graph"]},
        )

    def _alpha_descriptors(self) -> list[StrategyDescriptor]:
        descriptors: list[StrategyDescriptor] = []
        for alpha_id in self._registry.list(universe="equity_cn"):
            alpha = self._registry.get(alpha_id)
            metadata = dict(alpha.meta or {})
            descriptors.append(
                StrategyDescriptor(
                    id=alpha.id,
                    name=str(metadata.get("nickname") or alpha.id),
                    kind="alpha_zoo",
                    zoo=alpha.zoo,
                    columns_required=tuple(str(item) for item in metadata.get("columns_required", ())),
                    min_warmup_bars=int(metadata.get("min_warmup_bars") or 0),
                    metadata=metadata,
                )
            )
        return descriptors
