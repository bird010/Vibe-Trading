# 文章三因子代表基金轮动策略设计

日期：2026-08-24

状态：设计已确认，尚未实现、尚未回测

拟新增策略 ID：`ai_rotation_r57_three_factor_representative`

研究基准：`ai_rotation_r39_incumbent_carry`

前端默认：继续保持 R34，本设计不改变前端默认策略

## 1. 背景与证据修正

本设计用于在现有基金轮动回测框架内复现用户提供的微信公众号文章策略。此前只能依据二手复现资料推断文章结构；用户现已提供原文，因此以下内容可视为文章明确提出：

- 候选资产由 ETF 构成；原文示例使用红利低波、创业板50、纳指和黄金四只 ETF。
- 每只候选 ETF 分别计算乖离动量、斜率动量和效率动量。
- 三个因子先做横截面 Z-Score，再按 `0.3/0.3/0.4` 加权。
- 选择综合评分最高的 ETF。
- 新候选只有在综合得分严格超过当前持仓得分的 `1.5` 倍时才换仓。
- QMT 版本每日生成交易计划并执行，使用复利账户。
- 原文权重和阈值来自历史参数遍历，作者同时明确提示过拟合风险。

用户已确认两项项目适配：

1. 不使用原文固定四 ETF，而使用现有相关性聚类流程在每个簇中锁定的当前代表基金作为每日候选集合。
2. 三因子评分和换仓采用日频；聚类和代表选择仍遵守现有框架的冻结周期与锁定语义，不改为每日重新聚类。

因此，本策略是“文章信号在当前代表基金 Universe 上的框架内复现”，不是文章固定四 ETF 业绩的逐数值复刻，也不是 R39 的叠加优化。

## 2. 第一性原理与不可变约束

### 2.1 问题

需要回答的唯一研究问题是：

> 在保持当前 PIT Universe、相关性聚类和代表基金选择机制的条件下，文章的日频三因子 Top-1 加 `1.5` 倍换仓阈值，能否形成一条因果、可审计、可与 R39 公平比较的独立基金轮动策略？

### 2.2 基本事实

- 三因子解决“如何评价当前代表基金的趋势强度与路径质量”。
- Z-Score 解决三个因子量纲不同的问题。
- `1.5` 倍阈值解决候选分数接近时的频繁换仓问题。
- 聚类与代表基金机制解决候选池去重和同类 ETF 代表选择问题。
- 公共 Runner 负责实际执行、费用、滑点、容量、整手、残单和连续账户复利。

### 2.3 不可变约束

- 新增独立策略，不修改 R39、R34 或其他既有策略及其默认值。
- 不修改公共 Runner、执行器、PIT/data/API contract 或评价政策。
- 只读取 signal close 及之前可见的数据；执行由公共 Runner 在后续可交易时点完成。
- 不使用文章的 2019 年至今结果选择参数，也不使用本项目已消费 confirmation `20220801..20260801` 做设计或选择。
- 本轮固定文章参数，不做权重、窗口或阈值搜索。
- 最高结果等级仍为 research-only；不得据此声称部署资格。

## 3. 方案选择

采用“文章信号忠实复现、数据与执行遵守现有框架”的方案：

- 复现：三因子公式、Z-Score、`0.3/0.3/0.4`、Top-1、日频、严格 `1.5` 倍换仓。
- 适配：候选池改为当前聚类代表基金；价格采用 PIT 可见复权口径；信号在收盘后形成，公共 Runner 后续执行。
- 不复现：QMT 的买入价 `当前价×1.01`、卖出价 `当前价×0.99`、8 点打印计划和本地文件备份。这些属于平台或执行层，不属于本策略唯一信号机制；公共框架已有连续账户、产物持久化和执行模型。

拒绝以下方案：

- 修改公共执行器以模拟 QMT 限价，因为文章未给出未成交、撤单、重试和排队细节，且会破坏与 R39 的执行可比性。
- 将三因子叠加到 R39 Top-3、staging 或 carry，因为这会变成另一条优化假设，而不是本文策略复现。

## 4. 总体架构

```text
PIT Universe
  → 现有相关性聚类与质量门禁
  → 现有代表基金选择及锁定
  → 当前有效代表基金集合
  → 日频三因子原始值
  → 完整样本过滤
  → 横截面 Z-Score
  → 0.3/0.3/0.4 综合评分
  → 确定性 Top-1
  → 与上一目标执行 1.5 倍阈值比较
  → SET_TARGETS / HOLD_TARGETS / 现金
  → 公共 Runner 后续执行
```

策略插件负责四个边界清晰的单元：

1. `RepresentativeLifecycle`：复用现有聚类、质量门禁、代表选择和锁定语义，维护当前代表集合。
2. `ThreeFactorModel`：从因果日线计算三项原始因子。
3. `CrossSectionalScorer`：完整样本过滤、Z-Score、加权和排名。
4. `ThresholdSelector`：维护上一目标状态并决定首次建仓、继续持有、阈值换仓、代表失效强制切换或转现金。

这些是设计职责边界，不要求为每个单元建立公共抽象。首版应遵循现有策略目录风格，以最少新文件实现，不把文章专用逻辑提升到公共 Runner。

## 5. 聚类与代表基金生命周期

### 5.1 冻结配置

沿用 `CorrelationRepresentativeConfig` 当前默认值：

| 参数 | 值 |
|---|---:|
| `k` | 8 |
| `top_n` | 1（仅 Schema/描述一致性） |
| `correlation_lookback_weeks` | 52 |
| `momentum_window_weeks` | 4（仅继承校验，不参与评分） |
| `recluster_interval_weeks` | 26 |
| `min_valid_weeks` | 20 |
| `min_pairwise_weeks` | 20 |
| `representative_candidate_count` | 5 |
| `representative_min_cluster_corr` | 0.85 |
| `representative_liquidity_window_days` | 20 |
| `representative_min_liquidity_observations` | 15 |
| `max_cluster_share_warn/reject` | 0.50 / 0.80 |
| `min_effective_cluster_count_warn/reject` | 4.0 / 2.5 |

父策略的 `top_n` 组合选择和四周簇动量不参与文章策略最终选择。R57 将继承字段 `top_n` 固定为1，仅用于 Catalog Schema 与 pipeline 描述一致性；聚类仍生成最多8个当前代表，文章因子从这些代表中选 Top-1。

### 5.2 日频与重聚类

- `scheduled_dates` 返回模拟起止日期之间的全部交易日。
- 首个具备完整聚类预热数据的信号日建立初始聚类和代表集合。
- 之后只在达到现有 `26` 周重聚类间隔的 ISO 周末信号日重聚类。
- 重聚类计数单位必须是已完成的不同 ISO 周，不是 `evaluate()` 调用次数；日频调用不能让现有周计数器每天递增。Session 应保存上次处理的 ISO 周键和自上次重聚类以来完成的周数，只在进入新的 ISO 周末信号时推进一次。
- 其他交易日保持冻结簇结构，并调用既有代表锁维护逻辑；锁定代表只有在硬失效时才更换，不因 ADV 短期排序每天漂移。
- 每次因子评分使用当日维护后的当前有效代表集合。

### 5.3 聚类与代表异常

- 初始聚类数据不足：返回 `INVALID`，原因码 `CLUSTERING_DATA_INSUFFICIENT`；技术运行按现有契约失败，不伪装成现金策略。
- 聚类质量为 WARN/REJECT：沿用现有 `correlation_representative` 的质量状态和诊断语义；策略产物必须记录门禁结果。不得在本策略中另造质量政策。
- 某簇没有有效代表：该簇不进入当日三因子候选，不从同簇外补位。
- 当前目标不再是当前代表、离开 PIT Universe 或发生硬失效：视为目标失效，绕过 `1.5` 阈值，强制切换到当前有效 Top-1；若没有足够候选则转现金。

## 6. 数据需求与复权

策略声明以下数据集和字段：

- `fund`：`ts_code, trade_date, open, high, low, close, vol, amount`
- `fact_fund_adj`：`ts_code, trade_date, adj_factor`
- `dim_fund`：`ts_code, name, list_date`

信号只通过 `CausalDataView` 读取：

- 聚类使用 PIT 可见复权周收益。
- 代表选择使用截至 signal date 的 ADV 与上市/可交易信息。
- 因子使用截至 signal date 的复权日 OHLC。

对每只基金，以 signal date 当日复权因子为基准，将窗口内 OHLC 同比例调整：

```text
adjusted_field_t = raw_field_t × adj_factor_t / adj_factor_signal
```

同一日的 open/high/low/close 使用相同系数，保留日内相对关系。该适配避免 ETF 拆分或份额调整制造虚假动量。不得前向填充跨越缺失交易日；只允许对文章原始代码中明确出现的序列内值执行其既定计算。

预热天数取聚类与因子需求最大值。聚类 52 周收益需要至少 53 个有效周末价格，现有默认换算为 `(52+1)×5-1=264` 个交易日；它大于因子的 49 日需求，因此策略 `warmup_trade_days=264`。

## 7. 三因子模型

### 7.1 乖离动量

冻结参数：

- `bias_ma_days=25`
- `bias_regression_days=25`
- 输入复权收盘价数量：`49`

公式：

```text
MA_t = mean(Close[t-24:t])
Bias_t = Close_t / MA_t
Y_j = Bias_j / Bias_first, j 为最近 25 个完整 Bias
Score_bias = 10000 × OLS_slope(x=0..24, y=Y)
```

回归包含截距。要求最近25个进入回归的 Bias 都拥有完整25日均线，因此读取 `25+25-1=49` 个有效交易日，而不依赖样本起点的 expanding mean。任一收盘价、均线或归一化基值非正/非有限时，该基金当日乖离因子无效。

### 7.2 斜率动量

冻结参数：`slope_days=25`。

```text
Y_j = Close_j / Close_first, j 为最近 25 个交易日
拟合 Y = intercept + slope × x, x=1..25
Score_slope = 10000 × slope × R²
```

回归包含截距。常数价格序列定义 `slope=0, R²=0, Score_slope=0`，不输出 NaN。首价非正、有效观测不足或回归结果非有限时，该基金当日斜率因子无效。

### 7.3 效率动量

冻结参数：`efficiency_days=25`。

```text
Pivot_t = (Open_t + High_t + Low_t + Close_t) / 4
Momentum = 100 × ln(Pivot_last / Pivot_first)
Direction = abs(ln(Pivot_last) - ln(Pivot_first))
Volatility = Σ abs(diff(ln(Pivot_t)))
EfficiencyRatio = Direction / Volatility, Volatility>0；否则为 0
Score_efficiency = Momentum × EfficiencyRatio
```

要求最近25个交易日的复权 OHLC 全部有限且严格为正。`Volatility=0` 时得分为0。效率比必须位于 `[0,1]` 的浮点容差范围内；若数值误差轻微越界可截到边界，实质越界则视为无效。

## 8. 完整样本、Z-Score 与排名

### 8.1 完整样本

当日候选集合是“三项原始因子都有限”的当前代表基金。三个因子必须在同一个完整样本集合上标准化，禁止每个因子使用不同基金子集。

- `minimum_complete_candidates=2`。
- 少于2只完整候选时不计算横截面排名。
- 不把缺失因子填成0，不沿用上一日因子，不使用截面均值插补。

### 8.2 标准化

对每个因子使用横截面总体标准差，即 `ddof=0`：

```text
Z_f(i) = (Score_f(i) - mean_f) / std_f
```

若某因子的横截面标准差在绝对容差 `1e-12` 内为0，则该因子所有候选的 Z-Score 定义为0。容差仅用于识别数值常数，不得作为参数搜索项。

### 8.3 综合评分

```text
Composite(i) = 0.3 × Z_bias(i)
             + 0.3 × Z_slope(i)
             + 0.4 × Z_efficiency(i)
```

权重必须有限、非负且和严格等于1（绝对容差 `1e-12`）。综合分从高到低排名；完全并列时按 `ts_code` 升序。文章是相对排名策略，不增加“综合分必须为正”的过滤。

## 9. Top-1 与 1.5 倍换仓阈值

Session 保存上一 signal decision 的正权重目标代码，不能读取实际持仓、订单或成交决定信号。

### 9.1 决策规则

1. 首次有效决策：对 Top-1 返回 `SET_TARGETS`，目标权重 `1.0`、现金 `0.0`。
2. 上一目标仍为当日 Top-1：返回 `HOLD_TARGETS`。
3. Top-1 改变，且上一目标仍在当日完整候选集合：仅当

   ```text
   Composite_new > 1.5 × Composite_held
   ```

   时返回新 Top-1 的 `SET_TARGETS`；恰好等于阈值不换仓，否则 `HOLD_TARGETS`。
4. 上一目标不再是当前代表、不在 PIT Universe、因子不完整或发生硬失效：不应用阈值，强制切换到当日 Top-1。
5. 完整候选少于2只：
   - 上一目标仍是当前有效代表且其三因子完整时，返回 `HOLD_TARGETS`；
   - 否则返回 `SET_TARGETS` 空目标、现金 `1.0`，原因码 `INSUFFICIENT_COMPLETE_CANDIDATES`。

### 9.2 负分语义

Z-Score 综合分可能为负，原文没有定义倍率阈值在负分下的特殊处理。为忠实复现，首版按字面公式执行，不做平移、绝对值、百分位或正分门禁。

例如 `held=-1.0` 时阈值为 `-1.5`，排名更高的新候选可能较容易通过。这会削弱震荡抑制作用，但属于待验证的原文机制风险，不得在首轮复现中事后修正。每次负分阈值比较必须记录诊断字段 `negative_threshold_case=true`。

## 10. 策略配置

拟新增冻结 Pydantic 配置 `ArticleThreeFactorRepresentativeConfig`，`frozen=True, extra="forbid"`。聚类/代表字段保持现有名称和默认值；文章字段如下：

| 字段 | 默认值 | 约束 |
|---|---:|---|
| `bias_ma_days` | 25 | `>=2` |
| `bias_regression_days` | 25 | `>=2` |
| `slope_days` | 25 | `>=2` |
| `efficiency_days` | 25 | `>=2` |
| `bias_weight` | 0.3 | 有限、`>=0` |
| `slope_weight` | 0.3 | 有限、`>=0` |
| `efficiency_weight` | 0.4 | 有限、`>=0` |
| `rebalance_threshold` | 1.5 | 有限、`>0` |
| `target_weight` | 1.0 | 固定为 `1.0` |
| `minimum_complete_candidates` | 2 | 固定为 `2` |
| `zscore_ddof` | 0 | 固定为 `0` |
| `rebalance_freq` | `D` | 固定为日频 |

首轮不允许通过 API 改变文章参数。若配置模型为展示 Schema 暴露这些字段，也必须由校验器拒绝非冻结值，确保此次回测检验的是一个预注册策略，而不是参数搜索面。

## 11. 因果时序与执行边界

每日事件顺序为：

1. signal date 收盘后，CausalDataView 暴露截至当日的 PIT Universe、周收益、日 OHLC、复权和 amount。
2. 如达到重聚类日期，先重聚类并锁定代表；否则维护当前代表锁。
3. 在当前代表集合上计算三因子、Z-Score 和综合排名。
4. 使用上一目标状态执行阈值规则，形成目标权重。
5. 公共 Runner 在后续可交易时点按冻结执行合同下单、成交和估值。

不得使用 signal date 之后的价格，不得假设以 signal close 成交。文章 QMT 的“8点计划、9:30交易”在框架中对应“上一交易日收盘形成信号、下一可交易日执行”，不要求策略插件实现时钟、打印或下单价格偏移。

复利由公共连续账户净值自然实现；策略不得自行计算或重置本金。

## 12. 决策诊断与产物

策略 artifact roles 至少包含：

- `cluster_history`
- `gates`
- `representatives`
- `exclusions`
- `factor_scores`
- `decisions`

每日 `factor_scores` 对每个当前代表记录：

- `ts_code`、`cluster_id`、是否当前代表；
- 49/25日有效观测数量；
- 三项原始因子及有效状态；
- 完整样本资格；
- 三项截面均值、标准差和 Z-Score；
- 综合评分、排名；
- 当日 Top-1 和上一目标；
- `threshold=1.5`、`held_score`、`challenger_score`、阈值右侧值；
- `threshold_passed`、`negative_threshold_case`；
- `forced_switch_reason`、最终 action、目标权重和现金。

所有诊断必须符合严格 JSON 子集，禁止 NaN/Infinity；不可用值写 `null` 并附稳定状态码。

## 13. 失败与回退语义

| 情况 | 行为 |
|---|---|
| 聚类预热不足或无法形成合法聚类 | `INVALID/CLUSTERING_DATA_INSUFFICIENT` |
| 单个代表日线不足、价格非正、复权缺失 | 该代表退出当日完整样本 |
| 某因子截面标准差为0 | 该因子全部 Z-Score 为0，其他因子继续 |
| 完整候选少于2且旧目标仍合法完整 | `HOLD_TARGETS` |
| 完整候选少于2且旧目标失效 | 100%现金 |
| 旧目标离开代表集合或 PIT Universe | 绕过阈值，强制切换 Top-1；无 Top-1 则现金 |
| 输入出现重复代码、非法键或非有限权重状态 | fail-closed，不输出部分目标；按契约返回 INVALID 或现金，具体由发生层决定 |
| 权重加现金不能验证为1 | `StrategyContractViolation`，技术运行失败 |

不得使用未来数据、零填充因子、旧分数沿用或跨标的补值来挽救缺失。

## 14. 与现有代码的接口

### 14.1 策略插件接口

新策略必须实现现有 `FundRotationStrategy`/`FundRotationStrategySession` 契约，不增加新的公共接口。拟定类与方法如下：

```python
class AiRotationR57ThreeFactorRepresentativeStrategy:
    descriptor: FundRotationStrategyDescriptor
    config_model = ArticleThreeFactorRepresentativeConfig
    artifact_roles: tuple[str, ...]

    def describe_decision_pipeline(
        self,
        config: BaseModel,
    ) -> dict[str, object]: ...

    def resolve_requirements(
        self,
        config: BaseModel,
    ) -> StrategyDataRequirements: ...

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> AiRotationR57ThreeFactorRepresentativeSession: ...
```

```python
class AiRotationR57ThreeFactorRepresentativeSession(
    CorrelationRepresentativeSession
):
    def scheduled_dates(
        self,
        calendar: tuple[str, ...],
        simulation_start_date: str,
        evaluation_end_date: str,
    ) -> tuple[str, ...]: ...

    def evaluate(
        self,
        context: StrategyDecisionContext,
    ) -> TargetWeightDecision: ...

    def finalize(self) -> StrategyDiagnostics: ...
```

接口语义：

- `descriptor.id` 固定为 `ai_rotation_r57_three_factor_representative`，`interface_version="1.0"`，`supported_universe=("etf",)`，`deterministic=True`。
- `describe_decision_pipeline()` 供现有 batch child runtime 写入策略快照；必须返回普通 `dict`，明确 Universe、相关性去重、流动性代表、三因子模型、Top-1、满仓和日频阈值规则。
- `resolve_requirements()` 返回第6节声明的数据集、字段、`warmup_trade_days=264`、`frequency="D"`、`needs_benchmark=True`。
- `create_session()` 只创建每个子运行独享的有状态 Session，不保存跨运行全局状态。
- `scheduled_dates()` 使用公共 Runner 传入的评价日历，返回区间内全部交易日。
- `evaluate()` 只通过 `StrategyDecisionContext.data_view` 读取数据，返回 `SET_TARGETS`、`HOLD_TARGETS` 或 `INVALID`；不能访问账户对象。
- `finalize()` 返回第12节定义的策略诊断产物和逐日 decision trace。

Session 继承 `CorrelationRepresentativeSession` 仅为复用 `_pool_at_signal()`、`_recluster()`、`_maintain_locks()`、聚类/门禁/代表状态和既有 artifact 容器；必须覆盖 `scheduled_dates()`、`evaluate()` 和 `finalize()`。不得调用父类 `evaluate()`，因为它同时执行周频簇动量 Top-3，会把文章信号和父策略信号混合。子类必须维护独立的 `last_processed_iso_week`/`completed_iso_weeks_since_recluster`，不能沿用父类“每次 evaluate 增加 week_index”的日频错误语义。

`describe_decision_pipeline()` 虽未列入最小 `FundRotationStrategy` Protocol，但现有 `batch_child_runtime.py` 会在运行时调用并要求返回 `dict`，因此它是本项目实际必需接口，不能省略。

### 14.2 配置接口

新增：

```python
class ArticleThreeFactorRepresentativeConfig(
    CorrelationRepresentativeConfig
):
    model_config = ConfigDict(frozen=True, extra="forbid")
```

该配置继承第5节的现有聚类/代表字段，并新增第10节的文章字段。继承字段 `top_n` 固定为 `1`，只用于 Schema/描述一致性，实际候选 Top-1 由文章评分器产生；继承字段 `momentum_window_weeks` 保留父配置校验所需的默认 `4`，不参与 R57 排名或权重。Catalog 继续通过：

```python
config_model.model_validate(raw_params)
config.model_dump(mode="json", exclude_none=False, exclude_unset=False)
config_model.model_json_schema()
```

完成参数校验、默认值解析、JSON Schema 暴露和配置 hash。新策略不需要修改 Catalog 的解析方式。

### 14.3 因果数据接口

新策略只能调用现有 `CausalDataView`：

```python
view.returns("weekly", lookback=52)
view.daily_bars(
    ["open", "high", "low", "close", "vol", "amount"],
    lookback=49,
)
view.fund_adjustments(lookback=49)
view.eligible_universe()
```

代表选择继续复用现有 helper 所需的周收益、eligible universe 和 ADV 信息。因子层把 `daily_bars()` 与 `fund_adjustments()` 按 `ts_code, trade_date` 一对一合并；重复键、缺失复权或多对多合并必须 fail-closed。现有 `CausalDataView` 已支持这些调用，无需新增数据方法或放宽字段白名单。

### 14.4 策略内部函数边界

首版建议在新策略包内提供以下纯函数，方便手算测试和独立审查；名称和签名在实施计划中必须保持一致：

```python
def adjust_ohlc(
    bars: pd.DataFrame,
    adjustments: pd.DataFrame,
    signal_date: str,
) -> pd.DataFrame: ...

def compute_bias_momentum(
    adjusted_close: pd.Series,
    ma_days: int = 25,
    regression_days: int = 25,
) -> float | None: ...

def compute_slope_momentum(
    adjusted_close: pd.Series,
    lookback_days: int = 25,
) -> float | None: ...

def compute_efficiency_momentum(
    adjusted_ohlc: pd.DataFrame,
    lookback_days: int = 25,
) -> float | None: ...

def score_complete_candidates(
    raw_scores: Mapping[str, Mapping[str, float | None]],
    weights: Mapping[str, float],
    minimum_candidates: int = 2,
) -> tuple[dict[str, float], dict[str, object]]: ...

def apply_rebalance_threshold(
    ranked_scores: Mapping[str, float],
    previous_target: str | None,
    threshold: float = 1.5,
) -> tuple[str | None, str, dict[str, object]]: ...
```

这些函数属于 R57 私有实现，不加入 `backtest.fund_rotation` 公共模块。函数名、参数和返回类型作为 focused tests 与策略 Session 之间的包内接口冻结；若实施发现签名无法满足现有类型契约，必须先修订规格，不能在实现中静默改名或合并。

### 14.5 决策输出接口

正常目标使用现有对象：

```python
TargetWeightDecision(
    decision_id=f"{signal_date}-ai_rotation_r57_three_factor_representative",
    signal_date=signal_date,
    action=DecisionKind.SET_TARGETS,
    target_weights={selected_code: 1.0},
    cash_weight=0.0,
    reason_code="ARTICLE_TOP1_ENTRY|ARTICLE_THRESHOLD_SWITCH|FORCED_REP_SWITCH",
    quality_status=QualityStatus.VALID,
    diagnostics={...},
)
```

`HOLD_TARGETS` 必须使用空 `target_weights`，由 Runner 维持既有目标；转现金使用 `SET_TARGETS`、空目标和 `cash_weight=1.0`。所有 decision ID 在子运行内唯一，诊断只包含严格 JSON 值。

## 15. 需要创建或修改的文件

### 15.1 新建策略文件

| 文件 | 责任 |
|---|---|
| `agent/backtest/fund_rotation/strategies/ai_rotation_r57_three_factor_representative/__init__.py` | 导出策略、配置及必要的公开策略包符号 |
| `agent/backtest/fund_rotation/strategies/ai_rotation_r57_three_factor_representative/config.py` | 冻结 Pydantic 配置及交叉字段校验 |
| `agent/backtest/fund_rotation/strategies/ai_rotation_r57_three_factor_representative/factors.py` | 复权 OHLC、三因子、完整样本 Z-Score 和确定性排名纯函数 |
| `agent/backtest/fund_rotation/strategies/ai_rotation_r57_three_factor_representative/strategy.py` | descriptor、日频 Session、代表生命周期、阈值状态、决策与产物 |

`factors.py` 单独存在是因为它有完整的数学输入/输出和大量手算测试，能在不构造 Runner/Session 的情况下审查。不得进一步拆出只有单一调用点的抽象文件。

### 15.2 新建测试文件

| 文件 | 责任 |
|---|---|
| `agent/tests/fund_rotation/test_ai_rotation_r57_three_factor_representative.py` | 第17节的因子、标准化、阈值、日频、聚类代表、因果性、缺失和守恒 focused tests |

### 15.3 必须修改的既有文件

除新策略及测试文件外，生产代码只需修改一个既有文件：

| 文件 | 最小改动 |
|---|---|
| `agent/backtest/fund_rotation/strategies/registry.py` | 导入 `AiRotationR57ThreeFactorRepresentativeStrategy`，并在 `default_fund_rotation_strategies()` 显式白名单尾部追加该类 |

还需修改两个既有测试文件，但只能追加新 ID：

| 文件 | 最小改动 |
|---|---|
| `agent/tests/fund_rotation/test_strategy_catalog.py` | 在精确排序 ID 列表中追加 `ai_rotation_r57_three_factor_representative`，保留全部旧 ID 和断言 |
| `agent/tests/fund_rotation/test_fund_rotation_catalog_api.py` | 在 API 精确策略列表中追加 R57，继续验证 descriptor、snapshot、requirements 和 frequency 来自 Catalog |

### 15.4 明确不需要修改的文件

| 文件/层 | 不修改原因 |
|---|---|
| `agent/backtest/fund_rotation/contracts.py` | 现有 Strategy、Session、Decision 和 Requirements 契约足够 |
| `agent/backtest/fund_rotation/causal_data.py` | 已支持周收益、日 OHLC、复权和 PIT Universe |
| `agent/backtest/fund_rotation/catalog.py` | 自动读取 registry、配置 Schema、requirements 和策略包 snapshot |
| `agent/src/api/fund_rotation_routes.py` | 策略列表和详情由 Catalog 动态生成，无策略专用路由 |
| `agent/src/stockpred/fund_rotation/batch_service.py` | 批次按通用 strategy binding 执行，不硬编码策略 ID |
| `agent/src/stockpred/fund_rotation/batch_child_runtime.py` | 已支持 `describe_decision_pipeline()` 和通用 Session |
| 公共 Runner、执行器、费用、滑点、容量、整手和估值模块 | 复用冻结执行合同，不复现 QMT 专用限价 |
| 前端策略表单和默认策略配置 | Catalog/Schema 动态展示；本任务不把 R57 设为默认，R34 保持不变 |
| R34、R39 和其他既有策略目录 | 新策略隔离，不能改变历史行为或 hash |

若实施时发现必须修改本表列出的“不需要修改”生产文件，应停止并报告 `DESIGN_SCOPE_BLOCKED`，先更新并重新批准规格，不能把扩大范围作为顺手修复。

### 15.5 研究工件不是源代码修改

实施、审查和回测获单独授权后，会新增 R57 的 analysis/design/implementation/review/test/backtest/decision 工件及 append-only ledger 记录。这些是研究证据，不是运行时接口修改。前端默认、历史 run 和既有实验工件不得改写。

## 16. 实现范围

后续获得实施授权后，只允许：

- 新增 `agent/backtest/fund_rotation/strategies/ai_rotation_r57_three_factor_representative/` 下的配置、因子和策略文件；
- 新增对应 focused tests；
- 在 registry 仅追加 R57；
- 在精确 catalog/API 枚举测试保留全部既有 ID 和断言后追加 R57；
- 新增本策略研究工件。

不得修改 R34、R39、`correlation_representative` 现有行为或默认值。若无法通过组合/复用现有代表选择组件实现，而必须修改公共 Runner、CausalDataView 或执行合同，应停止并报告 `DESIGN_SCOPE_BLOCKED`，不得静默扩大范围。

## 17. 测试设计

### 17.1 因子单元测试

- 乖离因子用49日手算数据验证完整25日均线和最近25点 OLS 斜率。
- 斜率因子验证归一化、含截距斜率、R² 和 `×10000`。
- 常数价格验证斜率因子精确为0。
- 效率因子验证单调路径 `ER=1`、往返路径 `ER<1`、平坦路径得分0。
- 非正、NaN、Infinity、观测不足返回明确无效状态。
- 构造拆分前后价格和复权因子，证明拆分不产生虚假动量。

### 17.2 横截面评分测试

- 三项因子仅在同一完整样本集合上标准化。
- Z-Score 使用 `ddof=0`。
- 常数因子列贡献全部为0。
- `0.3/0.3/0.4` 加权值与手算一致。
- 完全并列按代码升序。
- 少于2只完整候选不产生排名。

### 17.3 阈值和状态测试

- 首次有效 Top-1 满仓。
- 旧目标仍为 Top-1 时 HOLD。
- 新分数严格大于 `1.5×` 时切换。
- 新分数恰好等于 `1.5×` 时不切换。
- 正分、零分、负分三类阈值行为按字面公式执行。
- 旧目标失效时绕过阈值强制切换。
- 候选不足时合法旧目标 HOLD，非法旧目标转现金。
- 不读取实际持仓、订单或成交状态。

### 17.4 聚类、因果和回归测试

- 日频 scheduled dates 覆盖所有评价交易日。
- 重聚类只在初次和冻结26周边界发生。
- 非重聚类日锁定代表不因 ADV 排名漂移而变更；硬失效替换正确。
- 修改 signal date 之后的数据不改变该日及以前任何决策。
- 未声明字段、越过 lookback 或越过 signal date 的访问在数据返回前失败。
- 所有目标权重与现金严格守恒，无负权重、无杠杆。
- R34、R39、现有代表策略 focused tests 原样通过。
- catalog 与 API 精确列表保留全部旧 ID，仅追加 R57。

## 18. 研究验证协议

### 18.1 忠实度门禁

回测前必须先通过：

1. 三因子公式与原文一致；唯一明确适配是复权和动态代表候选池。
2. 权重固定 `0.3/0.3/0.4`，阈值固定 `1.5`，窗口固定25日（乖离计算读取49日）。
3. Top-1、100%目标、日频和严格大于阈值语义已由测试证明。
4. QMT 限价、日志和参数遍历没有被混入策略插件。
5. 无未来数据、同收盘成交或 confirmation 推断。

任一不通过即停止，不运行正式 paired backtest。

### 18.2 Champion–Challenger

实现、测试及独立审查无 P0/P1 后，将 R39 与 R57 放入同一个 `StrategyBatchRequest`：

- `schema_version="1"`
- `mode="RESEARCH_ONLY"`
- 完整研究选择区间 `20170707..20220729`
- 相同 snapshot、PIT Universe、calendar、benchmark 和随机种子
- 相同公共执行合同：初始资金100万元、佣金0.025%、最低5元、ADV20/最少10日、最大参与率5%、滑点5–30bps、整手100份
- exactly two variants：R39 Champion 与 R57 Challenger

三折沿用冻结区间：

| Fold | Validation |
|---|---|
| fold_01 | `20190802..20200731` |
| fold_02 | `20200807..20210730` |
| fold_03 | `20210730..20220729` |

R57 只有同时满足以下条件才晋级 research Champion：

1. 聚合 Validation Sharpe 严格高于 R39。
2. 聚合年化收益不低于 R39。
3. 最大回撤恶化不超过1个百分点。
4. 三折 Sharpe 至少赢2/3。
5. snapshot/framework/execution/comparison identity、quality、partial、publishable、reconciliation 和可比性门禁全部通过。

共享 `RESEARCH_ONLY_UNVERIFIED_UNIVERSE` 可用于研究排名，但结果继续保持 research-only。任一门禁失败即保留 R39；不得事后调整权重、阈值、窗口、负分处理或候选最小数量。

## 19. 报告要求与风险

正式结果必须同时报告：

- R39/R57 完整区间及三折的收益、Sharpe、最大回撤；
- 换手、佣金、滑点、持仓数量、现金和受阻订单；
- 每日阈值触发次数、负分阈值次数、强制切换次数；
- 每个代表基金的持有期、收益贡献和最大损失交易；
- 因候选不足转现金的日期与持续时间；
- 与文章外部业绩不能直接比较的原因。

主要残余风险：

- 原文参数来自2019年至今的遍历，存在过拟合和多重试验风险。
- 从固定四 ETF 改为动态聚类代表，改变了文章 Universe，结果只代表框架适配版。
- 横截面候选通常约8只，Z-Score 样本较小且会随代表变化。
- Top-1 满仓的集中风险显著高于 R39 Top-3。
- 负分倍率阈值可能无法有效降低震荡换手。
- 日频信号可能增加成本、滑点和容量约束影响。
- QDII ETF 的估值时差、溢价和跨市场交易时段没有被文章价格因子显式处理。

这些风险只能通过预注册的 paired validation 和后续 forward shadow 观察，不能通过同区间调参消除。

## 20. 完成定义

本设计阶段完成的条件是：

- 原文明确信息与此前二手推断完成修正；
- 日频、动态代表、复权、完整样本、负分阈值和执行边界无歧义；
- 仅定义一条 R57 复现策略，没有引入 R58/R59 或其他候选；
- 实现范围、测试、忠实度门禁和 Champion 门禁完整；
- 未修改代码、未运行回测、未改变前端默认。

后续只有在用户批准本规格后，才进入实施计划编写；实施和回测均需单独授权。
