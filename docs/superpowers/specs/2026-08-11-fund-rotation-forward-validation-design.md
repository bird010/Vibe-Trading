# 基金轮动策略冻结、Shadow Portfolio 与 Forward Test 设计

**目标：** 建立从研究、OOS、冻结到连续前向观察和决策资格审批的生命周期；本阶段不连接真实券商自动下单。

## 1. 为什么修改

历史回测即使严格，也使用已经发生的数据。研究人员可能知道历史特征，并在多次查看 OOS 后不自觉调参。

**通俗解释：** 回测是看录像后回答问题；Forward Test 是每周先提交答案，再等待未来价格揭晓。

**不修改的后果：** 策略会不断根据新历史调整；无法证明测试的是哪个版本；回测假设与真实可执行差异无法持续比较；漂亮回测可能跳过观察期直接影响决策。

## 2. 不可变 FrozenStrategyVersion

```python
class FrozenStrategyVersion:
    strategy_version_id: str
    strategy_id: str
    parent_research_experiment_id: str
    implementation_hash: str
    framework_hash: str
    config_hash: str
    data_contract_version: str
    execution_contract_version: str
    qualification_policy_hash: str
    frozen_at: str
    effective_from: str
```

同时保存完整配置、Universe Policy、执行规则、OOS、归因、批准门禁和已知限制。任何核心逻辑、参数或数据契约变化都生成新版本，禁止覆盖冻结版本。

## 3. 分离的状态机

```text
StrategyVersionLifecycle：CANDIDATE → FROZEN → RETIRED
                         └────────────→ INVALIDATED

ShadowDeploymentStatus：CREATED → RUNNING → COMPLETED
                                  ├→ SUSPENDED
                                  └→ FAILED

DecisionQualification：INELIGIBLE → ELIGIBLE → REVOKED
```

数据快照、Backtest Run 和 Research Experiment 使用总体设计中各自独立的资格或生命周期，不能塞入 StrategyVersion。`ELIGIBLE` 只表示该策略版本在指定用途和政策下可作为研究及投资决策参考，不表示自动下单或收益保证。Shadow suspension 只暂停某个 deployment，不自动改写 Frozen 版本；发现因果泄漏或版本篡改时才独立 INVALIDATE 策略版本并 REVOKE 决策资格。

## 4. Qualification Evidence、Policy 与 Assessment

资格判定拆成三个不可变对象：

```python
class QualificationEvidence:
    evidence_id: str
    evidence_type: str
    subject_id: str
    artifact_ids: tuple[str, ...]
    artifact_hashes: tuple[str, ...]
    quality_status: str
    generated_at: str

class QualificationPolicy:
    policy_id: str
    policy_hash: str
    target_transition: str
    hard_gates: tuple[GateSpec, ...]
    warning_gates: tuple[GateSpec, ...]
    frozen_at: str

class QualificationAssessment:
    assessment_id: str
    target_transition: str
    subject_id: str
    policy_hash: str
    evidence_ids: tuple[str, ...]
    decision: str
    failed_hard_gates: tuple[str, ...]
    warnings: tuple[str, ...]
    reason_codes: tuple[str, ...]
    evaluated_at: str
    evaluator_version: str
```

Policy 必须在评估区间运行前冻结并进入实验或策略版本身份哈希。阈值按 experiment/policy profile 声明，不设置适用于所有策略的一刀切全局数值；hard gate、warning 和人工审批必须分开。

### 4.1 冻结门禁

只有同时具备可验证 PIT data、execution contract、independent baseline、Sealed OOS evidence、attribution reconciliation，以及预声明的 parameter stability、cost sensitivity 和 drawdown assessment，才能创建 FROZEN 版本并生成 `frozen_strategy_manifest.json`。

首版每个 gate 至少明确 `metric_name / formula / evaluation_scope / threshold / comparison_operator / missing_data_policy / evidence_artifact`。固定参数或单候选实验不适用 parameter-selection frequency 时，应在 Policy 中声明 `NOT_APPLICABLE` 及理由，而不是伪造 PASS。

禁止手选漂亮 OOS、隐藏失败 Variant、覆盖参数、冻结后更换主要指标或重新优化后沿用旧版本号。

### 4.2 其他资格转换

`CAN_START_SHADOW` 与 `CAN_GRANT_DECISION_ELIGIBILITY` 分别生成新的 assessment，不复用冻结结论。证据或政策失效时追加 `REVOKED/INVALIDATED` assessment，禁止覆盖历史结论，也不维护会过期的 `can_freeze=true` 可变字段。

## 5. Shadow Portfolio 输入与输出

Decision 阶段输入：

```text
strategy_version_id
scheduled_signal_date
as_of_time
data_snapshot_version
previous_shadow_state
```

明确区分 `signal_date / as_of_time / expected_execution_date / actual_shadow_execution_date`。

Decision 阶段只输出当前持仓、现金、NAV、原始信号、入选簇、目标 ETF、目标权重、目标变化原因和 expected execution date，不得在同次调用中生成 execution attempt、shadow fill 或未来成本。

Execution 阶段输入已封存的 `shadow_decision_id`、到期 parent/residual orders、`execution_as_of_time` 和届时已到达的市场数据；输出 attempts、shadow fills、显式成本、机会成本诊断以及更新后的账户状态。

Shadow 是持续账户：本期结束状态成为下期开始状态，持仓、现金、residual order 和公司行为必须连续继承。

## 6. 数据到达与因果性

信号只能读取 `as_of_time` 前已经可用的数据。数据未到齐时输出 `DATA_NOT_READY`，保持旧目标；不能读取第二天补齐数据后声称前一天已生成信号。

每期不可变记录保存 `shadow_decision_id / strategy_version_id / generated_at / signal_date / as_of_time / snapshot fingerprint / previous targets / new targets / reason codes / expected execution date / status`。

未来数据更正只能追加 correction record，不能覆盖原决策。

**为什么改：** Forward Test 的核心证据是证明当时已经提交该决策，而不只是后来净值看起来合理。

## 7. 决策封存与执行的时序边界

Shadow Decision 与 Shadow Execution 必须是两个逻辑处理阶段，但首版可以部署在同一进程并由同一 façade 编排，不要求拆成微服务。

```python
class ShadowRunScheduler:
    def due_versions(self, now: datetime) -> tuple[str, ...]:
        ...

class ShadowDecisionService:
    def seal_scheduled_decision(
        self,
        strategy_version_id: str,
        as_of_time: datetime,
    ) -> ShadowDecisionResult:
        ...

class ShadowExecutionService:
    def execute_due_orders(
        self,
        shadow_decision_id: str,
        execution_as_of_time: datetime,
    ) -> ShadowExecutionResult:
        ...
```

调度器只负责唤醒。Decision 阶段在未来执行价格出现前封存信号、目标、输入快照和 `decision_idempotency_key`；Execution 阶段只能在预期执行日的市场数据实际到达后，使用独立的 `execution_idempotency_key` 生成 attempts 和 fills。两阶段复用正式 PIT Resolver、策略实现、目标权重契约、市场规则、成本模型、Execution Ledger 和指标定义，禁止另写简化 Shadow 策略。

## 8. Shadow 成交与双净值

使用当时实际可获得的开盘价、成交量、涨跌停、停牌和交易规则，并沿用容量、滑点、佣金、T+1、零股和 residual retry。

持续维护：

```text
shadow_ideal_nav
shadow_executable_nav
```

行情晚到必须记录数据延迟，不能无痕回填成交。

## 9. 最低观察期

申请 `DecisionQualification.ELIGIBLE` 前至少满足：

```text
forward_observation_weeks >= 26
completed_rebalance_cycles >= 6
```

不设置“至少经历一个正收益阶段和一个负收益或回撤阶段”的硬门禁，因为阶段窗口、基准和阈值没有唯一合理定义，可能造成任意解释或无限等待。改为报告 return、drawdown、volatility 和预注册 regime exposure 的观察覆盖；覆盖不足产生 warning，由 QualificationPolicy 决定是否延长观察，但不能事后改变定义。26周和6次再平衡只是最低运行完整性要求，不代表足以证明长期 Alpha。

## 10. Forward 指标

报告收益、波动率、Sharpe、最大回撤、换手、成本、信号命中率、目标变化数、blocked attempt rate、order fill rate、ideal/executable gap、数据延迟和决策失败。

同时比较 OOS 与 Shadow 的收益、波动、换手和成本偏差。短期负收益不自动判死，短期正收益也不自动升级，必须使用冻结时预先声明的失效条件。

## 11. 漂移与暂停

监控：

- 数据：Universe 数量、类型构成、缺失率、流动性、跟踪质量。
- 信号：分数分布、簇集中度、现金比例、换手。
- 结果：实现波动、回撤、成本、ideal/executable divergence。

冻结时声明 maximum shadow drawdown、execution cost ratio、data failure count、consecutive invalid decisions 和 ideal/execution gap。触发后对应 `ShadowDeploymentStatus` 进入 `SUSPENDED`，并产生新的资格 assessment。

发现未来泄漏、PIT 失真、账本无法对账、冻结配置被修改、哈希不一致或使用错误版本时，`StrategyVersionLifecycle` 进入 `INVALIDATED`，已有 `DecisionQualification` 追加 `REVOKED`。

暂停后不得直接调参恢复；必须开启新研究实验、新版本、新 OOS 和新的 Shadow 周期。

## 12. 多版本并行与冠军偏差

新版本不覆盖旧版本，可同时 Shadow 运行。所有版本包括失败版本都必须保留并报告尝试总数，禁止只展示事后冠军。旧版本可以继续、暂停或退役，但历史证据永久保留。

## 13. 人工审批

从运行中的 Shadow evidence 申请 `DecisionQualification.ELIGIBLE` 必须记录 approver、approved at、policy hash、assessment id、evidence version、known limitations 和 allowed use。真实交易连接属于另一份独立设计和风险审批。

## 14. 产物

```text
frozen_strategy_manifest.json
qualification_evidence.json
qualification_policy.json
qualification_assessments.json
shadow_account_state.json
shadow_decisions.csv
shadow_targets.csv
shadow_orders.csv
shadow_attempts.csv
shadow_trades.csv
shadow_positions.csv
shadow_equity.csv
shadow_metrics.json
shadow_drift_report.json
shadow_incidents.json
shadow_manifest.json
```

每次运行保存独立事件，不只保存最新状态。

## 15. 失败恢复

数据未到齐、临时文件锁和服务短暂中断可以使用相同 strategy version、signal date、as-of time 和 idempotency key 重试，不得重复决策或成交。

哈希不一致、快照被替换、账本无法对账、冻结配置改变或同日冲突决策不可自动重试，必须人工审核。

## 16. 测试与验收

- 冻结版本不可修改，核心参数变化生成新版本。
- 未通过 OOS 不能 FROZEN。
- 未冻结 QualificationPolicy 或门禁缺少公式、阈值、范围和缺失处理时不能 FROZEN。
- 重复调度不产生重复决策，as-of 后数据不可见；Decision 与 Execution 使用不同幂等键。
- Execution 在相应市场数据到达前不能产生 attempt 或 fill。
- 数据未到齐保持旧目标，Shadow 账户跨周期连续。
- residual、公司行为和持仓在重启后连续。
- ideal 与 executable 净值独立维护。
- 历史决策不可覆盖，更正只追加记录。
- 触发阈值后对应 Shadow deployment 进入 SUSPENDED，不能通过直接调参恢复。
- StrategyVersion、ShadowDeployment 与 DecisionQualification 的状态互不冒充，暂停 deployment 不改写 Frozen 事实。
- 多版本互不污染，失败版本仍保留。
- 未经人工审批不能获得 `DecisionQualification.ELIGIBLE`。
- 市场环境覆盖不足只产生预注册 warning，不以未定义的“正负收益阶段”阻塞或放行。
- 系统能证明每期决策在未来价格出现前固化。
- 本阶段不会直接触发真实资金交易。
