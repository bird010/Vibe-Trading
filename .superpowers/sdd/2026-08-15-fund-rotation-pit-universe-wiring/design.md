# 基金轮动 PIT Universe 接线与 Research Fallback 语义修正设计

- **状态**：Proposed
- **日期**：2026-08-15
- **目标分支**：`data-layer-improve`
- **优先级**：P0
- **影响范围**：基金轮动回测编排层 / PIT Universe 解析边界 / 审计诊断 / 测试
- **不涉及**：聚类算法、聚类质量 Gate 阈值、动量信号、代表 ETF 选择、Production PIT Master 数据建设

---

## 1. 背景

基金轮动回测已经具备 `UniverseResolver` / `pit_universe_resolver` 相关基础能力，但当前批量回测生产路径存在一个编排缺口：

`BatchService` 创建 `FundRotationBacktestRunner` 时会注入市场交易规则 resolver，却没有注入 PIT Universe resolver。因此，即使底层 Runner 已支持 PIT Universe 解析，BatchService 发起的正常批量回测仍会进入“缺少 PIT Master”的 fallback 路径。

与此同时，Runner 当前把两类不同语义的数据集合合并成一个 `fallback_universe`：

```python
fallback_universe = frozenset(
    snapshot.historical_candidate_codes or snapshot.universe_codes
)
```

但这两个集合的定义不同：

- `snapshot.historical_candidate_codes`：广义历史候选集，主要用于交给 PIT resolver 做历史时点资格判定；它不是已经验证过的策略可投资 universe。
- `snapshot.universe_codes`：当前 Research Static 数据口径下，已经经过 ETF 静态筛选的研究型 universe。

将 `historical_candidate_codes` 在 resolver 缺失时直接作为实际可投资集合，会把“待解析候选集”错误提升为“已准入 universe”，破坏 PIT 边界，也可能扩大到本不应进入基金轮动策略的基金代码。

该问题与聚类质量 Gate 导致全程现金是两个不同层次的问题。本文只处理 PIT Universe 接线和 fallback 语义，不通过放宽聚类阈值来掩盖数据边界问题。

---

## 2. 问题定义

### 2.1 P0-A：BatchService 未接入 PIT Universe Resolver

当前逻辑可抽象为：

```text
BatchService
  ├─ execution_rule_context_loader
  │    └─ market_rule_resolver
  └─ FundRotationBacktestRunner(
         market_rule_resolver=...,
         pit_universe_resolver=缺失
     )
```

因此 Runner 无法区分：

1. 系统根本没有配置 PIT 数据能力；
2. 系统有 PIT 数据能力，但编排层没有把能力传递下来。

这会导致正常批量回测产生：

- `PIT_MASTER_MISSING`
- `RESEARCH_ONLY_UNVERIFIED_UNIVERSE`

其中一部分并非数据本身缺失，而是依赖注入链路缺失。

### 2.2 P0-B：Research fallback 使用了错误的数据边界

当前一个变量 `fallback_universe` 同时承担：

1. PIT resolver 的候选输入；
2. resolver 缺失时的实际 research universe。

这两个语义必须拆开。

正确语义应为：

```python
pit_candidate_codes = frozenset(
    snapshot.historical_candidate_codes or snapshot.universe_codes
)

research_fallback_codes = frozenset(snapshot.universe_codes)
```

含义：

- **PIT resolver 存在**：resolver 可以接收更宽的 `pit_candidate_codes`，再根据历史时点的 master/version/status 等信息筛选出真正 universe。
- **PIT resolver 不存在**：Research 模式只能退回 `research_fallback_codes`，不能把原始历史候选集直接提升为合格 universe。

### 2.3 P0-C：fallback 缺少足够显式的审计语义

当系统因缺失 PIT Master 而使用 Research Static universe 时，必须明确告诉后续产物消费者：

- 本次 universe 不是 PIT verified；
- fallback 来源是什么；
- fallback 前后的规模是多少；
- 为什么 fallback。

否则“能跑完”容易被误解为“历史 universe 已经过 PIT 验证”。

---

## 3. 设计目标

本次修改目标如下：

1. **补齐依赖注入链路**：让 BatchService 能够向 Runner 传递 PIT Universe resolver。
2. **拆分候选集与 fallback 集合语义**：禁止将 `historical_candidate_codes` 在无 resolver 时直接作为 research universe。
3. **保持 Research 模式可运行**：当前没有正式 PIT Master 时，仍允许使用静态 ETF universe 回测，但必须保留未验证质量标识。
4. **增强可审计性**：产物能够明确区分 PIT resolver 结果与 Research Static fallback。
5. **保持最小影响面**：不改变策略经济逻辑，不通过此次修改改变交易信号、聚类算法或 Gate 阈值。
6. **为后续 Production PIT Master 留出清晰接口**：本次只修接线和边界，不伪装为完整 PIT 数据建设已经完成。

---

## 4. 非目标

以下内容明确不属于本次改动：

1. 不构建新的 Production PIT Fund Master 数据表。
2. 不补齐 `valid_from` / `valid_to` / `known_from` / revision / provenance 等完整历史主数据。
3. 不新增 `tracking_index` 驱动的 ETF exposure 去重。
4. 不修改 `correlation_representative` 聚类算法。
5. 不修改 `k=8`。
6. 不修改 `max_cluster_share_reject`、`min_effective_cluster_count_reject` 等 Gate 阈值。
7. 不修改动量窗口、代表 ETF 选择规则或流动性约束。
8. 不为了让回测产生交易而增加特殊 case。
9. 不把 Research Static fallback 标记成 PIT verified。

这些事项必须作为独立设计和独立验证任务处理。

---

## 5. 第一性原理约束

PIT Universe 的核心不是“某个代码今天是否是 ETF”，而是：

> 在历史决策时点，策略当时能够知道、且按当时规则允许进入投资集合的 instrument 是哪些？

因此系统必须保持三个概念分离：

```text
原始数据候选集
    ↓
PIT 时点资格解析
    ↓
策略可投资 Universe
```

Research Static fallback 只能是一条明确降级路径：

```text
当前静态 ETF universe
    ↓
历史 list_date 等最低限度约束
    ↓
RESEARCH_ONLY_UNVERIFIED_UNIVERSE
```

它不能被当作正式 PIT 结果。

---

## 6. 当前数据流

### 6.1 当前结构

```text
FundRotationBatchService
        │
        ├── load fund_daily / fund_adj / dim_fund
        │
        ├── build execution rule context
        │
        └── FundRotationBacktestRunner
                │
                ├── market_rule_resolver     ✓
                └── pit_universe_resolver    ✗

DataSnapshot
        │
        ├── historical_candidate_codes
        │      broad raw candidates
        │
        └── universe_codes
               static ETF-filtered universe

Runner
        │
        └── fallback_universe =
              historical_candidate_codes
              OR universe_codes
                    ↓
             resolver missing
                    ↓
       raw candidates become actual universe
```

### 6.2 当前结构的问题

上述结构同时违反两条边界：

- **依赖边界错误**：BatchService 没有将 PIT 能力传递给 Runner。
- **数据语义边界错误**：candidate 被当成 fallback eligible universe。

---

## 7. 目标架构

```text
FundRotationBatchService
        │
        ├── execution_rule_context_loader
        │       └── market_rule_resolver
        │
        ├── pit_universe_resolver_loader
        │       └── pit_universe_resolver
        │
        └── FundRotationBacktestRunner(
                market_rule_resolver=...,
                pit_universe_resolver=...
            )
                       │
                       ▼
DataSnapshot
        │
        ├── historical_candidate_codes
        │       ↓
        │   pit_candidate_codes
        │
        └── universe_codes
                ↓
        research_fallback_codes
                       │
                       ▼
Runner / signal_date
        │
        ├── resolver exists
        │       │
        │       └── resolve(
        │              signal_date,
        │              pit_candidate_codes
        │           )
        │              ↓
        │          PIT universe
        │
        └── resolver absent
                │
                └── research_fallback_codes
                       + PIT_MASTER_MISSING
                       + RESEARCH_ONLY_UNVERIFIED_UNIVERSE
                       + pit_verified=false
```

---

## 8. 详细设计

## 8.1 BatchService：新增 PIT resolver 注入能力

### 8.1.1 设计原则

沿用当前 execution rule 的依赖注入方式，不让 BatchService 自己承担 PIT 解析逻辑。

BatchService 只负责：

1. 获取 resolver / resolver context；
2. 将 resolver 传递给 Runner；
3. 不自行判断某只基金在历史时点是否合格。

### 8.1.2 建议接口

优先新增与现有 execution rule loader 对称的依赖，例如：

```python
pit_universe_resolver_loader: Callable[..., UniverseResolver | None] | None
```

如果当前 production adapter 更适合携带 source/version/quality 等上下文，也可以使用 context 形式：

```python
PitUniverseContext(
    resolver=...,
    source_id=...,
    quality_status=...,
)
```

但本次不为了形式统一而引入无必要的新抽象。若单一 resolver 已足够，保持最小接口即可。

### 8.1.3 Runner 构造

目标行为：

```python
runner = FundRotationBacktestRunner(
    fund_daily,
    fund_adj,
    dim_fund,
    market_rule_resolver=rule_context.resolver,
    market_rule_instruments=rule_context.instruments,
    pit_universe_resolver=pit_universe_resolver,
)
```

### 8.1.4 不允许的行为

禁止：

```python
try:
    resolver = load_pit_resolver()
except Exception:
    resolver = None
```

然后无任何诊断继续运行。

“resolver 不存在”和“resolver 配置/加载失败”不是同一个状态。

- resolver 本来未配置：可以进入明确的 Research fallback。
- resolver 已配置但加载失败：必须显式暴露错误或质量失败，不能静默降级。

---

## 8.2 Runner：拆分两类 universe 输入

### 8.2.1 新语义

Runner 初始化回测快照后生成：

```python
pit_candidate_codes = frozenset(
    snapshot.historical_candidate_codes or snapshot.universe_codes
)

research_fallback_codes = frozenset(snapshot.universe_codes)
```

不得继续使用同时表示两种含义的 `fallback_universe`。

### 8.2.2 `pit_candidate_codes`

用途仅限：

- 作为 PIT resolver 的待解析候选范围；
- 允许比最终策略 universe 更宽；
- 不能直接代表“可交易 ETF universe”。

### 8.2.3 `research_fallback_codes`

用途仅限：

- PIT resolver 不存在时的 Research Static fallback；
- 必须来源于 `snapshot.universe_codes`；
- 继续受现有 signal-date 历史 eligibility / list-date 等逻辑约束；
- 必须带未验证质量标记。

---

## 8.3 PIT Universe 解析接口

建议把当前 helper 的参数从一个歧义变量：

```python
_resolve_pit_universe_for_signal(
    resolver,
    snapshot=...,
    signal_date=...,
    fallback_universe=...,
)
```

调整为显式语义：

```python
_resolve_pit_universe_for_signal(
    resolver,
    snapshot=...,
    signal_date=...,
    pit_candidate_codes=...,
    research_fallback_codes=...,
)
```

或封装为不可混淆的输入对象。

本次更推荐显式命名参数，原因是：

- 改动量小；
- review 时语义直接可见；
- 不为两个集合额外制造 DTO；
- 能有效防止后续重构再次把 candidate/fallback 混为一谈。

---

## 8.4 分支行为定义

| PIT resolver 状态 | 输入集合 | 结果 | Quality | 是否允许静默扩大 Universe |
|---|---|---|---|---|
| 存在且正常 | `pit_candidate_codes` | resolver 解析结果 | 由 PIT evidence 决定 | 否 |
| 未配置 | `research_fallback_codes` | Research Static ETF universe | `RESEARCH_ONLY_UNVERIFIED_UNIVERSE` | 否 |
| 已配置但加载失败 | 不应使用 raw candidate fallback | 显式失败/质量异常 | INVALID 或异常 | 否 |
| resolver 执行异常 | 不应使用 raw candidate fallback | 显式失败/质量异常 | INVALID 或异常 | 否 |

关键规则：

> **任何 PIT 能力异常都不能通过扩大 universe 来“提高可用性”。**

---

## 8.5 Research fallback 质量语义

当 resolver 未配置时，保留现有核心标记：

```text
reason_code = PIT_MASTER_MISSING
quality_status = RESEARCH_ONLY_UNVERIFIED_UNIVERSE
```

并建议在 evidence/diagnostics 中至少增加以下信息：

```json
{
  "universe_source": "STATIC_ETF_SNAPSHOT",
  "pit_verified": false,
  "pit_candidate_count": 1234,
  "research_fallback_count": 987,
  "resolved_universe_count": 987,
  "reason_code": "PIT_MASTER_MISSING"
}
```

字段名可以根据现有 artifact schema 做最小适配，但语义必须保留。

### 为什么保留 Research fallback？

当前系统仍需要支持研究型回测。如果因为暂时没有正式 PIT Master 而完全禁止运行，会把“数据质量等级”错误变成“系统不可用”。

正确做法是：

- 研究可运行；
- 结果明确降级；
- 不允许进入“PIT verified”质量等级；
- 不掩盖缺失事实。

---

## 8.6 Formal / Verified 模式

如果当前代码已经存在正式 verified 模式开关，则正式模式必须 fail closed：

```text
PIT Master missing
    → no research fallback
    → INVALID / blocked
```

如果当前版本还没有清晰的运行模式枚举，本次不为此强行扩展运行模式体系，但必须保留以下契约作为后续实现约束：

> **任何未来声明为 Formal / Verified 的基金轮动回测，都不得使用 `STATIC_ETF_SNAPSHOT` 作为 PIT fallback。**

---

## 9. 异常与失败策略

### 9.1 未配置 resolver

这是一个可识别的能力缺失状态：

- 使用 `research_fallback_codes`；
- quality 降级；
- 明确 `PIT_MASTER_MISSING`；
- `pit_verified=false`。

### 9.2 resolver loader 失败

如果系统明确配置了 PIT resolver loader，而 loader 因数据损坏、schema 错误、I/O 等原因失败：

- 不允许等价处理为“未配置”；
- 不允许静默使用 fallback；
- 应抛出可定位异常，或按现有 BatchService 错误契约将 subrun 标记失败。

### 9.3 resolver 对某 signal date 无法给出有效 universe

该情况属于 PIT 数据/解析质量问题，而不是“resolver 缺失”。

不得自动改用 `historical_candidate_codes`。

是否允许降级到 Research Static 应由明确运行模式决定；在没有模式开关前，推荐 fail closed，避免把数据异常隐藏成正常研究 fallback。

---

## 10. 数据契约

### 10.1 `historical_candidate_codes`

定义：

> 具有进入 PIT resolver 解析资格的广义历史候选 instrument 集合。

保证：

- 可以包含最终不会进入 ETF 策略 universe 的 instrument；
- 不能直接视为策略 universe；
- 不能作为无 PIT resolver 时的默认 fallback。

### 10.2 `universe_codes`

当前阶段定义：

> 基于当前 Research Static dim 数据筛选得到的 ETF universe。

保证：

- 比 raw candidate 边界更窄；
- 可以作为研究型 fallback；
- 不是 formal PIT 结果；
- 必须标记 research-only / unverified。

### 10.3 PIT resolver output

定义：

> 基于 `signal_date`、PIT Fund Master、strategy universe policy 以及必要的 instrument lifecycle 信息解析出的历史时点 universe。

其质量由 PIT evidence 决定，不由代码数量、ETF 名称匹配数量或回测是否产生订单决定。

---

## 11. 审计与可观测性

本次修改后，每个 run 至少应能回答以下问题：

1. 本次使用了 PIT resolver 还是 Research fallback？
2. PIT resolver 是否真正加载成功？
3. 传入 PIT resolver 的候选数量是多少？
4. Research Static fallback 数量是多少？
5. signal date 最终 universe 数量是多少？
6. universe 是否 PIT verified？
7. 如果不是，具体原因是什么？

推荐 diagnostics：

```text
pit_resolver_configured
pit_resolver_used
pit_verified
universe_source
pit_candidate_count
research_fallback_count
resolved_universe_count
universe_reason_code
```

若已有等价字段，应复用而不是重复创建。

---

## 12. 代码修改范围

预计主要涉及以下文件：

### 12.1 `agent/src/stockpred/fund_rotation/batch_service.py`

修改：

- 增加 PIT resolver loader/context 注入点；
- 构造 Runner 时传入 `pit_universe_resolver`；
- 区分“未配置”和“配置后加载失败”。

### 12.2 `agent/backtest/fund_rotation/runner.py`

修改：

- 将 `fallback_universe` 拆成：
  - `pit_candidate_codes`
  - `research_fallback_codes`
- 修改 `_resolve_pit_universe_for_signal` 参数语义；
- no-resolver 分支只能使用 `research_fallback_codes`；
- 增补必要 evidence/diagnostics。

### 12.3 BatchService 测试

修改/新增：

- resolver 注入测试；
- loader 失败不静默 fallback 测试。

### 12.4 Runner / PIT Universe 测试

修改/新增：

- candidate/fallback 边界测试；
- research fallback quality/evidence 测试；
- resolver path 使用 broad candidate set 测试。

预计实现规模仍应控制在约 **4 个文件、80–150 行左右（含测试，具体以现有 fixture 复用程度为准）**。如果实现过程中需要大规模修改 strategy、schema 或 Production adapter，应停止扩 scope，单独设计。

---

## 13. 测试设计

### T1：BatchService 正确向 Runner 注入 PIT resolver

**Given**：BatchService 提供一个可识别的 fake resolver。

**When**：执行 batch subrun。

**Then**：Runner 实际使用该 resolver，而不是进入 `PIT_MASTER_MISSING` 路径。

验收重点不是 mock 调用次数，而是通过行为确认依赖已进入真实运行路径。

---

### T2：无 resolver 时只使用 `snapshot.universe_codes`

构造：

```text
historical_candidate_codes = {ETF_A, ETF_B, NON_ETF_X}
universe_codes             = {ETF_A, ETF_B}
```

无 PIT resolver。

期望最终 Research fallback：

```text
{ETF_A, ETF_B}
```

禁止：

```text
{ETF_A, ETF_B, NON_ETF_X}
```

同时必须保留：

```text
PIT_MASTER_MISSING
RESEARCH_ONLY_UNVERIFIED_UNIVERSE
pit_verified=false
```

---

### T3：有 resolver 时使用 broad candidate set

同样构造：

```text
historical_candidate_codes = {ETF_A, ETF_B, NON_ETF_X}
universe_codes             = {ETF_A, ETF_B}
```

存在 resolver。

期望 resolver 的候选输入仍然可以包含：

```text
{ETF_A, ETF_B, NON_ETF_X}
```

由 resolver 决定历史时点最终合法集合。

该测试用于防止修 fallback 时误把 PIT resolver 的候选范围也缩窄。

---

### T4：resolver loader 失败不得静默降级

**Given**：PIT resolver 已配置，但 loader 抛异常。

**Then**：

- subrun / batch 按现有错误契约失败；
- 不能伪装成“未配置 resolver”；
- 不能产生 `STATIC_ETF_SNAPSHOT` 正常 fallback 结果。

---

### T5：resolver 执行失败不得使用 raw candidates

**Given**：resolver 本身在某个 signal date 抛异常或返回不可接受结果。

**Then**：不得直接把 `historical_candidate_codes` 作为 universe。

---

### T6：策略层行为不因本次改动被修改

固定相同最终 universe、相同价格数据和相同策略配置：

- clustering 输入一致；
- momentum 输入一致；
- representative selection 一致；
- cluster gate 行为一致。

本次变更只允许因“最终 universe 本来就取错”而导致回测结果变化。

---

### T7：Research fallback 可审计

验证 artifact / evidence 至少能区分：

```text
universe_source=STATIC_ETF_SNAPSHOT
pit_verified=false
reason_code=PIT_MASTER_MISSING
```

不得仅靠 `quality_status` 间接推断。

---

## 14. 验收标准

本次 P0 完成的 Definition of Done：

1. BatchService 可以向 Runner 注入 PIT Universe resolver。
2. 当 resolver 已提供时，不再因为 BatchService 漏接线而产生 `PIT_MASTER_MISSING`。
3. 当 resolver 未提供时，Research fallback **严格等于/来源于 `snapshot.universe_codes`**，不再优先使用 `historical_candidate_codes`。
4. `historical_candidate_codes` 仍可作为真实 PIT resolver 的 broad candidate input。
5. Research fallback 始终标记 `RESEARCH_ONLY_UNVERIFIED_UNIVERSE` / `pit_verified=false`。
6. resolver 配置/执行异常不能静默扩大 universe。
7. 新增测试覆盖 candidate/fallback 语义边界。
8. 所有既有相关测试通过。
9. 不修改 correlation strategy 的聚类、动量、代表 ETF、Gate 阈值。
10. 不宣称本次修改已经完成 Production PIT Master 建设。

---

## 15. 回归与风险

### 15.1 预期结果变化

修复后，部分 Research fallback 回测的 universe 数量可能下降，进而改变：

- 聚类分布；
- cluster quality diagnostics；
- 代表 ETF；
- 动量排名；
- 订单和收益。

这是合理变化，因为此前 universe 边界本身可能过宽。

不能为了维持旧回测指标而保留错误 fallback。

### 15.2 `PIT_MASTER_MISSING` 可能仍然存在

如果生产环境当前根本没有可加载的正式 PIT Fund Master/resolver，那么完成本 P0 后 Research 回测仍然会合理产生：

```text
PIT_MASTER_MISSING
RESEARCH_ONLY_UNVERIFIED_UNIVERSE
```

区别在于：

- fallback universe 边界正确；
- 原因真实；
- 不再包含“其实有 resolver 但 BatchService 没传”的编排问题。

因此本任务的验收条件不是“所有 run 都消灭 `PIT_MASTER_MISSING`”，而是“该状态只在真实缺少 PIT 能力时出现”。

### 15.3 不应把策略收益作为 P0 验收标准

本次修复后 run 仍可能：

- 全现金；
- 被 cluster gate 降级/拒绝；
- 无代表 ETF；
- 动量不满足条件。

这些属于策略层问题。

P0 的正确性必须通过数据边界和 resolver 路径验证，而不是通过“终于产生订单”验证。

---

## 16. 被否决的方案

### 16.1 方案 A：直接放宽 cluster reject 阈值

例如：

```text
max_cluster_share_reject: 0.80 -> 0.95
min_effective_cluster_count_reject: 2.5 -> 1.5
```

否决原因：

- 与 PIT Universe 接线问题无关；
- 可能让策略开始交易，但错误 universe 仍然存在；
- 会把数据问题伪装成策略参数问题。

### 16.2 方案 B：继续使用 `historical_candidate_codes` 作为 fallback

否决原因：

- candidate 不是 eligible universe；
- 破坏数据契约；
- 可能允许非目标 instrument 进入策略；
- 未来 PIT Master 上线后会形成两套难以解释的 universe 语义。

### 16.3 方案 C：本次同时建设完整 Production PIT Master

否决原因：

- scope 从编排修复扩大为数据基础设施建设；
- 需要处理 temporal validity、knowledge time、revision、status、delist、provenance 等问题；
- 风险和验证成本明显高于本 P0；
- 不应阻塞当前错误 fallback 边界修复。

### 16.4 方案 D：没有 PIT Master 就禁止一切回测

本阶段否决原因：

- Research 模式仍有价值；
- 可以通过明确 quality 标记防止误用；
- formal verified 与 research exploration 应分层，而不是把两者混为一个运行等级。

---

## 17. 后续任务

本 P0 完成后，建议按以下顺序推进：

```text
P0 PIT resolver wiring + fallback boundary
        ↓
重新跑代表性基金轮动回测
        ↓
检查 universe 数量 / cluster size / PIT evidence
        ↓
P1 cluster quality gate 语义修正
        ↓
检查 representative coverage
        ↓
P1/P2 tracking_index / exposure canonicalization
        ↓
Production PIT Master 数据建设
        ↓
Formal / Verified 模式强制 fail-closed
```

后续独立设计至少包括：

1. **Production PIT Fund Master**
   - `valid_from` / `valid_to`
   - `known_from`
   - revision/version
   - list/delist lifecycle
   - status
   - fund_type / asset_class / tracking_index
   - source/provenance/quality

2. **Exposure canonicalization**
   - ETF wrapper → tracking exposure → cluster → final ETF wrapper selection。

3. **Cluster quality Gate 重构**
   - cluster concentration 从“自动清仓条件”改为更合理的质量/诊断信号。

这些任务不得与本 P0 的验收绑定。

---

## 18. 最终决策

采用以下最小且语义正确的方案：

```text
1. BatchService 补齐 pit_universe_resolver 注入
2. Runner 将 pit_candidate_codes 与 research_fallback_codes 拆分
3. resolver 存在 → broad candidates 交由 PIT resolver
4. resolver 未配置 → 仅使用 static ETF snapshot fallback
5. fallback 永远保持 research-only / unverified 标记
6. resolver 配置或执行失败 → 不静默扩大 universe
7. 增加针对上述边界的单元与集成测试
```

该方案解决的是：

> **让“没有 PIT Master”只代表真实的数据能力缺失，而不再同时掺杂编排漏接线和 raw candidate 被错误当成 universe 的问题。**

它不会保证基金轮动一定产生交易，也不应该以此为目标；它为后续判断聚类 Gate、代表 ETF 和策略收益提供一个可信的数据边界。
