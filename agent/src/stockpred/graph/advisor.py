"""A股图谱模块 - 投资决策顾问

将 predict_batch 的原始评分转化为可执行的投资决策：
- 置信度估计（基于因子一致性、得分百分位）
- 止盈止损建议（基于历史波动率）
- 仓位建议（基于 Kelly 准则简化版）
- 行动建议（买入/增持/持有/减持/卖出）
- 投资建议文本生成
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
# 因子列名映射到 predict_batch_vectorized 输出的 f_* 列
_FACTOR_COLS: dict[str, str] = {
    "industry_momentum": "f_ind_mom",
    "relative_strength": "f_rel_str",
    "diffusion": "f_diffusion",
    "moneyflow": "f_moneyflow",
    "crowding": "f_crowding",
    "reversal": "f_reversal",
    "neighbor_momentum": "f_neighbor",
    "volume_price": "f_vol_price",
    "short_term_momentum": "f_short_mom",
    "fundamental": "f_fundamental",
    "industry_corr_momentum": "f_icm",
    "low_volatility": "f_low_vol",
    "liquidity": "f_liquidity",
    "index_membership": "f_index_mem",
}

# IC-Signed 因子方向（与 config.py PredictionConfig.factor_signs 保持一致）
_FACTOR_SIGNS: dict[str, int] = {
    "industry_momentum": 1,
    "relative_strength": -1,
    "diffusion": -1,
    "moneyflow": 1,
    "crowding": 1,
    "reversal": 1,
    "neighbor_momentum": -1,
    "volume_price": -1,
    "short_term_momentum": 1,
    "fundamental": -1,
    "industry_corr_momentum": 1,
    "low_volatility": 1,
    "liquidity": 1,
    "index_membership": 1,
}

# 阶段 → 止盈映射
_STAGE_TP_MAP: dict[str, float] = {
    "启动": 15.0,
    "确认": 12.0,
    "扩散": 8.0,
    "拥挤": 5.0,
    "退潮": 3.0,
    "无行情": 0.0,
}


# ---------------------------------------------------------------------------
# 核心函数
# ---------------------------------------------------------------------------

def compute_confidence(pred_df: pd.DataFrame) -> pd.Series:
    """计算置信度（0-1）。

    综合两个维度：
    1. 因子一致性：翻转后得分 > 10 的因子占比（所有因子中看涨因子的比例）
    2. 得分百分位：score 在全市场中的百分位排名

    边界加固：
    - 所有 f_* 列全为 NaN → 返回 0.5（中间值，表示无信息）
    - 只有 1 行股票 → 正常工作

    Returns:
        pd.Series: 与 pred_df 等长的置信度序列，值域 [0, 1]
    """
    n = len(pred_df)
    if n == 0:
        return pd.Series(dtype=float)

    # 边界加固：检查所有 f_* 因子列是否全为 NaN
    all_factor_nan = True
    for col_name in _FACTOR_COLS.values():
        if col_name in pred_df.columns and pred_df[col_name].notna().any():
            all_factor_nan = False
            break
    if all_factor_nan:
        return pd.Series(np.full(n, 0.5), index=pred_df.index)

    # 1. 因子一致性
    positive_counts = np.zeros(n)
    valid_factors = 0
    for factor_name, col_name in _FACTOR_COLS.items():
        if col_name not in pred_df.columns:
            continue
        valid_factors += 1
        raw_scores = pred_df[col_name].values.astype(float)
        sign = _FACTOR_SIGNS.get(factor_name, 1)
        # 翻转后得分: sign=-1 → 20-raw; sign=+1 → raw
        flipped = np.where(sign < 0, 20.0 - raw_scores, raw_scores)
        positive_counts += (flipped > 10.0).astype(float)

    factor_consistency = np.where(valid_factors > 0, positive_counts / valid_factors, 0.5)

    # 2. 得分百分位
    scores = pred_df["score"].values.astype(float)
    sorted_scores = np.sort(scores)
    score_percentile = np.searchsorted(sorted_scores, scores, side="right") / n

    # 3. 综合
    confidence = 0.5 * factor_consistency + 0.5 * score_percentile
    return pd.Series(np.clip(confidence, 0.0, 1.0), index=pred_df.index)


def suggest_stop_levels(
    pred_df: pd.DataFrame,
    default_stop: float = -5.0,
) -> pd.Series:
    """基于风险特征或拥挤度计算建议止损幅度（%）。

    优先使用风险特征（波动率/Beta/回撤）：
    - 高波动(volatility_20d > 行业75分位)：止损放宽到 -8%
    - 低波动(volatility_20d < 行业25分位)：止损收紧到 -3%
    - 高 beta 股票：额外收紧 1%
    - 大回撤(max_drawdown_60d < -20%)：额外收紧 1%

    如果风险特征列不存在，回退到原有的 crowding_score 逻辑：
    - 高拥挤 (>0.75)：收紧止损到 -3%
    - 低拥挤 (<0.3)：放宽止损到 -8%
    - 中间：线性插值

    Returns:
        pd.Series: 止损幅度序列，值域 [-8%, -3%]
    """
    n = len(pred_df)
    if n == 0:
        return pd.Series(dtype=float)

    # 检查风险特征列是否存在
    has_risk_features = "volatility_20d" in pred_df.columns

    if has_risk_features:
        # 基于风险特征的止损计算
        stop = np.full(n, default_stop, dtype=float)

        industries = pred_df["industry"].values
        volatility = pred_df["volatility_20d"].fillna(0.02).values.astype(float)
        beta = pred_df["beta_to_industry"].fillna(1.0).values.astype(float)
        drawdown = pred_df["max_drawdown_60d"].fillna(0.0).values.astype(float)

        for ind in np.unique(industries):
            mask = industries == ind
            if mask.sum() < 2:
                continue
            vol_ind = volatility[mask]
            q25 = np.percentile(vol_ind, 25)
            q75 = np.percentile(vol_ind, 75)

            high_vol = mask & (volatility > q75)
            low_vol = mask & (volatility < q25)
            stop[high_vol] = -8.0
            stop[low_vol] = -3.0

        # 高 beta 额外收紧 1%
        stop[beta > 1.3] -= 1.0

        # 大回撤额外收紧 1%
        stop[drawdown < -0.20] -= 1.0

        stop = np.clip(stop, -8.0, -3.0)
        return pd.Series(stop, index=pred_df.index)
    else:
        # 回退到原有的 crowding_score 逻辑
        crowding = pred_df["crowding_score"].fillna(0.5).values.astype(float)
        crowding = np.clip(crowding, 0.0, 1.0)

        # 线性插值: crowding 0 → -8%, crowding 1 → -3%
        stop = -8.0 + 5.0 * crowding
        stop = np.clip(stop, -8.0, -3.0)

        return pd.Series(stop, index=pred_df.index)


def suggest_take_profit_levels(
    pred_df: pd.DataFrame,
    default_tp: float = 10.0,
) -> pd.Series:
    """基于行情阶段计算建议止盈幅度（%）。

    - 启动期：15%（空间大）
    - 确认期：12%
    - 扩散期：8%（已涨不少）
    - 拥挤期：5%（快跑）
    - 退潮期：3%
    - 无行情：0（不建议买入）

    Returns:
        pd.Series: 止盈幅度序列
    """
    n = len(pred_df)
    if n == 0:
        return pd.Series(dtype=float)

    stages = pred_df["stage"].values
    tp = np.array([_STAGE_TP_MAP.get(s, default_tp) for s in stages], dtype=float)

    return pd.Series(tp, index=pred_df.index)


def suggest_position_weights(
    pred_df: pd.DataFrame,
    confidence: pd.Series,
) -> pd.Series:
    """基于简化 Kelly 准则计算建议仓位权重。

    position = score_norm * confidence
    仅对 action=买入/增持 的股票分配权重，归一化使总和 = 1.0

    边界加固：所有 score 相同时，对买入/增持股票均匀分配。

    Returns:
        pd.Series: 仓位权重序列，非买入/增持的为 0
    """
    n = len(pred_df)
    if n == 0:
        return pd.Series(dtype=float)

    scores = pred_df["score"].values.astype(float)
    score_min = scores.min()
    score_max = scores.max()

    # 先算 action（需要 confidence 列），再据此归一化
    # 这里需要临时构造带 confidence 的 DataFrame 来调 determine_action
    temp_df = pred_df.copy()
    temp_df["confidence"] = confidence.values
    actions = temp_df.apply(determine_action, axis=1)

    # 只对 买入/增持 分配权重
    positive_mask = actions.isin(["买入", "增持"]).values

    # 边界加固：所有 score 相同时均匀分配
    if score_max - score_min < 1e-9:
        # score 全部相同，对买入/增持的股票均匀分配
        uniform_weight = np.where(positive_mask, 1.0, 0.0)
        total = uniform_weight.sum()
        if total > 0:
            weights = uniform_weight / total
        else:
            weights = uniform_weight
        return pd.Series(weights, index=pred_df.index)

    score_norm = (scores - score_min) / (score_max - score_min + 1e-9)
    raw_weight = score_norm * confidence.values
    raw_weight = np.where(positive_mask, raw_weight, 0.0)

    # 归一化
    total = raw_weight.sum()
    if total > 0:
        weights = raw_weight / total
    else:
        weights = raw_weight

    return pd.Series(weights, index=pred_df.index)


def determine_action(row: pd.Series) -> str:
    """基于 direction + stage + confidence 综合判断行动建议。

    规则（按优先级从高到低）：
    - direction=弱 → 卖出
    - direction=偏弱 → 减持
    - stage in (拥挤,退潮) 且 direction!=强 → 减持
    - direction=强 且 confidence>=0.7 且 stage in (启动,确认) → 买入
    - direction=强/偏强 且 confidence>=0.5 → 增持
    - direction=中性 → 持有
    - 默认 → 持有
    """
    direction = str(row.get("direction", "中性"))
    stage = str(row.get("stage", "无行情"))
    confidence = float(row.get("confidence", 0.5))

    # 优先级 1：弱方向 → 卖出
    if direction == "弱":
        return "卖出"

    # 优先级 2：偏弱方向 → 减持
    if direction == "偏弱":
        return "减持"

    # 优先级 3：拥挤/退潮阶段且非强方向 → 减持
    if stage in ("拥挤", "退潮") and direction != "强":
        return "减持"

    # 优先级 4：强方向 + 高置信 + 早期阶段 → 买入
    if direction == "强" and confidence >= 0.7 and stage in ("启动", "确认"):
        return "买入"

    # 优先级 5：强/偏强方向 + 中置信 → 增持
    if direction in ("强", "偏强") and confidence >= 0.5:
        return "增持"

    # 优先级 5.5：强方向（低置信度仍增持，方向比置信度更重要）
    if direction == "强":
        return "增持"

    # 优先级 6：中性 → 持有
    if direction == "中性":
        return "持有"

    # 默认
    return "持有"


def generate_advisory(pred_df: pd.DataFrame, regime_info: dict | None = None) -> pd.DataFrame:
    """主入口：调用所有顾问函数，返回增强的 DataFrame。

    在 pred_df 基础上新增:
    confidence, stop_loss_pct, take_profit_pct, position_weight, action 列

    Args:
        pred_df: predict_batch_vectorized 输出的 DataFrame
        regime_info: detect_market_regime 输出的 dict（可选），
            如提供则调用 apply_regime_overlay 覆盖行动建议

    Returns:
        增强后的 DataFrame
    """
    if pred_df.empty:
        return pd.DataFrame()

    result = pred_df.copy()

    # 1. 置信度
    confidence = compute_confidence(pred_df)
    result["confidence"] = confidence

    # 2. 止损
    result["stop_loss_pct"] = suggest_stop_levels(pred_df)

    # 3. 止盈
    result["take_profit_pct"] = suggest_take_profit_levels(pred_df)

    # 4. 行动建议
    result["action"] = result.apply(determine_action, axis=1)

    # 5. 仓位权重（依赖 action，所以放在最后）
    result["position_weight"] = suggest_position_weights(pred_df, confidence)

    # 6. 市场环境 overlay（如有 regime_info）
    if regime_info is not None:
        from src.stockpred.graph.market_regime import apply_regime_overlay
        result = apply_regime_overlay(result, regime_info)

    return result


def generate_investment_summary(
    advisory_df: pd.DataFrame,
    top_n: int = 20,
) -> str:
    """生成结构化投资建议文本（Markdown 格式）。

    包含：市场总览、Top N 推荐、行业配置建议、风险提示

    Args:
        advisory_df: generate_advisory 输出的 DataFrame
        top_n: 推荐股票数量上限

    Returns:
        Markdown 格式的投资建议文本
    """
    if advisory_df.empty:
        return "# 投资建议\n\n无数据。"

    lines: list[str] = []

    # ---- 市场总览 ----
    lines.append("# 投资建议报告\n")
    lines.append("## 市场总览\n")
    total = len(advisory_df)
    action_counts = advisory_df["action"].value_counts()
    for act in ["买入", "增持", "持有", "减持", "卖出"]:
        cnt = action_counts.get(act, 0)
        lines.append(f"- {act}: {cnt} 只 ({cnt/total*100:.1f}%)")

    avg_conf = advisory_df["confidence"].mean()
    avg_score = advisory_df["score"].mean()
    lines.append(f"\n- 市场平均置信度: {avg_conf:.3f}")
    lines.append(f"- 市场平均得分: {avg_score:.1f}")

    # 市场情绪判断
    buy_ratio = (action_counts.get("买入", 0) + action_counts.get("增持", 0)) / total
    sell_ratio = (action_counts.get("卖出", 0) + action_counts.get("减持", 0)) / total
    if buy_ratio > 0.5:
        sentiment = "偏多（进攻为主）"
    elif sell_ratio > 0.5:
        sentiment = "偏空（防守为主）"
    else:
        sentiment = "中性（均衡配置）"
    lines.append(f"- 市场情绪: {sentiment}\n")

    # ---- Top N 推荐 ----
    positive_actions = advisory_df[advisory_df["action"].isin(["买入", "增持"])]
    top = positive_actions.nlargest(min(top_n, len(positive_actions)), "score")

    if not top.empty:
        lines.append(f"## Top {len(top)} 推荐\n")
        lines.append("| 代码 | 行业 | 行动 | 得分 | 置信度 | 止盈% | 止损% | 仓位% |")
        lines.append("|------|------|------|------|--------|-------|-------|-------|")
        for _, row in top.iterrows():
            lines.append(
                f"| {row['ts_code']} "
                f"| {row.get('industry', '-')} "
                f"| {row['action']} "
                f"| {row['score']:.1f} "
                f"| {row['confidence']:.2f} "
                f"| {row['take_profit_pct']:.0f} "
                f"| {row['stop_loss_pct']:.1f} "
                f"| {row['position_weight']*100:.1f} |"
            )
        lines.append("")

    # ---- 行业配置建议 ----
    lines.append("## 行业配置建议\n")
    if not positive_actions.empty:
        industry_weight = (
            positive_actions.groupby("industry")["position_weight"]
            .sum()
            .sort_values(ascending=False)
            .head(5)
        )
        for ind, w in industry_weight.items():
            lines.append(f"- **{ind}**: 建议仓位 {w*100:.1f}%")
    else:
        lines.append("- 当前无推荐买入/增持的标的，建议空仓观望。")
    lines.append("")

    # ---- 风险提示 ----
    lines.append("## 风险提示\n")
    lines.append("- 以上建议基于图谱模型评分，不构成投资建议。")
    lines.append("- 请结合基本面、资金面、政策面综合判断。")
    lines.append("- 严格执行止损纪律，单笔亏损不超过总资金的 2%。")

    # 阶段分布风险
    stage_counts = advisory_df["stage"].value_counts()
    crowded = stage_counts.get("拥挤", 0) + stage_counts.get("退潮", 0)
    if crowded / total > 0.3:
        lines.append(f"- ⚠️ 当前市场 {crowded}/{total} 只股票处于拥挤/退潮阶段，注意系统性风险。")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML 报告生成
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 组合层面分析函数
# ---------------------------------------------------------------------------

def suggest_industry_allocation(pred_df: pd.DataFrame) -> pd.DataFrame:
    """按行业聚合，生成行业配置建议。

    Args:
        pred_df: predict_batch_vectorized 输出（含 industry, score, direction, stage 列）

    Returns:
        DataFrame: industry, avg_score, bullish_count, total_count, stage_mode,
                   allocation_suggestion, suggested_weight
    """
    if pred_df.empty:
        return pd.DataFrame(columns=[
            "industry", "avg_score", "bullish_count", "total_count",
            "stage_mode", "allocation_suggestion", "suggested_weight",
        ])

    # 按行业聚合
    grouped = pred_df.groupby("industry")
    agg = grouped.agg(
        avg_score=("score", "mean"),
        total_count=("score", "size"),
    )

    # 强/偏强股票数量
    bullish = pred_df[pred_df["direction"].isin(["强", "偏强"])]
    bullish_count = bullish.groupby("industry").size().reindex(agg.index, fill_value=0)
    agg["bullish_count"] = bullish_count

    # 主要阶段（众数）
    stage_mode = grouped["stage"].agg(lambda x: x.mode().iloc[0] if not x.mode().empty else "无行情")
    agg["stage_mode"] = stage_mode

    agg = agg.sort_values("avg_score", ascending=False).reset_index()

    # 按排名分位映射配置建议
    n = len(agg)

    # 边界加固：所有行业得分相同时全部标配、均匀分配
    score_range = agg["avg_score"].max() - agg["avg_score"].min()
    if score_range < 1e-6:
        agg["allocation_suggestion"] = "标配"
        agg["suggested_weight"] = 1.0 / n
        agg = agg.drop(columns=["rank_pct"]) if "rank_pct" in agg.columns else agg
        return agg

    agg["rank_pct"] = (agg.index + 1) / n  # 1/n, 2/n, ..., 1.0

    def _alloc(pct: float) -> str:
        if pct <= 0.2:
            return "超配"
        elif pct <= 0.5:
            return "标配"
        elif pct <= 0.8:
            return "低配"
        else:
            return "回避"

    agg["allocation_suggestion"] = agg["rank_pct"].apply(_alloc)

    # 建议权重：基于 avg_score 归一化
    # 超配比重大、回避比重小
    weight_map = {"超配": 3.0, "标配": 2.0, "低配": 1.0, "回避": 0.5}
    agg["raw_weight"] = agg["allocation_suggestion"].map(weight_map) * agg["avg_score"]
    total_w = agg["raw_weight"].sum()
    if total_w > 0:
        agg["suggested_weight"] = agg["raw_weight"] / total_w
    else:
        agg["suggested_weight"] = 1.0 / n

    # 清理临时列
    agg = agg.drop(columns=["rank_pct", "raw_weight"])

    return agg


def check_concentration_risk(pred_df: pd.DataFrame, top_n: int = 50) -> dict:
    """检查 Top N 股票组合的行业集中度风险。

    Args:
        pred_df: predict_batch_vectorized 输出
        top_n: 选取的高分股票数量

    Returns:
        dict: risk_level, top_3_industries, concentration_ratio, recommendation
    """
    if pred_df.empty:
        return {
            "risk_level": "low",
            "top_3_industries": [],
            "concentration_ratio": 0.0,
            "recommendation": "无数据",
        }

    actual_n = min(top_n, len(pred_df))
    top = pred_df.nlargest(actual_n, "score")

    # 行业分布
    ind_counts = top["industry"].value_counts()
    top_3 = ind_counts.head(3)
    top_3_industries = top_3.index.tolist()
    top_3_count = top_3.sum()
    concentration_ratio = top_3_count / actual_n

    # 风险级别
    if concentration_ratio > 0.6:
        risk_level = "high"
        recommendation = (
            f"⚠️ 前3大行业占比 {concentration_ratio:.1%}，高度集中。"
            "建议分散到更多行业以降低系统性风险。"
        )
    elif concentration_ratio > 0.4:
        risk_level = "medium"
        recommendation = (
            f"前3大行业占比 {concentration_ratio:.1%}，中度集中。"
            "建议适当增加其他行业配置。"
        )
    else:
        risk_level = "low"
        recommendation = (
            f"前3大行业占比 {concentration_ratio:.1%}，分散良好。"
        )

    return {
        "risk_level": risk_level,
        "top_3_industries": top_3_industries,
        "concentration_ratio": round(concentration_ratio, 4),
        "recommendation": recommendation,
    }


def compute_portfolio_risk_metrics(pred_df: pd.DataFrame, top_n: int = 50) -> dict:
    """计算 Top N 组合的风险指标。

    Args:
        pred_df: predict_batch_vectorized 输出
        top_n: 选取的高分股票数量

    Returns:
        dict: score_std, factor_dispersion, avg_industry_momentum, risk_level
    """
    if pred_df.empty:
        return {
            "score_std": 0.0,
            "factor_dispersion": 0.0,
            "avg_industry_momentum": 0.0,
            "risk_level": "low",
        }

    actual_n = min(top_n, len(pred_df))
    top = pred_df.nlargest(actual_n, "score")

    # 1. 组合得分标准差
    score_std = float(top["score"].std(ddof=1)) if actual_n > 1 else 0.0

    # 2. 因子分散度：各 f_* 因子得分的变异系数（std/mean）的均值
    cv_list = []
    for col in _FACTOR_COLS.values():
        if col in top.columns:
            vals = top[col].astype(float)
            mean_val = vals.mean()
            if mean_val > 1e-9:
                cv_list.append(float(vals.std(ddof=1) / mean_val))
    factor_dispersion = float(np.mean(cv_list)) if cv_list else 0.0

    # 3. 行业加权平均动量（基于 industry_momentum_20d 或 industry_momentum_rank）
    if "industry_momentum_20d" in top.columns:
        avg_industry_momentum = float(top["industry_momentum_20d"].mean())
    elif "industry_momentum_rank" in top.columns:
        avg_industry_momentum = float(top["industry_momentum_rank"].mean())
    else:
        avg_industry_momentum = 0.0

    # 4. 风险级别：综合 score_std 和 factor_dispersion
    risk_score = 0
    if score_std > 40:
        risk_score += 2
    elif score_std > 25:
        risk_score += 1
    if factor_dispersion > 0.8:
        risk_score += 2
    elif factor_dispersion > 0.5:
        risk_score += 1

    if risk_score >= 3:
        risk_level = "high"
    elif risk_score >= 1:
        risk_level = "medium"
    else:
        risk_level = "low"

    return {
        "score_std": round(score_std, 4),
        "factor_dispersion": round(factor_dispersion, 4),
        "avg_industry_momentum": round(avg_industry_momentum, 6),
        "risk_level": risk_level,
    }


def select_industry_leaders(
    pred_df: pd.DataFrame,
    n_per_industry: int = 3,
) -> pd.DataFrame:
    """为每个推荐行业选出 score 最高的 n_per_industry 只股票。

    只对 allocation_suggestion 为"超配"或"标配"的行业选股。

    Args:
        pred_df: predict_batch_vectorized 输出
        n_per_industry: 每个行业选出的股票数

    Returns:
        DataFrame: industry, ts_code, score, direction, stage, confidence（如有）
    """
    if pred_df.empty:
        return pd.DataFrame(columns=[
            "industry", "ts_code", "score", "direction", "stage",
        ])

    # 获取推荐行业
    alloc = suggest_industry_allocation(pred_df)
    recommended = set(
        alloc[alloc["allocation_suggestion"].isin(["超配", "标配"])]["industry"]
    )

    if not recommended:
        return pd.DataFrame(columns=[
            "industry", "ts_code", "score", "direction", "stage",
        ])

    filtered = pred_df[pred_df["industry"].isin(recommended)].copy()
    filtered = filtered.sort_values("score", ascending=False)

    # 每行业取 top N
    leaders = filtered.groupby("industry").head(n_per_industry)

    # 选取输出列
    out_cols = ["industry", "ts_code", "score", "direction", "stage"]
    if "confidence" in leaders.columns:
        out_cols.append("confidence")
    leaders = leaders[out_cols].reset_index(drop=True)

    return leaders


def generate_portfolio_advisory(
    pred_df: pd.DataFrame,
    top_n: int = 50,
) -> dict:
    """主入口：调用所有组合层面函数，返回结构化投资建议。

    Args:
        pred_df: predict_batch_vectorized 输出
        top_n: 组合分析的股票数上限

    Returns:
        dict: industry_allocation, concentration_risk, risk_metrics,
              industry_leaders, summary
    """
    if pred_df.empty:
        return {
            "industry_allocation": pd.DataFrame(),
            "concentration_risk": {
                "risk_level": "low",
                "top_3_industries": [],
                "concentration_ratio": 0.0,
                "recommendation": "无数据",
            },
            "risk_metrics": {
                "score_std": 0.0,
                "factor_dispersion": 0.0,
                "avg_industry_momentum": 0.0,
                "risk_level": "low",
            },
            "industry_leaders": pd.DataFrame(),
            "summary": "无数据，无法生成组合建议。",
        }

    industry_allocation = suggest_industry_allocation(pred_df)
    concentration_risk = check_concentration_risk(pred_df, top_n=top_n)
    risk_metrics = compute_portfolio_risk_metrics(pred_df, top_n=top_n)
    industry_leaders = select_industry_leaders(pred_df)
    advisory = {
        "industry_allocation": industry_allocation,
        "concentration_risk": concentration_risk,
        "risk_metrics": risk_metrics,
        "industry_leaders": industry_leaders,
    }
    summary = generate_portfolio_summary(advisory)
    advisory["summary"] = summary

    return advisory


def generate_portfolio_summary(advisory: dict) -> str:
    """将组合建议格式化为可读的 Markdown 摘要。

    Args:
        advisory: generate_portfolio_advisory 返回的 dict

    Returns:
        Markdown 格式的摘要文本
    """
    lines: list[str] = []

    # ---- 标题 ----
    lines.append("# 组合层面投资建议\n")

    # ---- 1. 行业配置建议表 ----
    lines.append("## 行业配置建议\n")
    alloc = advisory.get("industry_allocation", pd.DataFrame())
    if isinstance(alloc, pd.DataFrame) and not alloc.empty:
        lines.append("| 行业 | 平均得分 | 强/偏强数 | 总数 | 主要阶段 | 配置建议 | 建议权重 |")
        lines.append("|------|----------|-----------|------|----------|----------|----------|")
        for _, row in alloc.iterrows():
            lines.append(
                f"| {row['industry']} "
                f"| {row['avg_score']:.1f} "
                f"| {int(row['bullish_count'])} "
                f"| {int(row['total_count'])} "
                f"| {row['stage_mode']} "
                f"| {row['allocation_suggestion']} "
                f"| {row['suggested_weight']:.2%} |"
            )
        lines.append("")
    else:
        lines.append("无行业配置数据。\n")

    # ---- 2. 集中度风险提示 ----
    lines.append("## 集中度风险\n")
    conc = advisory.get("concentration_risk", {})
    if conc:
        level_map = {"high": "🔴 高", "medium": "🟡 中", "low": "🟢 低"}
        level_text = level_map.get(conc.get("risk_level", "low"), "未知")
        lines.append(f"- **风险级别**: {level_text}")
        top_3 = conc.get("top_3_industries", [])
        if top_3:
            lines.append(f"- **前3大行业**: {', '.join(top_3)}")
        ratio = conc.get("concentration_ratio", 0.0)
        lines.append(f"- **集中度**: {ratio:.1%}")
        rec = conc.get("recommendation", "")
        if rec:
            lines.append(f"- {rec}")
        lines.append("")

    # ---- 3. 组合风险指标 ----
    lines.append("## 组合风险指标\n")
    metrics = advisory.get("risk_metrics", {})
    if metrics:
        level_map = {"high": "🔴 高", "medium": "🟡 中", "low": "🟢 低"}
        risk_text = level_map.get(metrics.get("risk_level", "low"), "未知")
        lines.append(f"- **综合风险级别**: {risk_text}")
        lines.append(f"- **得分标准差**: {metrics.get('score_std', 0.0):.2f}")
        lines.append(f"- **因子分散度**: {metrics.get('factor_dispersion', 0.0):.4f}")
        lines.append(f"- **平均行业动量**: {metrics.get('avg_industry_momentum', 0.0):.4f}")
        lines.append("")

    # ---- 4. 各行业龙头股列表 ----
    lines.append("## 行业龙头股\n")
    leaders = advisory.get("industry_leaders", pd.DataFrame())
    if isinstance(leaders, pd.DataFrame) and not leaders.empty:
        for ind in leaders["industry"].unique():
            ind_leaders = leaders[leaders["industry"] == ind]
            lines.append(f"### {ind}\n")
            lines.append("| 代码 | 得分 | 方向 | 阶段 |")
            lines.append("|------|------|------|------|")
            for _, row in ind_leaders.iterrows():
                lines.append(
                    f"| {row['ts_code']} "
                    f"| {row['score']:.1f} "
                    f"| {row['direction']} "
                    f"| {row['stage']} |"
                )
            lines.append("")
    else:
        lines.append("无龙头股数据。\n")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 多投资期限交叉分析
# ---------------------------------------------------------------------------

# 看多行动集合（用于 consensus 判断）
_BULLISH_ACTIONS: frozenset[str] = frozenset({"买入", "增持"})


def generate_multi_horizon_advisory(
    multi_horizon_preds: dict[int, pd.DataFrame],
    top_n: int = 20,
) -> pd.DataFrame:
    """合并多期限预测，生成交叉分析。

    对每只股票，统计在多少个期限中获得“买入/增持”评级。
    多期限一致 → 更高置信度。

    Args:
        multi_horizon_preds: {horizon_days: DataFrame} predict_multi_horizon 输出
        top_n: 未使用，保留接口

    Returns:
        DataFrame: ts_code, industry, score_5d, score_20d, score_60d,
                   action_5d, action_20d, action_60d,
                   consensus_count (几个期限一致看多),
                   consensus_action (综合行动建议)
    """
    if not multi_horizon_preds:
        return pd.DataFrame()

    # 收集各期限的 score/direction/stage/industry
    horizon_dfs: list[pd.DataFrame] = []
    for h, df in sorted(multi_horizon_preds.items()):
        if df.empty:
            continue
        sub = df[["ts_code", "industry", "score", "direction", "stage"]].copy()
        sub = sub.rename(columns={
            "score": f"score_{h}d",
            "direction": f"direction_{h}d",
            "stage": f"stage_{h}d",
        })
        horizon_dfs.append(sub)

    if not horizon_dfs:
        return pd.DataFrame()

    # 以第一个期限为基准 merge
    merged = horizon_dfs[0]
    for sub in horizon_dfs[1:]:
        merged = merged.merge(sub, on=["ts_code", "industry"], how="outer")

    # 为每个期限生成 action（需要 direction + stage + confidence）
    # 此处 confidence 简化为 0.6（多期限交叉本身就是置信度增强）
    horizons_present = sorted(multi_horizon_preds.keys())
    for h in horizons_present:
        dir_col = f"direction_{h}d"
        stage_col = f"stage_{h}d"
        action_col = f"action_{h}d"
        if dir_col in merged.columns:
            def _action_for_row(row: pd.Series, _dc: str = dir_col, _sc: str = stage_col) -> str:
                return determine_action(pd.Series({
                    "direction": row.get(_dc, "中性"),
                    "stage": row.get(_sc, "无行情"),
                    "confidence": 0.6,
                }))
            merged[action_col] = merged.apply(_action_for_row, axis=1)

    # consensus_count: 几个期限看多（买入/增持）
    action_cols = [f"action_{h}d" for h in horizons_present]
    merged["consensus_count"] = merged[action_cols].apply(
        lambda row: sum(1 for a in row if a in _BULLISH_ACTIONS), axis=1
    )

    # consensus_action 规则
    def _consensus_action(cnt: int) -> str:
        if cnt >= 3:
            return "强烈推荐买入"
        elif cnt == 2:
            return "建议买入"
        elif cnt == 1:
            return "观望"
        else:
            return "不建议"

    merged["consensus_action"] = merged["consensus_count"].apply(_consensus_action)

    # 整理输出列顺序
    out_cols = ["ts_code", "industry"]
    for h in horizons_present:
        out_cols.extend([f"score_{h}d", f"action_{h}d"])
    out_cols.extend(["consensus_count", "consensus_action"])
    # 只保留存在的列
    out_cols = [c for c in out_cols if c in merged.columns]
    merged = merged[out_cols].sort_values("consensus_count", ascending=False).reset_index(drop=True)

    return merged


def generate_multi_horizon_summary(
    multi_horizon_advisory: pd.DataFrame,
    top_n: int = 20,
) -> str:
    """生成多期限对比的 Markdown 摘要。

    包含：
    - 多期限一致看好的股票（强烈推荐）
    - 各期限 Top 5 对比表
    - 期限分歧提示

    Args:
        multi_horizon_advisory: generate_multi_horizon_advisory 输出
        top_n: 推荐股票数量上限

    Returns:
        Markdown 格式的多期限投资建议文本
    """
    if multi_horizon_advisory.empty:
        return "# 多期限投资建议\n\n无数据。"

    lines: list[str] = []
    lines.append("# 多期限投资建议\n")

    # ---- 1. 强烈推荐（共识度高） ----
    strong = multi_horizon_advisory[multi_horizon_advisory["consensus_action"] == "强烈推荐买入"]
    lines.append("## 多期限一致看好（强烈推荐）\n")
    if strong.empty:
        lines.append("当前无多期限一致看好的标的。\n")
    else:
        show = strong.head(top_n)
        lines.append(f"共 {len(strong)} 只股票获得多期限一致看好，展示前 {len(show)} 只：\n")
        # 动态构建表头
        score_cols = [c for c in show.columns if c.startswith("score_")]
        action_cols = [c for c in show.columns if c.startswith("action_")]
        header = "| 代码 | 行业 |"
        sep = "|------|------|"
        for sc in score_cols:
            label = sc.replace("score_", "").replace("d", "d得分")
            header += f" {label} |"
            sep += "------|"
        for ac in action_cols:
            label = ac.replace("action_", "").replace("d", "d行动")
            header += f" {label} |"
            sep += "------|"
        header += " 共识数 |"
        sep += "------|"
        lines.append(header)
        lines.append(sep)
        for _, row in show.iterrows():
            line = f"| {row['ts_code']} | {row.get('industry', '-')} |"
            for sc in score_cols:
                line += f" {row.get(sc, 0):.1f} |"
            for ac in action_cols:
                line += f" {row.get(ac, '-')} |"
            line += f" {int(row['consensus_count'])} |"
            lines.append(line)
        lines.append("")

    # ---- 2. 各期限 Top 5 对比 ----
    lines.append("## 各期限 Top 5 对比\n")
    score_cols = [c for c in multi_horizon_advisory.columns if c.startswith("score_")]
    for sc in score_cols:
        horizon_label = sc.replace("score_", "").replace("d", "日")
        top5 = multi_horizon_advisory.nlargest(min(5, len(multi_horizon_advisory)), sc)
        lines.append(f"### {horizon_label}期限 Top 5\n")
        lines.append("| 代码 | 行业 | 得分 |")
        lines.append("|------|------|------|")
        for _, row in top5.iterrows():
            lines.append(f"| {row['ts_code']} | {row.get('industry', '-')} | {row[sc]:.1f} |")
        lines.append("")

    # ---- 3. 期限分歧提示 ----
    lines.append("## 期限分歧提示\n")
    mixed = multi_horizon_advisory[
        (multi_horizon_advisory["consensus_count"] >= 1)
        & (multi_horizon_advisory["consensus_count"] <= 2)
    ]
    if mixed.empty:
        lines.append("当前各期限观点一致，无明显分歧。\n")
    else:
        lines.append(f"共 {len(mixed)} 只股票存在期限分歧（仅部分期限看多），需进一步研判：\n")
        show_mixed = mixed.head(10)
        for _, row in show_mixed.iterrows():
            lines.append(f"- {row['ts_code']} ({row.get('industry', '-')}): 共识度={int(row['consensus_count'])}，建议={row['consensus_action']}")
        lines.append("")

    # ---- 4. 风险提示 ----
    lines.append("## 风险提示\n")
    lines.append("- 以上建议基于图谱模型多期限评分，不构成投资建议。")
    lines.append("- 多期限一致并不保证未来收益，请结合基本面、资金面综合判断。")
    lines.append("- 严格执行止损纪律。")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 行业轮动分析函数
# ---------------------------------------------------------------------------

def generate_sector_rotation_advisory(
    pred_df: pd.DataFrame,
    rotation_df: pd.DataFrame | None = None,
) -> dict:
    """分析行业轮动趋势

    识别'领涨'行业（建议加仓）和'领跌'行业（建议减仓），
    识别'补涨'行业（潜在机会）和'见顶'行业（风险警示）。

    Args:
        pred_df: predict_batch 输出的 DataFrame
        rotation_df: 包含 rotation_phase, rotation_score 的 DataFrame，
            如为 None 则尝试从 pred_df 中获取

    Returns:
        dict: leading_industries, catching_up_industries, peaking_industries,
              lagging_industries, rotation_direction, summary
    """
    empty_result = {
        "leading_industries": [],
        "catching_up_industries": [],
        "peaking_industries": [],
        "lagging_industries": [],
        "rotation_direction": "均衡",
        "summary": "无行业轮动数据。",
    }

    if pred_df.empty:
        return empty_result

    # 获取 rotation 数据
    if rotation_df is not None and not rotation_df.empty:
        rot = rotation_df
    elif "rotation_phase" in pred_df.columns:
        rot = pred_df[["ts_code", "industry", "rotation_phase", "rotation_score"]].copy()
    else:
        return empty_result

    if rot.empty or "rotation_phase" not in rot.columns:
        return empty_result

    # 按行业聚合轮动信息
    industry_rotation = rot.groupby("industry").agg(
        phase_mode=("rotation_phase", lambda x: x.mode().iloc[0] if not x.mode().empty else ""),
        avg_score=("rotation_score", "mean"),
    ).reset_index()

    # 分类行业
    leading = industry_rotation[industry_rotation["phase_mode"] == "领涨"]["industry"].tolist()
    catching_up = industry_rotation[industry_rotation["phase_mode"] == "补涨"]["industry"].tolist()
    peaking = industry_rotation[industry_rotation["phase_mode"] == "见顶"]["industry"].tolist()
    lagging = industry_rotation[industry_rotation["phase_mode"] == "领跌"]["industry"].tolist()

    # 判断轮动方向
    n_leading = len(leading)
    n_lagging = len(lagging)
    n_total = len(industry_rotation)

    if n_leading > n_total * 0.3:
        rotation_direction = "进攻周期"  # 领涨行业多，市场进攻
    elif n_lagging > n_total * 0.3:
        rotation_direction = "防御轮动"  # 领跌行业多，市场防御
    else:
        rotation_direction = "均衡"

    # 生成摘要
    summary_lines = [f"行业轮动分析：当前处于【{rotation_direction}】阶段。"]
    if leading:
        summary_lines.append(f"领涨行业({len(leading)}个)：{'、'.join(leading[:5])}，建议关注加仓机会。")
    if catching_up:
        summary_lines.append(f"补涨行业({len(catching_up)}个)：{'、'.join(catching_up[:5])}，潜在启动机会。")
    if peaking:
        summary_lines.append(f"⚠️ 见顶行业({len(peaking)}个)：{'、'.join(peaking[:5])}，注意回调风险。")
    if lagging:
        summary_lines.append(f"⚠️ 领跌行业({len(lagging)}个)：{'、'.join(lagging[:5])}，建议减仓回避。")

    return {
        "leading_industries": leading,
        "catching_up_industries": catching_up,
        "peaking_industries": peaking,
        "lagging_industries": lagging,
        "rotation_direction": rotation_direction,
        "summary": " ".join(summary_lines),
    }


def generate_rotation_summary(rotation_advisory: dict) -> str:
    """生成行业轮动的 Markdown 摘要

    Args:
        rotation_advisory: generate_sector_rotation_advisory 返回的 dict

    Returns:
        Markdown 格式的轮动摘要文本
    """
    lines: list[str] = []
    lines.append("## 行业轮动分析\n")

    direction = rotation_advisory.get("rotation_direction", "均衡")
    lines.append(f"**轮动方向**: {direction}\n")

    # 领涨行业
    leading = rotation_advisory.get("leading_industries", [])
    if leading:
        lines.append("### 🚀 领涨行业（建议加仓）\n")
        for ind in leading[:10]:
            lines.append(f"- **{ind}**")
        lines.append("")

    # 补涨行业
    catching = rotation_advisory.get("catching_up_industries", [])
    if catching:
        lines.append("### 📈 补涨行业（潜在机会）\n")
        for ind in catching[:10]:
            lines.append(f"- {ind}")
        lines.append("")

    # 见顶行业
    peaking = rotation_advisory.get("peaking_industries", [])
    if peaking:
        lines.append("### ⚠️ 见顶行业（风险警示）\n")
        for ind in peaking[:10]:
            lines.append(f"- {ind}")
        lines.append("")

    # 领跌行业
    lagging = rotation_advisory.get("lagging_industries", [])
    if lagging:
        lines.append("### 📉 领跌行业（建议减仓）\n")
        for ind in lagging[:10]:
            lines.append(f"- {ind}")
        lines.append("")

    # 轮动总结
    summary = rotation_advisory.get("summary", "")
    if summary:
        lines.append(f"**轮动总结**: {summary}\n")

    return "\n".join(lines)
