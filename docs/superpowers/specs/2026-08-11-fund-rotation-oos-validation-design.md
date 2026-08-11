# 基金轮动独立基线与 OOS/Walk-forward 设计

**目标：** 将同区间探索性比较升级为可审计的 Train、Validation、untouched OOS 和 rolling walk-forward 研究框架，并用非聚类策略隔离聚类的增量价值。

## 1. 为什么修改

当前一个 Batch 内所有 Variant 共享一段评价区间，比较器在整段历史上计算并排名。若研究人员据此选择参数，再汇报同一段历史表现，就发生样本内污染。

**通俗解释：** 在同一份试卷上尝试100组答案，挑最高分后再用这张试卷证明能力。尝试越多，偶然高分概率越大。

当前两个正式策略又共享相关性聚类、Cluster Momentum 和 Top-N，只能比较持仓表达，不能证明聚类本身贡献 Alpha。

**不修改的后果：** 全历史 Sharpe 和年化收益可能被高估；策略排名易随起止日期反转；参数越多越容易过拟合；无法建立可信的冻结版本。

## 2. 三类基准

### 2.1 市场绩效基准

保留 `cash / equal_weight_etf / buy_hold_510300`，用于回答是否跑赢现金或市场。

### 2.2 独立 Alpha 基线

第一阶段新增三个不调用聚类模块的正式策略族：

1. `etf_absolute_momentum`：ETF 独立动量、正动量过滤、Top-N、固定 slot 等权。
2. `etf_dual_momentum`：相对排名加绝对门槛，不满足时留现金。
3. `etf_trend_momentum`：长期趋势门禁后做相对动量排名。

若不增加这些基线，系统无法判断复杂聚类是否只是重复直接 ETF 排名已经能够取得的收益。

### 2.3 组件消融链

```text
A：ETF 直接动量排名
B：A + 相关性聚类
C：B + 代表 ETF
D：C + ETF 质量选择
E：D + 组合风险层
```

只有 A→B 的差异才能近似回答聚类贡献。

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
    candidate_variants: tuple[VariantSpec, ...]
```

运行前必须声明假设、独立基线、参数空间、主要指标、淘汰条件、时间切分和 Variant 总数。禁止运行结束后更改主要指标或淘汰条件。

## 5. 时间切分

### 5.1 固定切分

```text
Train → Validation → Untouched OOS
```

最低门禁：OOS 至少占完整样本20%，且不少于104周。数据不足时可以研究，但不能升级为 `OOS_VALIDATED`。

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

## 6. 参数选择

默认主要指标为 `validation_sharpe`，同时声明最大回撤、最小交易数、数据质量和执行质量硬约束。Sharpe 相近时依次偏好较低回撤、较低换手、较低复杂度和稳定 `variant_key`。

当前同区间比较按年化收益排名；它继续保留但更名为 `Exploratory Comparison`，不能生成正式策略推荐。

## 7. OOS 泄漏防护

OOS 禁止参与参数搜索、ETF 规则研究、coverage 阈值、动量权重、策略淘汰、主要指标选择和成本校准。查看 OOS 后若修改核心逻辑，原 OOS 自动成为研究数据；新版本必须使用新的未来区间或进入 Shadow Test。

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
experiment_manifest.json
```

至少报告 IS、Validation、OOS 的收益、Sharpe、最大回撤、换手和执行成本，以及参数选择频率、排名稳定性、盈利 fold 比例和最差 fold。总体 OOS 净值由 Test fold 连续拼接，禁止对各自从1开始的 fold 收益简单平均。

## 9. 多重尝试与稳定性

首版记录 `number_of_tested_variants / parameter_space_size / selection_metric / selection_rank`，并检查最优参数邻域。若只有孤立参数表现极好而邻域很差，输出 `PARAMETER_INSTABILITY_WARNING`。

Deflated Sharpe Ratio 和 Probability of Backtest Overfitting 属于第二阶段；首版先完整保留全部尝试和 fold 结果，不能只保存胜出者。

## 10. 公平比较

正式排名要求相同 PIT 快照、评价日历、执行规则、成本、OOS fold 和市场基准。新增 `Validation Selection Ranking` 与 `OOS Evidence Ranking`；正式报告默认展示 OOS 榜单。

## 11. 失败与降级

- Train、Validation、OOS 重叠：实验失败。
- OOS 不足104周：可运行但不能标记 OOS 合格。
- 参数空间运行后改变：生成新实验身份。
- fold 数据门禁失败：fold 无效，不得用零收益替代。
- 查看 Test 后调参：策略版本失去 OOS 资格。
- Variant 使用不同日历或成本：不得排名。
- 独立基线失败：依赖它的增量归因不得发布。

## 12. 测试与验收

- 时间区间严格不重叠，OOS 不进入选择函数。
- 每个 fold 只使用 Test 开始前冻结参数。
- 相同输入生成相同选择，参数空间变化改变实验身份。
- OOS 不足时不能升级状态。
- 直接 ETF 动量不调用聚类模块。
- A→B→C 使用相同 Universe 和执行条件。
- OOS 拼接不发生 fold 净值重置错误。
- 所有尝试均被记录，探索、Validation 和 OOS 报告明确分开。
- 至少一个非聚类正式基线完成相同契约下的 OOS 比较后，研究层才可升级为 `OOS_VALIDATED`。
