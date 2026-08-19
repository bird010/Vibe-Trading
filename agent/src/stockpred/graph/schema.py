"""A股图谱模块 - 数据结构定义"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StockNode:
    """股票节点"""

    ts_code: str  # 300750.SZ
    name: str  # 宁德时代
    industry: str  # 电气设备
    list_date: str | None = None
    list_status: str = "L"  # L=上市, D=退市, P=暂停


@dataclass(frozen=True)
class IndustryNode:
    """行业节点"""

    name: str  # 银行
    stock_count: int = 0  # 行业内股票数量


@dataclass(frozen=True)
class Edge:
    """图边"""

    src: str  # stock:300750.SZ 或 industry:银行
    dst: str
    edge_type: str  # BELONGS_TO_INDUSTRY / PEER_CORRELATED / PART_OF_INDEX
    weight: float
    trade_date: str
    attributes: dict = field(default_factory=dict)


@dataclass
class GraphFeatures:
    """单只股票的图谱特征"""

    ts_code: str
    trade_date: str
    industry: str
    # 行业维度
    industry_momentum_5d: float = 0.0
    industry_momentum_20d: float = 0.0
    industry_momentum_60d: float = 0.0
    industry_rank: int = 0
    industry_stock_count: int = 0
    # 个股维度
    return_5d: float = 0.0
    return_20d: float = 0.0
    return_60d: float = 0.0
    rel_strength_20d: float = 0.0
    rank_in_industry: int = 0
    # 扩散维度
    diffusion_score: float = 0.0
    leader_momentum: float = 0.0
    # 资金维度
    net_big_inflow_5d: float = 0.0
    industry_inflow_diffusion: float = 0.0
    # 风险维度
    turnover_percentile: float = 0.0
    pe_percentile: float = 0.0
    crowding_score: float = 0.0
    # 图结构维度
    degree_centrality: float = 0.0
    industry_corr_momentum: float = 0.0
    # 风险指标维度
    volatility_20d: float = 0.0
    volatility_60d: float = 0.0
    beta_to_industry: float = 1.0
    max_drawdown_60d: float = 0.0
    # 因子多元化维度
    low_volatility_signal: float = 0.0
    liquidity_signal: float = 0.0
    # 指数成分维度
    is_index_component: int = 0
    index_weight: float = 0.0
    # 行业轮动维度
    rotation_phase: str = ""
    rotation_score: float = 0.5


@dataclass
class TrendPrediction:
    """趋势预测输出"""

    ts_code: str
    trade_date: str
    score: float  # 0-140 综合得分（加权归一化）
    direction: str  # 强 / 偏强 / 中性 / 偏弱 / 弱
    stage: str  # 无行情 / 启动 / 确认 / 扩散 / 拥挤 / 退潮
    industry: str
    industry_momentum_rank: int = 0
    relative_strength: float = 0.0
    diffusion_score: float = 0.0
    crowding_score: float = 0.0
    base_score: float = 0.0
    retreat_severity: float = 0.0
    retreat_penalty: float = 0.0
    industry_turning_severity: float = 0.0
    industry_turning_penalty: float = 0.0
    risk_overlay_penalty: float = 0.0
    rotation_phase: str = ""
    evidence: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    # 逐因子得分（0-20，用于 IC 诊断）
    industry_momentum_score: float = 0.0
    relative_strength_score: float = 0.0
    diffusion_dim_score: float = 0.0
    moneyflow_score: float = 0.0
    crowding_dim_score: float = 0.0
    reversal_score: float = 0.0
    neighbor_momentum_score: float = 0.0
    volume_price_score: float = 0.0
    short_term_momentum_score: float = 0.0
    fundamental_score: float = 0.0
    industry_corr_mom_score: float = 0.0
    low_volatility_score: float = 0.0
    liquidity_score: float = 0.0
    index_membership_score: float = 0.0
    # 投资决策顾问层输出
    confidence: float = 0.0  # 0-1 置信度
    stop_loss_pct: float = 0.0  # 建议止损幅度（%，如 -5.0 表示跌5%止损）
    take_profit_pct: float = 0.0  # 建议止盈幅度（%）
    position_weight: float = 0.0  # 建议仓位权重（0-1，归一化后）
    action: str = ""  # 买入/增持/持有/减持/卖出
