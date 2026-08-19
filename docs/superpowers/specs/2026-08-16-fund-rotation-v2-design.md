# 基金轮动前端 V2 端到端设计

**日期：** 2026-08-16  
**范围：** 分阶段实现完整端到端 V2，包括回测证据产物、后端 API、轮动分析页面、K 线策略证据、页面联动与 URL 深链。

## 目标

在保持现有“概览、收益曲线、K 线证据、基金候选池”标签页稳定的前提下，新增“轮动分析”，完整解释：

1. 多年维度的实际持仓、Cash 和权重迁移；
2. 单次调仓从 Before 到 After 的决策链；
3. 单基金价格、Momentum、Relative Strength、Volatility 与实际 benchmark 证据。

## 基本事实与约束

- 核心对象是 `run_id + signal_date`，一次调仓对应一个 Decision Bundle。
- 前端不按日期拼接 signals、candidate pool、positions、orders、trades，也不重算历史 Momentum。
- `actual_weight` 只能来自实际持仓快照；缺少实际持仓历史时不得使用 target weight 替代。
- 策略证据必须由回测运行阶段生成，前端只展示真实运行产物。
- 现有一级标签页不合并、不删除；只新增 `rotation_analysis`。
- 不做资产类别热力图、基金穿透、Sector exposure、Sankey、K 线 Signal marker、K 线 Signal table row，也不改变共享 `CandlestickChart` 的默认行为。
- 新字段和新产物均为可选，旧运行必须能够打开详情并显示降级提示。

## 后端数据契约

### 发布产物

运行阶段新增以下 checksum 保护的发布产物：

### `holdings_timeline.json`

包含回测区间、实际持仓行和调仓 marker。每一行通过连续持仓区间表达，不按交易日展开 UI cell。

```ts
interface HoldingInterval {
  ts_code: string;
  start_date: string;
  end_date: string;
  actual_weight: number;
  target_weight?: number | null;
  market_value?: number | null;
  opened_by_signal_date?: string | null;
  changed_by_signal_date?: string | null;
}
```

区间仅在持仓状态或目标组合发生变化时切分；区间内 `actual_weight` 使用实际日权重的时间加权值。`_CASH` 作为独立行，实际权重来源于现金权益占比。

### `rebalance_index.json`

保存轻量导航信息：`signal_date`、`sequence`、`quality_status`、`changed_positions`、`target_count`、`turnover`、`cash_target_weight`、`cluster_snapshot_date` 和 `has_execution`。

### `rebalance_decisions.json`

按 `signal_date` 保存完整 Decision Bundle：

- `before`：signal date 之前最后一个有效组合状态；
- `after_target`：该 signal date 对应的 target portfolio；
- `decision.strategy`：universe、dedup、representative、ranking、selection、weighting 和 frequency；
- `decision.cluster_snapshot`：实际使用的 snapshot 日期与质量 gate；
- `decision.candidates`：候选经过 universe、cluster、representative、ranking、portfolio 各阶段的结构化证据；
- `execution.orders` 与 summary：订单、成交、blocked/partial/filled、commission、turnover。

当前 `correlation_representative` 策略必须保存 Momentum 横截面排名、Top-N cutoff、representative、NEW/KEEP/DROP/MISSED 和 SAME_CLUSTER_EXCLUDED 信息。没有完整排名证据的历史运行返回空 candidates，由前端显示明确降级提示。

### API

新增：

```text
GET /stockpred/fund-rotation/backtests/{run_id}/holdings-timeline
GET /stockpred/fund-rotation/backtests/{run_id}/rebalances
GET /stockpred/fund-rotation/backtests/{run_id}/rebalances/{signal_date}
```

路由只读取 manifest 中已列出且 checksum 校验通过的发布产物。缺少对应产物时返回稳定错误码的 404；前端将其映射为历史运行兼容提示。路由不临时依据多个原始 CSV 进行 temporal join。

## K 线策略证据

`InstrumentChartResponse` 扩展可选的 `strategy_evidence`：

```ts
interface StrategyEvidenceSeries {
  id: string;
  label: string;
  formula_id: string;
  window?: number | null;
  unit: "ratio" | "percent" | "score" | "price";
  points: Array<{ date: string; value: number }>;
}
```

响应包含可选的 benchmark normalized price、Momentum/RS/Volatility series 和 evidence version。默认指标为 Momentum；没有 evidence 时保留 OHLCV、交易标记和通用技术指标，并提示“该历史运行未保存策略 Momentum 时间序列”。

## 前端结构

新增独立 `useRotationAnalysis` 状态，不把轮动状态塞进 `useBacktestDetail`。状态包括 timeline、rebalance index、当前 signal date、按 signal date 缓存的 Decision Bundle、时间窗口、candidate view 和当前策略指标。

组件边界：

```text
RotationAnalysisTab
├── HoldingsWeightTimeline
├── HoldingsTimeBrush
├── RebalanceNavigator
├── PortfolioChangeChart
├── WhyDecisionPanel
│   ├── StrategyPipeline
│   ├── ClusterRepresentativeMap
│   └── RankingLane
└── ExecutionSummary
```

持仓时间线使用 SVG/custom rendering，只渲染连续 interval，支持 overview、brush、缩放、semantic zoom、重要调仓筛选、长尾折叠、Cash 独立行、tooltip 和点击 signal date。单次调仓使用 Weight Dumbbell；WHY 以 pipeline、cluster representatives、cluster quality 和 Ranking Lane 为主；执行区显示目标组合、订单、成交和摘要。

K 线新增 `FundRotationStrategyEvidenceChart`，复用现有交易标记和共享蜡烛图，只在基金轮动页面增加 Momentum、Relative Strength、Volatility、实际 benchmark 与专属默认指标选择。

## URL 与联动

支持：

```text
?run_id=...
&detail_tab=rotation_analysis
&signal_date=...
&instrument=...
&focus_date=...
&strategy_indicator=momentum
```

时间线点击选中调仓；WHY 点击 ETF 切换 K 线并聚焦该 ETF、signal date 和指标；候选池中匹配 snapshot 时跳转到关联调仓；刷新、分享链接和浏览器 Back 能恢复页面状态。

## 分阶段实施

### 阶段一：数据契约与后端证据

扩展运行结果、artifact publisher、Pydantic/TypeScript types 和 API；生成三类新产物；测试 actual/target 不混淆、因果时间语义、checksum、旧运行降级。

### 阶段二：持仓时间线与调仓导航

新增 `rotation_analysis` 标签；首次进入并行加载 timeline/index；实现 interval timeline、brush、semantic zoom、Cash、长尾折叠、筛选、前后导航和 timeline → signal date 联动。

### 阶段三：单次调仓解释

实现 Before → After dumbbell、Strategy Pipeline、Cluster Representative Map、Cluster Quality、Ranking Lane、Top-N cutoff、NEW/KEEP/DROP/MISS 和 Execution Summary，覆盖全现金和缺失排名证据。

### 阶段四：K 线策略证据

在真实运行阶段发布策略 evidence；扩展 API；实现 Momentum/RS/Volatility/benchmark；保留 MA/EMA/BOLL/MACD/RSI/KDJ；不增加 Signal marker 或 Signal table row。

### 阶段五：联动、深链与性能

完成 URL 状态同步、WHY ↔ K 线、候选池 ↔ 调仓联动，验证多年回测 DOM 数量、缓存、fallback、前后端回归和构建。

## 验收标准

- 新增“轮动分析”且现有四个标签页行为稳定。
- 有真实新产物的运行可展示完整时间线、调仓导航、决策链和执行摘要。
- 没有新产物的旧运行可以打开，并准确提示缺失，不伪造证据。
- 时间线不使用每日 cell；Cash 可见；权重变化点击能定位 signal date。
- Before、cluster snapshot、ranking、After、execution 均使用正确的 signal-date 因果语义。
- Ranking Lane 能表达 NEW、KEEP、DROP、MISS、Top-N cutoff 和 cluster exclusion。
- K 线默认展示运行时 Momentum，可切换 RS/Volatility；缺失时正常 fallback。
- URL 深链、刷新、Back、跨标签页联动可恢复。
- 后端测试、前端测试、类型检查和生产构建全部通过。
