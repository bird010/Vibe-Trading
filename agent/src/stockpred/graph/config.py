"""A股图谱模块 - 配置参数"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GraphConfig:
    """图谱构建配置"""

    # 相关性计算
    corr_window_short: int = 20
    corr_window_long: int = 60
    corr_threshold: float = 0.5
    corr_within_industry_only: bool = True
    industry_corr_threshold: float = 0.6  # 行业间相关性阈值（更高，因为只有 ~30 个行业）

    # 特征计算窗口
    feature_windows: tuple[int, ...] = (5, 20, 60)

    # 扩散度：龙头组定义（行业内涨幅前 N%）
    leader_top_pct: float = 0.10

    # 拥挤度权重
    crowding_turnover_weight: float = 0.5
    crowding_pe_weight: float = 0.5

    # 风险特征参数
    volatility_windows: tuple[int, ...] = (20, 60)
    beta_window: int = 60
    drawdown_window: int = 60

    # 行业轮动参数
    rotation_short_window: int = 5
    rotation_long_window: int = 20


@dataclass
class PredictionConfig:
    """趋势预测配置"""

    # 评分权重（各维度满分 20，总分 140 = 7 个维度）
    max_score_per_dim: int = 20

    # 维度权重（加权平均后归一化到 0-140，保持阈值兼容）
    # Round 6 (IC-Signed): 权重 ∝ |IC|，方向由 factor_signs 决定
    # 基于 10年 102点 20d horizon IC 诊断
    dimension_weights: dict[str, float] = field(
        default_factory=lambda: {
            "industry_momentum": 0.0,  # IC=+0.003（太弱，关闭）
            "relative_strength": 2.0,  # |IC|=0.031，翻转后启用
            "diffusion": 0.0,  # |IC|=0.059，最强因子，翻转后启用
            "moneyflow": 0.0,  # IC=+0.009（太弱）
            "crowding": 3.0,  # IC=+0.034（正IC，保持原方向）
            "reversal": 0.0,  # |IC|=0.005（太弱）
            "neighbor_momentum": 0.0,  # |IC|=0.050，翻转后启用
            "volume_price": 0.0,  # |IC|=0.018，翻转后启用
            "short_term_momentum": 0.0,  # IC=+0.006（太弱）
            "fundamental": 0.7,  # |IC|=0.018，翻转后启用（附加加分模式）
            "industry_corr_momentum": 0.0,  # IC=-0.001（无用）
            "low_volatility": 4.0,  # 低波动异象
            "liquidity": 2.5,  # 流动性溢价
            "index_membership": 0.5,  # 指数成分溢价
        }
    )

    # 因子方向符号（基于 IC 符号）
    # +1 = 原始方向（高原始分→高最终分→看涨）
    # -1 = 翻转方向（低原始值→高最终分→看涨），用于负 IC 因子
    # 理论依据：A股中期反转效应 (Jegadeesh 1990; 20日 horizon 动量因子 IC<0)
    factor_signs: dict[str, int] = field(
        default_factory=lambda: {
            "industry_momentum": 1,
            "relative_strength": -1,  # IC=-0.031: 弱者恒强(20d均值回归)
            "diffusion": -1,  # IC=-0.059: 低扩散(启动期)优于高扩散(退潮期)
            "moneyflow": 1,
            "crowding": 1,  # IC=+0.034: 原始得分已反转(低拥挤=高分)
            "reversal": 1,
            "neighbor_momentum": -1,  # IC=-0.050: 信息溢出已反映，反向获利
            "volume_price": -1,  # IC=-0.018: 缩量上涨→动能不足(反向)
            "short_term_momentum": 1,
            "fundamental": -1,  # IC=-0.018: 低估值优于高估值
            "industry_corr_momentum": 1,
            "low_volatility": 1,  # 正IC：低波动→高收益
            "liquidity": 1,  # 正IC：高非流动性→溢价
            "index_membership": 1,  # 正IC：指数成分→流动性溢价
        }
    )

    # 非线性评分指数 (Jegadeesh & Titman 1993: 动量策略需放大顶部差异)
    # score_power > 1 使高分更高、中分压缩，增强 Top N 选股精度
    # 1.0 = 线性(原始), 1.5 = 中等非线性, 2.0 = 强非线性
    score_power: float = 1.5

    # 因子协同效应 (仅对同向正 IC 因子有效)
    synergy_power: float = 2.0

    # 因子交叉互动项 (Chordia & Shivakumar 2002)
    # 两个最强 IC 因子 (moneyflow × industry_momentum) 同时出色时额外加分
    # interaction_max_bonus: 互动项最大加分分值
    interaction_max_bonus: float = 0.0  # 关闭 (实证无效果)
    # 互动项幂次 (3.0 = 只有两者都极强时才给显著加分)
    interaction_power: float = 3.0

    # 基本面附加加分（不影响加权平均排名，作为独立加分项）
    # fundamental_bonus_max: 基本面因子最大加分（总分 0-140 基础上额外加分）
    fundamental_bonus_max: float = 5.0  # IC=-0.018(翻转后)，降低加分避免噪声
    # 基本面加分幂次（1.5 = 中等非线性，只有基本面很强的股票才给显著加分）
    fundamental_bonus_power: float = 1.5

    # 风险覆盖层：在基础因子总分之后执行，不改变逐因子 IC 定义。
    retreat_penalty_max: float = 20.0
    industry_turning_penalty_max: float = 5.0
    risk_overlay_penalty_cap: float = 20.0
    industry_turning_deceleration_start: float = 0.80
    industry_turning_overlap_weight: float = 0.50

    # 方向阈值（基于加权归一化后 0-140 总分）
    direction_thresholds: dict[str, int] = field(
        default_factory=lambda: {
            "强": 95,
            "偏强": 78,
            "中性": 58,
            "偏弱": 40,
            "弱": 0,
        }
    )

    # 阶段判断阈值
    # 注意：stage_*_momentum 与 _score_industry_momentum 输出量纲一致，
    # 即 0-20 区间（=行业动量百分位 * 20）。历史值 30/50/60/70 是错误量纲，
    # 会导致所有股票恒落入'无行情'分支。已按百分位分位映射重新校准：
    #   no_action: pct<0.30 → 6.0    （行业动量在全市场后 30%）
    #   start    : pct≥0.50 → 10.0   （进入中位以上，刚启动）
    #   confirm  : pct≥0.60 → 12.0   （前 40%，进入确认）
    #   spread   : pct≥0.70 → 14.0   （前 30%，进入扩散）
    stage_no_action_momentum: float = 6.0
    stage_no_action_diffusion: float = 0.3
    stage_start_momentum: float = 10.0
    stage_start_diffusion: float = 0.4
    stage_confirm_momentum: float = 12.0
    stage_confirm_diffusion_low: float = 0.4
    stage_confirm_diffusion_high: float = 0.6
    stage_spread_momentum: float = 14.0
    stage_spread_diffusion: float = 0.6
    stage_crowding_score: float = 6.0


# ---------------------------------------------------------------------------
# 多投资期限配置
# ---------------------------------------------------------------------------


@dataclass
class HorizonConfig:
    """投资期限配置

    为不同前瞻期（5d / 20d / 60d）提供差异化的维度权重、方向阈值和阶段阈值。
    """

    horizon_days: int  # 前瞻天数 (5/20/60)
    dimension_weights: dict[str, float]  # 维度权重（按期限调整）
    direction_thresholds: dict[str, int]  # 方向阈值
    stage_thresholds: dict[str, float]  # 阶段阈值覆盖


# 短期 (5d)：动量/扩散度为主，与当前默认 PredictionConfig 一致
SHORT_TERM_CONFIG = HorizonConfig(
    horizon_days=5,
    dimension_weights={
        "industry_momentum": 0.0,
        "relative_strength": 2.0,
        "diffusion": 0.0,  # 最强因子
        "moneyflow": 0.0,
        "crowding": 3.0,
        "reversal": 0.0,
        "neighbor_momentum": 0.0,
        "volume_price": 0.0,
        "short_term_momentum": 0.0,
        "fundamental": 0.7,
        "industry_corr_momentum": 0.0,
        "low_volatility": 0.5,  # 短期效果弱
        "liquidity": 0.5,  # 短期效果弱
        "index_membership": 0.3,  # 短期效果弱
    },
    direction_thresholds={"强": 95, "偏强": 78, "中性": 58, "偏弱": 40, "弱": 0},
    stage_thresholds={},
)

# 中期 (20d)：基本面 + 扩散度权重增加
MEDIUM_TERM_CONFIG = HorizonConfig(
    horizon_days=20,
    dimension_weights={
        "industry_momentum": 0.0,
        "relative_strength": 1.0,
        "diffusion": 2.5,
        "moneyflow": 0.0,
        "crowding": 3.0,
        "reversal": 0.0,
        "neighbor_momentum": 2.0,
        "volume_price": 0.0,
        "short_term_momentum": 0.0,
        "fundamental": 2.5,  # 中期基本面权重提升
        "industry_corr_momentum": 0.0,
        "low_volatility": 4.0,
        "liquidity": 2.5,
        "index_membership": 0.5,  # 中期成分效应中等
    },
    direction_thresholds={"强": 95, "偏强": 78, "中性": 58, "偏弱": 40, "弱": 0},
    stage_thresholds={},
)

# 长期 (60d)：基本面权重最高，技术面降低
LONG_TERM_CONFIG = HorizonConfig(
    horizon_days=60,
    dimension_weights={
        "industry_momentum": 0.0,
        "relative_strength": 2.0,
        "diffusion": 2.0,
        "moneyflow": 0.0,
        "crowding": 2.0,
        "reversal": 0.0,
        "neighbor_momentum": 1.0,
        "volume_price": 0.5,
        "short_term_momentum": 0.0,
        "fundamental": 3.5,  # 长期基本面权重最高
        "industry_corr_momentum": 0.0,
        "low_volatility": 2.0,  # 长期效果更强
        "liquidity": 1.5,  # 长期效果更强
        "index_membership": 0.8,  # 长期成分效应更强
    },
    direction_thresholds={"强": 95, "偏强": 78, "中性": 58, "偏弱": 40, "弱": 0},
    stage_thresholds={},
)

# 默认期限配置映射: horizon_days → HorizonConfig
DEFAULT_HORIZON_CONFIGS: dict[int, HorizonConfig] = {
    5: SHORT_TERM_CONFIG,
    20: MEDIUM_TERM_CONFIG,
    60: LONG_TERM_CONFIG,
}


# 默认配置实例
DEFAULT_GRAPH_CONFIG = GraphConfig()
DEFAULT_PREDICTION_CONFIG = PredictionConfig()
