from __future__ import annotations

import pandas as pd

from src.stockpred.strategies.adapters import AlphaZooStrategyAdapter
from src.stockpred.strategies.contracts import StrategyDescriptor


class _Registry:
    def compute(self, alpha_id: str, panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
        assert alpha_id == "alpha101_1"
        return panel["close"]


class _PanelBuilder:
    def build(self, eval_date: str, descriptor: StrategyDescriptor) -> dict[str, pd.DataFrame]:
        return {"close": pd.DataFrame({"000001.SZ": [1.0, 2.0]}, index=pd.to_datetime(["2025-01-02", "2025-01-03"]))}


def test_alpha_adapter_returns_last_cross_section_as_scores() -> None:
    descriptor = StrategyDescriptor(id="alpha101_1", name="Alpha", kind="alpha_zoo", zoo="alpha101", columns_required=("close",))

    result = AlphaZooStrategyAdapter(_Registry(), _PanelBuilder(), descriptor).evaluate("20250103")

    assert result.scores.to_dict("records") == [{"ts_code": "000001.SZ", "score": 2.0, "trade_date": "20250103"}]
