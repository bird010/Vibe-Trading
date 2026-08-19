from __future__ import annotations

import pandas as pd

from src.stockpred.graph.portfolio import (
    build_equal_weight_targets,
    rank_signals,
    select_buffered_portfolio,
)


def test_rank_signals_breaks_ties_by_ts_code() -> None:
    ranked = rank_signals(
        pd.DataFrame({"ts_code": ["B", "A", "C"], "score": [1.0, 1.0, 0.5]})
    )

    assert ranked["ts_code"].tolist() == ["A", "B", "C"]
    assert ranked["rank"].tolist() == [1, 2, 3]


def test_default_buffer_still_selects_pure_top_50() -> None:
    codes = [f"S{index:03d}" for index in range(80)]
    selected = select_buffered_portfolio(
        codes,
        previous_holdings={"S060"},
        target_size=50,
        retain_rank=15,
    )

    assert selected == codes[:50]


def test_buffer_retains_eligible_holding_before_filling_target() -> None:
    codes = [f"S{index:02d}" for index in range(1, 21)]
    selected = select_buffered_portfolio(
        codes,
        previous_holdings={"S02", "S03", "S04", "S12"},
        target_size=5,
        retain_rank=15,
    )

    assert selected == ["S01", "S02", "S03", "S04", "S12"]


def test_equal_weight_targets_preserve_rank_and_sum_to_one() -> None:
    targets = build_equal_weight_targets(
        pd.DataFrame(
            {
                "ts_code": ["C", "A", "B"],
                "score": [0.7, 0.9, 0.8],
                "direction": ["中性", "强", "偏强"],
            }
        ),
        top_n=2,
        previous_holdings=set(),
        retain_rank=1,
    )

    assert targets["ts_code"].tolist() == ["A", "B"]
    assert targets["target_weight"].tolist() == [0.5, 0.5]
    assert targets["target_weight"].sum() == 1.0
