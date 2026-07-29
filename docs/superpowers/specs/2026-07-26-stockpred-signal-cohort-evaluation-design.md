# StockPred 信号独立批次评价设计

## 1. 文档状态

- 设计状态：已批准
- 目标版本：`signal_cohort_v1`
- 适用范围：StockPred Graph 与 Alpha Zoo 策略海选、单策略信号评价和批次横向比较
- 当前目标：验证信号在不同时段的平均有效性与稳定性
- 后续扩展：真实单账户组合回测、显式杠杆组合和模拟盘

## 2. 背景

当前 StockPred 将每个评估日的 Top-N 目标分别按固定 `portfolio_capital` 生成订单，再把全部评估日的成交合并进一个只初始化一次的现金账本。该实现同时混合了两种不同问题：

1. 信号研究：如果每个历史时点独立投入相同名义资金，策略平均表现如何；
2. 组合回测：如果只有一笔真实资金，持仓、现金和订单连续演化后表现如何。

当持有期和评估间隔发生重叠时，现有实现会重复使用同一笔本金，产生负现金和未声明的隐性杠杆；随后又用初始本金计算账户净值、回撤和 Sharpe。该结果既不是严格的独立事件研究，也不是资金守恒的真实组合回测。

本设计重新把 StockPred 的当前目标定义为“信号独立批次评价”。每个评估日建立一个相互隔离的 cohort，使用相同承诺资金，计入拒单、闲置现金、成交成本和退出困难，最后从所有 cohort 的收益分布与分时期表现判断信号是否有效。

未来真实组合回测使用另一套账户状态和订单生成器，但复用信号、股票池、市场数据、成交政策和估值政策。

## 3. 第一性原理与不可变约束

### 3.1 历史可见性

策略在评估时点只能访问当时已经公开且已经进入系统的数据。标签、未来收益和执行期行情必须通过独立接口提供，不能暴露给策略。

### 3.2 样本先于结果冻结

评价股票集合和目标组合必须在知道未来成交与收益前冻结。涨停、停牌、容量不足、无法退出和收益缺失不能成为事后删除样本的理由。

### 3.3 每个 cohort 资金守恒

每个 cohort 的承诺资金只能处于以下状态：

- 闲置现金；
- 已成交持仓；
- 已支付费用；
- 已退出现金；
- 未退出残仓。

单个 cohort 内现金不能为负。不同 cohort 互不共享现金和持仓，因此不需要全局现金约束，也不构造全局账户净值。

### 3.4 成交因果性

订单只能使用订单决策时已经知道的市场信息。T+1 开盘订单不能使用 T+1 整日成交额决定容量或滑点。

### 3.5 比较口径一致

只有数据快照、策略版本、股票池、评价日期、选择规则、成交规则、费用、基准和质量门槛全部一致的策略才可进入同一严格排行榜。

### 3.6 研究结果不冒充账户结果

Cohort 模式不输出具有单一真实账户含义的净值、最大回撤和传统账户 Sharpe。任何聚合指标必须明确以独立 cohort 收益为样本。

## 4. 目标与非目标

### 4.1 目标

1. 以固定承诺资金独立评价每个历史评估日的信号。
2. 将未成交资金视为现金，避免只评价成功成交的幸存者样本。
3. 同时报告纯信号、固定持有期估值和实际退出三种收益。
4. 评价信号在年份、市场阶段和不同流动性环境中的稳定性。
5. 对重叠持有期使用 HAC 和时间块 Bootstrap 估计统计不确定性。
6. Graph 与 Alpha Zoo 使用同一评价引擎和指标定义。
7. 海选完成时同步生成所有相关股票的完整 OHLCV 图表包，前端可立即展示 K 线和买卖点。
8. 为未来真实组合回测保留稳定的信号、订单、成交和估值接口。
9. 清理现有会固化错误资金语义、幸存者筛选或延迟详情物化的代码。

### 4.2 非目标

1. 当前阶段不实现单账户差额调仓。
2. 当前阶段不实现融资、保证金、杠杆上限和强平。
3. 当前阶段不接券商或真实交易接口。
4. 当前阶段不自动调参或自动晋级实盘。
5. 当前阶段不为每个策略复制整个 eligible universe 的 OHLCV。
6. 当前阶段不重写或重新解释历史报告。

## 5. 术语

| 术语 | 定义 |
|---|---|
| evaluation date | 产生信号并冻结目标的历史评估日 |
| cohort | 一个评估日产生的独立虚拟资金批次 |
| committed capital | 为一个 cohort 承诺的固定名义资金 |
| raw signal return | 忽略成交限制和费用的理论信号收益 |
| horizon mark return | 在统一目标持有期末按可得价格估值的收益 |
| liquidation return | 按实际退出规则完成或终止退出后的收益 |
| idle cash | 因拒单、容量或主动空仓而未投入的现金 |
| chart bundle | 海选完成时发布的 OHLCV 文件、索引和订单买卖点事实 |
| strict leaderboard | 仅包含 comparison identity 和质量门槛一致策略的排行榜 |

## 6. 备选方案与决策

### 6.1 方案一：直接改造 Oracle/Parity

优点是改动较少。缺点是继续把兼容验证、标签、成交、指标和顾问评价放在一个模块中，未来真实组合回测仍需重新拆分。

### 6.2 方案二：独立 Cohort 引擎，预留 Portfolio 引擎

当前建立 `CohortEvaluationEngine`；未来建立 `PortfolioSimulationEngine`。两者共享信号、数据和成交原语，资金状态完全隔离。

该方案边界清晰、实施风险可控，并避免为尚未实现的真实账户提前构建过度通用的资金抽象。

### 6.3 方案三：立即构建可插拔 CapitalModel

该方案从第一天同时抽象独立批次、现金账户和杠杆账户，扩展性最高，但当前需求只验证信号，容易产生过度设计。

### 6.4 决策

采用方案二。当前只实现 Cohort 引擎和未来 Portfolio 引擎确实需要共享的最小接口。

## 7. 总体架构

```text
固定数据快照
    |
    v
StrategyAdapter
    每个评估日输出完整信号截面
    |
    v
SignalEligibilityGate
    冻结可评价、可交易股票池
    |
    v
CohortTargetBuilder
    Top-N、固定承诺资金、目标权重
    |
    v
ExecutionPolicy
    T+1、涨跌停、停牌、容量、费用
    |
    v
CohortLedger
    每个评估日独立账本
    |
    v
CohortMetrics + CrossPeriodAggregator
    单期收益、平均表现、稳定性和统计显著性
    |
    v
ChartBundlePublisher
    完整图表包与原子发布
```

未来真实组合路径为：

```text
相同 StrategyAdapter
相同 SignalEligibilityGate
相同 ExecutionPolicy
相同 ValuationPolicy
           |
           v
PortfolioTargetBuilder
           |
           v
PortfolioLedger
单账户现金、实际持仓、差额调仓、杠杆和风险约束
```

未来复用的是信号定义、股票池和成交原语，不复用 Cohort 的独立资金账本。

## 8. 组件职责

### 8.1 StrategyAdapter

职责：

- 接收评估日、冻结数据视图和可用股票池；
- 输出每只股票的分数和策略诊断；
- Graph 与 Alpha Zoo 使用相同返回契约；
- 不访问资金、成交结果和未来收益。

最小输出：

```text
evaluation_date
ts_code
score
direction               optional nullable
action                  optional nullable
crowding_score          optional nullable
confidence              optional nullable
strategy_diagnostics    optional map/json
```

公共指标、前端筛选和列式查询直接使用的诊断字段必须是显式类型化列。`strategy_diagnostics`
只保存策略私有、展示性质或尚未标准化的扩展字段，不允许公共指标在运行时依赖解析任意 JSON。

策略未提供某个可选标准字段时使用 `null`，同时输出 coverage 和
`metric_status="not_applicable"`，不得把缺失字段解释为准确率0。

### 8.2 SignalEligibilityGate

职责：

- 在信号时点冻结评价样本；
- 验证上市满期、交易所、ST、停牌、评估日行情、复权覆盖和关键特征时效；
- 输出逐项剔除原因和覆盖统计；
- 区分策略主动空仓与数据故障。

数据故障不得转成合法空仓或零收益。

### 8.3 CohortTargetBuilder

职责：

- 对冻结信号截面确定性排序；
- 创建 Top-N 或策略声明的目标组合；
- 为 cohort 分配固定承诺资金；
- 在计算目标数量前预留预计入场费用，使目标成交金额、实际入场费用与闲置现金之和不超过承诺资金；
- 在知道成交结果前冻结目标。

当前版本只支持 cohort 内目标权重，不支持跨 cohort 持仓缓冲。`buffer_retain_rank` 不属于 Cohort 模式。

### 8.4 ExecutionPolicy

职责：

- 接收订单意图和执行时点市场视图；
- 返回成交、拒单、部分成交、费用和剩余订单；
- 不管理全局现金；
- 保持无状态或只持有单个订单生命周期状态。

政策至少覆盖：

- T+1 最早入场；
- 停牌；
- 涨停不可买；
- 跌停退出延迟；
- 容量上限；
- 100 股买入整手；
- 最低佣金；
- 按生效日期确定税费；
- 可解释的滑点和市场冲击；
- 部分卖出跨日继续。

### 8.5 ValuationPolicy

职责：

- 为 cohort 持仓提供目标持有期末和退出过程中的估值；
- 当日无行情时使用受控的最后有效价格；
- 输出 `price_stale` 和 `stale_days`；
- 对超过陈旧上限的估值标记质量失败；
- 对退市和长期无法退出使用明确政策，不能静默归零。

### 8.6 CohortLedger

职责：

- 维护单个 cohort 的承诺资金、现金、持仓、订单、费用和退出状态；
- 保证现金不为负；
- 保证资金恒等式；
- 同时计算目标持有期估值和最终退出估值；
- 不与其他 cohort 交换现金或持仓。

### 8.7 CohortMetrics

职责：

- 从 CohortLedger 事实计算单期收益；
- 不通过筛选未来结果改变样本；
- 输出收益、基准、成交、成本、残仓和质量指标。

### 8.8 CrossPeriodAggregator

职责：

- 聚合所有评估日的 cohort；
- 计算均值、中位数、分位数和胜率；
- 对重叠样本使用 HAC；
- 通过时间块 Bootstrap 输出置信区间；
- 输出年度、季度、市场状态和流动性分层表现；
- 执行严格排行榜质量门禁。

### 8.9 ChartBundlePublisher

职责：

- 在海选阶段为策略涉及的全部股票生成完整 OHLCV；
- 生成图表索引和文件哈希；
- 与 cohort 事实和指标一起原子发布；
- 确保策略进入成功状态时前端图表已经可用。

### 8.10 BatchEvaluationCoordinator

职责：

- 以策略为并行任务分发评价工作；
- 每个策略任务内部按评估日升序串行运行 cohort；
- 在进程内复用冻结数据视图、`BatchDataContext`、panel 和滚动特征缓存；
- 每完成一个 cohort 发布进度和可恢复检查点；
- 所有 cohort 完成后统一计算跨期统计并原子发布产物。

第一期禁止在策略 worker 内再创建 cohort 级进程池，避免嵌套并行、数据读取放大和结果
顺序不确定。进程池中的物理进程可以复用；“每策略一个 worker”表示每个策略对应一个
独立任务，不表示永久独占一个进程。

并行度通过 `STOCKPRED_BATCH_WORKERS` 配置，默认：

```text
min(2, available_cpu)
```

后续性能优化顺序为批量读取、跨评估日向量化、滚动特征缓存和图表数据复用；只有这些优化
仍不能满足运行时间目标时，才评估 cohort 级并行。

## 9. 领域模型

### 9.1 SignalSnapshot

```text
evaluation_date
strategy_id
strategy_version
data_snapshot_id
eligible_universe
signals:
  ts_code
  score
  direction               optional nullable
  action                  optional nullable
  crowding_score          optional nullable
  confidence              optional nullable
  strategy_diagnostics    optional map/json
data_quality
```

信号快照不可修改，不包含订单或未来收益。

### 9.2 TargetSnapshot

```text
cohort_id
evaluation_date
committed_capital
selected_codes
target_weights
target_values
selection_reason
```

目标在执行前冻结，后续拒单不得改变目标集合。

`cohort_id` 是确定性的逻辑实体 ID：

```text
cohort_id
= "cohort_"
  + sha256(
      engine_schema_version
      + evaluation_protocol_key
      + strategy_id
      + strategy_version
      + evaluation_date
    )[0:24]
```

单次执行尝试使用独立 UUID `cohort_attempt_id`，批次使用独立 `run_id`。同一
`cohort_id` 重跑得到的 `target_snapshot_hash` 必须一致，否则以
`NON_DETERMINISTIC_SIGNAL` 失败，禁止静默覆盖。

### 9.3 OrderIntent

```text
order_id
cohort_id
signal_date
eligible_from
code
side
requested_quantity
requested_value
execution_policy_version
```

未来 Portfolio 引擎从 `OrderIntent` 开始复用同一执行契约。

### 9.4 ExecutionEvent

```text
order_id
cohort_id
trade_date
side
requested_quantity
executed_quantity
executed_value
price
fee_components
status
reason_code
remaining_quantity
market_data_as_of
```

费用保存分项绝对金额：

- commission；
- stamp_duty；
- transfer_fee；
- slippage；
- market_impact。

### 9.5 CohortState

```text
cohort_id
committed_cash
available_cash
positions
pending_orders
target_exit_date
last_valuation_date
status
```

状态机：

```text
PLANNED
→ ENTERING
→ HOLDING
→ EXITING
→ LIQUIDATED

任意运行状态
→ UNLIQUIDATED
→ FAILED_DATA
→ FAILED_EXECUTION
```

`FAILED_DATA` 不生成伪造的零收益，但进入覆盖率分母。

### 9.6 CohortResult

```text
committed_capital_return
executed_capital_return
raw_signal_return
raw_label_coverage
horizon_mark_return
liquidation_return
benchmark_target_horizon_return
benchmark_liquidation_matched_return
target_horizon_excess_return
liquidation_policy_excess_return
fill_rate
idle_cash_ratio
cost_ratio
exit_delay_days
unliquidated_ratio
data_quality
```

## 10. 配置契约

评价引擎和参数冻结模式使用两个正交字段：

```python
evaluation_engine: Literal["cohort", "portfolio"]
parameter_mode: Literal["parity", "research"]
```

当前仅允许 `evaluation_engine="cohort"`。

核心配置：

```text
start
end
evaluation_engine
parameter_mode
eval_step
holding_days
top_n
committed_capital_per_cohort
benchmark_code
max_participation
max_exit_extension_days
stale_price_limit_days
selection_policy
execution_policy_version
cost_policy_version
quality_gate
```

旧的 `portfolio_capital` 在新契约中替换为 `committed_capital_per_cohort`。

## 11. 数据视图与历史可见性

### 11.1 SignalMarketView

只允许读取 evaluation timestamp 前已经可见的数据。StrategyAdapter 只能接收该视图。

### 11.2 ExecutionMarketView

只允许读取订单执行时点已经可见的数据。开盘订单不得读取当日收盘、整日成交额或开盘后的未来数据。

日频数据模式默认使用截至信号日已知的滚动 ADV 作为容量代理。未来有分钟数据时，才能使用截至订单时间的累计成交量。

### 11.3 LabelMarketView

允许读取评价期未来行情，但只能用于收益和标签计算。策略、股票池和目标生成代码不得获得该视图。

### 11.4 双时态要求

可修订数据需要同时保存：

```text
valid_from
valid_to
available_at
system_from
system_to
source_row_id
```

数据快照身份需要区分：

```text
knowledge_as_of
execution_data_until
snapshot_created_at
```

后续财报或名称修订不得改变修订可见日前的历史信号。

## 12. 收益口径

### 12.1 承诺资金

每个 cohort 使用相同承诺资金 \(C\)：

```text
C
= 实际买入金额
+ 买入费用
+ 未成交闲置现金
```

主收益：

```text
committed_capital_return
= (最终现金 + 剩余持仓估值 - C) / C
```

规则：

- 买入失败时对应资金保持现金，收益为零；
- 部分成交时未成交资金保持现金；
- 未成交资金不重新分配；
- 目标数量在下单前按预计费用和 100 股整手向下调整；
- 实际费用高于预估时继续缩减可买数量，禁止透支现金；
- 所有费用计入；
- 无法退出的股票保留在样本和期末持仓中；
- 单个 cohort 现金不得为负。

### 12.2 诊断收益

同时输出：

- `executed_capital_return`：收益除以实际成交资金，仅用于诊断；
- `raw_signal_return`：冻结目标组合按目标权重聚合的固定期限理论收益，使用信号日后
  第一个交易日的复权开盘价入场、固定目标退出日的复权开盘价退出，忽略涨跌停、
  停牌可成交性、容量、整手、费用和退出延迟；
- `raw_label_coverage`：固定入场和目标退出边界均有有效价格的目标权重覆盖率；
- `horizon_mark_return`：统一目标持有期末的估值收益；
- `liquidation_return`：按实际退出规则完成或终止后的收益。

`raw_signal_return` 由 `LabelMarketView` 独立计算，不复用执行模拟收益。目标退出日没有
有效价格时，该股票标签缺失且不得顺延；报告必须披露 coverage，禁止通过静默删除缺失
股票并对剩余样本重新归一化来提高收益。

默认排行榜使用基于承诺资金的 `liquidation_return`。

### 12.3 残仓

从目标退出日开始持续尝试退出，直到：

1. 全部成交，状态为 `LIQUIDATED`；或
2. 达到 `max_exit_extension_days`，状态为 `UNLIQUIDATED`。

`UNLIQUIDATED` cohort：

- 保留残仓；
- 使用最后有效价格估值；
- 扣除保守预期退出成本；
- 记录残仓金额、比例、陈旧天数和退出延迟；
- 不得从统计分母删除；
- 超过质量阈值时策略不得进入严格排行榜。

## 13. 基准口径

固定期限信号评价与实际清算评价使用两套明确命名的基准，不使用语义不明确的单一
`benchmark_return` 或 `excess_return`。

固定期限基准使用与理论信号一致的时间边界：

```text
信号日 T
→ T+1 基准复权开盘价
→ 固定目标退出日基准复权开盘价
```

退出延迟不得改变该基准的持有期限：

```text
benchmark_target_horizon_return
target_horizon_excess_return
= horizon_mark_return - benchmark_target_horizon_return
```

实际清算评价使用现金流匹配基准。虚拟基准组合在策略入场时买入基准，并按照策略各笔
持仓实际退出的日期和资金比例同步赎回；已赎回部分转为现金，不使用最后一笔卖出日代表
全部资金：

```text
benchmark_liquidation_matched_return
liquidation_policy_excess_return
= liquidation_return - benchmark_liquidation_matched_return
```

每个 cohort 还输出：

- 股票池中位数收益；
- Top-N 相对股票池分位数。

固定期限信号分析使用 `target_horizon_excess_return`；执行和清算分析使用
`liquidation_policy_excess_return`。如果兼容接口暂时保留旧字段，必须在 schema 中
显式声明其映射口径，不允许动态切换。

行业中性超额收益作为后续增强指标，不阻断 `signal_cohort_v1`。

## 14. 跨期聚合与统计

独立 cohort 不构造复利净值。默认聚合：

- 平均净收益；
- 中位数净收益；
- 收益标准差；
- 胜率；
- 5%、25%、75%、95% 分位数；
- 平均目标期限超额收益；
- 目标期限正超额收益比例；
- 平均清算政策超额收益；
- 清算政策正超额收益比例；
- 平均拒单率；
- 平均成交率；
- 平均闲置现金率；
- 平均交易成本；
- 未退出 cohort 比例；
- 有效 cohort 数和覆盖率。

持有期可能大于评估间隔，因此显著性使用：

```text
Newey-West/HAC lag
= max(ceil(holding_days / eval_step) - 1, 0)
```

同时使用时间块 Bootstrap 输出净收益、目标期限超额收益和清算政策超额收益的置信区间。
不使用普通独立样本 t 检验。

默认排行榜排序键：

```text
mean_committed_capital_liquidation_return
```

可选排序：

- 平均目标期限超额收益；
- 平均清算政策超额收益；
- `cohort_return_sharpe_hac`；
- 胜率；
- 中位数收益；
- 最差分位收益；
- 成交率；
- 成本率；
- 未退出率；
- 有效 cohort 数。

传统账户年化收益、最大回撤和账户 Sharpe 不作为 Cohort 主指标。

## 15. 质量门禁

策略只有同时满足以下条件才能进入严格排行榜：

1. 有效评估日覆盖率达到配置阈值；
2. cohort 数达到最小样本数；
3. 拒单比例不过线；
4. 未退出比例不过线；
5. 数据缺失和陈旧估值比例不过线；
6. 数据快照和策略版本完整；
7. 评价配置和比较身份完整；
8. 不存在静默跳过的评估日；
9. 图表包完整并通过哈希校验。

不满足门禁的策略仍展示，但进入“不可严格排名”分组。

`signal_cohort_v1` 的默认严格门槛为：

```text
min_valid_eval_ratio = 0.95
min_cohort_count = max(30, ceil(244 / eval_step))
max_data_failure_ratio = 0.05
max_rejected_target_value_ratio = 0.30
max_unliquidated_cohort_ratio = 0.05
max_stale_valuation_ratio = 0.02
chart_bundle_completeness = 1.00
```

门槛可以在 Research 模式显式修改，但必须进入 `evaluation_protocol_key`。Parity 模式使用以上固定值。

## 16. 图表包

### 16.1 股票范围

每个策略海选阶段生成以下股票集合的完整 OHLCV：

```text
所有 cohort 的
selected_codes ∪ ordered_codes ∪ held_codes
```

不为从未入选、下单或持仓的 eligible universe 股票复制行情。

### 16.2 日期范围

```text
回测开始日 - 最大数据回看期
至
回测结束日 + 最大退出延长期
```

### 16.3 文件

```text
artifacts/chart_bundle_manifest.json
artifacts/charts/ohlcv_<code>.parquet
artifacts/cohort_orders.csv
```

`chart_bundle_manifest.json` 每只股票记录：

```text
code
relative_path
start_date
end_date
row_count
columns
sha256
byte_size
```

空仓策略发布合法的空 manifest。

### 16.4 原子发布

策略状态：

```text
EVALUATING_SIGNALS
→ BUILDING_COHORTS
→ SIMULATING_EXECUTION
→ CALCULATING_METRICS
→ PUBLISHING_CHART_BUNDLE
→ SUCCEEDED
```

发布流程：

1. Cohort 事实、指标和全部 OHLCV 写入 `artifacts_versions/.staging.<uuid>/`；
2. 校验股票集合、文件数、行数、日期范围和 SHA-256；
3. 将完整 staging 目录改名为不可变的 `artifacts_versions/<version_id>/`；
4. 以临时文件加 `os.replace` 原子发布 `artifacts_current.json`，内容记录 `version_id`、manifest SHA-256 和 schema version；
5. 所有读取方只跟随 `artifacts_current.json`，不扫描 staging 或未被指针引用的版本目录；
6. 写入最终成功状态；
7. 任一文件失败时不得进入 `SUCCEEDED`。

该协议不依赖符号链接或跨目录原子替换，适用于当前 Windows 运行环境。失败 staging 在确认没有活跃写入者后由回收任务清理。

前端摘要不加载 OHLCV。用户点击股票时，通过 manifest 精确读取一个 Parquet，并与 `cohort_orders.csv` 中该股票的订单事件合并。

同一股票存在多个 cohort 时，买卖点必须包含 `cohort_id`，前端支持按 cohort 筛选或同时展示。

## 17. 运行产物

```text
config.json
data_snapshot.json
strategy_snapshot.json
evaluation_manifest.json
quality_report.json

artifacts/signals.csv
artifacts/cohort_targets.csv
artifacts/cohort_orders.csv
artifacts/cohort_returns.csv
artifacts/benchmark_returns.csv
artifacts/period_breakdown.csv
artifacts/aggregate_metrics.json
artifacts/chart_bundle_manifest.json
artifacts/charts/ohlcv_<code>.parquet
```

文件职责：

- `signals.csv`：完整信号截面和资格状态；
- `cohort_targets.csv`：执行前冻结的目标；
- `cohort_orders.csv`：全部订单、成交、拒单、部分成交和剩余数量；
- `cohort_returns.csv`：每个 cohort 一行的事实表；
- `benchmark_returns.csv`：与 cohort 对齐的固定期限基准和现金流匹配基准；
- `period_breakdown.csv`：年度、季度和市场状态分组；
- `quality_report.json`：覆盖、失败、拒单、残仓和可比性；
- `aggregate_metrics.json`：排行榜指标；
- `chart_bundle_manifest.json`：图表文件索引与完整性证明。

## 18. API 契约

批次创建请求示例：

```json
{
  "evaluation_engine": "cohort",
  "parameter_mode": "research",
  "committed_capital_per_cohort": 1000000,
  "holding_days": 5,
  "eval_step": 5,
  "top_n": 50,
  "max_exit_extension_days": 20
}
```

响应和运行上下文返回：

```text
evaluation_engine
metric_schema_version
execution_policy_version
cost_policy_version
comparison_key              equals evaluation_protocol_key in signal_cohort_v1
strategy_version
run_identity
ranking_eligible
quality_gate_failures
```

同一个幂等键必须绑定规范化请求摘要：

- 同 key、同请求返回相同 batch；
- 同 key、不同请求返回 HTTP 409；
- 幂等映射和执行租约继续使用 fencing token。

## 19. 前端设计

报告首页回答：

1. 信号平均是否有效；
2. 在不同年份和市场阶段是否稳定；
3. 理论收益损失在成交、成本还是退出困难；
4. 样本和数据质量是否足够。

核心卡片：

```text
平均承诺资金净收益
平均目标期限超额收益
平均清算政策超额收益
HAC 置信区间
中位数收益
胜率
有效 cohort 数
成交率
闲置现金率
交易成本
未退出率
```

图表：

- cohort 收益时间序列散点；
- cohort 收益分布；
- 滚动年度和季度平均收益；
- raw signal、horizon mark、liquidation 收益瀑布；
- 拒单率、成本率、残仓率时间序列；
- 年度和市场状态稳定性表；
- 个股 K 线和带 `cohort_id` 的买卖点。

不绘制伪账户净值。

## 20. 比较身份

比较身份拆分为三个正交字段：

```text
evaluation_protocol_key
strategy_version
run_identity
```

`evaluation_protocol_key` 表示跨策略共享的评价协议，至少包含：

```text
数据快照及 knowledge_as_of
评价引擎及版本
选择规则
成交规则和费用版本
估值规则
基准定义
日期范围
eval_step
holding_days
top_n
每 cohort 承诺资金
质量门槛
```

`strategy_version` 包含策略源码、参数、Adapter 和一方依赖闭包哈希。

`run_identity` 由 `evaluation_protocol_key`、`strategy_version` 和单次运行 ID 组成，
用于定位具体产物，不用于判断不同策略是否可比较。

只有相同 `evaluation_protocol_key` 的报告才能进入同一严格排行榜。为兼容既有 API，
外部字段 `comparison_key` 在 `signal_cohort_v1` 中固定映射到
`evaluation_protocol_key`；不得把 `strategy_version` 混入该字段，否则不同策略无法
进入同一比较组。

## 21. 错误处理

稳定错误码：

```text
DATA_UNAVAILABLE
DATA_NOT_POINT_IN_TIME
UNIVERSE_INCOMPLETE
SIGNAL_EVALUATION_FAILED
NON_DETERMINISTIC_SIGNAL
NO_ELIGIBLE_SECURITIES
EXECUTION_DATA_INCOMPLETE
VALUATION_STALE
UNLIQUIDATED_POSITION
METRIC_INSUFFICIENT_HISTORY
CHART_BUNDLE_INCOMPLETE
COMPARISON_IDENTITY_MISMATCH
```

规则：

- 数据或信号异常不得转为零收益；
- 合法主动空仓记为全现金、收益为零；
- 数据故障导致的空信号记为失败 cohort；
- 每个失败保存评估日、错误码和脱敏原因；
- eval 异常不得静默跳过；
- 失败 cohort 进入覆盖率统计；
- JSON 损坏必须暴露为稳定错误，不能从列表中消失。

## 22. 现有代码清理与删除

清理分为“立即停止使用”“迁移期兼容”“未来保留”，避免误删仍承担真实账户或历史读取职责的模块。

### 22.1 停止使用旧全本金合并账本

新 Cohort runner 停止调用：

- `agent/backtest/stockpred_graph/execution.py::execute_target_portfolio`
- `agent/backtest/stockpred_graph/execution.py::build_daily_ledger`
- `agent/backtest/stockpred_graph/performance.py::calculate_performance_metrics`

需要迁移的调用方：

- `agent/backtest/stockpred_graph/runner.py`
- `agent/backtest/stockpred_strategy/runner.py`
- `agent/scripts/correlation_experiment.py`

旧实现迁入 `agent/backtest/stockpred_legacy/`，仅用于历史报告和迁移对照；新 Cohort 代码禁止导入。

### 22.2 删除 Cohort 中的账户参数

新契约删除：

- `portfolio_capital`
- `initial_cash`
- `buffer_retain_rank`
- 未实际参与计算的 `lookback_days`

替换为：

- `committed_capital_per_cohort`
- `data_lookback_days`
- `holding_days`
- `max_exit_extension_days`

未来 Portfolio 引擎使用 `entry_rank/exit_rank` 双阈值，不复用 `buffer_retain_rank`。

### 22.3 停止生成伪账户产物

`signal_cohort_v1` 不生成：

- `equity.csv`
- `positions.csv`
- 账户 `total_return`
- 账户 `annual_return`
- 账户 `max_drawdown`
- 账户 Sharpe、Calmar 和 Sortino

该限制只适用于 StockPred Cohort。仓库内真正使用单账户语义的其他回测引擎继续保留这些产物。

### 22.4 清理延迟详情链路

图表包上线后，新运行不再使用：

- `agent/src/stockpred/strategy_detail.py`
- `StrategyReportExecutor.materialize_detail()`
- 海选成功后的 `materialize_strategy_detail()` 调用
- `agent/src/ui_services.py` 中的 lazy materialization
- `detail_manifest.json`
- `detail_complete.json`
- `detail_status/detail_reason`
- `detail_done/detail_total`
- 前端 detail 状态展示

迁移步骤：

1. 新运行只使用 `chart_bundle_manifest.json`；
2. 旧报告由隔离的 `legacy_detail_reader.py` 只读兼容；
3. 旧报告读取不得自动写文件；
4. 历史报告保留期结束后删除旧物化实现、状态字段和测试；
5. 用图表包原子发布与完整性测试替代 detail marker 测试。

### 22.5 拆分 Oracle/Parity

当前 `oracle_parity.py` 同时包含兼容比较、标签、选股、成交近似、指标、顾问评价和基准，应拆分为：

```text
stockpred/parity.py
    纯 golden bundle 兼容比较

stockpred/cohort/labels.py
    使用 LabelMarketView 计算标签

stockpred/cohort/benchmark.py
    对齐 cohort 基准

stockpred/cohort/metrics.py
    cohort 统计
```

移除 `GraphBacktestResult` 中：

- `parity_signals`
- `parity_selected`
- `parity_trades`
- `parity_equity`
- `parity_metrics`

迁移完成后删除 `oracle_parity.py` 和固化“只保留可完成 forward return 样本”的测试。

### 22.6 拆分指标模块

现有指标按语义拆分：

```text
cohort_metrics.py
    独立批次统计、HAC、Bootstrap 和覆盖门槛

execution_metrics.py
    成交率、拒单率、费用、容量和退出延迟

portfolio_metrics.py
    账户 NAV、回撤和 Sharpe
    当前 Cohort 不使用，为未来 Portfolio 引擎保留
```

单股票账户式 `symbol_metrics` 替换为：

- `symbol_signal_metrics`
- `symbol_execution_metrics`

### 22.7 Runner 和实验脚本

清理：

- Graph runner 的 Oracle 和合并账本分支；
- Strategy runner 的列表式全本金交易；
- `correlation_experiment.py` 对旧执行器和账户指标的直接调用；
- StockPred Cohort artifacts writer 的 `equity/positions` 输出；
- run card 中 `initial_cash`。

无人维护且没有复现实验说明的脚本迁入 `scripts/legacy/`。新研究脚本必须调用正式 Cohort API。

### 22.8 UI 清理

根据 `metric_schema_version` 分流：

```text
signal_cohort_v1
    Cohort 收益、分布、稳定性、成交损耗、K线和买卖点

legacy_portfolio_like_v1
    只读展示旧 equity 和旧指标
    显示“非真实账户口径”
```

新页面删除：

- 将 Cohort 解释为账户年化收益和最大回撤的组件；
- detail 状态和延迟物化提示；
- 扫描 `ohlcv_*.csv` 推断股票集合；
- 无 manifest 时自动重建行情。

旧报告兼容逻辑放入独立 legacy 组件，避免继续在新页面堆积分支。

### 22.9 测试清理

删除或替换以下锁定错误行为的测试：

- 每期重复使用完整 `portfolio_capital`；
- 允许负现金但只验证账面恒等；
- 无法退出样本从统计中删除；
- `top_n=50、retain_rank=15` 被视为有效缓冲；
- T+1 开盘容量使用当日整日 amount；
- 海选后延迟生成 OHLCV；
- 缺少 detail marker 时自动写回；
- Cohort 模式要求 `equity.csv`。

保留并升级：

- PIT 防未来数据；
- T+1 成交；
- 涨跌停与停牌；
- 容量不足跨日退出；
- 策略和数据快照版本；
- 原子发布；
- 幂等和批次恢复。

## 23. 目标目录

```text
agent/backtest/stockpred/
    signals/
    cohort/
        engine.py
        contracts.py
        targets.py
        ledger.py
        metrics.py
        benchmark.py
        artifacts.py
        chart_bundle.py
    execution/
        policy.py
        costs.py
        valuation.py
    portfolio/
        contracts.py
        metrics.py
    legacy/
        old_runner.py
        old_execution.py
        old_detail_reader.py
```

Graph 和 Alpha Zoo 只保留策略适配器，不各自维护回测和指标语义。

## 24. 实施分期

### 第一期：Cohort 领域模型与事实表

- 实现独立 cohort；
- 固定承诺资金；
- 冻结目标；
- 拒单资金保留为现金；
- 生成 cohort 事实表。

### 第二期：执行与估值正确性

- 使用时点可知容量；
- 持仓持续估值；
- 跨日退出；
- 残仓终态；
- 对齐基准。

### 第三期：统计与排行榜

- HAC；
- 时间块 Bootstrap；
- 质量门禁；
- 分时期分析；
- 新批次排名。

### 第四期：图表包、API 和 UI

- 海选阶段同步发布完整图表包；
- 使用 `signal_cohort_v1`；
- 前端展示 Cohort 指标和即开即用的 K 线买卖点；
- 停止将合并 NAV 作为默认投资指标。

### 第五期：清理旧路径

- 隔离 legacy reader；
- 删除延迟详情物化；
- 删除错误语义测试；
- 拆分 Oracle、指标和 runner；
- 清理无主实验脚本。

### 第六期：未来 Portfolio 引擎

在不修改 `SignalProvider`、`ExecutionPolicy`、`ValuationPolicy` 和市场视图契约的前提下，增加：

- 单账户现金；
- 实际持仓；
- 目标差额订单；
- 风险约束；
- 显式杠杆；
- 融资成本；
- 账户 NAV 和回撤。

## 25. 测试与验收标准

### 25.1 Cohort 不变量

1. 相同信号和市场数据下，每个 cohort 可独立重现。
2. 增减其他评估日不会改变已有 cohort 的订单和收益。
3. 拒单资金保持现金，不从收益分母消失。
4. 单个 cohort 现金始终非负。
5. cohort 资金恒等式逐日成立。
6. Cohort 之间不存在现金和持仓传递。
7. 相同评价协议、策略版本和评估日生成相同 `cohort_id`。
8. 相同 `cohort_id` 的 `target_snapshot_hash` 不一致时运行失败。

### 25.2 因果与数据

1. 修改执行日开盘后的未来成交额不改变开盘订单。
2. 策略无法访问 LabelMarketView。
3. 未来财报修订不改变修订可见日前的历史信号。
4. 缺评估日行情、ST 状态不明和关键输入过期时按门禁处理。
5. 数据故障降低覆盖率，不能伪装为空仓。

### 25.3 收益与统计

1. raw、horizon mark、liquidation 三种收益口径可独立验证。
2. 无法退出股票不从 cohort 或分母删除。
3. `raw_signal_return` 使用固定 T+1 开盘到目标退出日开盘，不因可卖日延迟而顺延。
4. 固定期限基准不跟随策略退出延迟，现金流匹配基准按实际退出比例和日期赎回。
5. 两种超额收益均能从 cohort 事实和基准事实独立重建。
6. 重叠 cohort 使用 HAC 和时间块 Bootstrap。
7. 样本不足时不输出伪造的年化指标。
8. 不同年份和市场状态分组能从 cohort 事实表重建。

### 25.4 图表包

1. 策略进入成功状态时图表包同步完成。
2. manifest 股票集合等于 selected、ordered、held 的并集。
3. 全部 Parquet 的哈希、行数和日期范围与 manifest 一致。
4. 前端点击 symbol 不触发任何写操作。
5. 买卖点与 `cohort_orders.csv` 一致。
6. 同一股票多个 cohort 的标记可区分。
7. 空仓策略拥有合法空图表包。
8. 图表包写入失败时策略不进入成功状态。

### 25.5 比较与扩展

1. 同 `evaluation_protocol_key` 的 Graph 与 Alpha 使用相同评价规则。
2. 修改数据快照、成交政策或成本会改变 `evaluation_protocol_key`。
3. 修改策略源码或参数会改变 `strategy_version`，但不改变共享评价协议键。
4. 旧报告和新报告不进入同一严格排行榜。
5. Cohort 模式不生成全局负现金或伪账户 NAV。
6. 未来 PortfolioTargetBuilder 生成的订单可直接交给同一 ExecutionPolicy。

## 26. 风险与控制

### 26.1 图表包磁盘增长

控制措施：

- 只保存 selected、ordered、held 的并集；
- 使用 Parquet 压缩；
- 在 manifest 中记录字节数；
- 批次执行前预估产物空间；
- 设置单策略和单批次产物预算；
- 不复制整个 eligible universe。

### 26.2 新旧报告混淆

控制措施：

- 强制 `metric_schema_version`；
- UI 明确分流；
- comparison key 纳入评价引擎；
- 旧报告显示非真实账户口径警告。

### 26.3 未来 Portfolio 需求侵入 Cohort

控制措施：

- 资金状态分属不同 ledger；
- 共享接口只到订单、成交和估值；
- Cohort 不增加 previous holdings、全局现金或差额调仓字段。

### 26.4 清理过早导致历史报告不可读

控制措施：

- 先建立只读 legacy reader；
- 禁止旧报告读取时写回；
- 在保留期结束并完成历史报告抽样验证后删除旧实现。

## 27. 实现前确认 Q&A

### 27.1 容量代理的 ADV 窗口和退出日口径

**问题**

容量代理从执行日当日成交额改为滚动 ADV 后：

1. ADV 回看窗口是固定值还是可配置；
2. 退出日容量使用信号日 ADV，还是退出时点可见的最新 ADV。

**决策**

默认参数：

```text
adv_lookback_days = 20
min_adv_observations = 10
```

`adv_lookback_days` 在 Research 模式可配置，在 Parity 模式固定为20个市场交易日，并进入 `evaluation_protocol_key`。

具体语义：

- 入场日为 T+1 时，使用截至信号日 T 的 ADV20，包含 T 日成交额；
- 第一次退出日为 D 时，使用截至 D-1 的 ADV20；
- 后续每个退出尝试日重新计算截至前一交易日的 ADV20；
- 不使用执行日当天整日成交额；
- ADV 不冻结在信号日，因为退出时已经获得更多合法可见的历史流动性信息；
- 已确认停牌的交易日成交额按0计入窗口；
- 行情缺失但无法证明停牌时，视为数据质量错误，不直接填0；
- 有效观察数不足 `min_adv_observations` 时，订单拒绝或 cohort 标记 `EXECUTION_DATA_INCOMPLETE`。

容量：

```text
capacity_value
= ADV20_as_of_previous_close × max_participation
```

滑点和市场冲击的参与率分母也使用同一个因果 ADV。

### 27.2 100股整手和费用预扣算法

**问题**

费用预估、整手调整和现金验证是循环试算，还是只做一次向下取整。

**决策**

不采用不定次数循环，也不只做一次简单取整。实现确定性的最大可买整手求解器：

```python
max_affordable_quantity(
    cash_budget,
    reference_price,
    adv,
    capacity,
    fee_policy,
    lot_size=100,
) -> int
```

算法：

1. 目标金额、容量和可用现金形成数量上界；
2. 将数量上界转换为整手数量；
3. 对整手数量执行有界整数二分搜索；
4. 找到满足下式的最大数量：

```text
成交金额
+ 佣金
+ 过户费
+ 滑点和冲击
<= 可用现金
```

费用和冲击成本必须对数量单调不减，否则费用政策配置失败。最终成交事件生成前已经确定成交数量和精确费用，禁止先成交再形成负现金。

买入按100股整手；卖出允许一次性卖出实际剩余零股。

### 27.3 删除 buffer 后的持仓重叠

**问题**

Cohort 模式是否每个评估日独立做纯 Top-N，相邻 cohort 高度重叠时是否仍分别计算收益。

**决策**

是。每个 cohort：

- 独立执行纯 Top-N；
- 不读取前一期持仓；
- 独立买入、持有、退出和计算收益；
- 不跨 cohort 净额结算；
- 允许同一股票同时属于多个 cohort。

重叠导致的统计相关性由 HAC 和时间块 Bootstrap 处理，不通过持仓缓冲改变样本。

额外输出：

```text
adjacent_cohort_overlap_ratio
signal_rank_persistence
symbol_selection_frequency
```

以上指标只描述信号稳定性，不改变目标组合。

如果策略定义本身依赖历史持仓，例如跌出前70名才退出，它不是无状态 Cohort 策略，应由未来 Portfolio 引擎评价。

### 27.4 新旧目录与调用方切换

**问题**

新引擎是完全在新目录构建并到第五期才切换，还是第一期就改造现有 Graph 和 Strategy runner。

**决策**

采用绞杀式迁移。

第一期：

- 在 `agent/backtest/stockpred/cohort/` 和 `agent/backtest/stockpred/execution/` 构建新引擎；
- 旧目录不做破坏性修改；
- 通过单元测试、小样本双跑和事实表校验验证新引擎。

第二期：

- 在 `StrategyReportExecutor`、`GraphBacktestService` 和策略批次入口接入新引擎；
- `evaluation_engine="cohort"` 的 Graph 与 Alpha 全部进入新引擎；
- 旧 runner 不再产生新的 Cohort 报告。

迁移期：

- 旧 `stockpred_graph/runner.py` 和 `stockpred_strategy/runner.py` 仅服务 legacy 报告或对照测试；
- 新报告统一使用 `signal_cohort_v1`；
- 同一请求不得混用新旧收益实现。

第五期负责物理清理，不负责首次接入。

### 27.5 顾问评价指标归属

**问题**

`direction_accuracy`、`action_accuracy`、`crowding_alert`、`stop_loss_hit_rate` 和 `take_profit_hit_rate` 在 Cohort 模式中是否保留。

**决策**

以下指标在 v1 保留为非排名信号诊断，迁入：

```text
stockpred/cohort/signal_diagnostics.py
```

保留并修正：

- `direction_accuracy` 拆分为：
  - `bullish_hit_rate`
  - `bearish_hit_rate`
  - `balanced_direction_accuracy`
  - `direction_coverage`
- `action_accuracy`
- `crowding_alert_count`
- `crowding_alert_hit_rate`

这些指标必须：

- 基于执行前冻结的 direction、action 和 crowding；
- 报告 coverage；
- 不删除无法成交、无法退出或未来表现不完整的样本；
- 不参与默认排行榜。

v1 不沿用当前 `stop_loss_hit_rate` 和 `take_profit_hit_rate`。当前实现只比较期末收益与阈值，没有检查价格路径和触发顺序，语义不成立。

旧报告由 legacy reader 原样展示，并标注 `legacy_endpoint_diagnostic`。

未来如果恢复止盈止损诊断，应放入：

```text
stockpred/cohort/path_diagnostics.py
```

它需要使用逐日 high/low 模拟 barrier，并明确同日双触发的保守顺序。只有止盈止损实际产生订单时，才可以进入策略净收益。

### 27.6 HAC 和时间块 Bootstrap 的依赖

**问题**

是否新增 statsmodels，还是使用 NumPy/SciPy 实现 HAC。

**决策**

不新增 statsmodels。当前只需要估计样本均值的 HAC 标准误，不需要通用回归模型。

使用 NumPy 实现 Newey-West：

```text
L = max(ceil(holding_days / eval_step) - 1, 0)
w_k = 1 - k / (L + 1)

long_run_variance
= gamma_0 + 2 × sum(w_k × gamma_k)

variance_of_mean
= long_run_variance / n
```

`gamma_k` 使用固定分母 n，最终方差截断到非负。

时间块 Bootstrap：

```text
method = moving_block_bootstrap
block_length = max(2, ceil(holding_days / eval_step))
resamples = 2000
confidence_level = 0.95
```

随机种子由 `evaluation_protocol_key` 派生，保证结果可重现。

测试使用手算样本和冻结参考值，不引入 statsmodels 运行依赖。如果未来需要多变量回归、alpha 回归或通用 HAC，再单独评估依赖。

### 27.7 双时态数据是否为 v1 前置条件

**问题**

当前数据层没有完整 `available_at/system_from/system_to`，是否可以用数据快照冻结和 `knowledge_as_of` 作为替代。

**决策**

完整双时态不是开发 Cohort 引擎和生成研究报告的前置条件，但它是可修订数据策略进入严格排行榜的前置条件。

新增：

```text
pit_assurance:
  strict
  snapshot_only
```

规则：

- 行情、涨跌停等不可修订或有明确事件日期的数据，在版本冻结和日期截断后可以达到 `strict`；
- 财务、名称、行业等可修订数据，只有支持 `available_at/system_from` 历史查询后才能达到 `strict`；
- 策略依赖任一无法证明历史可见性的关键表时，本次运行标记为 `snapshot_only`。

`snapshot_only`：

- 可以用于开发、迁移双跑和查看报告；
- 必须显示 PIT 能力警告；
- `ranking_eligible=false`；
- 不进入严格排行榜或晋级流程。

Lance version 冻结加 `knowledge_as_of` 只能证明可重现，不能证明不存在修订前视，不能冒充完整 PIT。

### 27.8 新旧 artifacts 目录关系

**问题**

新 Cohort 是否完全使用 `artifacts_versions/`，API 和前端是否兼容旧 `artifacts/`。

**决策**

新 Cohort 只写：

```text
artifacts_versions/<version_id>/
artifacts_current.json
```

不同时向旧 `artifacts/` 双写，避免一致性问题。

后端增加统一解析器：

```python
class RunArtifactResolver:
    def resolve(run_dir) -> ResolvedArtifacts:
        if artifacts_current.json exists:
            return versioned_artifacts
        if artifacts/ exists:
            return legacy_artifacts
        raise ARTIFACTS_MISSING
```

规则：

- 新报告读取 `artifacts_current.json` 指向的不可变版本；
- 旧报告读取 `artifacts/`；
- 旧报告只读，不迁移、不写回；
- 所有读取方使用 resolver，不自行拼路径；
- 前端不感知物理目录；
- 不使用 Windows 符号链接模拟 `artifacts/`。

兼容两种结构是本次实现范围，但兼容逻辑只存在于统一后端 resolver。

### 27.9 前端实现范围

**问题**

新设计是重写整个 StockPred 页面，还是在现有页面增加 tab；旧报告兼容是否在本次范围。

**决策**

不重写整个 StockPred 页面。保留现有运行详情壳，按 schema 分流：

```text
RunDetail
├── CohortStockPredReport
└── LegacyStockPredReport
```

`CohortStockPredReport` 使用：

- 概览；
- Cohorts；
- 稳定性；
- 个股；
- 数据质量。

v1 必须完成：

- 新概览指标；
- cohort 散点与收益分布；
- 分时期稳定性表；
- 个股 K 线和买卖点；
- 数据质量页。

瀑布图和更多滚动图属于 v1.1，不阻断 Cohort 核心上线。

旧报告只读兼容组件属于本次实现范围。它从现有逻辑提取，不重新计算或改写旧报告。StockPred 批次首页只更新排序列、质量状态和 schema 标签。

### 27.10 UNLIQUIDATED 的保守退出成本

**问题**

未退出残仓是否只使用现有卖出成本估计，还是增加额外保守折扣。

**决策**

不只使用现有 `estimate_one_way_cost_bps(side="sell")`。正常费用和有限滑点不能代表长期无法退出的流动性风险。

终止估值：

```text
terminal_value
= last_valid_mark_value
- estimated_sell_fees
- liquidity_haircut
```

折扣公式：

```text
base_haircut_rate = 10%

limit_band_rate
= 最近可确认的适用跌停幅度
  无法确认时取10%

stale_penalty_rate
= min(stale_days × 0.5%, 10%)

liquidity_haircut_rate
= min(
    max(base_haircut_rate, limit_band_rate)
    + stale_penalty_rate,
    30%
  )

liquidity_haircut
= last_valid_mark_value × liquidity_haircut_rate
```

此外仍扣除新 `CostPolicy` 基于最新因果 ADV 计算的：

- 卖出佣金；
- 印花税；
- 过户费；
- 市场冲击。

参数可配置并进入 `evaluation_protocol_key`。报告同时输出5%、10%、20%、30%四档压力情景。

严格排行榜使用上述默认保守估值；未经 haircut 的 `horizon_mark_return` 同时保留，用于区分信号表现和流动性压力。

### 27.11 基准退出日与退出延迟

**问题**

cohort 因停牌、跌停或容量不足延迟退出时，基准使用固定目标退出日还是实际退出日。

**决策**

不把两种用途压缩为一个基准字段。

验证固定期限信号有效性时使用目标退出日：

```text
benchmark_target_horizon_return
= benchmark_adjusted_open(target_exit_date)
  / benchmark_adjusted_open(T+1)
  - 1

target_horizon_excess_return
= horizon_mark_return
  - benchmark_target_horizon_return
```

该基准不跟随策略退出延迟。否则同一持有期的 cohort 会因执行困难被改变评价区间，固定期限
信号将失去横向可比性。

验证实际执行和清算政策时，使用现金流匹配基准：

1. 在策略实际入场资金投入时，按相同金额建立虚拟基准头寸；
2. 策略某部分资金在日期 D 完成退出时，虚拟基准在 D 按相同比例赎回；
3. 已赎回部分转为现金并保持现金；
4. 部分退出跨越多个日期时逐笔匹配，不用最后一笔退出日代表全部头寸；
5. 未退出残仓终止估值时，虚拟基准按相同剩余资金比例在终止估值日估值。

```text
liquidation_policy_excess_return
= liquidation_return
  - benchmark_liquidation_matched_return
```

固定期限信号图表和诊断使用 `target_horizon_excess_return`；实际执行分析使用
`liquidation_policy_excess_return`。禁止只保留一个会随运行状态改变含义的
`excess_return`。

### 27.12 raw_signal_return 的精确定义

**问题**

`raw_signal_return` 是 T+1 开盘到目标日收盘，还是 T+1 开盘到固定目标退出日开盘。

**决策**

使用固定 open-to-open 标签：

```text
entry_date
= signal_date 后第一个市场交易日

target_exit_date
= entry_date 在市场交易日序列中的位置 + holding_days

raw_symbol_return
= adjusted_open(target_exit_date)
  / adjusted_open(entry_date)
  - 1

raw_signal_return
= sum(target_weight_i × raw_symbol_return_i)
```

该收益：

- 使用执行前冻结的目标股票和目标权重；
- 忽略涨跌停、停牌可成交性、容量、整手、费用和退出延迟；
- 不因目标退出日不可卖而顺延；
- 不读取 ExecutionPolicy 产生的成交结果；
- 只能由 `LabelMarketView` 计算。

如果固定入场或目标退出日没有有效开盘价，该股票的 raw label 为缺失。报告输出：

```text
raw_label_coverage
= 有完整固定期限标签的目标权重之和
  / 全部目标权重之和
```

不得删除缺失股票后对剩余目标权重重新归一化并把结果冒充完整 cohort 收益。低于质量门槛
时 `raw_signal_return` 标记为数据不足，不进入严格信号排名。

当前 `simulate_trades` 的 `fwd_ret_5d` 不能直接复用。现有实现是 T+1 可入场日开盘到
目标日或之后第一个可卖出日开盘，已经包含入场可行性和退出顺延。迁移期如继续输出该
字段，应标记为 `legacy_sellable_open_return`，避免与固定期限 raw label 混淆。

### 27.13 StrategyAdapter 诊断列

**问题**

`direction`、`action` 和 `crowding_score` 应放入 `strategy_diagnostics`，还是成为信号
快照的显式列；不产出这些字段的 Alpha Zoo 策略如何处理。

**决策**

采用标准字段显式化、策略扩展字段保留在 map/json 的契约：

```text
evaluation_date         required
ts_code                 required
score                   required
direction               optional nullable
action                  optional nullable
crowding_score          optional nullable
confidence              optional nullable
strategy_diagnostics    optional map/json
```

规则：

- 公共指标、前端筛选、schema 校验和 Parquet 查询依赖的字段必须显式化；
- `strategy_diagnostics` 只保存策略特有、展示性质或尚未标准化的字段；
- 公共评价器不得逐行解析任意 JSON 才能计算指标；
- Graph 适配器把现有 direction、action 和 crowding 映射到标准列；
- Alpha Zoo 可以不提供这些可选列。

策略完全不提供某字段时：

```text
coverage = 0
metric_value = null
metric_status = "not_applicable"
```

不能把准确率写成0，因为0表示全部预测错误，不表示策略没有产出该诊断。部分提供时输出
实际 coverage，仅在有效标签上计算指标；coverage 低于门槛时
`metric_status="insufficient_coverage"`。这些顾问诊断不参与默认排行榜。

### 27.14 cohort_id、运行 ID 和比较身份

**问题**

`cohort_id` 使用 UUID 还是确定性 ID；同一输入重跑如何保证幂等和可比。

**决策**

逻辑 `cohort_id` 使用确定性哈希：

```text
cohort_id
= "cohort_"
  + sha256(
      "signal_cohort_v1"
      + evaluation_protocol_key
      + strategy_id
      + strategy_version
      + evaluation_date
    )[0:24]
```

不把 `batch_id`、随机 UUID、进程号或开始时间放入 `cohort_id`。另外保存：

```text
target_snapshot_hash    冻结目标内容哈希
cohort_attempt_id       单次尝试 UUID
run_id                  批次运行 UUID
```

相同逻辑输入重跑必须生成同一 `cohort_id`。如果相同 `cohort_id` 对应不同
`target_snapshot_hash`，运行以 `NON_DETERMINISTIC_SIGNAL` 失败，提示排序不稳定、
数据漂移、随机种子或策略副作用问题。

比较身份同时拆分：

```text
evaluation_protocol_key
    数据快照、日期、持有期、选股数量、执行、成本、估值、基准和质量门槛

strategy_version
    策略源码、参数、Adapter 和一方依赖闭包

run_identity
    evaluation_protocol_key + strategy_version + run_id
```

严格排行榜比较相同 `evaluation_protocol_key` 下的不同策略。策略源码不得进入共享协议
键，否则不同策略不可能进入同一个严格比较组。兼容字段 `comparison_key` 在
`signal_cohort_v1` 中固定等于 `evaluation_protocol_key`。

### 27.15 批次并行模型

**问题**

新 Cohort 引擎是延续每策略一个 worker，还是增加跨评估日的 cohort 并行。

**决策**

第一期使用策略级进程并行、策略内部 cohort 串行：

```text
ProcessPoolExecutor
├── strategy task A
│   ├── evaluation_date 1
│   ├── evaluation_date 2
│   └── aggregate + publish
├── strategy task B
└── strategy task C
```

这里的“每策略一个 worker”表示每个策略是一个独立进程池任务；物理 worker 进程可以
复用并继续执行其他策略任务。每个策略任务：

1. 打开固定数据快照；
2. 初始化一次 StrategyAdapter 和 `BatchDataContext`；
3. 按评估日升序串行生成信号、目标、执行和 cohort 事实；
4. 在进程内复用 panel、ADV 和滚动特征缓存；
5. 每完成一个 cohort 发布进度并写入可恢复检查点；
6. 全部日期完成后计算 HAC、Bootstrap 和稳定性指标；
7. 对策略海选阶段涉及的全量个股统一生成图表包；
8. 原子发布 artifacts 版本。

禁止 worker 内再创建 `ProcessPoolExecutor`。这样可以避免嵌套进程池、CPU 超额订阅、
Lance/SQLite 并发读取放大和结果顺序不确定。

并行度由以下配置控制：

```text
STOCKPRED_BATCH_WORKERS
default = min(2, available_cpu)
```

当前实现的上限8不直接继承为新引擎默认值，应经过数据读取、峰值内存和图表产物压测后
再提高。未来性能优化优先使用批量读取、跨日期向量化、缓存和图表数据复用，不在 v1
引入 cohort 级多进程。

### 27.16 Q&A 决策后的实施顺序

```text
1. 新 Cohort 领域模型、确定性身份和标准诊断列
2. 因果 ADV、整手费用求解器及独立 Target、Execution 和 Ledger
3. 独立 raw label、双基准和 Cohort 收益
4. HAC、Bootstrap 和稳定性统计
5. versioned artifacts 与 RunArtifactResolver
6. 图表包
7. Graph 和 Alpha 调用方切换及策略级并行
8. 新前端与 legacy reader
9. 双时态数据逐表补齐
10. 严格排行榜开放
11. 旧代码清理
```

双时态数据可以与前七项并行实施，但在相关表达到 `pit_assurance=strict` 前，依赖这些表的策略只能生成 `snapshot_only` 报告。

## 28. 完成定义

`signal_cohort_v1` 只有满足以下条件才视为交付：

1. Graph 和 Alpha 均通过统一 Cohort 引擎产生结果；
2. 默认排行榜来自承诺资金口径的 liquidation return；
3. 拒单、闲置现金、成本和残仓完整进入收益；
4. 无全局账户 NAV 和隐性杠杆指标；
5. 重叠样本统计经过 HAC 或时间块 Bootstrap；
6. 质量不合格策略不能进入严格排行榜；
7. 海选成功即具备完整 OHLCV 图表包和买卖点；
8. 前端查看图表不触发物化或写操作；
9. 新旧报告按 schema 明确隔离；
10. 未来 Portfolio 引擎可以复用信号、订单、成交和估值接口。
