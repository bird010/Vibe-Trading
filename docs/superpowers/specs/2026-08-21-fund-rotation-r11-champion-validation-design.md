# R11 基金轮动 Champion 可信度验证设计

## 1. 文档目标

本设计定义 `ai_rotation_r11_persist_geom` 的独立可信度验证闭环。目标不是继续寻找更高 Sharpe 的局部公式，也不是创建 Round 31，而是验证以下命题：

> R11 相比足够简单、可执行且可比较的基准，是否具有可重复、可解释、扣除成本后仍存在，并在多重试验修正后仍可信的超额收益。

本期最高授权结果为 `P2_RESEARCH_AUTHORIZED`，表示可以另立第二代策略研究项目；它不代表 R11 获得部署资格。部署资格仍需要不少于 104 周的预注册 forward shadow。

## 2. 背景与证据边界

30 轮 Champion–Challenger 实验已完成，最终 research Champion 为 `ai_rotation_r11_persist_geom`。冻结研究指标为：

- 年化收益：`2.8973%`；
- Sharpe：`0.5419`；
- 最大回撤：`-10.8658%`；
- 总收益：`15.5126%`；
- 换手：`4.3216`；
- 质量状态：`RESEARCH_ONLY_UNVERIFIED_UNIVERSE`。

研究选择区间为 `2017-07-07..2022-07-29`。`2022-08-01..2026-08-01` 已被消费为研究输入，不能重新标记为 untouched OOS，也不能用于本设计的参数选择、门槛拟合或机制设计。

Round 12–30 没有候选同时通过全部晋级门禁。Round 13、17、21、23、29 出现代码不同但交易路径相同或等价的结果；Round 25 的 aggregate 指标略高，但仅在 1/3 validation folds 中胜出。这说明当前主要不确定性是 edge 的真实性与稳定性，而非局部公式的表达能力。

## 3. 第一性原理审查

### 3.1 基本事实

1. R11 的核心机制是两个相邻 4 周动量窗口同时为正，并使用几何聚合排序。
2. R11 之后的 19 个候选没有稳定超越它。
3. 聚合 Sharpe 改善不等于跨时期稳定性改善。
4. 代码正确、回测完成、Universe 可信、策略存在真实 alpha 是四个不同命题。
5. 30 次适应性试验增加了偶然发现的概率。
6. 现有代码已经具备 PIT Universe、基础 benchmark、excess metrics、归因、block bootstrap、walk-forward 和批量回测底层能力，但缺少统一的 Champion 验证编排与若干统计能力。

### 3.2 不可变约束

- 不修改 R11 源码、默认参数、策略身份或历史工件。
- 不创建 Round 31 或新的 Champion–Challenger 选优轮次。
- 不根据本次参数曲面选择新的 Champion 或推荐参数。
- 不改变公共 Runner、数据、执行、估值、成本、PIT 或 promotion 契约。
- 不使用 consumed confirmation interval 进行设计、选择或阈值拟合。
- 不删除失败、不可比或统计不显著的结果。
- 不把一天、数周或不足 104 周的 forward shadow 解释为部署证据。

### 3.3 重新推导的研究路径

```text
历史 Universe 可信
        ↓
R11 相对简单基准存在经济价值
        ↓
收益来源可解释
        ↓
参数、执行、市场状态和收益来源稳定
        ↓
多重试验修正后仍有统计证据
        ↓
才允许研究第二代策略
```

## 4. 方案选择

### 4.1 备选方案

1. 单次分析脚本：开发最快，但口径容易漂移，恢复和审计能力不足。
2. 独立 Champion Validation 编排层：复用现有领域模块，新增冻结契约、诊断组件、阶段门禁和证据工件。
3. 完整 API/UI 研究平台：长期体验较好，但超出当前验证 edge 的必要范围。

### 4.2 采用方案

采用方案 2。新能力位于独立的 `champion_validation` 子域中，不注册诊断变体，不修改现有策略目录，不扩展 Round 体系。

## 5. 实验定义与结论状态

冻结实验定义：

```text
experiment_type = CHAMPION_VALIDATION
subject_strategy = ai_rotation_r11_persist_geom
candidate_selection_enabled = false
promotion_enabled = false
subject_status = FROZEN_RESEARCH_CANDIDATE
multiple_testing_trial_count = 30
```

阶段结论使用三态：

- `PASS`：证据足以进入下一层；
- `INCONCLUSIVE`：点估计可能有价值，但数据或统计功效不足；
- `FAIL`：发现明确结构性问题。

最终只允许三个业务动作：

- `STOP_CURRENT_ARCHITECTURE`；
- `FORWARD_SHADOW_ONLY`；
- `P2_RESEARCH_AUTHORIZED`。

默认动作是 `FORWARD_SHADOW_ONLY`。只有所有历史验证层均为 `PASS`，才允许输出 `P2_RESEARCH_AUTHORIZED`。

## 6. 冻结契约

实验开始前冻结：

- R11 策略源码、配置、依赖与身份哈希；
- 数据快照、历史 Universe 规则与交易日历；
- 研究区间和 consumed confirmation interval；
- 执行、成本、容量、手数和估值规则；
- benchmark 定义；
- A–E 消融矩阵；
- 45 点参数稳定性网格；
- 压力场景；
- regime 分类方法；
- 统计方法、随机种子、trial count 和门槛。

任何冻结身份不匹配均产生 `BLOCKED_IDENTITY_DRIFT`，不得静默接受新哈希。

## 7. 工作流一：Universe Assurance

### 7.1 验证范围

- 上市、退市、清盘、合并和更名历史；
- 历史基金类型、资产类别和跟踪指数；
- `valid_from`、`valid_to`、`known_from` 和 revision chain；
- 当期可交易状态、停牌、行情完整性和复权状态；
- 历史规模与流动性；
- 当前存续 ETF 是否被错误投射到过去；
- 两个独立来源的代码覆盖、状态、日期与分类对账。

### 7.2 PASS 门槛

| 检查项 | PASS 条件 |
|---|---:|
| 未解决跨源冲突 | 0 |
| 重叠有效期 | 0 |
| 无法排序的 revision | 0 |
| 上市前被纳入 | 0 |
| 退市后被纳入 | 0 |
| 入选记录缺失 knowledge time | 0 |
| 当期不可交易却被选中 | 0 |
| 决策与 Universe 快照不一致 | 0 |
| 代码层质量状态 | `PITQualityStatus.VERIFIED` |

报告层可以显示 `POINT_IN_TIME_UNIVERSE_VERIFIED`，但内部沿用现有枚举，避免创造第二套质量语义。

若历史数据不能证明完整性，输出 `INCONCLUSIVE_UNIVERSE` 和数据缺口，不得通过删除问题 ETF 或放宽门槛获得 PASS。

## 8. 工作流二：Benchmark 与经济价值

### 8.1 预注册基准

| ID | 基准 | 研究问题 |
|---|---|---|
| B0 | 100% Cash | 复杂度和交易是否值得 |
| B1 | 动态 PIT Universe 可执行等权 | 聚类、择时和现金管理是否创造价值 |
| B2 | `510300.SH` 买入持有 | R11 是否只是低 Beta 大盘暴露 |
| B3 | 非聚类 Top-3、4W Momentum | clustering 是否提供增益 |
| B4 | 非聚类双窗口正动量、按 M0 排序 | persistence filter 是否解释主要收益 |
| B5 | 动态 PIT Universe 13W inverse-vol | Sharpe 是否主要来自低风险配置 |

B3 是主要策略基准。B0、B1 用于判断绝对经济价值，B2、B5 用于解释风险来源。

基准行为冻结如下：

- B0 在整个评价区间保持 100% 现金，不产生人为利息；
- B1 每个周度决策点对当期 PIT eligible Universe 等权，并通过公共执行器成交；
- B2 在共同可执行区间首次可成交日买入 `510300.SH` 后持有，仅应用首次建仓和公共执行成本；若该标的缺少共同区间或 PIT 可交易证据，本层返回 benchmark 不可比，不另选替代标的；
- B3 对当期非聚类 PIT Universe 计算已完成的 4 周动量，仅保留 `M0 > 0` 的标的，取 Top-3，每个有效槽位固定目标权重 1/3，空槽持有现金；
- B4 在 B3 基础上增加 `M1 > 0` 资格条件，仍按 M0 排序，其余行为不变；
- B5 对当期 PIT Universe 使用决策日前 13 个完整周的 realized volatility，要求 13 个有限观测，按 inverse-vol 归一化权重并周度再平衡；零波动、非有限值或样本不足的标的不得参与权重归一化。

### 8.2 可比性

执行型基准必须使用相同 PIT Universe、数据快照、评价日历、成本、滑点、容量和手数规则。理论等权指数必须标记为 `THEORETICAL_NO_COST`，不得与可执行策略混为一谈。

### 8.3 指标

- CAGR、Sharpe、MDD、Turnover；
- Annualized Excess Return；
- Tracking Error、Information Ratio；
- Alpha、Beta；
- Upside Capture、Downside Capture；
- Average Cash Exposure；
- Cost Drag。

### 8.4 经济价值 PASS 门槛

- R11 对 B0、B1、B3 的年化超额收益均大于 0；
- R11 相对 B3 的 Information Ratio 大于 0；
- 成本后 Alpha 大于 0；
- 相对 B3 的最大回撤恶化不超过 1 个百分点；
- 所有核心比较通过身份、Universe、执行与工件完整性门禁。

## 9. 工作流三：R11 Mechanism Ablation

诊断变体不注册为策略、不占 Round 编号、不允许 promotion：

| Variant | 资格条件 | 排序方式 |
|---|---|---|
| A | 无 | M0 |
| B | M0 > 0 | M0 |
| C | M0 > 0 且 M1 > 0 | M0 |
| D | M0 > 0 且 M1 > 0 | (M0 + M1) / 2 |
| E | M0 > 0 且 M1 > 0 | 几何动量 |
| R11 | 完整冻结实现 | 几何动量 |

变体只通过验证控制器调用，并统一进入公共目标、执行、估值与成本链路。E 是 R11 的一致性对照：E 与 R11 的目标、交易或净值出现超出数值容差的差异时，实验无效。

输出：

- `eligibility_difference_ratio`；
- `ranking_difference_ratio`；
- `selection_difference_ratio`；
- `weight_difference_ratio`；
- `trade_difference_ratio`；
- `cash_difference_ratio`；
- `incremental_excess_return`；
- `incremental_turnover`。

若 `selection_difference_ratio < 1%` 且 `trade_difference_ratio < 1%`，标记 `BEHAVIORALLY_EQUIVALENT`。本工作流只解释过滤、排序、聚类与现金的边际贡献，不选最优变体。

本层 `PASS` 表示机制证据可解释，而不是某一诊断变体获胜。PASS 必须同时满足：E 与 R11 parity 通过；A–E 每个相邻阶段只有一个声明差异；目标、交易和账户归因能够在固定数值容差内对账；所有行为等价阶段均被明确标记。任何 parity 或对账失败使本层为 `FAIL`；样本中所有阶段均行为等价时，本层为 `INCONCLUSIVE`。

## 10. 工作流四：参数稳定性曲面

预注册网格：

```text
momentum_window = [3, 4, 6, 8, 12]
top_n = [2, 3, 4]
recluster_weeks = [13, 26, 52]
```

总计 45 个诊断点。R11 的 `4 / 3 / 26` 仅作为参考中心。

每个点输出相对 B3 的超额收益、Information Ratio、Sharpe、MDD、换手、现金比例、逐 fold 结果和行为差异。

PASS 门槛：

- 至少 60% 的完整网格取得正年化超额收益；
- R11 直接邻域中至少 60% 的组合取得正年化超额收益；
- 邻域 Sharpe 中位数不低于 R11 的 80%；
- 不存在只有中心点为正、相邻点普遍失效的参数孤岛；
- 所有 45 点均有可审计终态，任一技术失败会阻止本层 PASS。

禁止缩短或重叠切分 validation fold 来制造样本量，也禁止输出 winner 或 recommended parameter。

## 11. 工作流五：压力测试

采用 one-factor-at-a-time：

| 类型 | 场景 |
|---|---|
| 基础滑点 | 5、10、20、30、50 bps |
| 成交延迟 | next open、next close、额外延迟 1 个交易日 |
| ADV participation | 1%、2%、5% |
| 费用 | 基线、双倍佣金、最低佣金敏感场景 |
| 可交易性 | 停牌、涨跌停、缺失 ADV 的严格拒单路径 |

PASS 门槛：

- 20 bps 滑点下相对 B3 的年化超额收益仍大于 0；
- 延迟 1 个交易日后相对 B3 的年化超额收益仍大于 0；
- 1% ADV participation 下无执行质量失败且超额收益大于 0；
- 输出 break-even transaction cost，但不据此调参。

## 12. 工作流六：Regime 与集中度归因

Regime 只用于事后解释，不能驱动本次交易。特征仅使用决策时点可知数据；阈值由各 fold 的 Train 部分拟合并在 Validation 冻结。

- Bull/Bear：PIT 等权基准的滞后 26 周收益方向；
- High/Low Vol：滞后 13 周波动率相对 Train 分布；
- Trend/Range：滞后趋势强度相对 Train 分布；
- High/Low Correlation：滞后 Universe 平均相关性相对 Train 分布。

输出各 regime 的 CAGR、Sharpe、MDD、Win Rate、Excess Return、Exposure、Cash Ratio 和 PnL Contribution。

集中度覆盖单 ETF、Top 5 ETF、Top 10 trades、年度、fold 和 cluster，并输出 PnL HHI 与有效收益来源数量。

以下任一条件成立，本层最高为 `INCONCLUSIVE`：

- 任一 ETF、年度、fold 或 cluster 贡献超过总正收益的 50%；
- 超额收益仅存在于一个市场状态；
- 超过一半预注册 regime 的超额收益为负；
- 低波动主要由长期现金暴露解释，且成本后 Alpha 不显著。

## 13. 工作流七：统计可信度

现有 block bootstrap 扩展为：

- 预注册 10,000 次抽样；
- 固定随机种子；
- 输出 CAGR、Sharpe、MDD 和相对 B3 超额收益的 95% CI；
- 保存抽样指标或足以完全复算的压缩工件；
- 增加 Deflated Sharpe Ratio；
- trial count 固定为 30；
- 对具有有效收益序列的候选执行 White Reality Check 或 Hansen SPA；
- 失败或未实现轮次仍计入 trial count，但不伪造收益序列。

统计状态：

| 状态 | 条件 |
|---|---|
| PASS | 超额收益 95% CI 下界 > 0、DSR 概率 >= 0.95、SPA/Reality Check p <= 0.10 |
| INCONCLUSIVE | 点估计为正，但一个或多个统计门槛未通过 |
| FAIL | 点估计非正，或修正后证据明确不支持 edge |

## 14. 组件边界

| 组件 | 职责 |
|---|---|
| Validation Contract | 冻结研究对象、数据、矩阵和门槛 |
| Validation Controller | 按阶段运行、恢复、停止和汇总 |
| Universe Assurance | PIT、生命周期与跨源审计 |
| Benchmark Suite | 构造 B0–B5 并检查可比性 |
| Diagnostic Variants | 生成 A–E 目标，不进入注册表 |
| Behavior Comparator | 比较信号、选择、权重、交易和现金 |
| Robustness Runner | 稳定性曲面和压力测试 |
| Attribution Engine | regime 与集中度归因 |
| Statistical Validator | Bootstrap、DSR、Reality Check/SPA |
| Evidence Decision | 执行三态门禁并生成最终动作 |

建议代码结构：

```text
agent/backtest/fund_rotation/champion_validation/
  __init__.py
  contracts.py
  controller.py
  universe_assurance.py
  benchmark_suite.py
  diagnostic_variants.py
  behavior_comparison.py
  stability_surface.py
  stress_tests.py
  regime_attribution.py
  statistical_validation.py
  decision.py
  report.py

agent/scripts/run_fund_rotation_champion_validation.py
agent/tests/fund_rotation/champion_validation/
```

## 15. 工件与状态

独立实验目录：

```text
experiments/ai_fund_rotation_r11_validation_20260821/
  validation_spec.json
  frozen_subject.json
  data_contract.json
  benchmark_spec.json
  ablation_spec.json
  stability_spec.json
  stress_spec.json
  regime_spec.json
  statistics_spec.json
  validation_ledger.jsonl
  stage_state.json
  stages/00_preflight/
  stages/01_universe/
  stages/02_benchmarks/
  stages/03_ablation/
  stages/04_stability/
  stages/05_stress/
  stages/06_attribution/
  stages/07_statistics/
  stages/08_final/
```

所有结构化工件包含 `schema_version`、`experiment_id`、时间戳、输入 checksum、数据/框架/策略/执行/spec 哈希、状态和 reason codes。`validation_ledger.jsonl` 仅追加。

状态机主路径：

```text
CREATED
  -> PREFLIGHT_PASSED
  -> UNIVERSE_VERIFIED
  -> ECONOMIC_VALUE_PASSED
  -> MECHANISM_IDENTIFIED
  -> ROBUSTNESS_PASSED
  -> STATISTICAL_CREDIBILITY_PASSED
  -> P2_RESEARCH_AUTHORIZED
```

允许的旁路终态包括身份漂移、数据缺口、Universe 不确定、经济价值失败、稳健性失败或不确定、统计失败或不确定。

## 16. 执行顺序

### M1：冻结与研究安全

- 处理现有 pytest 临时目录 ACL 阻塞；
- 运行 R11、Runner、PIT、benchmark、attribution 和 batch 基线测试；
- 重建并验证 R11 冻结身份；
- 写入全部预注册规格；
- 建立 confirmation interval 访问防线、状态机与 ledger。

### M2：Universe 与 Benchmark

- 接入真实 PIT 主数据和独立来源；
- 运行 Universe Assurance；
- 仅在 Universe PASS 后运行正式 B0–B5 经济价值比较。

### M3：机制与行为

- 实现诊断 A–E；
- 先验证 E 与 R11 parity；
- 输出行为差异和机制边际贡献。

### M4：稳定性、压力与归因

- 执行完整 45 点网格；
- 执行 one-factor-at-a-time 压力测试；
- 对冻结工件执行 regime 和集中度归因。

### M5：统计与最终决策

- 执行 Bootstrap、DSR、Reality Check/SPA；
- 汇总阶段 gate；
- 输出中文报告、最终动作和 forward-shadow 交接工件。

每个里程碑均可独立验收。Universe 或经济价值失败时，控制器应停止解释性结论，不必继续消耗后续完整计算预算。

## 17. 错误、恢复与幂等

- HTTP 202、后台进程存在或 SSE 中断均不表示完成；
- 重试复用相同幂等键并轮询到终态；
- partial、corrupt、checksum mismatch 或身份不一致的结果不参与比较；
- 缺失、空样本和非有限指标不得默认填 0；
- 恢复运行前重新验证输入哈希；
- 单个网格点技术失败可记录后继续其他点，但本层不能 PASS；
- 统计功效不足返回 `INCONCLUSIVE`，不伪装成技术成功或 FAIL；
- Universe 数据不足返回数据缺口，不通过降低门槛恢复。

## 18. 测试设计

### 18.1 单元测试

- 契约冻结、哈希和 consumed interval 防线；
- 各门禁边界值；
- 行为差异比例；
- 45 点网格完整性；
- regime 因果时序；
- Bootstrap 可重复性；
- DSR/SPA 数值稳定性；
- 三态状态传播。

### 18.2 性质测试

- 修改未来数据不影响过去 Universe、signal 或 regime；
- 调换 ETF 输入顺序不改变确定性结果；
- 成本增加时净收益不得因计算错误上升；
- 延迟增加不能使用更早成交价格；
- 相同目标路径的行为差异为零；
- 报告和工件 schema 不允许 winner 或 recommended parameter 字段。

### 18.3 Golden 测试

冻结 Round 11 的小型可审计 fixture。诊断 E 与 R11 的选择、权重、订单、成交完全一致，净值在固定数值容差内一致。

### 18.4 集成测试

使用小型 PIT 快照覆盖全部 PASS、Universe 缺口、benchmark 不可比、行为等价、参数孤岛、统计不确定和中断后幂等恢复。

## 19. 范围

### 19.1 范围内

- R11 身份冻结和可信度验证；
- PIT Universe 端到端审计；
- B0–B5、A–E、45 点网格和预注册压力场景；
- 行为差异、regime、集中度与统计可信度；
- 三态门禁、中文报告和 forward-shadow 交接。

### 19.2 范围外

- Round 31；
- 修改 R11 或现有 promotion gate；
- 从网格重新选优；
- clustering 第二代、portfolio risk overlay、multi-horizon 策略；
- 公共 Runner、执行、估值或 PIT 语义修改；
- 大规模 UI 产品化；
- 任何部署或收益保证结论。

## 20. 总体验收标准

### 20.1 研究安全

- R11 策略目录和 Round 01–30 工件零修改；
- confirmation interval 未参与设计、选择或阈值拟合；
- trial count 固定为 30；
- 参数曲面不生成 Champion 或推荐参数。

### 20.2 可比性与机制

- 所有执行型比较共享快照、Universe、日历与执行身份；
- 理论基准明确隔离；
- E 与 R11 parity 通过；
- A–E 相邻阶段只有一个声明差异；
- 行为比较能识别既有等价候选。

### 20.3 统计与工程

- Bootstrap 保留时间相关性并可复算；
- DSR 使用样本长度、偏度、峰度和 30 次试验；
- Reality Check/SPA 使用所有有效收益序列；
- 所有新增组件具有单元、性质、golden 和集成测试；
- 工件具有完整身份与 checksum；
- 中断后可幂等恢复；
- 最终报告使用中文。

## 21. 最终决策规则

```text
Universe FAIL 或 INCONCLUSIVE
  -> 补齐数据，禁止 alpha 结论

Universe PASS + Economic FAIL
  -> STOP_CURRENT_ARCHITECTURE

Economic PASS + Robustness/Statistics INCONCLUSIVE
  -> FORWARD_SHADOW_ONLY

任一明确 Robustness/Statistics FAIL
  -> STOP_CURRENT_ARCHITECTURE

全部历史验证层 PASS
  -> P2_RESEARCH_AUTHORIZED
```

软件实现完成与研究验证通过是两个不同验收层。软件只需可靠地产生可审计终态，不以让 R11 获得 PASS 为成功条件。

## 22. Forward Shadow 边界

冻结候选从 `2026-08-20` 起接受不少于 104 周的预注册 forward shadow。截至 `2026-08-21`，不可能形成满足时长的部署证据。当前或近期运行的最高状态仍为研究用途，不能升级为 deployment candidate。

## 23. 工作区保护

当前工作区包含 Round 05–30 策略、注册表、测试和 skill 的未提交修改。实施必须：

- 不修改或清理这些文件；
- 不覆盖现有实验账本；
- 将新增代码和工件限制在新的验证子目录；
- 实施前记录基线 `git status` 和相关哈希；
- 每次提交只包含本验证闭环的可追溯变更。
