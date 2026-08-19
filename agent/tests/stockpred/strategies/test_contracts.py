from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from src.stockpred.strategies.contracts import (
    StrategyBatchRequest,
    StrategyDescriptor,
    metric_sort_value,
)


def test_batch_request_requires_selection_and_deduplicates_ids() -> None:
    request = StrategyBatchRequest(
        start="2025-01-01",
        end="2025-03-31",
        strategy_ids=("alpha101_1", "alpha101_1", "stockpred_graph"),
    )

    assert request.strategy_ids == ("alpha101_1", "stockpred_graph")
    assert request.select_all is False


@pytest.mark.parametrize(
    "values",
    [
        {"strategy_ids": ()},
        {"strategy_ids": ("alpha101_1",), "select_all": True},
    ],
)
def test_batch_request_requires_exactly_one_selection_mode(
    values: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="select exactly one"):
        StrategyBatchRequest(start="2025-01-01", end="2025-03-31", **values)


def test_descriptor_preserves_strategy_identity() -> None:
    descriptor = StrategyDescriptor(
        id="stockpred_graph",
        name="StockPred Graph",
        kind="graph",
        zoo=None,
        columns_required=(),
        min_warmup_bars=120,
    )

    assert descriptor.id == "stockpred_graph"
    assert descriptor.kind == "graph"


def test_metric_sort_value_rejects_non_finite_values() -> None:
    assert metric_sort_value({"sharpe": 1.2}, "sharpe") == 1.2
    assert metric_sort_value({"sharpe": math.inf}, "sharpe") is None
    assert metric_sort_value({"sharpe": "not-a-number"}, "sharpe") is None
