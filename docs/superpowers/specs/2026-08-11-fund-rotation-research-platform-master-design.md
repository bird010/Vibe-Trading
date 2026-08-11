# 基金轮动可信研究平台总体修改设计

**日期：** 2026-08-11
**状态：** 已确认设计
**适用分支：** `data-layer-improve`
**目标：** 将当前“工程化的相关性聚类动量回测”升级为能够持续验证策略假设、识别过拟合、解释收益来源并开展前向验证的基金轮动研究平台。

## 1. 背景与结论

当前系统已经具备固定数据快照、策略和框架源码哈希、共同评价日历、统一执行器、批次比较、目标决策、订单、成交、持仓和策略专用审计产物。它已经能够较可靠地回答“某个策略版本在某个固定数据快照上如何运行”。

但它还不能充分回答“这个收益是否可信、是否来自真正的策略假设、能否在未知未来继续出现”。主要缺口是：

1. ETF 历史候选池只显式使用当前 `name` 和 `list_date`，尚不能证明不存在幸存者偏差和当前信息污染。
2. 当前执行统计混合了 parent order 和每日 execution attempt 的语义；另有遗留指标函数形成第二套定义。
3. 两个正式策略都属于“相关性聚类 + 短周期动量”假设族，缺少不聚类的独立 Alpha 基线。
4. Batch Variants 共用同一评价区间，没有制度化隔离 Train、Validation 和 untouched OOS。
5. Cluster Momentum 缺少成员覆盖率门禁，Top-N 缺少换手迟滞，产品选择、组合权重和总风险敞口仍较简单。
6. 已有丰富审计事实，但缺少可对账的收益归因、策略组件消融和市场状态分析。
7. 没有冻结策略、连续 Shadow Portfolio 和 Forward Test 生命周期。

因此本设计不以继续扩充 UI 为主，而以建立“可信度因果链”为主。

## 2. 第一性原理

### 2.1 基本事实

- 回测收益只有在候选池、行情、复权、规则和参数都满足当时可知时才具有因果意义。
- 在同一段历史上反复搜索并汇报同一段表现，会产生选择偏差。
- 两个共享同一核心信号的策略不能互相充当独立假设基线。
- 理论目标权重、订单、尝试、成交和公司行为是不同事实对象，统计口径不能混用。
- 历史回测即使严格，也不能替代冻结后的真实时间前向观察。

### 2.2 不可变约束

- 任一 `signal_date` 必须同时受事实生效时间与 `knowledge_cutoff` 约束，不得读取该时点之后才生效或才被系统知晓的信息。
- 正式比较必须共享数据快照、评价日历、执行契约、成本假设和 OOS fold。
- Sealed OOS 只能评估预先登记并冻结身份的候选；OOS 结果不得用于候选淘汰、选优、调参或放宽资格门禁。
- 已冻结版本不可原地修改；核心逻辑或参数变化必须生成新版本。
- 真实资金账使用实际成交价记账；滑点只作为相对参考价格的机会成本归因，不得再次从现金或 NAV 扣除。任何归因必须可以还原真实 NAV，无法对账时拒绝发布。
- 本阶段只建立研究和决策参考资格，不连接真实资金自动交易。

## 3. 可信度分层

```text
历史数据可信
    ↓
执行结果可解释
    ↓
策略假设可比较
    ↓
样本外表现成立
    ↓
收益来源可归因
    ↓
前向运行可持续
```

每一层都是下一层的前置条件。系统允许在前置层未通过时继续做探索性运行，但必须降级标识，不得把结果描述为已验证 Alpha。

## 4. 总体门禁

### 4.1 数据可信门禁

PIT 审计未通过时，结果状态为 `RESEARCH_ONLY_UNVERIFIED_UNIVERSE`。

**通俗解释：** 如果系统提前知道哪些 ETF 后来活了下来，就像考试前知道哪些答案最终会被保留。

**不修改的后果：** 幸存者偏差可能制造虚假的长期收益，后续选参、归因和 OOS 都会建立在错误样本上。

### 4.2 执行口径门禁

必须分别记录 parent order、execution attempt、executed trade 和 corporate action。禁止使用含义不明的裸 `fill_rate`、`trade_count`。

**通俗解释：** 一张订单连续尝试三天仍是一张订单、三次尝试；基金份额调整也不是交易。

**不修改的后果：** 成交率、阻塞率、交易数和成本报告会相互矛盾，无法判断策略是否真正可执行。

### 4.3 研究有效性门禁

没有独立基线和 untouched OOS 时，结果状态为 `IN_SAMPLE_RESEARCH_RESULT`。

**通俗解释：** 在同一套试卷上反复修改答案再汇报最高分，只能证明记住了试卷。

**不修改的后果：** 参数越多，越容易把偶然最优组合误认为稳定策略。

### 4.4 前向验证门禁

只有完成 OOS、参数冻结、Shadow Portfolio 最低观察期和人工审批，策略版本才可获得 `DecisionQualification.ELIGIBLE`。

**通俗解释：** 回测是看录像，Shadow Portfolio 是在未来价格出现前先提交答案。

**不修改的后果：** 新历史会不断被用于调参，所谓前向表现再次退化为样本内结果。

## 5. 分离的生命周期与资格评估

单一状态链会把数据快照、一次运行、研究实验、不可变策略版本和可暂停的 Shadow 实例混成同一个对象，因此改为分别管理：

```text
DataSnapshotQualification：UNVERIFIED / VERIFIED / DEGRADED / INVALID
BacktestRunStatus：PENDING / RUNNING / SUCCEEDED / FAILED / CANCELED
RunQualification：RESEARCH_ONLY / BACKTEST_VALID / INVALID
ResearchExperimentLifecycle：DRAFT / REGISTERED / RUNNING / COMPLETED / INVALIDATED
StrategyVersionLifecycle：CANDIDATE / FROZEN / RETIRED / INVALIDATED
ShadowDeploymentStatus：CREATED / RUNNING / SUSPENDED / COMPLETED / FAILED
DecisionQualification：INELIGIBLE / ELIGIBLE / REVOKED
```

这些状态不能互相代替。例如，`FROZEN` 描述策略版本不可变，不表示某个 Shadow deployment 正在运行；`DataSnapshotQualification.VERIFIED` 是快照资格，不是策略版本状态。

状态转换由三类不可变对象支撑：

```text
QualificationEvidence：记录快照、运行、实验、归因、Shadow 和审批等事实证据
QualificationPolicy：记录预先冻结的 hard gates、warnings、阈值和适用转换
QualificationAssessment：记录某次转换评估所用 policy、evidence、结论和原因码
```

政策或证据变化时追加新的 assessment；禁止用会随证据变化而过期的 `can_freeze=true` 一类可变布尔字段代替审计记录。

## 6. 子设计与依赖

| 顺序 | 子设计 | 核心问题 | 正式产物 |
|---|---|---|---|
| 1 | [PIT Universe 与历史基金主数据](2026-08-11-fund-rotation-pit-universe-design.md) | 当时有哪些 ETF 可选 | PIT master、覆盖报告、差异报告 |
| 2 | [执行规则与诊断统计](2026-08-11-fund-rotation-execution-diagnostics-design.md) | 想买、尝试、成交和公司行为如何区分 | Execution Ledger、v2 diagnostics |
| 3 | [独立基线与 OOS/Walk-forward](2026-08-11-fund-rotation-oos-validation-design.md) | 收益是否来自真实假设而非选参 | 实验契约、fold、OOS 证据 |
| 4 | [信号、组合与风险层](2026-08-11-fund-rotation-signal-portfolio-risk-design.md) | 选什么、买多少、何时不换、承担多少风险 | 分层决策与消融结果 |
| 5 | [收益归因与市场状态](2026-08-11-fund-rotation-attribution-design.md) | 为什么赚、为什么亏 | 可对账归因与状态分析 |
| 6 | [策略冻结与 Forward Test](2026-08-11-fund-rotation-forward-validation-design.md) | 未知未来能否持续运行 | Frozen 版本、Shadow 账本、前向证据 |

```text
Bitemporal PIT Data
→ DataSnapshotQualification
→ Versioned Market Rules + Execution Ledger
→ Reproducible Run Contract
→ ResearchExperiment（Train / Validation / Walk-forward / Sealed OOS）
→ Candidate StrategyVersion
→ Accounting / Strategy / Execution Attribution
→ QualificationAssessment（CAN_FREEZE）
→ Frozen StrategyVersion
→ Shadow Decision Seal
→ Shadow Execution + Forward Evidence
→ QualificationAssessment（CAN_GRANT_DECISION_ELIGIBILITY）
→ Human Approval
→ DecisionQualification.ELIGIBLE
```

PIT 数据建设和执行契约可以部分并行，但正式实验必须建立在已定版的数据、规则和记账契约上；新信号和组合模型不得绕过 Sealed OOS；Forward Test 只接收经过冻结 assessment 的不可变策略版本。

## 7. 共同架构约束

### 7.1 统一身份

所有正式结果绑定：

```text
strategy_implementation_hash
framework_implementation_hash
resolved_config_hash
data_snapshot_fingerprint
knowledge_cutoff
execution_contract_version
market_rule_contract_version
evaluation_calendar_hash
benchmark_policy_hash
qualification_policy_hash
research_experiment_id
```

### 7.2 统一质量状态

各子系统输出 `VALID / DEGRADED / INVALID` 及稳定 `reason_code`。总体质量取最差状态；禁止将缺失、非有限值或失败 fold 静默替换为零。

### 7.3 统一比较规则

只有共享 PIT 快照、知识截止时间、日历、成本、执行规则、fold 和基准政策的 Validation 结果才能进入候选选择排名。探索性比较与 `Validation Selection Ranking` 必须分开；Sealed OOS 只生成描述性 Evidence Table，不产生用于选优的正式榜单。

### 7.4 统一可追溯性

```text
订单
← 执行目标
← 风险缩放权重
← 原始组合权重
← 代表 ETF
← 入选 Cluster
← 原始信号
← PIT Universe
```

## 8. 分阶段交付

### 阶段 A：修复可信度基础

- 完成历史 Fund Master 审计和 PIT Resolver。
- 建立 Execution Ledger v2 与唯一统计定义。
- 产出 legacy 与新契约的差异报告。

通过后对应 run 可获得 `RunQualification.BACKTEST_VALID`，但策略版本仍不得称为已验证 Alpha。

### 阶段 B：建立研究有效性

- 增加不聚类的独立策略基线。
- 建立固定 OOS 和 rolling walk-forward。
- 保存全部尝试、参数稳定性、Validation 选择记录和 Sealed OOS 描述性证据。

通过后 ResearchExperiment 可形成合格 OOS evidence，供后续 StrategyVersion 冻结评估使用；它不直接改写数据或运行状态。

### 阶段 C：逐项增强并消融

- 增加 coverage gate、Momentum Family、Cluster hysteresis。
- 增加 ETF 质量、逆波动组合和独立风险层。
- 每项增强必须独立开关、独立产物、独立 OOS 对照。

### 阶段 D：解释和前向验证

- 建立可对账收益归因和固定组件消融链。
- 冻结策略版本并持续运行 Shadow Portfolio。
- 满足观察期并通过独立资格 assessment 和人工审批后，才能获得 `DecisionQualification.ELIGIBLE`。

## 9. 总体验收标准

- 任一历史代码都能被解释为当时合格、明确排除或数据缺陷。
- 任一正式 PIT 查询都同时指定 `signal_date`、`knowledge_cutoff` 和不可变快照；无法证明历史知识时间的区间明确降级。
- 任一执行指标都能唯一对应订单、尝试、成交或公司行为。
- 至少一个不聚类的正式策略完成相同契约下的 OOS 比较。
- Sealed OOS 数据不能进入候选选择、参数选择或资格阈值调整；被后续设计使用的区间必须记录为已消费研究输入。
- 每个策略增强都可独立关闭并拥有消融证据。
- 每日及全区间归因在数值容差内还原 NAV。
- Frozen 版本不可修改；Shadow 决策在未来价格出现前固化，执行只能在相应市场数据到达后发生。
- 未满足门禁的结果在 API、产物和 UI 中都不能被描述为正式 Alpha 或实际决策资格。

## 10. 明确不做

- 不在本轮连接真实券商自动下单。
- 不预设 Momentum Ensemble、Risk Parity 或 Regime Filter 一定提升收益。
- 不使用 UI 标签替代后端研究门禁。
- 不为了快速通过而把未知历史基金、缺失价格或失败 fold 当作零。
- 不允许新复杂策略替换简单永久基线。
