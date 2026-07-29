from __future__ import annotations

import networkx as nx
import numpy as np
import pandas as pd

from src.stockpred.graph.config import GraphConfig
from src.stockpred.graph.features import (
    compute_all_graph_features,
    compute_crowding_features,
    compute_industry_momentum,
    compute_stock_relative_strength,
    compute_volume_price_feature,
)


def _inputs() -> tuple[nx.Graph, pd.DataFrame, pd.DataFrame]:
    codes = [f"{600000 + index:06d}.SH" for index in range(8)]
    universe = pd.DataFrame(
        {
            "ts_code": codes,
            "industry": ["A", "B"] * 4,
        }
    ).sample(frac=1.0, random_state=7)
    dates = pd.bdate_range("2025-09-01", periods=70).strftime("%Y%m%d")
    rows: list[dict[str, object]] = []
    for code_index, code in enumerate(codes):
        for date_index, date in enumerate(dates):
            close = 10.0 + code_index + date_index * (0.01 + code_index * 0.001)
            rows.append(
                {
                    "ts_code": code,
                    "trade_date": date,
                    "adj_close": close,
                    "close": close,
                    "vol": 1000.0 + date_index * (code_index + 1),
                    "amount": close * 1000.0,
                }
            )
    graph = nx.Graph()
    for code, industry in zip(codes, ["A", "B"] * 4, strict=True):
        graph.add_node(f"stock:{code}", node_type="Stock", ts_code=code)
        graph.add_node(f"industry:{industry}", node_type="Industry", name=industry)
        graph.add_edge(
            f"stock:{code}",
            f"industry:{industry}",
            edge_type="BELONGS_TO_INDUSTRY",
            weight=1.0,
        )
    return graph, universe, pd.DataFrame(rows)


def _all_features(
    graph: nx.Graph,
    universe: pd.DataFrame,
    prices: pd.DataFrame,
    trade_date: str,
) -> pd.DataFrame:
    return compute_all_graph_features(
        graph=graph,
        universe=universe,
        prices=prices,
        daily_basic=pd.DataFrame(),
        moneyflow=pd.DataFrame(),
        trade_date=trade_date,
        config=GraphConfig(),
        fina_df=pd.DataFrame(),
    )


def test_features_do_not_use_rows_after_eval_date() -> None:
    graph, universe, prices = _inputs()
    dates = sorted(prices["trade_date"].unique())
    cut_date = dates[-2]
    baseline = _all_features(graph, universe, prices[prices["trade_date"] <= cut_date], cut_date)
    with_future = prices.copy()
    with_future.loc[with_future["trade_date"] > cut_date, "adj_close"] *= 100.0
    actual = _all_features(graph, universe, with_future, cut_date)

    pd.testing.assert_frame_equal(baseline, actual)


def test_feature_output_has_stable_stock_order() -> None:
    graph, universe, prices = _inputs()
    result = _all_features(graph, universe, prices, str(prices["trade_date"].max()))

    assert result["ts_code"].tolist() == sorted(result["ts_code"].tolist())


def test_core_factor_formulas_and_missing_value_semantics() -> None:
    graph, universe, prices = _inputs()
    trade_date = str(prices["trade_date"].max())
    momentum = compute_industry_momentum(prices, universe, trade_date)
    relative = compute_stock_relative_strength(prices, universe, momentum)
    volume_price = compute_volume_price_feature(prices, universe)
    crowding = compute_crowding_features(
        pd.DataFrame(
            {
                "ts_code": universe["ts_code"].tolist(),
                "turnover_rate": np.arange(1, len(universe) + 1, dtype=float),
                "pe_ttm": [np.nan] + [10.0] * (len(universe) - 1),
            }
        ),
        universe,
    )

    assert {"momentum_5d", "momentum_20d", "momentum_60d"}.issubset(momentum)
    assert np.isfinite(relative["rel_strength_20d"]).all()
    assert np.isfinite(volume_price["volume_price_trend"]).all()
    missing_pe = crowding.loc[crowding["ts_code"] == universe.iloc[0]["ts_code"]]
    assert missing_pe["pe_percentile"].iloc[0] > 0.0
