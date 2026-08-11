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
    frozen_at: str
    effective_from: str
    status: StrategyLifecycleStatus
```

同时保存完整配置、Universe Policy、执行规则、OOS、归因、批准门禁和已知限制。任何核心逻辑、参数或数据契约变化都生成新版本，禁止覆盖冻结版本。

## 3. 状态机

```text
DRAFT → DATA_VERIFIED → BACKTEST_VALID → OOS_VALIDATED
→ FROZEN → SHADOW_RUNNING → DECISION_ELIGIBLE → RETIRED
```

异常状态为 `SUSPENDED / INVALIDATED`。`DECISION_ELIGIBLE` 只表示可作为研究和投资决策参考，不表示自动下单或收益保证。

## 4. 冻结门禁

只有同时满足 PIT data、execution contract、independent baseline、OOS evidence、attribution reconciliation、parameter stability、cost sensitivity 和 drawdown 门禁，才能 FROZEN，并生成 `frozen_strategy_manifest.json`。

禁止手选漂亮 OOS、隐藏失败 Variant、覆盖参数、冻结后更换主要指标或重新优化后沿用旧版本号。

## 5. Shadow Portfolio 输入与输出

每期输入：

```text
strategy_version_id
scheduled_signal_date
as_of_time
data_snapshot_version
previous_shadow_state
```

明确区分 `signal_date / as_of_time / expected_execution_date / actual_shadow_execution_date`。

每期输出当前持仓、现金、NAV、原始信号、入选簇、目标 ETF、目标权重、目标变化原因、执行尝试、shadow fills 和成本。

Shadow 是持续账户：本期结束状态成为下期开始状态，持仓、现金、residual order 和公司行为必须连续继承。

## 6. 数据到达与因果性

信号只能读取 `as_of_time` 前已经可用的数据。数据未到齐时输出 `DATA_NOT_READY`，保持旧目标；不能读取第二天补齐数据后声称前一天已生成信号。

每期不可变记录保存 `shadow_decision_id / strategy_version_id / generated_at / signal_date / as_of_time / snapshot fingerprint / previous targets / new targets / reason codes / expected execution date / status`。

未来数据更正只能追加 correction record，不能覆盖原决策。

**为什么改：** Forward Test 的核心证据是证明当时已经提交该决策，而不只是后来净值看起来合理。

## 7. 调度与运行服务

```python
class ShadowRunScheduler:
    def due_versions(self, now: datetime) -> tuple[str, ...]:
        ...

class ShadowPortfolioService:
    def run_scheduled_decision(
        self,
        strategy_version_id: str,
        as_of_time: datetime,
    ) -> ShadowDecisionResult:
        ...
```

调度器只负责唤醒。服务复用正式 PIT Resolver、策略实现、目标权重契约、ETF 规则、Execution Ledger 和指标定义，禁止另写简化 Shadow 策略。

## 8. Shadow 成交与双净值

使用当时实际可获得的开盘价、成交量、涨跌停、停牌和交易规则，并沿用容量、滑点、佣金、T+1、零股和 residual retry。

持续维护：

```text
shadow_ideal_nav
shadow_executable_nav
```

行情晚到必须记录数据延迟，不能无痕回填成交。

## 9. 最低观察期

进入 `DECISION_ELIGIBLE` 前至少满足：

```text
forward_observation_weeks >= 26
completed_rebalance_cycles >= 6
```

并至少经历一个正收益阶段和一个负收益或回撤阶段。市场变化不足时继续观察，不能为了按时升级降低门禁。26周只是最低运行完整性要求，不代表足以证明长期 Alpha。

## 10. Forward 指标

报告收益、波动率、Sharpe、最大回撤、换手、成本、信号命中率、目标变化数、blocked attempt rate、order fill rate、ideal/executable gap、数据延迟和决策失败。

同时比较 OOS 与 Shadow 的收益、波动、换手和成本偏差。短期负收益不自动判死，短期正收益也不自动升级，必须使用冻结时预先声明的失效条件。

## 11. 漂移与暂停

监控：

- 数据：Universe 数量、类型构成、缺失率、流动性、跟踪质量。
- 信号：分数分布、簇集中度、现金比例、换手。
- 结果：实现波动、回撤、成本、ideal/executable divergence。

冻结时声明 maximum shadow drawdown、execution cost ratio、data failure count、consecutive invalid decisions 和 ideal/execution gap。触发后进入 `SUSPENDED`。

发现未来泄漏、PIT 失真、账本无法对账、冻结配置被修改、哈希不一致或使用错误版本时，进入 `INVALIDATED`。

暂停后不得直接调参恢复；必须开启新研究实验、新版本、新 OOS 和新的 Shadow 周期。

## 12. 多版本并行与冠军偏差

新版本不覆盖旧版本，可同时 Shadow 运行。所有版本包括失败版本都必须保留并报告尝试总数，禁止只展示事后冠军。旧版本可以继续、暂停或退役，但历史证据永久保留。

## 13. 人工审批

从 `SHADOW_RUNNING` 到 `DECISION_ELIGIBLE` 必须记录 approver、approved at、evidence version、known limitations 和 allowed use。真实交易连接属于另一份独立设计和风险审批。

## 14. 产物

```text
frozen_strategy_manifest.json
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
- 重复调度不产生重复决策，as-of 后数据不可见。
- 数据未到齐保持旧目标，Shadow 账户跨周期连续。
- residual、公司行为和持仓在重启后连续。
- ideal 与 executable 净值独立维护。
- 历史决策不可覆盖，更正只追加记录。
- 触发阈值进入 SUSPENDED，不能通过直接调参恢复。
- 多版本互不污染，失败版本仍保留。
- 未经人工审批不能 `DECISION_ELIGIBLE`。
- 系统能证明每期决策在未来价格出现前固化。
- 本阶段不会直接触发真实资金交易。
