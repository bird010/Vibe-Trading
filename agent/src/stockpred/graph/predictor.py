"""A股图谱模块 - 趋势预测引擎

基于图谱特征（行业动量 / 相对强度 / 扩散度 / 资金流 / 拥挤度）的规则引擎评分，
输出趋势方向（强/偏强/中性/偏弱/弱）和阶段判断（无行情/启动/确认/扩散/拥挤/退潮）。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.stockpred.graph.config import (
    DEFAULT_HORIZON_CONFIGS,
    DEFAULT_PREDICTION_CONFIG,
    HorizonConfig,
    PredictionConfig,
)
from src.stockpred.graph.schema import TrendPrediction


# ---------------------------------------------------------------------------
# 风险覆盖层
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RiskOverlay:
    """基础总分之后应用的连续风险惩罚。"""

    retreat_severity: float
    retreat_penalty: float
    industry_turning_severity: float
    industry_turning_penalty: float
    total_penalty: float


def compute_risk_overlay(
    momentum_5d: float,
    momentum_20d: float,
    diffusion_score: float,
    is_retreat: bool,
    cfg: PredictionConfig = DEFAULT_PREDICTION_CONFIG,
) -> RiskOverlay:
    """根据行业动量衰减和扩散度计算连续风险惩罚。"""
    if momentum_20d <= 0:
        ratio = 1.0
    else:
        ratio = momentum_5d / max(abs(momentum_20d), 1e-12)

    retreat_severity = (
        float(np.clip((0.30 - ratio) / 0.60, 0.0, 1.0))
        if is_retreat and momentum_20d > 0
        else 0.0
    )

    turning_active = (
        momentum_20d > 0
        and ratio < cfg.industry_turning_deceleration_start
    )
    if turning_active:
        deceleration = float(np.clip(
            (cfg.industry_turning_deceleration_start - ratio) / 1.10,
            0.0,
            1.0,
        ))
        sign_break = 1.0 if momentum_5d < 0 else 0.0
        overheat = float(np.clip(
            (diffusion_score - 0.65) / 0.20,
            0.0,
            1.0,
        ))
        turning_severity = float(np.clip(
            0.65 * deceleration + 0.25 * sign_break + 0.10 * overheat,
            0.0,
            1.0,
        ))
    else:
        turning_severity = 0.0

    retreat_penalty = retreat_severity * cfg.retreat_penalty_max
    turning_penalty = (
        turning_severity * cfg.industry_turning_penalty_max
    )
    turning_weight = (
        cfg.industry_turning_overlap_weight
        if retreat_severity > 0
        else 1.0
    )
    total_penalty = min(
        retreat_penalty + turning_weight * turning_penalty,
        cfg.risk_overlay_penalty_cap,
    )

    return RiskOverlay(
        retreat_severity=retreat_severity,
        retreat_penalty=retreat_penalty,
        industry_turning_severity=turning_severity,
        industry_turning_penalty=turning_penalty,
        total_penalty=total_penalty,
    )


def _compute_risk_overlay_vec(
    momentum_5d: np.ndarray,
    momentum_20d: np.ndarray,
    diffusion_score: np.ndarray,
    is_retreat: np.ndarray,
    cfg: PredictionConfig,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """compute_risk_overlay 的向量化等价实现。"""
    positive_medium = momentum_20d > 0
    ratio = np.where(
        positive_medium,
        momentum_5d / np.maximum(np.abs(momentum_20d), 1e-12),
        1.0,
    )

    retreat_severity = np.where(
        is_retreat & positive_medium,
        np.clip((0.30 - ratio) / 0.60, 0.0, 1.0),
        0.0,
    )

    turning_active = (
        positive_medium
        & (ratio < cfg.industry_turning_deceleration_start)
    )
    deceleration = np.clip(
        (cfg.industry_turning_deceleration_start - ratio) / 1.10,
        0.0,
        1.0,
    )
    sign_break = (momentum_5d < 0).astype(float)
    overheat = np.clip((diffusion_score - 0.65) / 0.20, 0.0, 1.0)
    turning_severity = np.where(
        turning_active,
        np.clip(
            0.65 * deceleration + 0.25 * sign_break + 0.10 * overheat,
            0.0,
            1.0,
        ),
        0.0,
    )

    retreat_penalty = retreat_severity * cfg.retreat_penalty_max
    turning_penalty = (
        turning_severity * cfg.industry_turning_penalty_max
    )
    turning_weight = np.where(
        retreat_severity > 0,
        cfg.industry_turning_overlap_weight,
        1.0,
    )
    total_penalty = np.minimum(
        retreat_penalty + turning_weight * turning_penalty,
        cfg.risk_overlay_penalty_cap,
    )
    return (
        retreat_severity,
        retreat_penalty,
        turning_severity,
        turning_penalty,
        total_penalty,
    )


# ---------------------------------------------------------------------------
# 内部评分函数（各维度 0-20 分）
# ---------------------------------------------------------------------------

def _score_industry_momentum(row: pd.Series, industry_rank_pctrank: float) -> tuple[float, list[str]]:
    """行业动量分（0-20）

    行业 20d 收益全市场排名的百分位（0-1，1=排名第一）。
    映射到 0-20 分：pctrank * 20。
    """
    score = max(0.0, min(20.0, industry_rank_pctrank * 20))
    evidence: list[str] = []
    if industry_rank_pctrank >= 0.8:
        evidence.append(f"行业动量领先全市场 (rank_pct={industry_rank_pctrank:.2f})")
    elif industry_rank_pctrank <= 0.2:
        evidence.append(f"行业动量落后全市场 (rank_pct={industry_rank_pctrank:.2f})")
    return score, evidence


def _score_relative_strength(row: pd.Series, rank_in_industry: int, industry_count: int) -> tuple[float, list[str]]:
    """个股相对强度分（0-20）

    行业内排名百分位 → 映射到 0-20 分。
    """
    if industry_count <= 1 or rank_in_industry <= 0:
        return 10.0, []
    # rank 越小越好（1=第一），百分位 = 1 - rank/count
    pctrank = 1.0 - (rank_in_industry - 1) / max(1, industry_count - 1)
    score = max(0.0, min(20.0, pctrank * 20))
    evidence: list[str] = []
    if pctrank >= 0.8:
        evidence.append(f"个股领先行业 (rank={rank_in_industry}/{industry_count})")
    elif pctrank <= 0.2:
        evidence.append(f"个股落后行业 (rank={rank_in_industry}/{industry_count})")
    return score, evidence


def _score_diffusion(diffusion_score: float, leader_momentum: float) -> tuple[float, list[str]]:
    """扩散度分（0-20）

    diffusion_score: 行业内 20d 正收益股票比例（0-1）
    leader_momentum: 龙头组（前10%）平均收益

    扩散度 0.5-0.7 最佳（给满分），过高（>0.9）或过低（<0.2）均扣分。
    """
    if pd.isna(diffusion_score):
        return 10.0, []

    # 扩散度映射（钟形曲线：0.6 最佳）
    diff_deviation = abs(diffusion_score - 0.6)
    diff_base = max(0.0, 1.0 - diff_deviation / 0.6) * 14  # 最多 14 分

    # 龙头动量附加分（最多 6 分）
    if pd.isna(leader_momentum):
        leader_bonus = 0.0
    elif leader_momentum > 0:
        leader_bonus = min(6.0, leader_momentum * 100)  # 5% 收益 → 5 分
    else:
        leader_bonus = max(-3.0, leader_momentum * 100)

    score = max(0.0, min(20.0, diff_base + leader_bonus))
    evidence: list[str] = []
    if diffusion_score >= 0.7:
        evidence.append(f"行业扩散度高 ({diffusion_score:.2f})")
    elif diffusion_score < 0.3:
        evidence.append(f"行业扩散度低 ({diffusion_score:.2f})")
    if leader_momentum > 0.05:
        evidence.append(f"龙头组强势 (momentum={leader_momentum:.3f})")
    return score, evidence


def _score_moneyflow(net_big_inflow: float, industry_inflow_diffusion: float) -> tuple[float, list[str]]:
    """资金流分（0-20）

    net_big_inflow: 5日累计大单净流入（元）
    industry_inflow_diffusion: 行业内净流入为正的比例（0-1）
    """
    if pd.isna(net_big_inflow) or pd.isna(industry_inflow_diffusion):
        return 10.0, []

    # 大单净流入方向分（最多 12 分）
    # 净流入>0 给分，<0 扣分
    inflow_score = max(-4.0, min(12.0, np.sign(net_big_inflow) * min(12.0, abs(net_big_inflow) / 1e7)))

    # 行业扩散分（最多 8 分）
    diff_score = max(0.0, min(8.0, industry_inflow_diffusion * 8))

    score = max(0.0, min(20.0, inflow_score + diff_score))
    evidence: list[str] = []
    if net_big_inflow > 1e7:
        evidence.append(f"大单净流入显著 ({net_big_inflow/1e8:.2f}亿)")
    elif net_big_inflow < -1e7:
        evidence.append(f"大单净流出显著 ({net_big_inflow/1e8:.2f}亿)")
    if industry_inflow_diffusion >= 0.7:
        evidence.append(f"行业资金扩散良好 ({industry_inflow_diffusion:.2f})")
    return score, evidence


def _score_crowding(crowding_score: float) -> tuple[float, list[str]]:
    """拥挤度分（反向，0-20）

    crowding_score: 拥挤度（0-1，1=最拥挤）
    反向：crowding=0 → 20分（不拥挤），crowding=1 → 0分（极度拥挤）
    """
    if pd.isna(crowding_score):
        return 10.0, []

    score = max(0.0, min(20.0, (1.0 - crowding_score) * 20))
    evidence: list[str] = []
    risks: list[str] = []
    if crowding_score >= 0.85:
        risks.append(f"行业拥挤度极高 ({crowding_score:.2f})，回调风险大")
    elif crowding_score >= 0.7:
        risks.append(f"行业拥挤度偏高 ({crowding_score:.2f})")
    elif crowding_score <= 0.2:
        evidence.append(f"行业尚不拥挤 ({crowding_score:.2f})")
    return score, evidence, risks


def _score_reversal(reversal_5d: float, all_reversals: pd.Series | None = None) -> tuple[float, list[str]]:
    """短期反转分（0-20）

    A 股短期均值回复效应：近5日跌得多的股票，未5日反弹概率大。
    reversal_5d = -ret_5d（正值表示近期跌得多 → 预期反弹）

    使用全市场百分位排名映射到 0-20 分：
    - reversal_5d 排名高（近期跌得多）→ 高分（看涨反弹）
    - reversal_5d 排名低（近期涨得多）→ 低分（可能回调）
    """
    if pd.isna(reversal_5d):
        return 10.0, []

    if all_reversals is not None and len(all_reversals) > 100:
        # 使用全市场百分位排名（更准确）
        pctrank = (all_reversals < reversal_5d).sum() / len(all_reversals)
    else:
        # 后备方案：用绝对值估算（5%跌幅 → 约0.5百分位）
        # A 股 5 日波动一般在 ±10% 以内
        pctrank = max(0.0, min(1.0, 0.5 + reversal_5d * 5))

    score = max(0.0, min(20.0, pctrank * 20))
    evidence: list[str] = []
    if pctrank >= 0.8:
        evidence.append(f"短期跌幅较大，反弹预期强 (reversal_pct={pctrank:.2f})")
    elif pctrank <= 0.2:
        evidence.append(f"短期涨幅较大，回调风险 (reversal_pct={pctrank:.2f})")
    return score, evidence


def _score_neighbor_momentum(neighbor_momentum: float) -> tuple[float, list[str]]:
    """图谱邻居动量分（0-20）

    利用 PEER_CORRELATED 边的加权邻居收益作为溢出效应信号。
    高相关性邻居涨 → 该股票也有望涨（信息溢出/板块联动）。
    """
    if pd.isna(neighbor_momentum):
        return 10.0, []

    # 邻居动量映射：±10% → 0-20 分（线性）
    # neighbor_momentum 范围约 [-0.10, 0.10]
    normalized = max(-1.0, min(1.0, neighbor_momentum * 10))  # [-1, 1]
    score = max(0.0, min(20.0, (normalized + 1.0) * 10))  # [0, 20]

    evidence: list[str] = []
    if neighbor_momentum > 0.03:
        evidence.append(f"相关性邻居整体走强 (neighbor_mom={neighbor_momentum:.3f})")
    elif neighbor_momentum < -0.03:
        evidence.append(f"相关性邻居整体走弱 (neighbor_mom={neighbor_momentum:.3f})")
    return score, evidence


def _score_short_term_momentum(ind_mom_5d: float, all_ind_mom_5d: pd.Series | None = None) -> tuple[float, list[str]]:
    """短期行业动量分（0-20）

    行业 5d 收益全市场百分位排名 → 映射到 0-20。
    Moskowitz & Grinblatt (1999): 行业短期动量是动量效应的核心驱动。
    5d 窗口与 5d 前瞻期匹配，捕捉近期行业超势的延续性。
    """
    if pd.isna(ind_mom_5d):
        return 10.0, []

    if all_ind_mom_5d is not None and len(all_ind_mom_5d) > 10:
        pctrank = (all_ind_mom_5d < ind_mom_5d).sum() / len(all_ind_mom_5d)
    else:
        # 后备: ind_mom_5d 范围约 [-0.10, 0.10]
        pctrank = max(0.0, min(1.0, 0.5 + ind_mom_5d * 5))

    score = max(0.0, min(20.0, pctrank * 20))
    evidence: list[str] = []
    if pctrank >= 0.8:
        evidence.append(f"短期行业动量领先 (5d_mom_pct={pctrank:.2f})")
    elif pctrank <= 0.2:
        evidence.append(f"短期行业动量落后 (5d_mom_pct={pctrank:.2f})")
    return score, evidence


def _score_volume_price(volume_price_trend: float, all_vpt: pd.Series | None = None) -> tuple[float, list[str]]:
    """量价趋势分（0-20）

    volume_price_trend = (vol_ratio - 1) * ret_5d
    正值: 放量上涨(趋势确认) 或 缩量下跌(卖压枯竭)
    负值: 缩量上涨(动能不足) 或 放量下跌(恐慌抛售)

    使用全市场百分位排名映射到 0-20 分。
    """
    if pd.isna(volume_price_trend):
        return 10.0, []

    if all_vpt is not None and len(all_vpt) > 100:
        pctrank = (all_vpt < volume_price_trend).sum() / len(all_vpt)
    else:
        # 后备: vpt 范围约 [-0.15, 0.15]
        pctrank = max(0.0, min(1.0, 0.5 + volume_price_trend * 3))

    score = max(0.0, min(20.0, pctrank * 20))
    evidence: list[str] = []
    if pctrank >= 0.8:
        evidence.append(f"量价配合良好 (vpt_pct={pctrank:.2f})")
    elif pctrank <= 0.2:
        evidence.append(f"量价背离警示 (vpt_pct={pctrank:.2f})")
    return score, evidence


def _score_fundamental(
    roe: float, eps_growth: float, gross_margin: float,
    all_roe: pd.Series | None = None,
    all_eps_g: pd.Series | None = None,
    all_gm: pd.Series | None = None,
) -> tuple[float, list[str]]:
    """基本面综合分（0-20）

    三个正交维度（与动量因子低相关）：
    1. ROE行业百分位（盈利质量，权重最高）
    2. EPS增速行业百分位（成长性）
    3. 毛利率行业百分位（护城河）

    各维度取百分位后加权求和，映射到 0-20。
    """
    # ROE 分 (0-8)
    if pd.isna(roe) or roe == 0:
        roe_pct = 0.5
    elif all_roe is not None and len(all_roe) > 50:
        roe_pct = (all_roe < roe).sum() / len(all_roe)
    else:
        # 后备: ROE 0~30 → 0~1
        roe_pct = max(0.0, min(1.0, roe / 30.0))
    roe_score = roe_pct * 8.0

    # EPS增速分 (0-6)
    if pd.isna(eps_growth):
        eps_pct = 0.5
    elif all_eps_g is not None and len(all_eps_g) > 50:
        eps_pct = (all_eps_g < eps_growth).sum() / len(all_eps_g)
    else:
        eps_pct = max(0.0, min(1.0, 0.5 + eps_growth * 0.5))
    eps_score = eps_pct * 6.0

    # 毛利率分 (0-6)
    if pd.isna(gross_margin) or gross_margin == 0:
        gm_pct = 0.5
    elif all_gm is not None and len(all_gm) > 50:
        gm_pct = (all_gm < gross_margin).sum() / len(all_gm)
    else:
        gm_pct = max(0.0, min(1.0, gross_margin / 60.0))
    gm_score = gm_pct * 6.0

    score = max(0.0, min(20.0, roe_score + eps_score + gm_score))
    evidence: list[str] = []
    if roe_pct >= 0.8:
        evidence.append(f"ROE行业领先 (pct={roe_pct:.2f})")
    if eps_growth > 0.3:
        evidence.append(f"EPS高增长 ({eps_growth:.2f})")
    return score, evidence


def _score_industry_corr_momentum(
    industry_corr_momentum: float, all_icm: pd.Series | None = None,
) -> tuple[float, list[str]]:
    """行业关联动量溢出分（0-20）

    利用 INDUSTRY_CORRELATED 边加权的关联行业动量，捕捉行业间信息溢出效应。
    例如：电气设备板块上涨时，关联的有色金属板块也可能跟涨。

    使用全市场百分位排名映射到 0-20 分。
    """
    if pd.isna(industry_corr_momentum):
        return 10.0, []

    if all_icm is not None and len(all_icm) > 100:
        pctrank = (all_icm < industry_corr_momentum).sum() / len(all_icm)
    else:
        # 后备: industry_corr_momentum 范围约 [-0.10, 0.10]
        pctrank = max(0.0, min(1.0, 0.5 + industry_corr_momentum * 5))

    score = max(0.0, min(20.0, pctrank * 20))
    evidence: list[str] = []
    if pctrank >= 0.8:
        evidence.append(f"关联行业动量溢出强 (icm_pct={pctrank:.2f})")
    elif pctrank <= 0.2:
        evidence.append(f"关联行业动量拖累 (icm_pct={pctrank:.2f})")
    return score, evidence


def _score_low_volatility(
    low_vol_signal: float, all_lv: pd.Series | None = None,
) -> tuple[float, list[str]]:
    """低波动因子评分（0-20）

    低波动异象 (Ang et al. 2006): 低波动股票长期跑赢高波动股票。
    low_vol_signal 已在全市场百分位归一化到 [0,1]，直接映射到 0-20。
    """
    if pd.isna(low_vol_signal):
        return 10.0, []

    if all_lv is not None and len(all_lv) > 100:
        pctrank = (all_lv < low_vol_signal).sum() / len(all_lv)
    else:
        # 已归一化到 [0,1]，直接使用
        pctrank = max(0.0, min(1.0, low_vol_signal))

    score = max(0.0, min(20.0, pctrank * 20))
    evidence: list[str] = []
    if pctrank >= 0.8:
        evidence.append(f"低波动信号强 (lv_pct={pctrank:.2f})")
    elif pctrank <= 0.2:
        evidence.append(f"高波动风险 (lv_pct={pctrank:.2f})")
    return score, evidence


def _score_liquidity(
    liq_signal: float, all_liq: pd.Series | None = None,
) -> tuple[float, list[str]]:
    """流动性因子评分（0-20）

    Amihud 非流动性溢价：非流动性越高→预期收益越高。
    liq_signal 已在全市场百分位归一化到 [0,1]，直接映射到 0-20。
    """
    if pd.isna(liq_signal):
        return 10.0, []

    if all_liq is not None and len(all_liq) > 100:
        pctrank = (all_liq < liq_signal).sum() / len(all_liq)
    else:
        # 已归一化到 [0,1]，直接使用
        pctrank = max(0.0, min(1.0, liq_signal))

    score = max(0.0, min(20.0, pctrank * 20))
    evidence: list[str] = []
    if pctrank >= 0.8:
        evidence.append(f"流动性溢价信号强 (liq_pct={pctrank:.2f})")
    elif pctrank <= 0.2:
        evidence.append(f"低流动性溢价 (liq_pct={pctrank:.2f})")
    return score, evidence


def _score_index_membership(is_index: float, all_index: pd.Series | None = None) -> tuple[float, list[str]]:
    """指数成分加分（0-20）

    指数成分股 → 流动性溢价 + 机构偏好 → 额外加分。
    is_index: 0/1，是否为指数成分股。
    成分股给 16 分，非成分股给 6 分，NaN 给 10 分（中性）。
    """
    if pd.isna(is_index):
        return 10.0, []

    if is_index > 0.5:
        score = 16.0
        evidence = ["指数成分股，享受流动性溢价和机构偏好"]
    else:
        score = 6.0
        evidence = []

    return max(0.0, min(20.0, score)), evidence


# ---------------------------------------------------------------------------
# 阶段判断
# ---------------------------------------------------------------------------

def _determine_stage(
    momentum_score: float,
    diffusion_score: float,
    crowding_score_raw: float,
    momentum_5d: float,
    momentum_20d: float,
    cfg: PredictionConfig,
) -> str:
    """判断趋势阶段

    规则（按优先级顺序匹配）：
    1. 拥挤：扩散度高（>0.6）且 crowding_score_raw 偏高
    2. 退潮：短期动量（5d）明显弱于中期（20d），趋势转弱
    3. 扩散：行业动量分≥stage_spread_momentum 且扩散度>0.6
    4. 确认：行业动量分≥stage_confirm_momentum 且扩散度 0.4-0.6
    5. 启动：行业动量分≥stage_start_momentum 且扩散度<0.4（尚未扩散，刚启动）
    6. 无行情：行业动量分<stage_no_action_momentum 或扩散度<0.3

    momentum_score 量纲为 0-20，与 _score_industry_momentum 输出一致。
    """
    # 退潮判断（优先级高：短期动量快速衰减）
    if momentum_5d < momentum_20d * 0.3 and momentum_20d > 0:
        return "退潮"

    # 拥挤：扩散已高且行业换手/估值偏高
    if diffusion_score >= cfg.stage_spread_diffusion and crowding_score_raw >= 0.75:
        return "拥挤"

    # 扩散阶段
    if momentum_score >= cfg.stage_spread_momentum and diffusion_score > cfg.stage_spread_diffusion:
        return "扩散"

    # 确认阶段
    if (
        momentum_score >= cfg.stage_confirm_momentum
        and cfg.stage_confirm_diffusion_low <= diffusion_score <= cfg.stage_confirm_diffusion_high
    ):
        return "确认"

    # 启动阶段
    if momentum_score >= cfg.stage_start_momentum and diffusion_score < cfg.stage_start_diffusion:
        return "启动"

    # 无行情
    if momentum_score < cfg.stage_no_action_momentum or diffusion_score < cfg.stage_no_action_diffusion:
        return "无行情"

    # 默认
    return "无行情"


def _determine_direction(score: float, cfg: PredictionConfig) -> str:
    """根据综合分判断方向"""
    if score >= cfg.direction_thresholds["强"]:
        return "强"
    elif score >= cfg.direction_thresholds["偏强"]:
        return "偏强"
    elif score >= cfg.direction_thresholds["中性"]:
        return "中性"
    elif score >= cfg.direction_thresholds["偏弱"]:
        return "偏弱"
    else:
        return "弱"


# ---------------------------------------------------------------------------
# 公共接口
# ---------------------------------------------------------------------------

def predict_single(
    row: pd.Series,
    cfg: PredictionConfig = DEFAULT_PREDICTION_CONFIG,
    all_reversals: pd.Series | None = None,
    all_vpt: pd.Series | None = None,
    all_ind_mom_5d: pd.Series | None = None,
    all_roe_ref: pd.Series | None = None,
    all_eps_g_ref: pd.Series | None = None,
    all_gm_ref: pd.Series | None = None,
) -> TrendPrediction:
    """对单只股票生成趋势预测

    Args:
        row: features DataFrame 中的一行（含所有图谱特征列）
        cfg: 预测配置
        all_reversals: 全市场反转因子分布（用于百分位排名）

    Returns:
        TrendPrediction
    """
    trade_date = str(row.get("trade_date", ""))
    ts_code = str(row["ts_code"])
    industry = str(row.get("industry", ""))

    # 行业动量分（需要行业排名百分位）
    industry_rank = int(row.get("industry_rank", 0))
    # 使用实际行业总数（修复硬编码 bug）
    total_industries = int(row.get("total_industries", 110))
    industry_rank_pctrank = max(0.0, 1.0 - (industry_rank - 1) / total_industries) if industry_rank > 0 else 0.5

    mom_score, mom_evidence = _score_industry_momentum(row, industry_rank_pctrank)

    # 个股相对强度分
    rank_in_industry = int(row.get("rank_in_industry", 0))
    industry_stock_count = int(row.get("industry_stock_count", 1))
    rel_score, rel_evidence = _score_relative_strength(row, rank_in_industry, industry_stock_count)

    # 扩散度分
    diffusion_raw = float(row.get("diffusion_score", 0.5))
    leader_momentum = float(row.get("leader_momentum", 0.0))
    diff_score, diff_evidence = _score_diffusion(diffusion_raw, leader_momentum)

    # 资金流分
    net_inflow = float(row.get("net_big_inflow_5d", 0.0))
    industry_inflow_diff = float(row.get("industry_inflow_diffusion", 0.5))
    mf_score, mf_evidence = _score_moneyflow(net_inflow, industry_inflow_diff)

    # 拥挤度分（反向）
    crowding_raw = float(row.get("crowding_score", 0.5))
    crowd_result = _score_crowding(crowding_raw)
    crowd_score, crowd_evidence = crowd_result[0], crowd_result[1]
    crowd_risks = crowd_result[2] if len(crowd_result) > 2 else []

    # 短期反转分（均值回复因子）
    reversal_5d = float(row.get("reversal_5d", 0.0))
    rev_score, rev_evidence = _score_reversal(reversal_5d, all_reversals)

    # 图谱邻居动量分
    neighbor_mom = float(row.get("neighbor_momentum", 0.0))
    nb_score, nb_evidence = _score_neighbor_momentum(neighbor_mom)

    # 综合分（加权归一化到 0-140，保持方向阈值兼容）
    raw_scores = {
        "industry_momentum": mom_score,
        "relative_strength": rel_score,
        "diffusion": diff_score,
        "moneyflow": mf_score,
        "crowding": crowd_score,
        "reversal": rev_score,
        "neighbor_momentum": nb_score,
    }
    weights = cfg.dimension_weights

    # 短期行业动量因子（第 9 维度）
    ind_mom_5d = float(row.get("industry_momentum_5d", 0.0))
    stm_score, stm_evidence = _score_short_term_momentum(ind_mom_5d, all_ind_mom_5d)
    raw_scores["short_term_momentum"] = stm_score

    # 量价因子（第 10 维度）
    vpt = float(row.get("volume_price_trend", 0.0))
    vp_score, vp_evidence = _score_volume_price(vpt, all_vpt)
    raw_scores["volume_price"] = vp_score

    # 行业关联动量溢出（第 12 维度，图结构因子）
    icm_val = float(row.get("industry_corr_momentum", 0.0))
    all_icm_ref = row.get("_all_icm_ref", None)  # 由 predict_batch 传入
    icm_score, icm_evidence = _score_industry_corr_momentum(icm_val, all_icm_ref)
    raw_scores["industry_corr_momentum"] = icm_score

    # 基本面因子（第 11 维度）
    roe_val = float(row.get("fundamental_roe", 0.0))
    eps_g = float(row.get("eps_growth", 0.0))
    gm_val = float(row.get("gross_margin", 0.0))
    fund_score, fund_evidence = _score_fundamental(
        roe_val, eps_g, gm_val,
        all_roe=all_roe_ref, all_eps_g=all_eps_g_ref, all_gm=all_gm_ref,
    )
    raw_scores["fundamental"] = fund_score

    # 低波动因子（第 13 维度）
    lv_signal = float(row.get("low_volatility_signal", 0.0))
    all_lv_ref = row.get("_all_lv_ref", None)
    lv_score, lv_evidence = _score_low_volatility(lv_signal, all_lv_ref)
    raw_scores["low_volatility"] = lv_score

    # 流动性因子（第 14 维度）
    liq_signal = float(row.get("liquidity_signal", 0.0))
    all_liq_ref = row.get("_all_liq_ref", None)
    liq_score, liq_evidence = _score_liquidity(liq_signal, all_liq_ref)
    raw_scores["liquidity"] = liq_score

    # 指数成分因子（第 15 维度）
    is_index = float(row.get("is_index_component", 0.0))
    all_index_ref = row.get("_all_index_ref", None)
    idx_score, idx_evidence = _score_index_membership(is_index, all_index_ref)
    raw_scores["index_membership"] = idx_score

    # 非线性评分变换 (Jegadeesh & Titman 1993)
    # score_power > 1 放大高/中分差异，提升 Top N 选股精度
    power = cfg.score_power
    if power != 1.0:
        # 归一化到 [0,1]，施加幂次，再缩放回 [0,20]
        transformed = {}
        for k, s in raw_scores.items():
            norm_s = max(0.0, min(1.0, s / 20.0))
            transformed[k] = (norm_s ** power) * 20.0
        raw_scores = transformed

    # IC-Signed 方向翻转 (A股中期反转效应: Jegadeesh 1990)
    # 对负 IC 因子翻转得分: score = 20 - score
    # 使得翻转后高分对应高前瞻收益
    factor_signs = cfg.factor_signs
    for k in list(raw_scores.keys()):
        if factor_signs.get(k, 1) < 0:
            raw_scores[k] = 20.0 - raw_scores[k]

    # 加权平均后归一化: weighted_avg = sum(w*s)/sum(w), total = weighted_avg * 7
    total_weight = sum(weights.get(k, 1.0) for k in raw_scores)
    weighted_sum = sum(weights.get(k, 1.0) * s for k, s in raw_scores.items())
    weighted_avg = weighted_sum / total_weight if total_weight > 0 else 0.0
    base_score = weighted_avg * 7  # 归一化到 0-140

    # 因子协同效应 (Chan et al. 1996)
    # 对翻转后同向的高分因子给予协同加分
    synergy_keys = [k for k in raw_scores if weights.get(k, 0) > 0]
    pos_scores = [raw_scores.get(k, 0.0) for k in synergy_keys]
    min_ratio = min(pos_scores) / 20.0 if pos_scores else 0.0
    synergy_p = cfg.synergy_power
    if synergy_p > 0 and min_ratio > 0:
        synergy = (min_ratio ** synergy_p) * 30.0
    else:
        synergy = 0.0
    total_score = round(base_score + synergy, 2)

    # 因子交叉互动项 (Chordia & Shivakumar 2002)
    # moneyflow × industry_momentum 互动：两者同时强时产生超额收益
    # 使用幂次 (product^power) 使互动项只在两者都极强时才给显著加分
    # 注意：此处使用变换+翻转后的分数计算互动，但不能覆盖原始 mf_score/mom_score（输出契约用原始分）
    _mf_for_inter = raw_scores.get("moneyflow", 0.0)
    _im_for_inter = raw_scores.get("industry_momentum", 0.0)
    product = (_mf_for_inter / 20.0) * (_im_for_inter / 20.0)
    interaction = (product ** cfg.interaction_power) * cfg.interaction_max_bonus
    total_score = round(total_score + interaction, 2)

    # 基本面附加加分（不进入加权平均，作为独立加分项）
    fund_raw = raw_scores.get("fundamental", 0.0)
    fund_norm = max(0.0, min(1.0, fund_raw / 20.0))
    fund_bonus = (fund_norm ** cfg.fundamental_bonus_power) * cfg.fundamental_bonus_max
    total_score = round(total_score + fund_bonus, 2)

    # 阶段
    momentum_5d = float(row.get("industry_momentum_5d", 0.0))
    momentum_20d = float(row.get("industry_momentum_20d", 0.0))
    stage = _determine_stage(
        momentum_score=mom_score,
        diffusion_score=diffusion_raw,
        crowding_score_raw=crowding_raw,
        momentum_5d=momentum_5d,
        momentum_20d=momentum_20d,
        cfg=cfg,
    )

    # 风险覆盖层：在基础因子总分之后连续扣分。
    base_score = total_score
    overlay = compute_risk_overlay(
        momentum_5d=momentum_5d,
        momentum_20d=momentum_20d,
        diffusion_score=diffusion_raw,
        is_retreat=stage == "退潮",
        cfg=cfg,
    )
    total_score = round(max(0.0, base_score - overlay.total_penalty), 2)

    # 方向必须基于风险覆盖后的最终分数。
    direction = _determine_direction(total_score, cfg)

    # 汇总 evidence 和 risks
    all_evidence = mom_evidence + rel_evidence + diff_evidence + mf_evidence + crowd_evidence + rev_evidence + nb_evidence + vp_evidence + stm_evidence + fund_evidence + icm_evidence + lv_evidence + liq_evidence + idx_evidence
    all_risks = crowd_risks.copy()
    if stage == "拥挤":
        all_risks.append("行业处于拥挤阶段，注意止盈")
    elif stage == "退潮":
        all_risks.append("行业动量衰退，趋势可能继续走弱")
    if overlay.retreat_penalty > 0:
        all_risks.append(f"退潮风险覆盖扣分 {overlay.retreat_penalty:.2f}")
    if overlay.industry_turning_penalty > 0:
        all_risks.append(
            f"行业景气拐点风险扣分 {overlay.industry_turning_penalty:.2f}"
        )
    if diffusion_raw >= 0.85:
        all_risks.append(f"行业扩散度极高({diffusion_raw:.2f})，或已过热")

    return TrendPrediction(
        ts_code=ts_code,
        trade_date=trade_date,
        score=round(total_score, 2),
        direction=direction,
        stage=stage,
        industry=industry,
        industry_momentum_rank=industry_rank,
        relative_strength=float(row.get("rel_strength_20d", 0.0)),
        diffusion_score=round(diffusion_raw, 4),
        crowding_score=round(crowding_raw, 4),
        base_score=round(base_score, 2),
        retreat_severity=round(overlay.retreat_severity, 6),
        retreat_penalty=round(overlay.retreat_penalty, 6),
        industry_turning_severity=round(
            overlay.industry_turning_severity, 6
        ),
        industry_turning_penalty=round(
            overlay.industry_turning_penalty, 6
        ),
        risk_overlay_penalty=round(overlay.total_penalty, 6),
        rotation_phase=str(row.get("rotation_phase", "")),
        evidence=all_evidence,
        risks=all_risks,
        industry_momentum_score=round(mom_score, 2),
        relative_strength_score=round(rel_score, 2),
        diffusion_dim_score=round(diff_score, 2),
        moneyflow_score=round(mf_score, 2),
        crowding_dim_score=round(crowd_score, 2),
        reversal_score=round(rev_score, 2),
        neighbor_momentum_score=round(nb_score, 2),
        volume_price_score=round(vp_score, 2),
        short_term_momentum_score=round(stm_score, 2),
        fundamental_score=round(fund_score, 2),
        industry_corr_mom_score=round(icm_score, 2),
        low_volatility_score=round(lv_score, 2),
        liquidity_score=round(liq_score, 2),
        index_membership_score=round(idx_score, 2),
    )


def predict_batch(
    features_df: pd.DataFrame,
    cfg: PredictionConfig = DEFAULT_PREDICTION_CONFIG,
    top_n_evidence: int = 100,
) -> pd.DataFrame:
    """批量生成趋势预测

    内部委托给 predict_batch_vectorized 进行快速向量化评分，
    然后对 top_n_evidence 只股票生成 evidence/risks 文本说明。

    这确保评分逻辑只维护一份（向量化版本），同时保留可解释性输出。

    Args:
        features_df: compute_all_graph_features 输出的特征 DataFrame
        cfg: 预测配置
        top_n_evidence: 为得分最高的 N 只股票生成 evidence/risks 文本

    Returns:
        DataFrame: 每行对应一只股票的预测结果
    """
    if features_df.empty:
        return pd.DataFrame()

    # 1. 向量化评分（唯一评分路径）
    result = predict_batch_vectorized(features_df, cfg)

    # 2. 为 Top N 股票生成 evidence/risks 文本（可解释性）
    if top_n_evidence > 0 and not result.empty:
        all_reversals = features_df["reversal_5d"] if "reversal_5d" in features_df.columns else pd.Series(dtype=float)
        all_vpt = features_df["volume_price_trend"] if "volume_price_trend" in features_df.columns else pd.Series(dtype=float)
        all_ind_mom_5d = features_df["industry_momentum_5d"] if "industry_momentum_5d" in features_df.columns else pd.Series(dtype=float)
        all_icm = features_df["industry_corr_momentum"] if "industry_corr_momentum" in features_df.columns else pd.Series(dtype=float)

        # 取得分最高的 N 只
        top_indices = result.nlargest(min(top_n_evidence, len(result)), "score").index
        top_codes = set(result.loc[top_indices, "ts_code"].tolist())

        # 只为 top N 生成 evidence
        evidence_map: dict[str, str] = {}
        risks_map: dict[str, str] = {}
        for _, row in features_df[features_df["ts_code"].isin(top_codes)].iterrows():
            row_with_ref = row.copy()
            row_with_ref["_all_icm_ref"] = all_icm
            pred = predict_single(row_with_ref, cfg, all_reversals=all_reversals, all_vpt=all_vpt, all_ind_mom_5d=all_ind_mom_5d)
            evidence_map[pred.ts_code] = "|".join(pred.evidence) if pred.evidence else ""
            risks_map[pred.ts_code] = "|".join(pred.risks) if pred.risks else ""

        result["evidence"] = result["ts_code"].map(evidence_map).fillna("")
        result["risks"] = result["ts_code"].map(risks_map).fillna("")

    return result


# ---------------------------------------------------------------------------
# 向量化评分函数 (numpy 批量运算，替代逐行 iterrows)
# ---------------------------------------------------------------------------

def _vec_pctrank_asc(values: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """升序百分位排名: (ref < v).sum() / len(ref)"""
    ref_sorted = np.sort(ref)
    return np.searchsorted(ref_sorted, values, side="left") / len(ref_sorted)


def _score_industry_momentum_vec(rank_pctrank: np.ndarray) -> np.ndarray:
    return np.clip(rank_pctrank * 20, 0, 20)


def _score_relative_strength_vec(
    rank_in_industry: np.ndarray, industry_count: np.ndarray,
) -> np.ndarray:
    pctrank = np.where(
        (industry_count <= 1) | (rank_in_industry <= 0),
        0.5,
        1.0 - (rank_in_industry - 1) / np.maximum(1, industry_count - 1),
    )
    return np.clip(pctrank * 20, 0, 20)


def _score_diffusion_vec(
    diffusion_score: np.ndarray, leader_momentum: np.ndarray,
) -> np.ndarray:
    diff_deviation = np.abs(diffusion_score - 0.6)
    diff_base = np.maximum(0.0, 1.0 - diff_deviation / 0.6) * 14
    leader_bonus = np.where(
        np.isnan(leader_momentum), 0.0,
        np.where(leader_momentum > 0,
                 np.minimum(6.0, leader_momentum * 100),
                 np.maximum(-3.0, leader_momentum * 100)),
    )
    score = np.where(np.isnan(diffusion_score), 10.0, diff_base + leader_bonus)
    return np.clip(score, 0, 20)


def _score_moneyflow_vec(
    net_big_inflow: np.ndarray, industry_inflow_diffusion: np.ndarray,
) -> np.ndarray:
    valid = ~np.isnan(net_big_inflow) & ~np.isnan(industry_inflow_diffusion)
    sign = np.sign(net_big_inflow)
    inflow_score = np.where(
        valid,
        np.clip(sign * np.minimum(12.0, np.abs(net_big_inflow) / 1e7), -4, 12),
        0.0,
    )
    diff_score = np.where(valid, np.clip(industry_inflow_diffusion * 8, 0, 8), 0.0)
    return np.where(valid, np.clip(inflow_score + diff_score, 0, 20), 10.0)


def _score_crowding_vec(crowding_score: np.ndarray) -> np.ndarray:
    return np.where(
        np.isnan(crowding_score), 10.0,
        np.clip((1.0 - crowding_score) * 20, 0, 20),
    )


def _score_reversal_vec(reversal_5d: np.ndarray, all_rev: np.ndarray, all_len: int) -> np.ndarray:
    # 原始反转方向: (all_reversals < reversal_5d).sum() / len(all_reversals)
    # 即升序排名: 值越大(近期跌得多) → 排名越高 → 得分越高
    if len(all_rev) > 100:
        ref_sorted = np.sort(all_rev)
        pctrank = np.searchsorted(ref_sorted, reversal_5d, side="left") / all_len
    else:
        pctrank = np.clip(0.5 + reversal_5d * 5, 0, 1)
    pctrank = np.where(np.isnan(reversal_5d), 0.5, pctrank)
    return np.clip(pctrank * 20, 0, 20)


def _score_neighbor_momentum_vec(neighbor_momentum: np.ndarray) -> np.ndarray:
    normalized = np.clip(neighbor_momentum * 10, -1, 1)
    normalized = np.where(np.isnan(neighbor_momentum), 0.0, normalized)
    return np.clip((normalized + 1.0) * 10, 0, 20)


def _score_short_term_momentum_vec(
    ind_mom_5d: np.ndarray, all_ind_mom_5d: np.ndarray,
) -> np.ndarray:
    if len(all_ind_mom_5d) > 10:
        pctrank = _vec_pctrank_asc(ind_mom_5d, all_ind_mom_5d)
    else:
        pctrank = np.clip(0.5 + ind_mom_5d * 5, 0, 1)
    pctrank = np.where(np.isnan(ind_mom_5d), 0.5, pctrank)
    return np.clip(pctrank * 20, 0, 20)


def _score_volume_price_vec(vpt: np.ndarray, all_vpt: np.ndarray) -> np.ndarray:
    if len(all_vpt) > 100:
        pctrank = _vec_pctrank_asc(vpt, all_vpt)
    else:
        pctrank = np.clip(0.5 + vpt * 3, 0, 1)
    pctrank = np.where(np.isnan(vpt), 0.5, pctrank)
    return np.clip(pctrank * 20, 0, 20)


def _score_industry_corr_momentum_vec(
    icm: np.ndarray, all_icm: np.ndarray,
) -> np.ndarray:
    """向量化行业关联动量溢出评分"""
    if len(all_icm) > 100:
        pctrank = _vec_pctrank_asc(icm, all_icm)
    else:
        pctrank = np.clip(0.5 + icm * 5, 0, 1)
    pctrank = np.where(np.isnan(icm), 0.5, pctrank)
    return np.clip(pctrank * 20, 0, 20)


# ---------------------------------------------------------------------------
# 向量化批量预测（替代 predict_batch 中的 iterrows 循环）
# ---------------------------------------------------------------------------

def predict_batch_vectorized(
    features_df: pd.DataFrame,
    cfg: PredictionConfig = DEFAULT_PREDICTION_CONFIG,
) -> pd.DataFrame:
    """批量生成趋势预测（向量化版本）

    与 predict_batch 功能完全一致，使用 numpy 向量化运算替代逐行
    iterrows() 循环，大幅提升批量预测速度。

    注意: evidence 和 risks 列在快速路径中为空字符串。
    所有数值列（score, direction, stage, 各因子得分）与原版本精确一致。
    """
    if features_df.empty:
        return pd.DataFrame()

    n = len(features_df)
    print(f"[predictor-vec] Generating predictions for {n} stocks (vectorized)...")

    # ---- 提取列（保留 NaN，不做 fillna） ----
    industry_rank = features_df["industry_rank"].values.astype(float)
    total_industries = features_df["total_industries"].values.astype(float)
    rank_in_industry = features_df["rank_in_industry"].values.astype(float)
    industry_stock_count = features_df["industry_stock_count"].values.astype(float)
    diffusion_raw = features_df["diffusion_score"].values.astype(float)
    leader_mom = features_df["leader_momentum"].values.astype(float)
    net_big_inflow = features_df["net_big_inflow_5d"].values.astype(float)
    industry_inflow_diff = features_df["industry_inflow_diffusion"].values.astype(float)
    crowding_raw = features_df["crowding_score"].values.astype(float)
    reversal_5d = features_df["reversal_5d"].values.astype(float) if "reversal_5d" in features_df.columns else np.full(n, np.nan)
    neighbor_mom = features_df["neighbor_momentum"].values.astype(float)
    ind_mom_5d = features_df["industry_momentum_5d"].values.astype(float) if "industry_momentum_5d" in features_df.columns else np.full(n, np.nan)
    vpt = features_df["volume_price_trend"].values.astype(float) if "volume_price_trend" in features_df.columns else np.full(n, np.nan)
    ind_mom_20d = features_df["industry_momentum_20d"].fillna(0.0).values.astype(float)

    # ---- 图结构因子列 ----
    icm = features_df["industry_corr_momentum"].values.astype(float) if "industry_corr_momentum" in features_df.columns else np.full(n, np.nan)

    # ---- 因子多元化列 ----
    lv_signal = features_df["low_volatility_signal"].values.astype(float) if "low_volatility_signal" in features_df.columns else np.full(n, np.nan)
    liq_signal = features_df["liquidity_signal"].values.astype(float) if "liquidity_signal" in features_df.columns else np.full(n, np.nan)

    # ---- 指数成分列 ----
    is_index = features_df["is_index_component"].values.astype(float) if "is_index_component" in features_df.columns else np.full(n, 0.0)

    # ---- 基本面因子列 ----
    fund_roe = features_df["fundamental_roe"].values.astype(float) if "fundamental_roe" in features_df.columns else np.full(n, np.nan)
    fund_eps_g = features_df["eps_growth"].values.astype(float) if "eps_growth" in features_df.columns else np.full(n, np.nan)
    fund_gm = features_df["gross_margin"].values.astype(float) if "gross_margin" in features_df.columns else np.full(n, np.nan)

    # ---- 百分位排名参考分布（dropna） ----
    all_rev = features_df["reversal_5d"].dropna().values if "reversal_5d" in features_df.columns else np.array([], dtype=float)
    all_vpt_arr = features_df["volume_price_trend"].dropna().values if "volume_price_trend" in features_df.columns else np.array([], dtype=float)
    all_ind_mom_5d_arr = features_df["industry_momentum_5d"].dropna().values if "industry_momentum_5d" in features_df.columns else np.array([], dtype=float)
    # 基本面参考分布
    all_roe_arr = features_df["fundamental_roe"].dropna().values if "fundamental_roe" in features_df.columns else np.array([], dtype=float)
    all_eps_g_arr = features_df["eps_growth"].dropna().values if "eps_growth" in features_df.columns else np.array([], dtype=float)
    all_gm_arr = features_df["gross_margin"].dropna().values if "gross_margin" in features_df.columns else np.array([], dtype=float)
    # 图结构因子参考分布
    all_icm_arr = features_df["industry_corr_momentum"].dropna().values if "industry_corr_momentum" in features_df.columns else np.array([], dtype=float)
    # 因子多元化参考分布
    all_lv_arr = features_df["low_volatility_signal"].dropna().values if "low_volatility_signal" in features_df.columns else np.array([], dtype=float)
    all_liq_arr = features_df["liquidity_signal"].dropna().values if "liquidity_signal" in features_df.columns else np.array([], dtype=float)

    # ---- 1. 行业动量 ----
    # NaN → pctrank=0.5 (与原始一致: industry_rank > 0 else 0.5)
    rank_nan = np.isnan(industry_rank) | (industry_rank == 0)
    rank_pctrank = np.where(
        rank_nan, 0.5,
        np.maximum(0.0, 1.0 - (industry_rank - 1) / np.where(np.isnan(total_industries), 110.0, total_industries)),
    )
    s_ind_mom = np.clip(rank_pctrank * 20, 0, 20)

    # ---- 2. 相对强度 ----
    # NaN rank_in_industry 或 industry_count<=1 → score=10.0
    rel_nan = np.isnan(rank_in_industry) | (rank_in_industry <= 0)
    ind_count_nan = np.isnan(industry_stock_count) | (industry_stock_count <= 1)
    rel_cond = rel_nan | ind_count_nan
    rel_pctrank = np.where(
        rel_cond, 0.5,
        1.0 - (np.where(rel_nan, 1.0, rank_in_industry) - 1) / np.maximum(1, np.where(ind_count_nan, 2.0, industry_stock_count) - 1),
    )
    s_rel_str = np.where(rel_cond, 10.0, np.clip(rel_pctrank * 20, 0, 20))

    # ---- 3. 扩散度 ----
    diff_nan = np.isnan(diffusion_raw)
    diff_deviation = np.abs(np.where(diff_nan, 0.6, diffusion_raw) - 0.6)
    diff_base = np.maximum(0.0, 1.0 - diff_deviation / 0.6) * 14
    leader_bonus = np.where(
        np.isnan(leader_mom), 0.0,
        np.where(leader_mom > 0,
                 np.minimum(6.0, leader_mom * 100),
                 np.maximum(-3.0, leader_mom * 100)),
    )
    s_diffusion = np.where(diff_nan, 10.0, np.clip(diff_base + leader_bonus, 0, 20))

    # ---- 4. 资金流 ----
    mf_valid = ~np.isnan(net_big_inflow) & ~np.isnan(industry_inflow_diff)
    sign = np.sign(np.where(mf_valid, net_big_inflow, 0.0))
    inflow_score = np.clip(sign * np.minimum(12.0, np.abs(np.where(mf_valid, net_big_inflow, 0.0)) / 1e7), -4, 12)
    diff_score_mf = np.clip(np.where(mf_valid, industry_inflow_diff, 0.5) * 8, 0, 8)
    s_moneyflow = np.where(mf_valid, np.clip(inflow_score + diff_score_mf, 0, 20), 10.0)

    # ---- 5. 拥挤度 ----
    s_crowding = np.where(
        np.isnan(crowding_raw), 10.0,
        np.clip((1.0 - crowding_raw) * 20, 0, 20),
    )

    # ---- 6. 反转 ----
    all_rev_len = len(features_df["reversal_5d"]) if "reversal_5d" in features_df.columns else n
    s_reversal = _score_reversal_vec(reversal_5d, all_rev, all_rev_len)

    # ---- 7. 邻居动量 ----
    nb_nan = np.isnan(neighbor_mom)
    normalized_nb = np.clip(np.where(nb_nan, 0.0, neighbor_mom) * 10, -1, 1)
    s_neighbor = np.where(nb_nan, 10.0, np.clip((normalized_nb + 1.0) * 10, 0, 20))

    # ---- 8. 短期行业动量 ----
    stm_nan = np.isnan(ind_mom_5d)
    if len(all_ind_mom_5d_arr) > 10:
        stm_ref = np.sort(all_ind_mom_5d_arr)
        # 分母用全 Series 长度（含 NaN），与原版 (all_ind < v).sum()/len(all_ind) 一致
        all_len = len(features_df["industry_momentum_5d"]) if "industry_momentum_5d" in features_df.columns else n
        stm_pctrank = np.searchsorted(stm_ref, np.where(stm_nan, -np.inf, ind_mom_5d), side="left") / all_len
    else:
        stm_pctrank = np.clip(0.5 + np.where(stm_nan, 0.0, ind_mom_5d) * 5, 0, 1)
    stm_pctrank = np.where(stm_nan, 0.5, stm_pctrank)
    s_short_mom = np.clip(stm_pctrank * 20, 0, 20)

    # ---- 9. 量价 ----
    vp_nan = np.isnan(vpt)
    if len(all_vpt_arr) > 100:
        vp_ref = np.sort(all_vpt_arr)
        all_len_vp = len(features_df["volume_price_trend"]) if "volume_price_trend" in features_df.columns else n
        vp_pctrank = np.searchsorted(vp_ref, np.where(vp_nan, -np.inf, vpt), side="left") / all_len_vp
    else:
        vp_pctrank = np.clip(0.5 + np.where(vp_nan, 0.0, vpt) * 3, 0, 1)
    vp_pctrank = np.where(vp_nan, 0.5, vp_pctrank)
    s_vol_price = np.clip(vp_pctrank * 20, 0, 20)

    # ---- 9.5 行业关联动量溢出（图结构因子） ----
    s_icm = _score_industry_corr_momentum_vec(icm, all_icm_arr)

    # ---- 10. 基本面 ----
    # ROE 分 (0-8): 百分位排名
    roe_nan = np.isnan(fund_roe) | (fund_roe == 0)
    if len(all_roe_arr) > 50:
        roe_ref = np.sort(all_roe_arr)
        roe_pctrank = np.where(roe_nan, 0.5, np.searchsorted(roe_ref, np.where(roe_nan, 0, fund_roe), side="left") / len(roe_ref))
    else:
        roe_pctrank = np.where(roe_nan, 0.5, np.clip(np.where(roe_nan, 0, fund_roe) / 30.0, 0, 1))
    s_fund_roe = roe_pctrank * 8.0

    # EPS增速分 (0-6)
    eps_nan = np.isnan(fund_eps_g)
    if len(all_eps_g_arr) > 50:
        eps_ref = np.sort(all_eps_g_arr)
        eps_pctrank = np.where(eps_nan, 0.5, np.searchsorted(eps_ref, np.where(eps_nan, 0, fund_eps_g), side="left") / len(eps_ref))
    else:
        eps_pctrank = np.where(eps_nan, 0.5, np.clip(0.5 + np.where(eps_nan, 0, fund_eps_g) * 0.5, 0, 1))
    s_fund_eps = eps_pctrank * 6.0

    # 毛利率分 (0-6)
    gm_nan = np.isnan(fund_gm) | (fund_gm == 0)
    if len(all_gm_arr) > 50:
        gm_ref = np.sort(all_gm_arr)
        gm_pctrank = np.where(gm_nan, 0.5, np.searchsorted(gm_ref, np.where(gm_nan, 0, fund_gm), side="left") / len(gm_ref))
    else:
        gm_pctrank = np.where(gm_nan, 0.5, np.clip(np.where(gm_nan, 0, fund_gm) / 60.0, 0, 1))
    s_fund_gm = gm_pctrank * 6.0

    s_fundamental = np.clip(s_fund_roe + s_fund_eps + s_fund_gm, 0, 20)

    # ---- 11. 低波动因子 ----
    lv_nan = np.isnan(lv_signal)
    if len(all_lv_arr) > 100:
        lv_ref = np.sort(all_lv_arr)
        lv_pctrank = np.searchsorted(lv_ref, np.where(lv_nan, -np.inf, lv_signal), side="left") / len(features_df["low_volatility_signal"]) if "low_volatility_signal" in features_df.columns else n
    else:
        lv_pctrank = np.where(lv_nan, 0.5, np.clip(lv_signal, 0, 1))
    lv_pctrank = np.where(lv_nan, 0.5, lv_pctrank)
    s_low_vol = np.clip(lv_pctrank * 20, 0, 20)

    # ---- 12. 流动性因子 ----
    liq_nan = np.isnan(liq_signal)
    if len(all_liq_arr) > 100:
        liq_ref = np.sort(all_liq_arr)
        liq_pctrank = np.searchsorted(liq_ref, np.where(liq_nan, -np.inf, liq_signal), side="left") / len(features_df["liquidity_signal"]) if "liquidity_signal" in features_df.columns else n
    else:
        liq_pctrank = np.where(liq_nan, 0.5, np.clip(liq_signal, 0, 1))
    liq_pctrank = np.where(liq_nan, 0.5, liq_pctrank)
    s_liquidity = np.clip(liq_pctrank * 20, 0, 20)

    # ---- 13. 指数成分因子 ----
    idx_nan = np.isnan(is_index)
    s_index_mem = np.where(
        idx_nan, 10.0,
        np.where(is_index > 0.5, 16.0, 6.0),
    )

    scores = {
        "industry_momentum": s_ind_mom,
        "relative_strength": s_rel_str,
        "diffusion": s_diffusion,
        "moneyflow": s_moneyflow,
        "crowding": s_crowding,
        "reversal": s_reversal,
        "neighbor_momentum": s_neighbor,
        "short_term_momentum": s_short_mom,
        "volume_price": s_vol_price,
        "fundamental": s_fundamental,
        "industry_corr_momentum": s_icm,
        "low_volatility": s_low_vol,
        "liquidity": s_liquidity,
        "index_membership": s_index_mem,
    }

    # 保存原始分数用于输出（f_* 列存储变换前的原始分）
    scores_raw = {k: v.copy() if hasattr(v, 'copy') else v for k, v in scores.items()}

    # ---- 非线性变换 (Jegadeesh & Titman 1993) ----
    power = cfg.score_power
    if power != 1.0:
        for k in scores:
            norm_s = np.clip(scores[k] / 20.0, 0, 1)
            scores[k] = (norm_s ** power) * 20.0

    # ---- IC-Signed 方向翻转 (A股中期反转效应) ----
    factor_signs = cfg.factor_signs
    for k in list(scores.keys()):
        if factor_signs.get(k, 1) < 0:
            scores[k] = 20.0 - scores[k]

    # ---- 加权平均 ----
    weights = cfg.dimension_weights
    total_weight = sum(weights.get(k, 1.0) for k in scores)
    weighted_sum = sum(weights.get(k, 1.0) * s for k, s in scores.items())
    weighted_avg = weighted_sum / total_weight if total_weight > 0 else np.zeros(n)
    base_score = weighted_avg * 7

    # ---- 因子协同效应 (Chan et al. 1996) ----
    synergy_keys = [k for k in scores if weights.get(k, 0) > 0]
    pos_scores = [scores[k] for k in synergy_keys] if synergy_keys else [np.zeros(n)]
    min_ratio = np.minimum.reduce(pos_scores) / 20.0 if pos_scores else np.zeros(n)
    synergy_p = cfg.synergy_power
    synergy = np.where(
        (synergy_p > 0) & (min_ratio > 0),
        (min_ratio ** synergy_p) * 30.0,
        0.0,
    )
    total_score = np.round(base_score + synergy, 2)

    # ---- 因子交叉互动项 (Chordia & Shivakumar 2002) ----
    mf_arr = scores.get("moneyflow", np.zeros(n))
    im_arr = scores.get("industry_momentum", np.zeros(n))
    product = (mf_arr / 20.0) * (im_arr / 20.0)
    interaction = (product ** cfg.interaction_power) * cfg.interaction_max_bonus
    total_score = np.round(total_score + interaction, 2)

    # ---- 基本面附加加分 ----
    fund_arr = scores.get("fundamental", np.zeros(n))
    fund_norm = np.clip(fund_arr / 20.0, 0, 1)
    fund_bonus = (fund_norm ** cfg.fundamental_bonus_power) * cfg.fundamental_bonus_max
    total_score = np.round(total_score + fund_bonus, 2)

    # ---- 阶段 ----
    # 使用原始（未 fillna）的 diffusion 和 crowding 值
    diffusion_for_stage = features_df["diffusion_score"].fillna(0.5).values.astype(float)
    crowding_for_stage = features_df["crowding_score"].fillna(0.5).values.astype(float)
    ind_mom_5d_for_stage = features_df["industry_momentum_5d"].fillna(0.0).values.astype(float)
    # 与单只路径保持一致：阶段判断使用未经 score_power 变换且未经 IC 翻转的原始 mom_score
    # （predict_single 中 _determine_stage 接收的是 _score_industry_momentum 输出的原始分）
    mom_score = scores_raw["industry_momentum"]
    stage = np.select(
        [
            (ind_mom_5d_for_stage < ind_mom_20d * 0.3) & (ind_mom_20d > 0),
            (diffusion_for_stage >= cfg.stage_spread_diffusion) & (crowding_for_stage >= 0.75),
            (mom_score >= cfg.stage_spread_momentum) & (diffusion_for_stage > cfg.stage_spread_diffusion),
            (mom_score >= cfg.stage_confirm_momentum)
            & (diffusion_for_stage >= cfg.stage_confirm_diffusion_low)
            & (diffusion_for_stage <= cfg.stage_confirm_diffusion_high),
            (mom_score >= cfg.stage_start_momentum) & (diffusion_for_stage < cfg.stage_start_diffusion),
            (mom_score < cfg.stage_no_action_momentum) | (diffusion_for_stage < cfg.stage_no_action_diffusion),
        ],
        ["退潮", "拥挤", "扩散", "确认", "启动", "无行情"],
        default="无行情",
    )

    # ---- 风险覆盖层 ----
    base_score = total_score.copy()
    (
        retreat_severity,
        retreat_penalty,
        turning_severity,
        turning_penalty,
        risk_overlay_penalty,
    ) = _compute_risk_overlay_vec(
        momentum_5d=ind_mom_5d_for_stage,
        momentum_20d=ind_mom_20d,
        diffusion_score=diffusion_for_stage,
        is_retreat=stage == "退潮",
        cfg=cfg,
    )
    total_score = np.round(
        np.maximum(0.0, base_score - risk_overlay_penalty),
        2,
    )

    # ---- 方向 ----
    th = cfg.direction_thresholds
    direction = np.select(
        [total_score >= th["强"], total_score >= th["偏强"],
         total_score >= th["中性"], total_score >= th["偏弱"]],
        ["强", "偏强", "中性", "偏弱"],
        default="弱",
    )

    result = pd.DataFrame({
        "ts_code": features_df["ts_code"].values,
        "trade_date": features_df["trade_date"].values,
        "score": total_score,
        "base_score": np.round(base_score, 2),
        "direction": direction,
        "stage": stage,
        "industry": features_df["industry"].values,
        "industry_momentum_rank": np.where(np.isnan(industry_rank), 0, industry_rank).astype(int),
        "relative_strength": features_df["rel_strength_20d"].fillna(0.0).values,
        "diffusion_score": np.round(np.where(np.isnan(diffusion_raw), 0.5, diffusion_raw), 4),
        "crowding_score": np.round(np.where(np.isnan(crowding_raw), 0.5, crowding_raw), 4),
        "retreat_severity": np.round(retreat_severity, 6),
        "retreat_penalty": np.round(retreat_penalty, 6),
        "industry_turning_severity": np.round(turning_severity, 6),
        "industry_turning_penalty": np.round(turning_penalty, 6),
        "risk_overlay_penalty": np.round(risk_overlay_penalty, 6),
        "rotation_phase": (
            features_df["rotation_phase"].fillna("").values
            if "rotation_phase" in features_df.columns
            else np.full(n, "")
        ),
        "rotation_score": (
            features_df["rotation_score"].fillna(0.5).values
            if "rotation_score" in features_df.columns
            else np.full(n, 0.5)
        ),
        "evidence": "",
        "risks": "",
        "f_ind_mom": np.round(scores_raw["industry_momentum"], 2),
        "f_rel_str": np.round(scores_raw["relative_strength"], 2),
        "f_diffusion": np.round(scores_raw["diffusion"], 2),
        "f_moneyflow": np.round(scores_raw["moneyflow"], 2),
        "f_crowding": np.round(scores_raw["crowding"], 2),
        "f_reversal": np.round(scores_raw["reversal"], 2),
        "f_neighbor": np.round(scores_raw["neighbor_momentum"], 2),
        "f_short_mom": np.round(scores_raw["short_term_momentum"], 2),
        "f_vol_price": np.round(scores_raw["volume_price"], 2),
        "f_fundamental": np.round(scores_raw["fundamental"], 2),
        "f_icm": np.round(scores_raw["industry_corr_momentum"], 2),
        "f_low_vol": np.round(scores_raw["low_volatility"], 2),
        "f_liquidity": np.round(scores_raw["liquidity"], 2),
        "f_index_mem": np.round(scores_raw["index_membership"], 2),
    })

    print(
        f"[predictor-vec] Done. "
        f"强:{(result['direction']=='强').sum()} "
        f"偏强:{(result['direction']=='偏强').sum()} "
        f"中性:{(result['direction']=='中性').sum()} "
        f"偏弱:{(result['direction']=='偏弱').sum()} "
        f"弱:{(result['direction']=='弱').sum()}"
    )
    return result


# ---------------------------------------------------------------------------
# 多投资期限预测
# ---------------------------------------------------------------------------

def _build_prediction_config_from_horizon(
    horizon_cfg: HorizonConfig,
    base_cfg: PredictionConfig = DEFAULT_PREDICTION_CONFIG,
) -> PredictionConfig:
    """用 HorizonConfig 覆盖 base PredictionConfig 的关键字段。

    覆盖: dimension_weights, direction_thresholds, stage 阈值(如有)。
    其他参数(score_power, synergy_power 等)保持 base_cfg 的值。
    """
    import dataclasses

    overrides: dict = {
        "dimension_weights": dict(horizon_cfg.dimension_weights),
        "direction_thresholds": dict(horizon_cfg.direction_thresholds),
    }

    # stage 阈值覆盖（仅在 HorizonConfig 提供时生效）
    if horizon_cfg.stage_thresholds:
        st = horizon_cfg.stage_thresholds
        stage_field_map = {
            "no_action_momentum": "stage_no_action_momentum",
            "no_action_diffusion": "stage_no_action_diffusion",
            "start_momentum": "stage_start_momentum",
            "start_diffusion": "stage_start_diffusion",
            "confirm_momentum": "stage_confirm_momentum",
            "confirm_diffusion_low": "stage_confirm_diffusion_low",
            "confirm_diffusion_high": "stage_confirm_diffusion_high",
            "spread_momentum": "stage_spread_momentum",
            "spread_diffusion": "stage_spread_diffusion",
        }
        for short_key, field_name in stage_field_map.items():
            if short_key in st:
                overrides[field_name] = st[short_key]

    return dataclasses.replace(base_cfg, **overrides)


def predict_multi_horizon(
    features_df: pd.DataFrame,
    horizons: list[int] | None = None,
    horizon_configs: dict[int, HorizonConfig] | None = None,
) -> dict[int, pd.DataFrame]:
    """为多个投资期限生成独立预测。

    对每个 horizon:
    1. 用对应的 HorizonConfig 构建 PredictionConfig（覆盖权重/阈值）
    2. 调用 predict_batch_vectorized 获取该期限的预测
    3. 在结果 DataFrame 中添加 'horizon' 列

    Args:
        features_df: compute_all_graph_features 输出的特征 DataFrame
        horizons: 要生成的期限列表，默认 [5, 20, 60]
        horizon_configs: 自定义期限配置，默认使用 DEFAULT_HORIZON_CONFIGS

    Returns:
        dict: {horizon_days: DataFrame} 每个 DataFrame 包含该期限的预测结果
    """
    if features_df.empty:
        return {}

    if horizons is None:
        horizons = [5, 20, 60]

    if horizon_configs is None:
        horizon_configs = DEFAULT_HORIZON_CONFIGS

    results: dict[int, pd.DataFrame] = {}
    for h in horizons:
        h_cfg = horizon_configs.get(h)
        if h_cfg is None:
            # 未配置的期限使用默认 PredictionConfig
            pred_cfg = DEFAULT_PREDICTION_CONFIG
        else:
            pred_cfg = _build_prediction_config_from_horizon(h_cfg)

        pred_df = predict_batch_vectorized(features_df, pred_cfg)
        if not pred_df.empty:
            pred_df["horizon"] = h
        results[h] = pred_df

    return results
