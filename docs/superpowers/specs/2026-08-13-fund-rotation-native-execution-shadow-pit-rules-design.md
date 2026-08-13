# 基金轮动原生执行、Shadow 生产适配器与 PIT 规则源设计

## 1. 目标

修复三个架构缺口：

1. Runner 的策略成交事实必须由正式 v2 执行引擎产生，不能继续把 legacy loop 的 `PipelineResult` 转换成 v2 ledger。
2. Shadow 生产路径必须复用正式策略会话、正式执行引擎和正式 accounting 合同，不能依赖预计算信号或 deterministic adapter。
3. `MarketRuleResolver` 必须从真实的 PIT 历史规则源解析交易规则，不能使用进程内静态类别表兜底。

本设计只覆盖基金轮动主路径；legacy 结果、旧版 benchmark 和迁移期 parity 报告可以保留，但不能成为正式成交或正式 Shadow account 的事实来源。

## 2. 基本事实与不可变约束

### 2.1 基本事实

- 当前 Runner 调用 `build_execution_context` 与 `run_execution_loop`，再由
  `build_execution_ledger_from_pipeline_result` 推导 v2 ledger。
- `PortfolioExecutor` 已经实现部分卖出优先、买入现金缩放、交易规则检查，但返回的是旧式
  `RebalanceResult`，不是 v2 的 parent/attempt/trade/corporate-action 事实链。
- 策略目录已有 `FundRotationStrategy.create_session(...).evaluate(...)` 正式策略会话接口。
- Shadow 当前的默认 provider 是 `StoreScheduledSignalProvider`，只读取预计算
  `ScheduledSignal`；执行和 accounting adapter 由调用方注入，生产 wiring 尚未绑定正式实现。
- PIT Universe 设计允许交易属性与基金主数据同表，也允许独立 PIT 规则表，要求同时满足事实有效时间和知识时间条件。

### 2.2 不可变约束

- 决策阶段不得读取执行日或未来价格；执行阶段只能消费已经 sealed 的 target。
- 正式执行、Shadow 执行和 forward validation 必须共享同一套策略、规则、成本、ledger 和 accounting 语义。
- 每条 parent order、attempt、trade 和 corporate action 必须能通过稳定 ID 互相追溯；数量守恒和会计事件顺序必须由统一合同校验。
- PIT 查询必须显式传入 `trade_date`、`knowledge_cutoff`、`snapshot_version` 和查询模式；规则缺失、冲突或知识时间不可证实必须 fail-closed。
- legacy loop 可以作为迁移比较工具，但不得被新 Runner、生产 Shadow 或 v2 diagnostics 隐式调用。

## 3. 推荐方案

采用三阶段渐进迁移，每一阶段都保留可测试的边界。

### 阶段 A：Runner 切换到原生 v2 执行引擎

新增正式的 `FundRotationExecutionEngine`（名称可按现有模块约定调整），输入为：

- sealed strategy decision/target；
- 前一日连续账户状态；
- 执行日可用行情与复权事件；
- PIT instrument/rule resolver；
- execution cost/capacity policy；
- `trade_date`、`knowledge_cutoff`、`snapshot_version` 和 run identity。

引擎直接输出：

- `ExecutionLedger` 的 parent orders、attempts、trades、corporate actions；
- 连续账户状态和每日 accounting event；
- execution diagnostics v2 所需的事实摘要。

`PortfolioExecutor` 只作为新的执行引擎内部撮合内核，输出必须经过显式映射和完整合同校验。Runner 直接调用该引擎，不再调用 `run_execution_loop`，也不再从 `PipelineResult` 反向构造正式 ledger。

迁移期可同时运行 legacy parity helper，但其输出只能放在 `legacy_result`/`parity_report`，不得覆盖 v2 ledger、account NAV 或正式 metrics。

执行引擎必须显式处理：

- target 到 parent order 的数量基准；
- residual order 的跨日延续与替换链；
- 复权导致的 parent cancel/replacement 和 corporate action 事实；
- 按 PIT rule 的 lot、tick、settlement、price limit 与 short restriction；
- 成交数量、未成交数量、现金、费用和 NAV 守恒。

### 阶段 B：Shadow 接入生产适配器

新增生产级 adapter bundle：

1. `ProductionFrozenStrategyDecisionProvider`：从冻结策略版本、catalog binding、snapshot 和 PIT data view 创建正式策略 session，调用 `evaluate` 生成并 sealed target；不读取未来执行价格。
2. `ProductionShadowExecutionAdapter`：把 Shadow order 转成正式执行请求，调用阶段 A 的 `FundRotationExecutionEngine`，再将正式 attempts/trades 映射为 Shadow 输出；映射必须保留原始 ledger ID。
3. `ProductionShadowAccountingAdapter`：消费正式执行结果或等价的正式 fill facts，按 `DAILY_ACCOUNTING_EVENT_ORDER` 更新连续 `ShadowAccountState`，不得自行实现另一套 NAV 公式。

生产构造函数必须显式注入这三个边界及 identity validator。缺少正式 provider、execution adapter、accounting adapter、策略 identity 或规则 identity 时，服务应返回结构化 fail-closed 结果，不得生成伪造 fill、伪造 account state 或降级到 store 预计算信号。

`StoreScheduledSignalProvider`、deterministic execution adapter 和 deterministic accounting adapter 继续用于单元测试和研究 fixture，但不作为生产默认值。

### 阶段 C：MarketRuleResolver 接入真实 PIT 规则源

定义显式 `PITMarketRuleSource` 接口，至少支持：

```python
resolve(
    *,
    ts_code: str,
    instrument_type: str,
    trade_date: str,
    knowledge_cutoff: str,
    snapshot_version: int,
    mode: PITQueryMode,
) -> MarketRuleRecord
```

`MarketRuleRecord` 至少包含：

- `settlement`；
- `price_limit_rule` 或规范化后的 `price_limit_pct`；
- `lot_size`；
- `tick_size`；
- `short_allowed`；
- `currency`；
- `valid_from`、`valid_to`、`known_from`、`revision_id`、`source_record_id`；
- `rule_version` 或等价 source fingerprint。

解析条件为：

```text
valid_from <= trade_date < valid_to
known_from <= knowledge_cutoff
snapshot_version 匹配固定快照
```

同一 instrument 在查询时点存在多条候选记录时，必须依据可验证的 revision order 选择唯一版本；无法唯一选择时抛出 PIT invalid/ambiguous 错误。没有规则记录时抛出 `UNKNOWN_EXECUTION_RULE`。禁止再回退到 `_RULES` 静态表；测试可以显式传入 in-memory PIT source，但不能让 Resolver 内部隐式创建静态默认 source。

解析后的 `MarketRules` 必须携带查询时间、知识时间、快照版本、source record identity 和 rule version，供 ledger、diagnostics、run manifest 和 OOS identity 使用。

## 4. 模块边界与数据流

```text
Frozen strategy session
        |
        v
Sealed target / decision
        |
        v
FundRotationExecutionEngine <--- PITMarketRuleSource / cost / capacity
        |
        +--> ExecutionLedger v2
        +--> ContinuousAccountState
        +--> DailyAccountingEvent
        |
        +--> Runner diagnostics
        +--> Production Shadow adapters
```

legacy pipeline 只允许作为并行 parity 输入：

```text
legacy loop --> parity report only
```

它不能回流到正式 ledger、Shadow state 或正式净值。

## 5. 测试与验收

### 阶段 A

- Runner 主路径不调用 `run_execution_loop`；测试使用 mock/spy 证明调用的是原生 v2 engine。
- 原生 engine 能产生 parent → attempt → trade 的完整引用链。
- residual、corporate action、lot/tick、成交数量和现金/NAV 守恒测试通过。
- legacy parity 差异只出现在 parity report，不改变 v2 结果。

### 阶段 B

- 生产 Shadow provider 调用正式 strategy session，而不是 `store.next_signal`。
- Shadow execution/accounting adapter 调用阶段 A 的正式组件。
- 缺 adapter、identity、规则或 account continuity 时 fail-closed，且不持久化伪造结果。
- 同一 sealed decision 在 backtest 与 Shadow 的 strategy/ledger/accounting identity 一致。

### 阶段 C

- 测试 valid time、knowledge time、snapshot、revision tie-break、缺失和重叠规则。
- `AS_WAS_KNOWN` 与 `LATEST_RESTATED` 的选择差异可复现。
- Resolver 不存在静态 `_RULES` fallback；未知规则必须报错。
- rule source fingerprint 进入 run identity，快照固定后新增记录不能改变结果。

### 总体验收

- 所有现有测试通过，并新增针对三项缺口的回归测试。
- `git diff --check` 通过。
- 独立子 agent 依据本设计文档 review 改动；发现不符合项时继续迭代，直到 review 与测试同时通过。

## 6. 迁移与回滚

- 每一阶段使用独立 feature flag 或显式构造参数选择新路径，默认主路径在阶段完成后才切换。
- 迁移期间保留 legacy parity 输出，便于定位行为差异；禁止静默 fallback。
- 若新引擎无法生成完整 v2 ledger，运行失败并保留诊断，不回退到 legacy 作为正式结果。
- 回滚只允许切换到上一阶段已验证的显式路径，不允许恢复隐式混合模式。
