"""A股图谱模块 - NetworkX 图谱构建器

构建股票-行业图、行业内相关性边、指数成分边。
"""

from __future__ import annotations

import pandas as pd
import networkx as nx

from src.stockpred.graph.config import DEFAULT_GRAPH_CONFIG, GraphConfig


def build_stock_industry_graph(universe: pd.DataFrame) -> nx.Graph:
    """构建股票-行业二部图

    Args:
        universe: 股票列表 DataFrame (ts_code, name, industry, list_date, list_status)

    Returns:
        NetworkX 图，包含 Stock 和 Industry 节点及 BELONGS_TO_INDUSTRY 边
    """
    G = nx.Graph()

    # 统计行业股票数量
    industry_counts = universe["industry"].value_counts().to_dict()

    # 添加 Industry 节点
    for industry_name, count in industry_counts.items():
        G.add_node(
            f"industry:{industry_name}",
            node_type="Industry",
            name=industry_name,
            stock_count=count,
        )

    # 添加 Stock 节点和 BELONGS_TO_INDUSTRY 边
    for _, row in universe.iterrows():
        ts_code = row["ts_code"]
        industry = row["industry"]

        G.add_node(
            f"stock:{ts_code}",
            node_type="Stock",
            ts_code=ts_code,
            name=row.get("name", ""),
            industry=industry,
            list_date=row.get("list_date"),
        )

        G.add_edge(
            f"stock:{ts_code}",
            f"industry:{industry}",
            edge_type="BELONGS_TO_INDUSTRY",
            weight=1.0,
        )

    return G


def compute_industry_correlations(
    prices: pd.DataFrame,
    universe: pd.DataFrame,
    config: GraphConfig = DEFAULT_GRAPH_CONFIG,
) -> pd.DataFrame:
    """计算行业内股票对的相关性

    只在同行业内计算，大幅降低计算复杂度。

    Args:
        prices: 日行情 DataFrame (ts_code, trade_date, close)
        universe: 股票列表 (ts_code, industry)
        config: 图谱配置

    Returns:
        相关性边 DataFrame (src, dst, corr_60d)
    """
    if prices.empty or universe.empty:
        return pd.DataFrame(columns=["src", "dst", "corr_60d"])

    # 计算收益率
    prices_sorted = prices.sort_values(["ts_code", "trade_date"])
    prices_sorted["return"] = prices_sorted.groupby("ts_code")["adj_close"].pct_change()

    # 构建行业映射
    code_to_industry = dict(zip(universe["ts_code"], universe["industry"]))

    # 按行业分组计算相关性
    edges = []
    for industry, group in prices_sorted.groupby(prices_sorted["ts_code"].map(code_to_industry)):
        if pd.isna(industry):
            continue

        # 构建收益率矩阵 (股票 x 日期)
        pivot = group.pivot_table(index="trade_date", columns="ts_code", values="return")

        if pivot.shape[1] < 2:
            continue

        # 计算相关性矩阵（仅 60d，corr_20d 从未使用）
        corr_60d = pivot.tail(config.corr_window_long).corr()

        # 提取上三角（避免重复边和自环）
        codes = corr_60d.columns.tolist()
        for i, code_a in enumerate(codes):
            for code_b in codes[i + 1 :]:
                c60 = corr_60d.loc[code_a, code_b]

                if pd.isna(c60):
                    continue
                if c60 < config.corr_threshold:
                    continue

                edges.append(
                    {
                        "src": f"stock:{code_a}",
                        "dst": f"stock:{code_b}",
                        "corr_60d": float(c60),
                    }
                )

    return pd.DataFrame(edges)


def add_correlation_edges(
    graph: nx.Graph,
    corr_edges: pd.DataFrame,
    trade_date: str,
) -> nx.Graph:
    """将相关性边添加到图中

    Args:
        graph: NetworkX 图
        corr_edges: 相关性边 DataFrame (src, dst, corr_60d)
        trade_date: 交易日期

    Returns:
        更新后的图
    """
    for _, row in corr_edges.iterrows():
        graph.add_edge(
            row["src"],
            row["dst"],
            edge_type="PEER_CORRELATED",
            weight=float(row["corr_60d"]),
            trade_date=trade_date,
            corr_60d=float(row["corr_60d"]),
        )
    return graph


def compute_industry_cross_correlations(
    prices: pd.DataFrame,
    universe: pd.DataFrame,
    config: GraphConfig = DEFAULT_GRAPH_CONFIG,
) -> pd.DataFrame:
    """计算行业间相关性

    基于每个行业的等权平均日收益率，计算行业对之间的 60d 滚动相关系数，
    筛选超过阈值的行业对作为 INDUSTRY_CORRELATED 边。

    Args:
        prices: 日行情 DataFrame (ts_code, trade_date, close, return 等)，需已排序
        universe: 股票列表 (ts_code, industry)
        config: 图谱配置

    Returns:
        行业间相关性边 DataFrame (industry_a, industry_b, corr_60d)
    """
    code_map = dict(zip(universe["ts_code"], universe["industry"]))
    prices = prices.copy()
    prices["industry"] = prices["ts_code"].map(code_map)
    prices = prices[prices["industry"].notna()]

    if prices.empty:
        return pd.DataFrame()

    # 先计算每只股票的日收益率，再按行业求均值（避免股价量级差异主导结果）
    prices = prices.sort_values(["ts_code", "trade_date"])
    prices["daily_ret"] = prices.groupby("ts_code")["adj_close"].pct_change()
    ind_returns = prices.groupby(["trade_date", "industry"])["daily_ret"].mean().unstack("industry")

    if ind_returns.shape[1] < 2:
        return pd.DataFrame()

    # 取最近 60 天的窗口数据（corr_20d 从未使用，已移除）
    n_long = config.corr_window_long
    recent_long = ind_returns.tail(n_long)

    corr_60 = recent_long.corr()

    # 提取上三角，筛选超过阈值的行业对
    industries = corr_60.columns.tolist()
    edges = []
    for i, ind_a in enumerate(industries):
        for ind_b in industries[i + 1 :]:
            c60 = corr_60.loc[ind_a, ind_b]
            if pd.isna(c60):
                continue
            if abs(c60) < config.industry_corr_threshold:
                continue
            edges.append(
                {
                    "industry_a": ind_a,
                    "industry_b": ind_b,
                    "corr_60d": float(c60),
                }
            )

    return pd.DataFrame(edges)


def add_industry_correlation_edges(
    graph: nx.Graph,
    industry_corr_edges: pd.DataFrame,
    trade_date: str,
) -> nx.Graph:
    """将行业间相关性边添加到图中

    Args:
        graph: NetworkX 图
        industry_corr_edges: 行业间相关性 DataFrame (industry_a, industry_b, corr_60d)
        trade_date: 交易日期

    Returns:
        更新后的图
    """
    for _, row in industry_corr_edges.iterrows():
        graph.add_edge(
            f"industry:{row['industry_a']}",
            f"industry:{row['industry_b']}",
            edge_type="INDUSTRY_CORRELATED",
            weight=abs(float(row["corr_60d"])),
            trade_date=trade_date,
            corr_60d=float(row["corr_60d"]),
        )
    return graph


def add_index_edges(
    graph: nx.Graph,
    index_weights: pd.DataFrame,
    index_code: str = "000300.SH",
) -> nx.Graph:
    """添加指数成分边

    Args:
        graph: NetworkX 图
        index_weights: 指数权重 DataFrame (con_code, weight)
        index_code: 指数代码

    Returns:
        更新后的图
    """
    if index_weights.empty:
        return graph

    # 添加 Index 节点
    graph.add_node(
        f"index:{index_code}",
        node_type="Index",
        ts_code=index_code,
    )

    # 添加 PART_OF_INDEX 边
    for _, row in index_weights.iterrows():
        stock_node = f"stock:{row['con_code']}"
        if stock_node in graph:
            graph.add_edge(
                stock_node,
                f"index:{index_code}",
                edge_type="PART_OF_INDEX",
                weight=float(row["weight"]) if "weight" in row else 1.0,
            )

    return graph


def build_daily_graph(
    universe: pd.DataFrame,
    prices: pd.DataFrame,
    index_weights: pd.DataFrame | None = None,
    trade_date: str = "",
    config: GraphConfig = DEFAULT_GRAPH_CONFIG,
) -> tuple[nx.Graph, pd.DataFrame, pd.DataFrame]:
    """构建每日完整图谱

    Args:
        universe: 股票列表
        prices: 日行情
        index_weights: 指数权重（可选）
        trade_date: 交易日期
        config: 图谱配置

    Returns:
        (NetworkX 图, 行业内相关性边 DataFrame, 行业间相关性边 DataFrame)
    """
    print(f"[builder] Building graph for {trade_date}...")

    # 1. 构建股票-行业图
    G = build_stock_industry_graph(universe)
    print(f"[builder] Added {G.number_of_nodes()} nodes, {G.number_of_edges()} industry edges")

    # 2. 计算行业内相关性并添加边
    corr_edges = compute_industry_correlations(prices, universe, config)
    G = add_correlation_edges(G, corr_edges, trade_date)
    print(f"[builder] Added {len(corr_edges)} stock correlation edges")

    # 3. 计算行业间相关性并添加边
    ind_corr_edges = compute_industry_cross_correlations(prices, universe, config)
    G = add_industry_correlation_edges(G, ind_corr_edges, trade_date)
    print(f"[builder] Added {len(ind_corr_edges)} industry correlation edges")

    # 4. 添加指数成分边
    if index_weights is not None and not index_weights.empty:
        G = add_index_edges(G, index_weights)
        print("[builder] Added index edges")

    print(f"[builder] Final graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
    return G, corr_edges, ind_corr_edges
