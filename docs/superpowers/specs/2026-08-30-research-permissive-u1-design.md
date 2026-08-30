# 研究可运行的可选 Identity/PIT 字段设计增补

## 1. 背景与目标

现有设计把底层指数、资产类别、区域、币种、`known_from`、`valid_from`、`revision_id` 及 PIT revision/knowledge 字段作为 U1 身份和时点证据的一部分。当这些字段在当前数据源中不存在时，研究流程被直接阻断。

本增补把“能否继续研究”和“是否具备 PIT/生产资格”拆开：上述字段可以缺失，当前研究和回测仍可运行；但缺失、部分缺失或冲突必须显式记录为未验证，不能被解释为已完成身份去重、PIT 证明或生产准入。

本增补只调整 U1 派生的状态与投影契约，不扩展数据源、不伪造历史字段、不修改聚类算法、R39、公共 Runner 或无关实验。

## 2. 第一性原理与不可变约束

研究运行的最低事实是：有可重算的行情、可执行的交易规则、稳定的快照和可比的输出。身份和 PIT 证据决定结论的可信等级，而不是当前研究任务能否产生可审计的研究产物。

因此固定以下约束：

1. U0 仍是每个调仓日冻结的输入，U1 只能从该 U0 派生。
2. 当前最小实现中，`U1.eligible_codes` 必须与 `U0.eligible_codes` 完全相同；聚类继续消费 U1。
3. 可选字段缺失不能用空值、当前状态或推测值填补，也不能阻断研究模式。
4. `u1_equals_u0` 只表示可审计的结果集合相等；身份是否验证由独立状态字段表达，不能从该布尔值推断。
5. identity/PIT 未验证的快照可以标记为 `research-only`，但不得通过 promotion、Shadow qualification 或生产部署门禁。
6. 行情缺失、快照不可重算、日期非法、不可交易状态冲突、数据哈希不一致等核心数据错误仍然 fail-closed；本增补不放宽这些门禁。
7. 每一步仍须先测试、再最小实现、运行相关实验、生成证据，并经 5.6 Luna 高推理 review 确认无 P0/P1 后才能推进。

## 3. 状态契约

### 3.1 Identity 状态

在现有 U1 coverage diagnostics 中增加或规范化：

```text
identity_validation_status:
  VERIFIED       # 必要 identity 字段完整，且 U0 内无身份冲突
  PARTIAL        # 至少一个字段存在，但字段集合不完整
  UNAVAILABLE    # 当前数据源未提供可验证 identity 字段
  CONFLICT       # identity 字段存在，但同一身份对应多个候选或字段互相冲突
```

`underlying_index`、`tracking_index`、`asset_class`、`region`、`currency`、杠杆/反向属性以及份额/联接关系都属于可选证据字段。当前数据源没有这些字段时，身份状态为 `UNAVAILABLE`，不得根据基金代码暗示其代表独立底层资产。

### 3.2 PIT 证据状态

增加或规范化：

```text
pit_evidence_status:
  VERIFIED       # required PIT revision/knowledge 证据完整且可验证
  PARTIAL        # 有部分 valid/known/revision/source 证据，但无法完整证明
  UNAVAILABLE    # 当前数据源未提供 PIT revision/knowledge 证据
```

`known_from`、`valid_from`、`revision_id`、source published/ingested 以及 revision/knowledge 链字段均为可选。字段缺失只降低证据等级，不自动把研究快照标为 `PIT_INVALID`。

### 3.3 研究和晋级状态

U1 派生结果增加显式诊断：

```text
research_execution_allowed: true | false
promotion_allowed: false
deployment_allowed: false
```

当核心行情、快照和交易可执行性检查通过，而 identity/PIT 证据缺失或冲突时：

- U1 所有原本属于 U0 eligible 的代码保持 `included=True`；
- `eligible_codes` 保持与 U0 相同；
- `u1_equals_u0=True`；
- `research_execution_allowed=True`；
- `promotion_allowed=False`、`deployment_allowed=False`；
- U1 quality 使用现有的 `RESEARCH_ONLY_UNVERIFIED_UNIVERSE`（或在不改变既有严重性语义的情况下保留更严格的未验证状态），不得使用 `PIT_INVALID` 表示“仅缺少可选字段”。

若发生核心数据错误，则 `research_execution_allowed=False`，继续沿用现有 `PIT_INVALID`/不可用行为。

## 4. U0 到 U1 的最小派生行为

对每个 U0 eligible code，U1 生成一条派生 membership：

```text
included=True
reason_code=U1_DERIVED_FROM_U0
identity_key=已有身份字段能构造时使用，否则为 null
layer=U1
```

身份键、代表映射、缺失数量、重复数量和 identity hash 仍然保留，作为诊断和审计证据；它们不再改变研究模式下的 U1 eligible 集合。遇到缺失身份时不进行猜测性去重；遇到重复/冲突时保留候选并记录 `CONFLICT`，不声称已经选择了唯一代表。

这样可以同时表达两件不同的事实：

```text
结果集合：U1 与 U0 相同，可继续当前研究
身份证据：未验证/冲突，不能晋级或部署
```

## 5. 下游门禁

### 5.1 聚类和研究回测

聚类仍只读取 U1。研究运行器允许 `research_execution_allowed=True` 且 quality 为 research-only/未验证状态的快照运行，并在 manifest、报告和比较结果中保留上述状态。研究结果可以用于探索、机制诊断和候选比较，但不得自动成为正式策略资格结论。

### 5.2 Shadow 与 promotion

Shadow qualification 和 promotion validator 必须同时要求：

```text
u1_equals_u0 == True
identity_validation_status == VERIFIED
pit_evidence_status == VERIFIED
promotion_allowed == True
deployment_allowed == True
```

因此同集合不再等价于已验证；缺失可选字段不会阻塞研究，但会阻塞晋级和生产使用。

### 5.3 报告和 manifest

所有研究产物必须输出：

- U0/U1 eligible code 集合及其 fingerprint；
- `u1_equals_u0`；
- identity/PIT 状态及缺失、部分、冲突计数；
- `research_execution_allowed`、`promotion_allowed`、`deployment_allowed`；
- 触发 research-only 的具体原因。

不得把“研究运行成功”表述为“PIT 身份已验证”或“可生产部署”。

## 6. 最小改动范围

仅允许修改以下范围：

1. `pit_universe.py`：调整 U1 投影和 coverage diagnostics，保留现有哈希与审计结构。
2. U1/PIT 序列化、研究 validator 和报告：增加/传递状态字段，区分研究门和晋级门。
3. 对应 resolver、Runner adapter、Shadow validator 和实验契约测试。
4. 中文设计、验收矩阵、运行说明和研究链路 continuation 记录器。

不允许：

- 为缺失 identity 字段引入基金代码唯一身份的隐式 fallback；
- 修改聚类算法或 R39 信号；
- 重写公共 Runner、行情层或 Lance/SQLite schema；
- 为了补齐 PIT 字段而构造历史 revision/knowledge 数据；
- 将 research-only 结果写入生产候选或替代真实 Shadow 观察。

## 7. 验收标准

实现完成必须满足：

1. identity/PIT 字段完整时，既有 U1 派生、哈希和验证行为保持不回归。
2. identity/PIT 字段缺失时，U1 与 U0 结果集合相同，研究回测可以继续，且状态明确为未验证/research-only。
3. identity 字段冲突时，不进行未经证明的去重；研究仍可运行，但身份状态为 `CONFLICT`，promotion/deployment 均为 false。
4. 核心行情、可执行性或快照完整性错误仍然阻断研究并 fail-closed。
5. Shadow/promotion validator 拒绝未验证 identity/PIT 的产物。
6. 相关测试、实验 manifest 和中文报告均能区分“研究可运行”与“资格已验证”。
7. `git diff --stat` 和变更文件清单证明没有超出上述最小范围的改动。
