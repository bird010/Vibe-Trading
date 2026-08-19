from __future__ import annotations

import pandas as pd

from src.stockpred.graph import config as graph_config
from src.stockpred.graph.builder import build_daily_graph, build_stock_industry_graph
from src.stockpred.graph.config import GraphConfig


def _universe() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000002.SZ", "600000.SH"],
            "name": ["A", "B", "C"],
            "industry": ["银行", "地产", "银行"],
            "list_date": ["19910101", "19920101", "19930101"],
        }
    )


def test_stock_industry_graph_preserves_node_and_edge_contract() -> None:
    graph = build_stock_industry_graph(_universe())

    assert graph.nodes["stock:000001.SZ"]["node_type"] == "Stock"
    assert graph.nodes["industry:银行"]["stock_count"] == 2
    assert graph.edges["stock:000001.SZ", "industry:银行"] == {
        "edge_type": "BELONGS_TO_INDUSTRY",
        "weight": 1.0,
    }


def test_daily_graph_adds_index_membership_without_hidden_io() -> None:
    graph, stock_edges, industry_edges = build_daily_graph(
        universe=_universe(),
        prices=pd.DataFrame(columns=["ts_code", "trade_date", "adj_close"]),
        index_weights=pd.DataFrame(
            {
                "index_code": ["000300.SH"],
                "con_code": ["000001.SZ"],
                "weight": [0.5],
            }
        ),
        trade_date="20260105",
        config=GraphConfig(),
    )

    assert stock_edges.empty
    assert industry_edges.empty
    assert graph.edges["stock:000001.SZ", "index:000300.SH"] == {
        "edge_type": "PART_OF_INDEX",
        "weight": 0.5,
    }


def test_graph_config_has_no_stockpred_filesystem_paths() -> None:
    forbidden = {
        "PROJECT_ROOT",
        "LANCE_MARKET_CORE",
        "LANCE_SOURCE_RAW",
        "LANCE_GRAPH",
        "REPORTS_DIR",
    }

    assert forbidden.isdisjoint(vars(graph_config))
