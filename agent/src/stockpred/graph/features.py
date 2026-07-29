"""A股图谱模块 - 图特征计算

基于图结构和行情数据计算行业动量、个股相对强度、扩散度、资金流、拥挤度等特征。
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd
import networkx as nx

from src.stockpred.graph.config import DEFAULT_GRAPH_CONFIG, GraphConfig

# 特征缓存（模块级）
_features_cache: dict[str, pd.DataFrame] = {}


def _precompute_latest_returns(
    prices: pd.DataFrame,
    windows: tuple[int, ...] = (5, 20, 60),
    include_vol_ratio: bool = True,
    short_window: int = 5,
    long_window: int = 20,
) -> pd.DataFrame:
    """一次性预计算各窗口收益率 + 量比，供多个特征函数复用

    避免 compute_industry_momentum / relative_strength / diffusion /
    reversal / volume_price 各自独立 sort + pct_change 的重复开销。

    Returns:
        DataFrame: ts_code, ret_5d, ret_20d, ret_60d, vol_ratio (latest date only)
    """
    prices_sorted = prices.sort_values(["ts_code", "trade_date"])

    for w in windows:
        col = f"ret_{w}d"
        if col not in prices_sorted.columns:
            prices_sorted[col] = prices_sorted.groupby("ts_code")["adj_close"].pct_change(w)

    if include_vol_ratio:
        vol_col = "vol" if "vol" in prices_sorted.columns else ("volume" if "volume" in prices_sorted.columns else None)
        if vol_col:
            prices_sorted["_vol_ma_s"] = prices_sorted.groupby("ts_code")[vol_col].transform(
                lambda x: x.rolling(short_window, min_periods=1).mean()
            )
            prices_sorted["_vol_ma_l"] = prices_sorted.groupby("ts_code")[vol_col].transform(
                lambda x: x.rolling(long_window, min_periods=5).mean()
            )

    latest_date = prices_sorted["trade_date"].max()
    latest = prices_sorted[prices_sorted["trade_date"] == latest_date].copy()

    result = latest[["ts_code"] + [f"ret_{w}d" for w in windows]].copy()

    if include_vol_ratio:
        if vol_col:
            vol_ratio = latest["_vol_ma_s"] / latest["_vol_ma_l"].replace(0, np.nan)
            result["vol_ratio"] = vol_ratio.fillna(1.0).clip(0.3, 3.0).values
        else:
            result["vol_ratio"] = 1.0

    return result


def compute_industry_momentum(
    prices: pd.DataFrame,
    universe: pd.DataFrame,
    trade_date: str,
    windows: tuple[int, ...] = (5, 20, 60),
    latest_returns: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """计算行业动量

    行业动量 = 行业内所有股票 N 日收益率的中位数

    Args:
        latest_returns: 预计算的收益率 (from _precompute_latest_returns)

    Returns:
        DataFrame: industry, momentum_5d, momentum_20d, momentum_60d, rank, stock_count
    """
    if prices.empty or universe.empty:
        return pd.DataFrame()

    code_to_industry = dict(zip(universe["ts_code"], universe["industry"]))

    if latest_returns is not None and all(f"ret_{w}d" in latest_returns.columns for w in windows):
        latest = latest_returns.copy()
    else:
        prices_sorted = prices.sort_values(["ts_code", "trade_date"])
        for w in windows:
            prices_sorted[f"ret_{w}d"] = prices_sorted.groupby("ts_code")["adj_close"].pct_change(w)
        latest_date = prices_sorted["trade_date"].max()
        latest = prices_sorted[prices_sorted["trade_date"] == latest_date]

    latest["industry"] = latest["ts_code"].map(code_to_industry)
    latest = latest[latest["industry"].notna()]

    agg_dict = {f"ret_{w}d": "median" for w in windows}
    agg_dict["ts_code"] = "count"
    industry_stats = latest.groupby("industry").agg(agg_dict).rename(columns={"ts_code": "stock_count"})

    # 重命名 ret_Xd → momentum_Xd（从列名提取窗口值，避免闭包变量捕获问题）
    new_cols = []
    for c in industry_stats.columns:
        m = re.match(r"ret_(\d+)d", c)
        if m:
            new_cols.append(f"momentum_{m.group(1)}d")
        else:
            new_cols.append(c)
    industry_stats.columns = new_cols

    # 计算排名（按 20d 动量降序）
    industry_stats = industry_stats.sort_values("momentum_20d", ascending=False)
    industry_stats["rank"] = range(1, len(industry_stats) + 1)
    industry_stats = industry_stats.reset_index()

    return industry_stats


def compute_stock_relative_strength(
    prices: pd.DataFrame,
    universe: pd.DataFrame,
    industry_momentum: pd.DataFrame,
    window: int = 20,
    latest_returns: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """计算个股相对行业强度

    个股相对强度 = 个股 N 日收益 - 行业中位数收益

    Args:
        latest_returns: 预计算的收益率 (from _precompute_latest_returns)

    Returns:
        DataFrame: ts_code, industry, return_20d, rel_strength_20d, rank_in_industry
    """
    if prices.empty:
        return pd.DataFrame()

    code_to_industry = dict(zip(universe["ts_code"], universe["industry"]))
    industry_median = dict(zip(industry_momentum["industry"], industry_momentum[f"momentum_{window}d"]))

    ret_col = f"ret_{window}d"
    if latest_returns is not None and ret_col in latest_returns.columns:
        latest = latest_returns[["ts_code", ret_col]].copy()
    else:
        prices_sorted = prices.sort_values(["ts_code", "trade_date"])
        prices_sorted[ret_col] = prices_sorted.groupby("ts_code")["adj_close"].pct_change(window)
        latest_date = prices_sorted["trade_date"].max()
        latest = prices_sorted[prices_sorted["trade_date"] == latest_date][["ts_code", ret_col]].copy()

    latest["industry"] = latest["ts_code"].map(code_to_industry)
    latest = latest[latest["industry"].notna()]

    latest["industry_median"] = latest["industry"].map(industry_median)
    latest["rel_strength"] = latest[f"ret_{window}d"] - latest["industry_median"]

    # 行业内排名
    latest["rank_in_industry"] = (
        latest.groupby("industry")[f"ret_{window}d"].rank(ascending=False, method="min").fillna(9999).astype(int)
    )

    result = latest[["ts_code", "industry", f"ret_{window}d", "rel_strength", "rank_in_industry"]].rename(
        columns={f"ret_{window}d": "return_20d", "rel_strength": "rel_strength_20d"}
    )
    result["return_20d"] = result["return_20d"].fillna(0.0)
    result["rel_strength_20d"] = result["rel_strength_20d"].fillna(0.0)
    return result


def compute_diffusion_features(
    prices: pd.DataFrame,
    universe: pd.DataFrame,
    trade_date: str,
    window: int = 20,
    leader_top_pct: float = 0.10,
    latest_returns: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """计算扩散度和龙头组动量

    扩散度 = 行业内 20d 收益 > 0 的股票比例
    龙头组动量 = 行业内涨幅前 10% 股票的平均收益

    Args:
        latest_returns: 预计算的收益率 (from _precompute_latest_returns)

    Returns:
        DataFrame: industry, diffusion_score, leader_momentum
    """
    if prices.empty or universe.empty:
        return pd.DataFrame()

    code_to_industry = dict(zip(universe["ts_code"], universe["industry"]))

    ret_col = f"ret_{window}d"
    if latest_returns is not None and ret_col in latest_returns.columns:
        latest = latest_returns[["ts_code", ret_col]].copy()
    else:
        prices_sorted = prices.sort_values(["ts_code", "trade_date"])
        prices_sorted[ret_col] = prices_sorted.groupby("ts_code")["adj_close"].pct_change(window)
        latest_date = prices_sorted["trade_date"].max()
        latest = prices_sorted[prices_sorted["trade_date"] == latest_date].copy()

    latest["industry"] = latest["ts_code"].map(code_to_industry)
    latest = latest[latest["industry"].notna()]

    results = []
    for industry, group in latest.groupby("industry"):
        valid = group[group[f"ret_{window}d"].notna()]
        if len(valid) == 0:
            continue

        # 扩散度
        positive_count = (valid[f"ret_{window}d"] > 0).sum()
        diffusion = positive_count / len(valid)

        # 龙头组（前 leader_top_pct）
        n_leaders = max(1, int(len(valid) * leader_top_pct))
        leaders = valid.nlargest(n_leaders, f"ret_{window}d")
        leader_momentum = leaders[f"ret_{window}d"].mean()

        results.append(
            {
                "industry": industry,
                "diffusion_score": diffusion,
                "leader_momentum": leader_momentum,
            }
        )

    return pd.DataFrame(results)


def compute_moneyflow_features(
    moneyflow: pd.DataFrame,
    universe: pd.DataFrame,
    window: int = 5,
) -> pd.DataFrame:
    """计算资金流特征

    大单净流入 = (buy_elg - sell_elg) 的 N 日累计
    行业资金扩散 = 行业内大单净流入为正的股票比例

    Returns:
        DataFrame: ts_code, net_big_inflow_5d, industry_inflow_diffusion
    """
    if moneyflow.empty or universe.empty:
        return pd.DataFrame()

    code_to_industry = dict(zip(universe["ts_code"], universe["industry"]))

    mf = moneyflow.copy()
    mf["net_big"] = mf["buy_elg_amount"].fillna(0) - mf["sell_elg_amount"].fillna(0)

    # N 日累计
    mf_sorted = mf.sort_values(["ts_code", "trade_date"])
    mf_sorted["net_big_cum"] = mf_sorted.groupby("ts_code")["net_big"].transform(
        lambda x: x.rolling(window, min_periods=1).sum()
    )

    latest_date = mf_sorted["trade_date"].max()
    latest = mf_sorted[mf_sorted["trade_date"] == latest_date][["ts_code", "net_big_cum"]].copy()
    latest = latest.rename(columns={"net_big_cum": f"net_big_inflow_{window}d"})

    # 行业资金扩散
    latest["industry"] = latest["ts_code"].map(code_to_industry)
    industry_diffusion = (
        latest.groupby("industry")
        .apply(lambda g: (g[f"net_big_inflow_{window}d"] > 0).sum() / len(g) if len(g) > 0 else 0)
        .reset_index(name="industry_inflow_diffusion")
    )

    latest = latest.merge(industry_diffusion, on="industry", how="left")
    return latest[["ts_code", f"net_big_inflow_{window}d", "industry_inflow_diffusion"]]


def compute_crowding_features(
    daily_basic: pd.DataFrame,
    universe: pd.DataFrame,
) -> pd.DataFrame:
    """计算拥挤度特征

    拥挤度 = 换手率行业内分位 * 0.5 + PE 行业内分位 * 0.5

    Returns:
        DataFrame: ts_code, turnover_percentile, pe_percentile, crowding_score
    """
    if daily_basic.empty or universe.empty:
        return pd.DataFrame()

    code_to_industry = dict(zip(universe["ts_code"], universe["industry"]))

    df = daily_basic.copy()
    df["industry"] = df["ts_code"].map(code_to_industry)
    df = df[df["industry"].notna()]

    # 行业内分位（0-1，1=最高）
    df["turnover_percentile"] = df.groupby("industry")["turnover_rate"].rank(pct=True)
    df["pe_percentile"] = df.groupby("industry")["pe_ttm"].rank(pct=True, na_option="bottom")

    df["crowding_score"] = df["turnover_percentile"] * 0.5 + df["pe_percentile"] * 0.5

    return df[["ts_code", "turnover_percentile", "pe_percentile", "crowding_score"]]


def compute_reversal_feature(
    prices: pd.DataFrame,
    universe: pd.DataFrame,
    window: int = 5,
    latest_returns: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """计算短期反转因子

    短期反转 = 负的 N 日收益率（跌得越多，预期反弹越强）。
    A 股短期均值回复效应显著：5日跌幅大的股票在接下来5日往往反弹。

    Args:
        latest_returns: 预计算的收益率 (from _precompute_latest_returns)

    Returns:
        DataFrame: ts_code, reversal_5d (正值=预期反弹)
    """
    if prices.empty:
        return pd.DataFrame()

    ret_col = f"ret_{window}d"
    if latest_returns is not None and ret_col in latest_returns.columns:
        latest = latest_returns[["ts_code", ret_col]].copy()
    else:
        prices_sorted = prices.sort_values(["ts_code", "trade_date"])
        prices_sorted[ret_col] = prices_sorted.groupby("ts_code")["adj_close"].pct_change(window)
        latest_date = prices_sorted["trade_date"].max()
        latest = prices_sorted[prices_sorted["trade_date"] == latest_date][["ts_code", ret_col]].copy()

    latest["reversal_5d"] = -latest[ret_col]  # 取反：跌得多 → 反转分高
    latest["reversal_5d"] = latest["reversal_5d"].fillna(0.0)
    return latest[["ts_code", "reversal_5d"]]


def compute_volume_price_feature(
    prices: pd.DataFrame,
    universe: pd.DataFrame,
    short_window: int = 5,
    long_window: int = 20,
    latest_returns: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """计算量价趋势因子

    量比 = 近5日均成交量 / 近20日均成交量
    vpt = (量比 - 1) * 5日收益率
      - 放量上涨 (vpt > 0): 趋势确认，看涨
      - 缩量上涨 (vpt < 0): 上涨动能不足，潜在反转
      - 放量下跌 (vpt < 0): 恐慌性抛售
      - 缩量下跌 (vpt > 0): 卖压枯竭，可能见底

    理论依据: 量价关系是技术分析的基石之一 (Wyckoff Method)。
    成交量确认价格趋势的持续性。

    Args:
        latest_returns: 预计算的收益率+量比 (from _precompute_latest_returns)

    Returns:
        DataFrame: ts_code, volume_price_trend
    """
    if prices.empty:
        return pd.DataFrame()

    if (
        latest_returns is not None
        and f"ret_{short_window}d" in latest_returns.columns
        and "vol_ratio" in latest_returns.columns
    ):
        latest = latest_returns[["ts_code", f"ret_{short_window}d", "vol_ratio"]].copy()
    else:
        prices_sorted = prices.sort_values(["ts_code", "trade_date"])
        prices_sorted[f"ret_{short_window}d"] = prices_sorted.groupby("ts_code")["adj_close"].pct_change(short_window)
        if "vol" in prices_sorted.columns:
            vol_col = "vol"
        elif "volume" in prices_sorted.columns:
            vol_col = "volume"
        else:
            latest_date = prices_sorted["trade_date"].max()
            latest = prices_sorted[prices_sorted["trade_date"] == latest_date][["ts_code"]].copy()
            latest["volume_price_trend"] = 0.0
            return latest
        prices_sorted["vol_ma_short"] = prices_sorted.groupby("ts_code")[vol_col].transform(
            lambda x: x.rolling(short_window, min_periods=1).mean()
        )
        prices_sorted["vol_ma_long"] = prices_sorted.groupby("ts_code")[vol_col].transform(
            lambda x: x.rolling(long_window, min_periods=5).mean()
        )
        latest_date = prices_sorted["trade_date"].max()
        latest = prices_sorted[prices_sorted["trade_date"] == latest_date].copy()
        vol_ratio = latest["vol_ma_short"] / latest["vol_ma_long"].replace(0, np.nan)
        latest["vol_ratio"] = vol_ratio.fillna(1.0).clip(0.3, 3.0)
        latest[f"ret_{short_window}d"] = latest[f"ret_{short_window}d"]

    vol_ratio = latest["vol_ratio"]
    ret = latest[f"ret_{short_window}d"].fillna(0.0)
    latest["volume_price_trend"] = (vol_ratio - 1.0) * ret

    return latest[["ts_code", "volume_price_trend"]]


def compute_graph_centrality_features(
    graph: nx.Graph,
    universe: pd.DataFrame,
    industry_momentum: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """计算图中心度特征 + INDUSTRY_CORRELATED 动量溢出（向量化优化版本）

    两个维度：
    1. degree_centrality: 使用 networkx 内置 degree_centrality
    2. industry_corr_momentum: 通过 INDUSTRY_CORRELATED 边加权的关联行业动量

    Returns:
        DataFrame: ts_code, degree_centrality, industry_corr_momentum
    """
    if graph.number_of_nodes() == 0:
        return pd.DataFrame(columns=["ts_code", "degree_centrality", "industry_corr_momentum"])

    stock_nodes = {n: d for n, d in graph.nodes(data=True) if d.get("node_type") == "Stock"}
    if not stock_nodes:
        return pd.DataFrame(columns=["ts_code", "degree_centrality", "industry_corr_momentum"])

    code_to_industry = dict(zip(universe["ts_code"], universe["industry"]))

    # 1. 度中心度 — 使用 networkx 内置算法（C 实现，比 Python 循环快得多）
    nx_centrality = nx.degree_centrality(graph)
    node_to_code = {n: d.get("ts_code", "") for n, d in stock_nodes.items() if d.get("ts_code", "")}
    centrality_map = {node_to_code[n]: nx_centrality.get(n, 0.0) for n in node_to_code}
    df = pd.DataFrame(
        [{"ts_code": code, "degree_centrality": centrality_map.get(code, 0.0)} for code in node_to_code.values()]
    )

    if df.empty:
        return pd.DataFrame(columns=["ts_code", "degree_centrality", "industry_corr_momentum"])

    # 2. INDUSTRY_CORRELATED 动量溢出（向量化）
    ind_mom_map: dict[str, float] = {}
    if industry_momentum is not None and not industry_momentum.empty:
        ind_mom_map = dict(zip(industry_momentum["industry"], industry_momentum["momentum_20d"].fillna(0.0)))

    # 提取行业间相关性边到 DataFrame
    industry_edges: list[dict] = []
    for n, d in graph.nodes(data=True):
        if d.get("node_type") != "Industry":
            continue
        ind_name = d.get("name", "")
        for neighbor in graph.neighbors(n):
            edge = graph.edges[n, neighbor]
            if edge.get("edge_type") != "INDUSTRY_CORRELATED":
                continue
            nb_name = graph.nodes.get(neighbor, {}).get("name", "")
            w = abs(edge.get("weight", 0.5))
            industry_edges.append({"industry": ind_name, "nb_industry": nb_name, "weight": w})

    if industry_edges and ind_mom_map:
        ie_df = pd.DataFrame(industry_edges)
        ie_df["nb_momentum"] = ie_df["nb_industry"].map(ind_mom_map).fillna(0.0)
        ie_df["w_mom"] = ie_df["weight"] * ie_df["nb_momentum"]
        ind_weighted = ie_df.groupby("industry").agg(
            w_sum=("w_mom", "sum"),
            wt_sum=("weight", "sum"),
        )
        ind_weighted["corr_mom"] = np.where(
            ind_weighted["wt_sum"] > 0,
            ind_weighted["w_sum"] / ind_weighted["wt_sum"],
            0.0,
        )
        corr_mom_series = ind_weighted["corr_mom"]
    else:
        corr_mom_series = pd.Series(dtype=float)

    # 映射到个股
    df["_industry"] = df["ts_code"].map(code_to_industry)
    if not corr_mom_series.empty:
        df["industry_corr_momentum"] = df["_industry"].map(corr_mom_series).fillna(0.0)
    else:
        df["industry_corr_momentum"] = 0.0
    df = df.drop(columns=["_industry"])

    return df


def compute_volatility_features(
    prices: pd.DataFrame,
    universe: pd.DataFrame,
    windows: tuple[int, ...] = (20, 60),
) -> pd.DataFrame:
    """计算个股历史波动率（收益率标准差）

    使用各窗口收益率的标准差衡量历史波动率。

    Returns:
        DataFrame: ts_code, volatility_20d, volatility_60d, volatility_ratio
    """
    if prices.empty or universe.empty:
        return pd.DataFrame()

    prices_sorted = prices.sort_values(["ts_code", "trade_date"]).copy()

    # 计算日收益率
    prices_sorted["daily_ret"] = prices_sorted.groupby("ts_code")["adj_close"].pct_change()

    # 计算各窗口滚动标准差
    for w in windows:
        prices_sorted[f"vol_{w}d"] = prices_sorted.groupby("ts_code")["daily_ret"].transform(
            lambda x: x.rolling(w, min_periods=max(2, w // 2)).std()
        )

    # 取最新日期
    latest_date = prices_sorted["trade_date"].max()
    latest = prices_sorted[prices_sorted["trade_date"] == latest_date].copy()

    result = latest[["ts_code"]].copy()
    for w in windows:
        col = f"volatility_{w}d"
        result[col] = latest[f"vol_{w}d"].fillna(0.0).values

    # 计算波动率比值（短期/长期，>1 表示近期波动加大）
    if len(windows) >= 2:
        short_col = f"volatility_{windows[0]}d"
        long_col = f"volatility_{windows[1]}d"
        result["volatility_ratio"] = (
            (result[short_col] / result[long_col].replace(0, np.nan)).fillna(1.0).clip(0.1, 10.0)
        )

    return result


def compute_beta_features(
    prices: pd.DataFrame,
    universe: pd.DataFrame,
    benchmark_code: str = "000300.SH",
    window: int = 60,
) -> pd.DataFrame:
    """计算个股相对行业的 Beta

    Beta = Cov(stock_ret, industry_ret) / Var(industry_ret)
    行业收益率使用行业内股票收益率的中位数。

    Returns:
        DataFrame: ts_code, beta_to_industry, beta_category
    """
    if prices.empty or universe.empty:
        return pd.DataFrame()

    code_to_industry = dict(zip(universe["ts_code"], universe["industry"]))

    prices_sorted = prices.sort_values(["ts_code", "trade_date"]).copy()
    prices_sorted["daily_ret"] = prices_sorted.groupby("ts_code")["adj_close"].pct_change()

    # 只保留在 universe 中的股票
    prices_sorted["industry"] = prices_sorted["ts_code"].map(code_to_industry)
    prices_sorted = prices_sorted[prices_sorted["industry"].notna()]

    if prices_sorted.empty:
        return pd.DataFrame()

    # 计算行业收益率中位数（按日期+行业分组）
    industry_ret = (
        prices_sorted.groupby(["trade_date", "industry"])["daily_ret"]
        .median()
        .reset_index()
        .rename(columns={"daily_ret": "industry_ret"})
    )

    # 合并行业收益率
    prices_sorted = prices_sorted.merge(industry_ret, on=["trade_date", "industry"], how="left")

    # 取最近 window 天数据
    all_dates = sorted(prices_sorted["trade_date"].unique())
    recent_dates = all_dates[-window:] if len(all_dates) > window else all_dates
    recent = prices_sorted[prices_sorted["trade_date"].isin(recent_dates)]

    # 计算 Beta
    results: list[dict] = []
    for ts_code, group in recent.groupby("ts_code"):
        if len(group) < max(5, window // 3):
            results.append({"ts_code": ts_code, "beta_to_industry": 1.0})
            continue

        stock_ret = group["daily_ret"].values
        ind_ret = group["industry_ret"].values

        # 去除 NaN
        valid = ~(np.isnan(stock_ret) | np.isnan(ind_ret))
        stock_ret = stock_ret[valid]
        ind_ret = ind_ret[valid]

        if len(stock_ret) < 5:
            results.append({"ts_code": ts_code, "beta_to_industry": 1.0})
            continue

        cov = np.cov(stock_ret, ind_ret)[0, 1]
        var = np.var(ind_ret, ddof=1)
        if var < 1e-12:
            beta = 1.0
        else:
            beta = cov / var

        beta = max(0.0, min(3.0, beta))
        results.append({"ts_code": ts_code, "beta_to_industry": beta})

    df = pd.DataFrame(results)

    # Beta 分类
    def _categorize(b: float) -> str:
        if b > 1.3:
            return "high"
        elif b < 0.7:
            return "low"
        else:
            return "normal"

    df["beta_category"] = df["beta_to_industry"].apply(_categorize)
    return df


def compute_drawdown_features(
    prices: pd.DataFrame,
    universe: pd.DataFrame,
    window: int = 60,
) -> pd.DataFrame:
    """计算窗口内真正的最大回撤

    近 window 日内，累计权益曲线的峰谷最大跌幅：
    max_drawdown = min over t in window: (price[t] - running_peak[t]) / running_peak[t]
    其中 running_peak[t] = max(price[0..t])

    这不会因价格修复而抹掉历史峰谷回撤。

    Returns:
        DataFrame: ts_code, max_drawdown_60d, current_drawdown, drawdown_ratio
    """
    if prices.empty or universe.empty:
        return pd.DataFrame()

    prices_sorted = prices.sort_values(["ts_code", "trade_date"]).copy()

    # 累计最高价（running peak）
    prices_sorted["rolling_max"] = prices_sorted.groupby("ts_code")["adj_close"].transform(lambda x: x.cummax())

    # 每个时点的回撤
    prices_sorted["drawdown"] = (prices_sorted["adj_close"] - prices_sorted["rolling_max"]) / prices_sorted[
        "rolling_max"
    ]

    # 窗口内最大回撤（取最近 window 日内 drawdown 的最小值）
    prices_sorted["max_dd_window"] = prices_sorted.groupby("ts_code")["drawdown"].transform(
        lambda x: x.rolling(window, min_periods=1).min()
    )

    # 取最新日期
    latest_date = prices_sorted["trade_date"].max()
    latest = prices_sorted[prices_sorted["trade_date"] == latest_date].copy()

    result = latest[["ts_code"]].copy()
    # 窗口最大回撤（负值，例如 -0.15 表示 15% 最大回撤）
    result["max_drawdown_60d"] = latest["max_dd_window"].fillna(0.0).values
    # 当前回撤（当前价相对历史峰值）
    result["current_drawdown"] = latest["drawdown"].fillna(0.0).values

    # drawdown_ratio = 当前回撤 / 窗口最大回撤（比值越接近1说明当前就是最大回撤点）
    result["drawdown_ratio"] = (
        (result["current_drawdown"] / result["max_drawdown_60d"].replace(0, np.nan)).fillna(1.0).clip(0.0, 5.0)
    )

    return result


def compute_low_volatility_factor(
    prices: pd.DataFrame,
    universe: pd.DataFrame,
    window: int = 20,
    latest_returns: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """计算低波动异象因子 (Ang et al. 2006)

    低波动股票长期跑赢高波动股票（低波动异象）。
    low_vol_signal = -volatility_20d（低波动→正信号），
    用全市场百分位排名归一化到 [0, 1]。

    Returns:
        DataFrame: ts_code, low_volatility_signal
    """
    if prices.empty or universe.empty:
        return pd.DataFrame()

    prices_sorted = prices.sort_values(["ts_code", "trade_date"]).copy()

    # 计算日收益率
    prices_sorted["daily_ret"] = prices_sorted.groupby("ts_code")["adj_close"].pct_change()

    # 计算滚动波动率
    prices_sorted["vol_w"] = prices_sorted.groupby("ts_code")["daily_ret"].transform(
        lambda x: x.rolling(window, min_periods=max(2, window // 2)).std()
    )

    # 取最新日期
    latest_date = prices_sorted["trade_date"].max()
    latest = prices_sorted[prices_sorted["trade_date"] == latest_date].copy()

    result = latest[["ts_code"]].copy()
    # 缺失波动率填为中位数（中性化），而非 0.0，避免新股/停牌/数据不足标的
    # 获得虚假的低波动信号（Ang et al. 2006 的“低波动异象”依赖真实波动率差异）
    vol_values = latest["vol_w"].values.astype(float)
    valid_mask = ~np.isnan(vol_values)
    if valid_mask.any():
        median_vol = float(np.nanmedian(vol_values))
    else:
        median_vol = 0.0
    result["volatility"] = np.where(valid_mask, vol_values, median_vol)
    # 标记数据不足的股票（用于下游过滤）
    result["_vol_data_sufficient"] = valid_mask

    # low_vol_signal = -volatility（低波动→正信号）
    neg_vol = -result["volatility"]
    # 全市场百分位排名归一化到 [0, 1]
    result["low_volatility_signal"] = neg_vol.rank(pct=True).values

    return result[["ts_code", "low_volatility_signal"]]


def compute_liquidity_factor(
    prices: pd.DataFrame,
    universe: pd.DataFrame,
    window: int = 20,
) -> pd.DataFrame:
    """计算 Amihud 非流动性因子

    Amihud 非流动性指标: abs(pct_chg) / amount（每单位成交额的价格冲击）。
    非流动性越高→流动性越差→正信号（流动性溢价）。
    计算 window 日均值，用全市场百分位排名归一化到 [0, 1]。

    Returns:
        DataFrame: ts_code, liquidity_signal
    """
    if prices.empty or universe.empty:
        return pd.DataFrame()

    df = prices.sort_values(["ts_code", "trade_date"]).copy()

    # 使用 pct_chg 列（如存在）或从 adj_close 计算
    if "pct_chg" in df.columns:
        df["abs_ret"] = df["pct_chg"].abs()
    else:
        df["abs_ret"] = df.groupby("ts_code")["adj_close"].pct_change().abs() * 100

    # 使用 amount 列（成交额），如不存在则用 vol 代替
    amt_col = "amount" if "amount" in df.columns else ("vol" if "vol" in df.columns else None)
    if amt_col is None:
        # 无成交额数据，返回空
        return pd.DataFrame()

    # Amihud 非流动性: abs_ret / amount（避免除零）
    df["amihud"] = df["abs_ret"] / df[amt_col].replace(0, np.nan)

    # 计算 window 日均值
    df["amihud_ma"] = df.groupby("ts_code")["amihud"].transform(
        lambda x: x.rolling(window, min_periods=max(1, window // 2)).mean()
    )

    # 取最新日期
    latest_date = df["trade_date"].max()
    latest = df[df["trade_date"] == latest_date].copy()

    result = latest[["ts_code"]].copy()
    result["illiquidity"] = latest["amihud_ma"].fillna(0.0).values

    # 全市场百分位排名归一化到 [0, 1]（非流动性越高→信号越强）
    result["liquidity_signal"] = result["illiquidity"].rank(pct=True).values

    return result[["ts_code", "liquidity_signal"]]


def compute_index_membership_features(
    graph: nx.Graph,
    universe: pd.DataFrame,
) -> pd.DataFrame:
    """计算指数成分特征

    利用 PART_OF_INDEX 边判断股票是否为指数成分股。
    指数成分股特征：流动性好、机构偏好、波动较低。

    Returns:
        DataFrame: ts_code, is_index_component, index_weight
    """
    if graph.number_of_nodes() == 0 or universe.empty:
        return pd.DataFrame(columns=["ts_code", "is_index_component", "index_weight"])

    # 遍历 PART_OF_INDEX 边，提取成分股及其权重
    member_map: dict[str, float] = {}  # ts_code → weight
    for u, v, data in graph.edges(data=True):
        if data.get("edge_type") != "PART_OF_INDEX":
            continue
        # 识别股票节点（可能是 u 或 v）
        for node_id in (u, v):
            node_data = graph.nodes.get(node_id, {})
            if node_data.get("node_type") == "Stock":
                ts_code = node_data.get("ts_code", "")
                if ts_code:
                    member_map[ts_code] = float(data.get("weight", 1.0))
                break

    # 构建结果 DataFrame
    result = universe[["ts_code"]].copy()
    result["is_index_component"] = result["ts_code"].isin(member_map).astype(int)
    result["index_weight"] = result["ts_code"].map(member_map).fillna(0.0)
    return result


def compute_sector_rotation_signal(
    prices: pd.DataFrame,
    universe: pd.DataFrame,
    trade_date: str,
    short_window: int = 5,
    long_window: int = 20,
    latest_returns: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """检测行业轮动方向

    核心逻辑：
    1. 计算每个行业的短期(5d)和长期(20d)动量
    2. 判断行业所处阶段：领涨/见顶/补涨/领跌/筑底
    3. 计算轮动得分 (0-1)
    4. 映射到个股

    Returns:
        DataFrame: ts_code, industry, rotation_phase, rotation_score,
                   short_momentum, long_momentum
    """
    if prices.empty or universe.empty:
        return pd.DataFrame()

    code_to_industry = dict(zip(universe["ts_code"], universe["industry"]))

    # 获取短期和长期收益率
    short_col = f"ret_{short_window}d"
    long_col = f"ret_{long_window}d"

    if latest_returns is not None and short_col in latest_returns.columns and long_col in latest_returns.columns:
        latest = latest_returns[["ts_code", short_col, long_col]].copy()
    else:
        prices_sorted = prices.sort_values(["ts_code", "trade_date"])
        for w in (short_window, long_window):
            col = f"ret_{w}d"
            if col not in prices_sorted.columns:
                prices_sorted[col] = prices_sorted.groupby("ts_code")["adj_close"].pct_change(w)
        latest_date = prices_sorted["trade_date"].max()
        latest = prices_sorted[prices_sorted["trade_date"] == latest_date][["ts_code", short_col, long_col]].copy()

    latest["industry"] = latest["ts_code"].map(code_to_industry)
    latest = latest[latest["industry"].notna()]

    if latest.empty:
        return pd.DataFrame()

    # 按行业聚合动量（中位数）
    industry_mom = (
        latest.groupby("industry")
        .agg(
            short_momentum=(short_col, "median"),
            long_momentum=(long_col, "median"),
        )
        .fillna(0.0)
    )

    # 判断行业阶段
    def _classify_phase(row: pd.Series) -> str:
        short_momentum = row["short_momentum"]
        long_momentum = row["long_momentum"]
        if short_momentum > long_momentum and short_momentum > 0 and long_momentum > 0:
            return "领涨"  # 短期 > 长期 > 0：加速上涨
        elif short_momentum < 0 and long_momentum > 0:
            return "见顶"  # 短期 < 0 但长期 > 0：开始走弱
        elif short_momentum > 0 and long_momentum < 0:
            # 短期反弹已覆盖长期跌幅视为补涨，否则仍处筑底。
            return "补涨" if short_momentum >= abs(long_momentum) else "筑底"
        elif short_momentum < long_momentum and short_momentum < 0 and long_momentum < 0:
            return "领跌"  # 短期 < 长期 < 0：加速下跌
        else:
            # 其他情况：根据动量方向默认分类
            if short_momentum > 0 and long_momentum > 0:
                return "领涨"
            elif short_momentum < 0 and long_momentum < 0:
                return "领跌"
            else:
                return "筑底"

    def _compute_score(
        phase: str,
        short_momentum: float,
        long_momentum: float,
    ) -> float:
        """根据阶段和动量强度计算轮动得分"""
        # 基础分数范围
        score_ranges = {
            "领涨": (0.8, 1.0),
            "补涨": (0.6, 0.8),
            "筑底": (0.4, 0.6),
            "见顶": (0.2, 0.4),
            "领跌": (0.0, 0.2),
        }
        low, high = score_ranges.get(phase, (0.3, 0.5))
        # 用短期动量的绝对值在阶段内插值
        abs_s = abs(short_momentum)
        # 归一化因子：假设动量绝对值范围 0~0.1
        norm = min(abs_s / 0.1, 1.0)
        return low + (high - low) * norm

    industry_mom["rotation_phase"] = industry_mom.apply(_classify_phase, axis=1)
    industry_mom["rotation_score"] = industry_mom.apply(
        lambda row: _compute_score(row["rotation_phase"], row["short_momentum"], row["long_momentum"]),
        axis=1,
    )
    industry_mom = industry_mom.reset_index()

    # 映射到个股
    result = universe[["ts_code", "industry"]].copy()
    result = result.merge(
        industry_mom[["industry", "rotation_phase", "rotation_score", "short_momentum", "long_momentum"]],
        on="industry",
        how="left",
    )
    result["rotation_score"] = result["rotation_score"].fillna(0.5)
    result["rotation_phase"] = result["rotation_phase"].fillna("")
    result["short_momentum"] = result["short_momentum"].fillna(0.0)
    result["long_momentum"] = result["long_momentum"].fillna(0.0)

    return result[["ts_code", "industry", "rotation_phase", "rotation_score", "short_momentum", "long_momentum"]]


def compute_money_flow_direction(
    features_df: pd.DataFrame,
) -> pd.DataFrame:
    """基于行业资金扩散度和行业动量判断资金流向

    核心逻辑：
    1. 按行业聚合 net_big_inflow_5d 和 industry_inflow_diffusion
    2. 资金净流入行业 → '资金流入'
    3. 资金净流出行业 → '资金流出'
    4. 计算行业资金流强度 (0-1)

    Returns:
        DataFrame: industry, money_flow_direction, flow_strength, net_inflow_avg
    """
    if features_df.empty:
        return pd.DataFrame()

    required_cols = {"industry", "net_big_inflow_5d", "industry_inflow_diffusion"}
    if not required_cols.issubset(set(features_df.columns)):
        return pd.DataFrame()

    # 按行业聚合
    industry_flow = (
        features_df.groupby("industry")
        .agg(
            net_inflow_avg=("net_big_inflow_5d", "mean"),
            diffusion_avg=("industry_inflow_diffusion", "mean"),
        )
        .fillna(0.0)
    )

    # 判断资金流向
    def _direction(row: pd.Series) -> str:
        if row["net_inflow_avg"] > 0:
            return "资金流入"
        else:
            return "资金流出"

    industry_flow["money_flow_direction"] = industry_flow.apply(_direction, axis=1)

    # 计算流强度 (0-1)
    # 使用 diffusion_avg 和 net_inflow 的绝对值的组合
    max_abs_inflow = industry_flow["net_inflow_avg"].abs().max()
    if max_abs_inflow > 0:
        norm_inflow = industry_flow["net_inflow_avg"].abs() / max_abs_inflow
    else:
        norm_inflow = pd.Series(0.0, index=industry_flow.index)
    # 综合强度：扩散度 * 0.5 + 归一化资金流 * 0.5
    industry_flow["flow_strength"] = (
        industry_flow["diffusion_avg"].clip(0, 1) * 0.5 + norm_inflow.clip(0, 1) * 0.5
    ).clip(0, 1)

    industry_flow = industry_flow.reset_index()

    return industry_flow[["industry", "money_flow_direction", "flow_strength", "net_inflow_avg"]]


def compute_fundamental_features(
    fina_df: pd.DataFrame,
    universe: pd.DataFrame,
) -> pd.DataFrame:
    """计算基本面因子

    三个正交维度（与动量因子低相关）：
    1. ROE: 最近一期净资产收益率（盈利质量）
    2. EPS增速: (eps - dt_eps) / |dt_eps|（同比增长）
    3. 毛利率: grossprofit_margin（竞争优势/护城河）

    Returns:
        DataFrame: ts_code, fundamental_roe, eps_growth, gross_margin
    """
    if fina_df.empty or universe.empty:
        return pd.DataFrame(columns=["ts_code", "fundamental_roe", "eps_growth", "gross_margin"])

    df = fina_df[["ts_code", "roe", "eps", "dt_eps", "grossprofit_margin"]].copy()

    # ROE: 直接使用（% 值，一般 0~30）
    df["fundamental_roe"] = df["roe"].fillna(0.0)

    # EPS 同比增速: (eps - dt_eps) / |dt_eps|
    dt_abs = df["dt_eps"].abs().replace(0, np.nan)
    df["eps_growth"] = ((df["eps"] - df["dt_eps"]) / dt_abs).fillna(0.0)
    # 截断极端值
    df["eps_growth"] = df["eps_growth"].clip(-3.0, 10.0)

    # 毛利率（%）
    df["gross_margin"] = df["grossprofit_margin"].fillna(0.0)

    return df[["ts_code", "fundamental_roe", "eps_growth", "gross_margin"]]


def compute_neighbor_momentum(
    graph: nx.Graph,
    prices: pd.DataFrame,
    universe: pd.DataFrame,
    window: int = 20,
) -> pd.DataFrame:
    """计算图谱邻居动量（向量化优化版本）

    利用 PEER_CORRELATED 边的权重（相关系数）计算邻居加权收益率。
    高相关性邻居涨 → 该股票也有望涨（溢出效应）。

    优化：将图遍历转换为 pandas 向量化操作（提取边列表 → merge → groupby）。

    Returns:
        DataFrame: ts_code, neighbor_momentum
    """
    if graph.number_of_edges() == 0:
        return pd.DataFrame(columns=["ts_code", "neighbor_momentum"])

    # 计算个股收益率
    prices_sorted = prices.sort_values(["ts_code", "trade_date"])
    prices_sorted[f"ret_{window}d"] = prices_sorted.groupby("ts_code")["adj_close"].pct_change(window)
    latest_date = prices_sorted["trade_date"].max()
    latest = prices_sorted[prices_sorted["trade_date"] == latest_date]
    ret_map = dict(zip(latest["ts_code"], latest[f"ret_{window}d"].fillna(0.0)))

    # 提取所有股票节点及其 ts_code
    stock_node_map = {
        n: d.get("ts_code", "")
        for n, d in graph.nodes(data=True)
        if d.get("node_type") == "Stock" and d.get("ts_code", "")
    }
    if not stock_node_map:
        return pd.DataFrame(columns=["ts_code", "neighbor_momentum"])

    # 提取所有 PEER_CORRELATED 边为边列表
    edges = []
    for u, v, data in graph.edges(data=True):
        if data.get("edge_type") != "PEER_CORRELATED":
            continue
        u_code = graph.nodes.get(u, {}).get("ts_code", "")
        v_code = graph.nodes.get(v, {}).get("ts_code", "")
        w = abs(data.get("weight", 0.5))
        if u_code and v_code:
            edges.append({"src": u_code, "nbr": v_code, "weight": w})
            edges.append({"src": v_code, "nbr": u_code, "weight": w})

    # 向量化计算邻居加权动量
    if edges:
        edge_df = pd.DataFrame(edges)
        edge_df["nbr_ret"] = edge_df["nbr"].map(ret_map).fillna(0.0)
        edge_df["w_ret"] = edge_df["weight"] * edge_df["nbr_ret"]
        weighted_sum = edge_df.groupby("src").agg(
            ws=("w_ret", "sum"),
            wt=("weight", "sum"),
        )
        weighted_sum["neighbor_momentum"] = np.where(
            weighted_sum["wt"] > 0,
            weighted_sum["ws"] / weighted_sum["wt"],
            0.0,
        )
        result_map = weighted_sum["neighbor_momentum"].to_dict()
    else:
        result_map = {}

    # 所有股票节点均输出（无 PEER_CORRELATED 边的填 0）
    results = [
        {"ts_code": ts_code, "neighbor_momentum": float(result_map.get(ts_code, 0.0))}
        for ts_code in stock_node_map.values()
    ]
    return pd.DataFrame(results)


def compute_all_graph_features(
    graph: nx.Graph,
    universe: pd.DataFrame,
    prices: pd.DataFrame,
    daily_basic: pd.DataFrame,
    moneyflow: pd.DataFrame,
    trade_date: str,
    config: GraphConfig = DEFAULT_GRAPH_CONFIG,
    fina_df: pd.DataFrame | None = None,
    use_cache: bool = False,
    cache_key: str | None = None,
) -> pd.DataFrame:
    """计算所有图谱特征并合并为一张表（支持缓存）

    当 use_cache=True 且 cache_key 命中时，直接返回缓存结果。
    缓存 key 建议使用 trade_date + universe hash。

    Returns:
        DataFrame: ts_code, trade_date, industry, + 所有特征列
    """
    # 缓存命中检查
    if use_cache and cache_key is not None and cache_key in _features_cache:
        print(f"[features] Cache hit: {cache_key}")
        return _features_cache[cache_key].copy()

    print(f"[features] Computing features for {trade_date}...")

    # P1-4 时间边界防御：严格过滤掉 trade_date 之后的行，避免上游调用方
    # 误传未来行情数据导致前瞻偏差。trade_date 以及之前的所有数据均保留。
    if not prices.empty and "trade_date" in prices.columns:
        prices = prices[prices["trade_date"] <= trade_date]
    if not daily_basic.empty and "trade_date" in daily_basic.columns:
        daily_basic = daily_basic[daily_basic["trade_date"] <= trade_date]
    if not moneyflow.empty and "trade_date" in moneyflow.columns:
        moneyflow = moneyflow[moneyflow["trade_date"] <= trade_date]

    # 0. 一次性预计算各窗口收益率 + 量比（消除重复 sort + pct_change）
    latest_returns = _precompute_latest_returns(
        prices,
        windows=config.feature_windows,
        include_vol_ratio=True,
    )
    print(f"[features] Precomputed returns: {len(latest_returns)} stocks")

    # 1. 行业动量
    industry_mom = compute_industry_momentum(
        prices,
        universe,
        trade_date,
        config.feature_windows,
        latest_returns=latest_returns,
    )
    print(f"[features] Industry momentum: {len(industry_mom)} industries")

    # 2. 个股相对强度
    stock_rel = compute_stock_relative_strength(
        prices,
        universe,
        industry_mom,
        window=20,
        latest_returns=latest_returns,
    )
    print(f"[features] Relative strength: {len(stock_rel)} stocks")

    # 3. 扩散度
    diffusion = compute_diffusion_features(
        prices,
        universe,
        trade_date,
        leader_top_pct=config.leader_top_pct,
        latest_returns=latest_returns,
    )
    print(f"[features] Diffusion: {len(diffusion)} industries")

    # 4. 资金流
    moneyflow_feat = compute_moneyflow_features(moneyflow, universe, window=5)
    print(f"[features] Moneyflow: {len(moneyflow_feat)} stocks")

    # 5. 拥挤度
    crowding = compute_crowding_features(daily_basic, universe)
    print(f"[features] Crowding: {len(crowding)} stocks")

    # 6. 短期反转因子
    reversal = compute_reversal_feature(
        prices,
        universe,
        window=5,
        latest_returns=latest_returns,
    )
    print(f"[features] Reversal: {len(reversal)} stocks")

    # 7. 图谱邻居动量
    neighbor_mom = compute_neighbor_momentum(graph, prices, universe, window=20)
    print(f"[features] Neighbor momentum: {len(neighbor_mom)} stocks")

    # 8. 量价趋势因子
    vol_price = compute_volume_price_feature(
        prices,
        universe,
        latest_returns=latest_returns,
    )
    print(f"[features] Volume-price: {len(vol_price)} stocks")

    # 9. 基本面因子（ROE/EPS增速/毛利率）
    fundamental = compute_fundamental_features(
        fina_df if fina_df is not None else pd.DataFrame(),
        universe,
    )
    print(f"[features] Fundamental: {len(fundamental)} stocks")

    # 10. 图中心度 + INDUSTRY_CORRELATED 动量溢出
    centrality = compute_graph_centrality_features(graph, universe, industry_mom)
    print(f"[features] Graph centrality: {len(centrality)} stocks")

    # 11. 风险指标特征（波动率 / Beta / 最大回撤）
    volatility = compute_volatility_features(
        prices,
        universe,
        windows=config.volatility_windows,
    )
    print(f"[features] Volatility: {len(volatility)} stocks")

    beta = compute_beta_features(
        prices,
        universe,
        window=config.beta_window,
    )
    print(f"[features] Beta: {len(beta)} stocks")

    drawdown = compute_drawdown_features(
        prices,
        universe,
        window=config.drawdown_window,
    )
    print(f"[features] Drawdown: {len(drawdown)} stocks")

    # 12. 因子多元化（低波动 + 流动性）
    low_vol = compute_low_volatility_factor(
        prices,
        universe,
        window=20,
        latest_returns=latest_returns,
    )
    print(f"[features] Low volatility: {len(low_vol)} stocks")

    liquidity = compute_liquidity_factor(
        prices,
        universe,
        window=20,
    )
    print(f"[features] Liquidity: {len(liquidity)} stocks")

    # 13. 行业轮动信号
    sector_rotation = compute_sector_rotation_signal(
        prices,
        universe,
        trade_date,
        short_window=config.rotation_short_window,
        long_window=config.rotation_long_window,
        latest_returns=latest_returns,
    )
    print(f"[features] Sector rotation: {len(sector_rotation)} stocks")

    # 14. 指数成分特征
    index_membership = compute_index_membership_features(graph, universe)
    print(f"[features] Index membership: {len(index_membership)} stocks")

    # 合并
    # 以股票列表为基础
    result = universe[["ts_code", "industry"]].copy()
    result["trade_date"] = trade_date

    # 合并行业动量
    result = result.merge(
        industry_mom[["industry", "momentum_5d", "momentum_20d", "momentum_60d", "rank", "stock_count"]],
        on="industry",
        how="left",
    ).rename(
        columns={
            "momentum_5d": "industry_momentum_5d",
            "momentum_20d": "industry_momentum_20d",
            "momentum_60d": "industry_momentum_60d",
            "rank": "industry_rank",
            "stock_count": "industry_stock_count",
        }
    )

    # 传递实际行业总数（修复 total_industries 硬编码 bug）
    result["total_industries"] = len(industry_mom)

    # 合并个股相对强度
    result = result.merge(
        stock_rel[["ts_code", "return_20d", "rel_strength_20d", "rank_in_industry"]], on="ts_code", how="left"
    )

    # 合并扩散度
    result = result.merge(diffusion[["industry", "diffusion_score", "leader_momentum"]], on="industry", how="left")

    # 合并资金流
    if not moneyflow_feat.empty:
        result = result.merge(
            moneyflow_feat[["ts_code", "net_big_inflow_5d", "industry_inflow_diffusion"]], on="ts_code", how="left"
        )
    else:
        result["net_big_inflow_5d"] = 0.0
        result["industry_inflow_diffusion"] = 0.0

    # 合并拥挤度
    if not crowding.empty:
        result = result.merge(
            crowding[["ts_code", "turnover_percentile", "pe_percentile", "crowding_score"]], on="ts_code", how="left"
        )
    else:
        result["turnover_percentile"] = 0.0
        result["pe_percentile"] = 0.0
        result["crowding_score"] = 0.0

    # 合并反转因子
    if not reversal.empty:
        result = result.merge(reversal[["ts_code", "reversal_5d"]], on="ts_code", how="left")
    else:
        result["reversal_5d"] = 0.0

    # 合并邻居动量
    if not neighbor_mom.empty:
        result = result.merge(neighbor_mom[["ts_code", "neighbor_momentum"]], on="ts_code", how="left")
    else:
        result["neighbor_momentum"] = 0.0

    # 合并量价趋势
    if not vol_price.empty:
        result = result.merge(vol_price[["ts_code", "volume_price_trend"]], on="ts_code", how="left")
    else:
        result["volume_price_trend"] = 0.0

    # 合并基本面因子
    if not fundamental.empty:
        result = result.merge(
            fundamental[["ts_code", "fundamental_roe", "eps_growth", "gross_margin"]], on="ts_code", how="left"
        )
    else:
        result["fundamental_roe"] = 0.0
        result["eps_growth"] = 0.0
        result["gross_margin"] = 0.0

    # 合并图中心度特征
    if not centrality.empty:
        result = result.merge(
            centrality[["ts_code", "degree_centrality", "industry_corr_momentum"]],
            on="ts_code",
            how="left",
        )
    else:
        result["degree_centrality"] = 0.0
        result["industry_corr_momentum"] = 0.0

    # 合并风险指标特征
    if not volatility.empty:
        result = result.merge(
            volatility[["ts_code", "volatility_20d", "volatility_60d", "volatility_ratio"]],
            on="ts_code",
            how="left",
        )
    else:
        result["volatility_20d"] = 0.0
        result["volatility_60d"] = 0.0
        result["volatility_ratio"] = 1.0

    if not beta.empty:
        result = result.merge(
            beta[["ts_code", "beta_to_industry", "beta_category"]],
            on="ts_code",
            how="left",
        )
    else:
        result["beta_to_industry"] = 1.0
        result["beta_category"] = "normal"

    if not drawdown.empty:
        result = result.merge(
            drawdown[["ts_code", "max_drawdown_60d", "current_drawdown", "drawdown_ratio"]],
            on="ts_code",
            how="left",
        )
    else:
        result["max_drawdown_60d"] = 0.0
        result["current_drawdown"] = 0.0
        result["drawdown_ratio"] = 1.0

    # 合并低波动因子
    if not low_vol.empty:
        result = result.merge(
            low_vol[["ts_code", "low_volatility_signal"]],
            on="ts_code",
            how="left",
        )
    else:
        result["low_volatility_signal"] = 0.0

    # 合并流动性因子
    if not liquidity.empty:
        result = result.merge(
            liquidity[["ts_code", "liquidity_signal"]],
            on="ts_code",
            how="left",
        )
    else:
        result["liquidity_signal"] = 0.0

    # 合并行业轮动信号
    if not sector_rotation.empty:
        result = result.merge(
            sector_rotation[
                [
                    "ts_code",
                    "rotation_phase",
                    "rotation_score",
                    "short_momentum",
                    "long_momentum",
                ]
            ],
            on="ts_code",
            how="left",
        )
    else:
        result["rotation_phase"] = ""
        result["rotation_score"] = 0.5
        result["short_momentum"] = 0.0
        result["long_momentum"] = 0.0

    # 合并指数成分特征
    if not index_membership.empty:
        result = result.merge(
            index_membership[["ts_code", "is_index_component", "index_weight"]],
            on="ts_code",
            how="left",
        )
    else:
        result["is_index_component"] = 0
        result["index_weight"] = 0.0

    # 填充 NaN
    numeric_cols = result.select_dtypes(include=[np.number]).columns
    result[numeric_cols] = result[numeric_cols].fillna(0.0)

    result = result.sort_values("ts_code", kind="stable").reset_index(drop=True)
    print(f"[features] Final features: {len(result)} stocks x {len(result.columns)} columns")

    # 写入缓存
    if use_cache and cache_key is not None:
        _features_cache[cache_key] = result.copy()

    return result
