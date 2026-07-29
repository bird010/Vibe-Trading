"""A股图谱模块 - 市场环境检测

基于市场宽度指标（涨跌比、新高新低比、行业动量分散度）
判断当前市场所处阶段：牛市/熊市/震荡市
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
_VALID_REGIMES = frozenset({"牛市", "熊市", "震荡市"})

_BULL_STAGES = frozenset({"启动", "确认"})
_BEAR_STAGES = frozenset({"拥挤", "退潮"})
_BULL_DIRECTIONS = frozenset({"强", "偏强"})
_BEAR_DIRECTIONS = frozenset({"弱", "偏弱"})

_POSITIONING_MAP = {"牛市": "进攻", "熊市": "防守", "震荡市": "精选"}
_RISK_APPETITE_MAP = {"牛市": "积极", "熊市": "谨慎", "震荡市": "中性"}

# 熊市 action 降级映射
_BEAR_ACTION_DOWNGRADE = {"买入": "增持", "增持": "持有"}


# ---------------------------------------------------------------------------
# 核心函数
# ---------------------------------------------------------------------------

def detect_market_regime(
    features_df: pd.DataFrame,
    prices: pd.DataFrame | None = None,
    benchmark_df: pd.DataFrame | None = None,
) -> dict:
    """检测当前市场环境。

    综合市场宽度、行业动量分散度、阶段分布和方向分布判断市场所处阶段。

    Args:
        features_df: compute_all_graph_features 输出的特征 DataFrame。
            如果已包含 stage/direction 列（来自 predict_batch），将直接使用；
            否则会调用 predict_batch_vectorized 自动计算。
        prices: 价格数据（预留扩展，当前未使用）
        benchmark_df: 基准指数数据（预留扩展，当前未使用）

    Returns:
        dict: regime, confidence, breadth, momentum_dispersion,
              stage_distribution, direction_distribution,
              positioning_suggestion, risk_appetite
    """
    default_result = {
        "regime": "震荡市",
        "confidence": 0.0,
        "breadth": 0.0,
        "momentum_dispersion": 0.0,
        "stage_distribution": {},
        "direction_distribution": {},
        "positioning_suggestion": "精选",
        "risk_appetite": "中性",
    }

    if features_df.empty:
        return default_result

    # ---- 1. 市场宽度（breadth）：正收益行业占比 ----
    ind_mom_col = None
    for col_name in ("industry_momentum_20d", "momentum_20d"):
        if col_name in features_df.columns:
            ind_mom_col = col_name
            break

    if ind_mom_col and "industry" in features_df.columns:
        ind_avg_mom = features_df.groupby("industry")[ind_mom_col].mean().fillna(0.0)
        total_ind = len(ind_avg_mom)
        positive_ind = (ind_avg_mom > 0).sum()
        breadth = positive_ind / total_ind if total_ind > 0 else 0.0
    else:
        breadth = 0.5
        ind_avg_mom = pd.Series(dtype=float)

    # ---- 2. 行业动量分散度（变异系数 CV） ----
    if len(ind_avg_mom) > 1:
        mean_mom = float(ind_avg_mom.mean())
        std_mom = float(ind_avg_mom.std(ddof=1))
        momentum_dispersion = abs(std_mom / mean_mom) if abs(mean_mom) > 1e-9 else 0.0
    else:
        momentum_dispersion = 0.0

    # ---- 3 & 4. 阶段分布 & 方向分布 ----
    has_stage = "stage" in features_df.columns
    has_direction = "direction" in features_df.columns

    if not has_stage or not has_direction:
        # 调用预测器生成 stage/direction
        try:
            from src.stockpred.graph.predictor import predict_batch_vectorized
            pred_df = predict_batch_vectorized(features_df)
            if not pred_df.empty:
                stage_series = pred_df["stage"]
                direction_series = pred_df["direction"]
            else:
                stage_series = pd.Series(dtype=str)
                direction_series = pd.Series(dtype=str)
        except Exception:
            stage_series = pd.Series(dtype=str)
            direction_series = pd.Series(dtype=str)
    else:
        stage_series = features_df["stage"]
        direction_series = features_df["direction"]

    # 阶段分布
    total = len(stage_series)
    if total > 0:
        stage_counts = stage_series.value_counts()
        stage_distribution = {k: round(v / total, 4) for k, v in stage_counts.items()}
    else:
        stage_distribution = {}

    # 方向分布
    total_dir = len(direction_series)
    if total_dir > 0:
        dir_counts = direction_series.value_counts()
        direction_distribution = {k: round(v / total_dir, 4) for k, v in dir_counts.items()}
    else:
        direction_distribution = {}

    # ---- 综合判断 ----
    bull_stage_ratio = sum(
        stage_distribution.get(s, 0.0) for s in _BULL_STAGES
    )
    bear_stage_ratio = sum(
        stage_distribution.get(s, 0.0) for s in _BEAR_STAGES
    )
    bull_dir_ratio = sum(
        direction_distribution.get(d, 0.0) for d in _BULL_DIRECTIONS
    )
    bear_dir_ratio = sum(
        direction_distribution.get(d, 0.0) for d in _BEAR_DIRECTIONS
    )

    # 牛市：偏多 + breadth高 + 启动/确认多
    is_bull = (
        bull_dir_ratio > 0.4
        and breadth > 0.7
        and bull_stage_ratio > bear_stage_ratio
    )
    # 熊市：偏空 + breadth低 + 退潮多
    is_bear = (
        bear_dir_ratio > 0.4
        and breadth < 0.3
        and bear_stage_ratio > bull_stage_ratio
    )

    if is_bull:
        regime = "牛市"
    elif is_bear:
        regime = "熊市"
    else:
        regime = "震荡市"

    # ---- 置信度 ----
    if is_bull:
        bias_strength = min(1.0, bull_dir_ratio)
        breadth_strength = min(1.0, breadth)
        stage_strength = min(1.0, bull_stage_ratio * 2)
    elif is_bear:
        bias_strength = min(1.0, bear_dir_ratio)
        breadth_strength = min(1.0, 1.0 - breadth)
        stage_strength = min(1.0, bear_stage_ratio * 2)
    else:
        # 震荡市：离极端越远，置信度越高
        bias_strength = 1.0 - abs(bull_dir_ratio - bear_dir_ratio)
        breadth_strength = 1.0 - abs(breadth - 0.5) * 2
        stage_strength = 1.0 - abs(bull_stage_ratio - bear_stage_ratio)

    confidence = round(
        float(np.clip((bias_strength + breadth_strength + stage_strength) / 3, 0.0, 1.0)),
        4,
    )

    return {
        "regime": regime,
        "confidence": confidence,
        "breadth": round(float(breadth), 4),
        "momentum_dispersion": round(float(momentum_dispersion), 4),
        "stage_distribution": stage_distribution,
        "direction_distribution": direction_distribution,
        "positioning_suggestion": _POSITIONING_MAP[regime],
        "risk_appetite": _RISK_APPETITE_MAP[regime],
    }


def apply_regime_overlay(
    advisory_df: pd.DataFrame,
    regime_info: dict,
) -> pd.DataFrame:
    """根据市场环境调整 advisor 的建议。

    - 牛市：增持/买入的 confidence +10%，position_weight +20%
    - 熊市：action 降级（买入→增持，增持→持有），position_weight -30%，stop_loss 收紧 2%
    - 震荡市：保持不变

    Args:
        advisory_df: generate_advisory 输出的 DataFrame
        regime_info: detect_market_regime 输出的 dict

    Returns:
        含 regime_overlay_action 列的 DataFrame
    """
    result = advisory_df.copy()
    regime = regime_info.get("regime", "震荡市")

    if regime == "牛市":
        bullish_mask = result["action"].isin(["买入", "增持"])
        result.loc[bullish_mask, "confidence"] = (
            result.loc[bullish_mask, "confidence"] * 1.1
        ).clip(upper=1.0)
        result.loc[bullish_mask, "position_weight"] = (
            result.loc[bullish_mask, "position_weight"] * 1.2
        )
        result["regime_overlay_action"] = result["action"]

    elif regime == "熊市":
        result["regime_overlay_action"] = result["action"].map(
            lambda x: _BEAR_ACTION_DOWNGRADE.get(x, x)
        )
        result["position_weight"] = result["position_weight"] * 0.7
        result["stop_loss_pct"] = result["stop_loss_pct"] - 2.0

    else:
        # 震荡市：保持不变
        result["regime_overlay_action"] = result["action"]

    return result


def generate_regime_summary(regime_info: dict) -> str:
    """生成市场环境判断的 Markdown 摘要。

    包含：当前市场环境、置信度、核心指标、投资建议。

    Args:
        regime_info: detect_market_regime 输出的 dict

    Returns:
        Markdown 格式的摘要文本
    """
    regime = regime_info.get("regime", "震荡市")
    confidence = regime_info.get("confidence", 0.0)
    breadth = regime_info.get("breadth", 0.0)
    dispersion = regime_info.get("momentum_dispersion", 0.0)
    positioning = regime_info.get("positioning_suggestion", "精选")
    risk = regime_info.get("risk_appetite", "中性")
    stage_dist = regime_info.get("stage_distribution", {})
    dir_dist = regime_info.get("direction_distribution", {})

    lines: list[str] = []
    lines.append("# 市场环境分析\n")
    lines.append(f"## 当前市场环境: {regime}\n")
    lines.append(f"- **置信度**: {confidence:.1%}")
    lines.append(f"- **市场宽度**: {breadth:.1%}（正收益行业占比）")
    lines.append(f"- **动量分散度**: {dispersion:.4f}（行业动量变异系数）")
    lines.append(f"- **仓位建议**: {positioning}")
    lines.append(f"- **风险偏好**: {risk}")

    if stage_dist:
        lines.append("\n### 阶段分布\n")
        for stage_name in ["启动", "确认", "扩散", "拥挤", "退潮", "无行情"]:
            ratio = stage_dist.get(stage_name, 0.0)
            if ratio > 0:
                lines.append(f"- {stage_name}: {ratio:.1%}")

    if dir_dist:
        lines.append("\n### 方向分布\n")
        for dir_name in ["强", "偏强", "中性", "偏弱", "弱"]:
            ratio = dir_dist.get(dir_name, 0.0)
            if ratio > 0:
                lines.append(f"- {dir_name}: {ratio:.1%}")

    lines.append("\n## 投资建议\n")
    if regime == "牛市":
        lines.append("- 市场整体偏强，建议**进攻**策略，增加仓位配置。")
        lines.append("- 重点关注启动期和确认期的行业龙头。")
        lines.append("- 适当提高风险偏好，但仍需控制单只个股仓位。")
    elif regime == "熊市":
        lines.append("- 市场整体偏弱，建议**防守**策略，降低仓位至最低水平。")
        lines.append("- 收紧止损纪律，严格执行风控标准。")
        lines.append("- 优先配置防御性板块和高股息标的。")
    else:
        lines.append("- 市场处于震荡格局，建议**精选个股**，控制总仓位。")
        lines.append("- 关注结构性机会，回避系统性风险。")
        lines.append("- 优选基本面扎实、估值合理的标的。")

    lines.append("\n---\n")
    lines.append("⚠️ 以上判断基于量化模型，不构成投资建议。请结合市场环境综合判断。")

    return "\n".join(lines)
