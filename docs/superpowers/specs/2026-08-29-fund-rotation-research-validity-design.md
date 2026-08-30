# 基金轮动研究有效性路线设计

> **契约增补说明：** U1 的可选 identity/PIT 字段及 research-only 语义由 `docs/superpowers/specs/2026-08-30-research-permissive-u1-design.md` 增补并取代本文件中的旧版冲突表述。当前执行以该增补为准；本文件仍作为 Batch 0–6 与 Shadow A 的总体路线记录。

**目标：** 按研究有效性优先的顺序，修复证据口径、建立 PIT 基金池和资产身份层、补齐容量感知执行，再以独立策略 ID 完成 R40 前瞻影子验证、单变量趋势实验、机制归因、风险层和幸存机制组合。

## 1. 范围与不可变约束

本设计覆盖 Batch 0–6 以及 Shadow A。历史区间可以用于探索、测试、压力分析和配对回测，但旧选择区间与 `1a8eb8560998` 全区间均视为已观察数据，不能重新命名为 OOS。R40 Shadow A 从冻结后的首个可用交易日开始真实累计；当前历史产物不能替代未来观察周数。

每个策略 ID 只对应一个机制变化。R39 作为控制组保持不变；所有 challenger 使用独立策略 ID。每个实验预先冻结假设、失败条件、主指标、三折范围、正常成本与 2× 成本、T+1/T+2 延迟、参数邻域和 fold contribution 规则。禁止开展文档明确停止的 R45–R56、R58/R59 重跑、R67 扩展、R69/R70 网格、R66/direct-correlation 收益优化、HMM、ML、宏观信号和大规模参数搜索。

本路线同时遵守最小改动原则：优先复用现有执行 v2、PIT resolver、fund-rotation Runner、strategy contract、registry、Shadow 生命周期和报告产物；只新增满足本设计所需的纯函数、数据结构、独立策略目录、测试和实验脚本。不得以“顺便重构”为理由改动无关模块、统一格式、重写公共 Runner、修改 R39 语义或另造平行账本。若现有接口已经满足要求，只补测试、适配层和证据产物；若必须改变共享接口，先以兼容方式扩展并为旧行为保留回归测试。

## 2. 第一性原理与系统边界

研究结论必须依次回答四个基本事实：

1. 决策日是否已经知道该数据；
2. 候选是否代表独立底层资产；
3. 目标是否能够按当时容量和市场规则成交；
4. 指标和收益是否能从不可变事件重算并归因。

对应的系统边界为：

```text
PIT 数据与身份
    → U0/U1 快照
    → 策略信号与目标
    → 代表基金容量选择
    → Parent/Attempt/Trade Ledger
    → v2 执行诊断
    → summary/报告/比较器
    → 资格与晋级判断
```

所有时间敏感字段必须区分 `event_time`、`available_time`、`knowledge_cutoff` 和 `revision_policy`。无法证明决策时可用的数据必须 fail-closed，不能以零值或当前存续状态填补。

## 3. 组件设计

### 3.1 指标合同与证据链

Batch 0 将执行诊断 v2 设为正式 summary 来源。换手率固定使用：

```text
gross_traded_notional_ratio = (buy_notional + sell_notional) / average_portfolio_nav
one_way_turnover = 0.5 * (buy_notional + sell_notional) / average_portfolio_nav
annualized_one_way_turnover = one_way_turnover * annualization_factor_from_actual_evaluation_days
```

取消率、阻塞率来自 attempt/执行诊断；佣金、显式费用、滑点机会成本分别报告。缺失值为 `unavailable` 或 `partial`，禁止默认填零。summary、报告和策略比较器只能消费带 `metric_contract_version` 的正式指标，并保留 legacy 对照，历史错误 summary 不能与 v2 直接排名。

Batch 0 重新生成运行 `1a8eb8560998` 的 summary，只允许改变汇总/报告产物；订单、持仓、净值及其校验值必须保持不变。

实现上 Batch 0 只修改 summary 生成/校验链和对应测试；不重跑策略引擎，不修改原始运行目录。已有 `execution_ledger_v2.py` 和 Runner 的正式诊断入口优先直接复用。

### 3.2 PIT Master、U0 和 U1

`PITMaster` 是按基金代码和有效时间版本化的身份事实表，至少覆盖：

```text
fund_code
underlying_index
asset_class
region
currency
leveraged_or_inverse
share_class_or_feeder_relationship
listed_at / delisted_at
tradable_status
price_volume_availability
event_time / available_time / revision_policy
```

U0 只筛选决策日已上市、可交易、历史价格和成交量可用的基金；U1 的语义仍然是 U0 的身份层，但当前最小实现明确采用“U1 由 U0 派生、结果集合相同”：每个调仓日先冻结 U0，再从该 U0 做身份校验并原样派生 U1。`u1_equals_u0=true` 仅表示本次快照的 U1 成员代码集合与 U0 相同，不取消 U1 层，也不跳过身份校验。可选身份/PIT 字段缺失、部分缺失或冲突时，U1 保持相同集合并标记 research-only，同时禁止晋级和部署；核心行情、快照完整性、日期和可交易性错误仍 fail-closed。代表基金只能使用决策日前已知的成交额、上市时长、跟踪误差和历史可靠费率选择。每个调仓日写入 membership reason、identity hash、snapshot fingerprint 和 U0/U1 集合相等性证据，U0/U1 不可覆盖。

U0/U1 先扩展现有 `pit_universe.py`、`universe.py` 和 snapshot/manifest 接口，不复制一套新的 universe 解析器；只有现有字段无法表达身份版本时才增加兼容字段。实现只增加 U0→U1 的显式派生适配和集合相等性证据，复用现有身份键、哈希和核心 fail-closed 规则；不修改聚类算法，聚类继续只消费 U1。

### 3.3 容量感知代表基金回退

新增独立 challenger，继承 R39 的动量、carry、仓位、聚类和执行规则，只改变代表基金生命周期：

```text
原代表仍满足信号
    → capacity = ADV * max_participation * execution_horizon
    → 足够：carry
    → 不足：按同簇/同身份候选的决策日可得容量顺序回退
    → 无可交易候选：降低目标仓位或持有现金
```

容量判断必须考虑最小交易单位、停牌、涨跌停、零成交量、最低佣金、买卖两侧可行性和 residual。连续多个调仓日使用确定性 tie-break，避免代表基金之间来回切换。必须记录 blocked attempts、capacity-zero、fallback、fill ratio、机会成本和目标偏离持续天数。

容量回退实现为独立选择器/策略目录和 focused tests，尽量通过现有策略 session 的 overlay 接入；不把容量判断散落到公共 Runner，也不改写 R39。

### 3.4 R40 Shadow

冻结现有 R40 单基金 50% 上限，不调整成 45% 或 40%。FrozenStrategyVersion 固定实现、框架、配置、数据、执行、会计和资格策略哈希。Shadow Decision 在未来执行价格出现前封存信号和目标；Shadow Execution 只能在预期执行日市场数据到达后生成 attempts/fills，使用不同幂等键。

Shadow 维护连续账户和双净值 `shadow_ideal_nav`、`shadow_executable_nav`，保存每期决策、目标、订单、尝试、成交、持仓、净值、数据延迟和漂移事件。申请决策资格前至少满足 26 周和 6 次完整调仓；用户文档要求的 104 周观察作为建议最低观察长度，在真实数据不足时只能标记 `INSUFFICIENT_FORWARD_EVIDENCE`，不能提前晋级。

Shadow 只补齐现有生命周期中缺失的冻结/事件/资格证据，不拆分微服务，不另写简化执行账本；能够复用的账户、会计和执行代码保持原样。

### 3.5 单变量趋势实验

三轮实验均从冻结 U1 和 R39 控制组开始，依次运行且不得提前组合：

- 3A：只增加 `R126d > 0` 绝对动量门；失败后只进入现金。
- 3B：只把排名替换为 `rank(R60) + rank(R120) + rank(R240)` 等权聚合，不加入 20 日。
- 3C：只把排名替换为 `momentum / volatility_60`，波动率缺失或接近零时 fail-closed，不改变仓位分配。

每轮在完整回测前先做覆盖率、排名相关性/翻转率、边界日、缺失窗口、T+1/T+2 因果性诊断。主比较项包括 MDD、CVaR、worst 3M、现金占比、switch count、持有期、换手和成本后收益；CAGR、Sharpe 和最大回撤作为护栏。

### 3.6 机制归因与风险层

Batch 4 在 U1 上固定三臂：M0 Momentum、M1 Momentum+Cluster、M2 Momentum+Cluster+R39 carry。只有证明聚类无边际价值，才允许增加 direct correlation 第四臂；R64/R66 只做实现等价性和失败归因。

Batch 5 只有趋势赢家稳定后才运行：先做一个固定目标波动率且不加杠杆的 exposure cap，再做现金、固定短债、防御资产相对动量三组比较。Breadth 必须按独立底层资产计票，不得按原始基金数计票。

Batch 6 只组合通过单变量门禁的机制，组合后重新做消融，证明每一层仍有边际贡献；不使用事后最优防御资产回填历史。

## 4. 执行顺序与 review gate

```text
Batch 0 → Luna review → Batch 1 → Luna review → Batch 2 → Luna review
    → 冻结/启动 Shadow A
    → 3A → review → 3B → review → 3C → review
    → Batch 4 → review → Batch 5 → review → Batch 6 → review
```

每一步必须完成：测试先行并观察红灯、最小实现、全量相关测试、实验运行、产物哈希/manifest、中文报告和独立 Luna 5.6 高推理 review。review 发现 P0/P1 时暂停推进，修复后只对受影响范围重新 review；没有明确“无 P0/P1”结论不得进入下一步。子 agent 报告不能替代主 agent 对 diff、测试和产物的独立核验。

每个任务结束时还要检查 `git diff --stat` 和变更文件清单：任何不能追溯到该 Batch 验收标准的改动都必须移除或单独说明，避免把研究有效性工作扩展成平台级重构。

## 5. 验收与失败处理

数据门、策略门和前瞻门分开判断。数据门失败时只能输出不可晋级报告，不得用收益弥补。历史实验失败仍保留其 manifest、结果和失败原因。核心数据缺失、未知规则、哈希不一致、未来字段可见、账本不平和冻结配置改变均 fail-closed；可选 identity/PIT 字段缺失或冲突只允许 research-only，并阻止晋级/部署。需要改变机制时开启新策略 ID、新实验和新版本。

最终交付必须包括：Batch 0–6 的代码、测试、运行 manifest、中文报告和晋级/停止结论；R40 Shadow 的冻结 manifest、持续账户产物、事件账本和当前观察状态；以及 requirement-by-requirement 验收矩阵。未达到真实前瞻观察长度的项目必须明确标记未完成，不能以历史回测结论冒充完成。
