# 基金轮动独立基线与 OOS/Walk-forward 设计

**目标：** 将同区间探索性比较升级为可审计的 Train、Validation、untouched OOS 和 rolling walk-forward 研究框架，并用非聚类策略隔离聚类的增量价值。

## 1. 为什么修改

当前一个 Batch 内所有 Variant 共享一段评价区间，比较器在整段历史上计算并排名。若研究人员据此选择参数，再汇报同一段历史表现，就发生样本内污染。

**通俗解释：** 在同一份试卷上尝试100组答案，挑最高分后再用这张试卷证明能力。尝试越多，偶然高分概率越大。

当前两个正式策略又共享相关性聚类、Cluster Momentum 和 Top-N，只能比较持仓表达，不能证明聚类本身贡献 Alpha。

**不修改的后果：** 全历史 Sharpe 和年化收益可能被高估；策略排名易随起止日期反转；参数越多越容易过拟合；无法建立可信的冻结版本。

## 2. 三类基准

### 2.1 市场绩效基准

基准由实验运行前冻结的 `BenchmarkPolicy` 决定：

```text
primary_benchmark
secondary_benchmarks
cash_benchmark
universe_equal_weight_benchmark
benchmark_data_version
```

`510300` 可以作为国内股票 ETF Universe 的 secondary comparator，但不能永久作为债券、商品、跨境或混合 Universe 的唯一市场基准。Primary benchmark 必须与 Universe 的资产类别和可投资范围匹配；同一实验的全部候选共享相同 policy。

### 2.2 独立 Alpha 基线

第一阶段新增三个不调用聚类模块的正式策略族：

1. `etf_absolute_momentum`：ETF 独立动量、正动量过滤、Top-N、固定 slot 等权。
2. `etf_dual_momentum`：相对排名加绝对门槛，不满足时留现金。
3. `etf_trend_momentum`：长期趋势门禁后做相对动量排名。

若不增加这些基线，系统无法判断复杂聚类是否只是重复直接 ETF 排名已经能够取得的收益。

### 2.3 策略组件消融链

```text
S0：Cash / 无 Alpha 基线
S1：ETF 直接动量排名
S2：S1 + 相关性聚类
S3：S2 + 代表 ETF
S4：S3 + ETF 质量选择
S5：S4 + 组合权重与 Portfolio Risk Layer
```

只有 S1→S2 的差异才能近似回答聚类贡献。S0→S5 全部使用相同的最终可执行契约；市场规则、容量、滑点和费用的影响由归因设计中的 X0→X5 Execution Ladder 单独测量，禁止把执行约束同时当作策略组件。

## 3. 动量家族

`1M / 3M / 6M / 12M / 6-1 / 12-1 / risk-adjusted momentum` 是同一策略族的 Variant，而不是伪装成七个独立策略。这样可以区分假设变化和参数变化。

## 4. ResearchExperiment 契约

```python
class ResearchExperiment:
    experiment_id: str
    hypothesis: str
    primary_metric: str
    secondary_metrics: tuple[str, ...]
    parameter_space: dict
    selection_policy: SelectionPolicy
    split_policy: TemporalSplitPolicy
    benchmark_policy: BenchmarkPolicy
    qualification_policy_hash: str
    candidate_variants: tuple[VariantSpec, ...]
    sealed_candidate_identity_hashes: tuple[str, ...]
```

运行前必须声明假设、独立基线、参数空间、主要指标、淘汰条件、时间切分、基准政策、资格政策和 Variant 总数。进入 Sealed OOS 前还必须登记全部候选的代码、配置、数据、执行和评估身份哈希。禁止运行结束后更改主要指标、淘汰条件或候选集合。

## 5. 时间切分

### 5.1 固定切分

```text
Train → Validation → Untouched OOS
```

最低门禁：OOS 至少占完整样本20%，且不少于104周。数据不足时可以研究，但不能生成 `QUALIFIED_OOS_EVIDENCE`。

### 5.2 Rolling Walk-forward

推荐默认窗口：

```text
Train：156周
Validation：52周
Test：52周
Step：52周
```

每个 fold 严格执行：

```text
Train/Validation 选择参数
→ 冻结该 fold 参数
→ Test 只运行一次
→ 保存 Test 结果
```

窗口长度可配置，但必须进入实验身份和结果快照。

### 5.3 Walk-forward 账户连续性

总体可执行 OOS 净值必须由一个连续账户产生，而不是把各 fold 独立初始化后做几何拼接。定义不可变的 `WalkForwardAccountState`：

```text
cash / positions / cost_basis
residual_orders / corporate_action_state
last_valuation_date / last_nav
```

Fold n 的结束状态成为 Fold n+1 的开始状态。新 fold 参数只在下一个正常计划信号生效，并通过相同执行契约再平衡；禁止在 fold 边界无成本清仓、换仓或重置 NAV。未完成 residual 按预先声明的 active-order policy 延续，直到正常成交、到期，或被新决策取消并替换。

## 6. 参数选择

默认主要指标为 `validation_sharpe`，同时声明最大回撤、最小交易数、数据质量和执行质量硬约束。Sharpe 相近时依次偏好较低回撤、较低换手、较低复杂度和稳定 `variant_key`。

当前同区间比较按年化收益排名；它继续保留但更名为 `Exploratory Comparison`，不能生成正式策略推荐。

## 7. Sealed OOS 泄漏防护

Sealed OOS 禁止参与参数搜索、ETF 规则研究、coverage 阈值、动量权重、候选淘汰或选优、主要指标选择、资格阈值调整和成本校准。开封后只允许对每个预登记候选分别执行预先冻结的 QualificationPolicy，不能用候选间 OOS 排名决定赢家。

如果 OOS 结果、事后 regime 分析或失败案例被用于设计新版本，该区间对新版本必须追加：

```text
CONSUMED_AS_RESEARCH_INPUT
consumed_at
derived_experiment_ids
```

原版本已经生成的历史证据不会被改写，但新版本必须使用新的未来区间或进入 Shadow Test。

**通俗解释：** 测试集一旦被用于修改答案，就不再是未知测试集。

## 8. 结果产物

```text
experiment_spec.json
split_manifest.json
variant_search_results.csv
selected_config.json
fold_results.csv
oos_equity.csv
oos_trades.csv
oos_metrics.json
parameter_stability.json
ranking_stability.json
oos_evidence_table.csv
oos_consumption_ledger.json
experiment_manifest.json
```

至少报告 IS、Validation、OOS 的收益、Sharpe、最大回撤、公式明确的换手和执行成本，以及参数选择频率、排名稳定性、盈利 fold 比例和最差 fold。`oos_evidence_table.csv` 是按预登记候选展示的描述性证据，不得提供 selection rank 或 winner 标记。总体 OOS 净值来自连续 `WalkForwardAccountState`；禁止对各自从1开始的 fold 收益简单平均，也禁止仅做不携带持仓和现金状态的几何拼接。

## 9. 多重尝试与稳定性

首版记录 `number_of_tested_variants / parameter_space_size / selection_metric / selection_rank`，并检查最优参数邻域。若只有孤立参数表现极好而邻域很差，输出 `PARAMETER_INSTABILITY_WARNING`。

Deflated Sharpe Ratio 和 Probability of Backtest Overfitting 属于第二阶段；首版先完整保留全部尝试和 fold 结果，不能只保存胜出者。

## 10. 公平比较

`Validation Selection Ranking` 要求相同 PIT 快照、知识截止时间、评价日历、执行规则、成本、Validation fold 和 BenchmarkPolicy，可用于按预注册 SelectionPolicy 选择候选。Sealed OOS 只允许生成 `OOS Evidence Table`：可固定顺序或按非绩效身份字段展示，但不得产生选优排名。每个候选独立通过或不通过预先冻结的资格门禁。

## 11. 失败与降级

- Train、Validation、OOS 重叠：实验失败。
- OOS 不足104周：可运行但不能标记 OOS 合格。
- 参数空间运行后改变：生成新实验身份。
- fold 数据门禁失败：fold 无效，不得用零收益替代。
- 查看 Test 后调参：策略版本失去 OOS 资格。
- 使用 Sealed OOS 排名选择候选、淘汰候选或放宽门禁：实验 INVALID。
- Variant 使用不同日历或成本：不得排名。
- 独立基线失败：依赖它的增量归因不得发布。

## 12. 测试与验收

- 时间区间严格不重叠，OOS 不进入选择函数。
- 每个 fold 只使用 Test 开始前冻结参数。
- 相同输入生成相同选择，参数空间变化改变实验身份。
- OOS 不足时不能生成 `QUALIFIED_OOS_EVIDENCE`。
- 直接 ETF 动量不调用聚类模块。
- S0→S5 使用相同 Universe 和最终执行条件，Execution Ladder 不混入策略组件链。
- Fold 边界完整传递 cash、positions、cost basis、residual 和公司行为状态，新参数通过正常交易生效。
- Sealed OOS Evidence Table 不进入选择函数，也不产生 winner；候选身份变化会生成新实验。
- OOS 或 post-hoc regime 结论用于新版本时，消费记录可追溯到派生实验。
- 所有尝试均被记录，探索、Validation 和 OOS 报告明确分开。
- 至少一个非聚类正式基线完成相同契约下的 OOS 比较后，ResearchExperiment 才可生成 `QUALIFIED_OOS_EVIDENCE`，供后续冻结资格评估引用。
