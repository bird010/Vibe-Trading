# 基金轮动信号、组合与风险层设计

**目标：** 保留当前简单策略作为永久基线，将信号质量、Cluster 选择、产品选择、组合权重和总风险敞口拆成可独立测试、消融和关闭的模块。

## 1. 分层原则

```text
信号质量 → Cluster 选择 → 产品选择 → 组合权重 → 总风险敞口
```

**通俗解释：** 五种药一起吃后病情好转，无法知道哪种有效、哪种有副作用。策略增强也必须逐项加入。

**不拆层的后果：** 无法归因；参数快速膨胀；OOS 失败时不知道撤销哪一项；风险控制会被误认为 Alpha；优化器成为新的过拟合来源。

## 2. Cluster Momentum 覆盖率门禁

每个簇、每周计算：

```text
valid_member_count
eligible_member_count
coverage_ratio = valid / eligible
```

分母只包含该周历史上合格的成员。一个动量窗口输出 `min_weekly_coverage / mean_weekly_coverage / low_coverage_week_count / coverage_distribution`。

```python
class ClusterCoveragePolicy:
    min_weekly_coverage: float
    max_low_coverage_weeks: int
    minimum_valid_members: int
```

阈值在 Train 研究、Validation 选择、OOS 前冻结，不能直接写死70%。覆盖不足时 momentum unavailable，原因码为 `INSUFFICIENT_CLUSTER_COVERAGE`；禁止用零或唯一有效 ETF 代替。

**为什么改：** 一个20成员簇只有1只 ETF 有数据时，1只的收益不能可靠代表整个方向。

**不改的后果：** 簇指数在不同周含义变化，缺失模式可能制造虚假强动量。

## 3. Momentum Family 与 Ensemble

永久保留当前 `single_window_momentum`。新增候选 `1M / 3M / 6M / 12M / 6-1 / 12-1 / risk-adjusted momentum`。

首版支持：

1. 排名聚合：各窗口独立排名后计算平均排名，推荐默认候选。
2. 标准化加权：`Σ weight_i × zscore(momentum_i)`，权重之和为1并进入冻结配置。

风险调整动量必须处理零、缺失和非有限波动率，不能产生无限分数。

**为什么改：** 当前4周信号易受短期噪声或反转影响，多周期用于检验趋势一致性。

**不改的后果：** 周频切换频繁，策略可能依赖偶然有效的短窗口。

多周期不预设一定更好，必须进入相同 OOS 框架。

## 4. Cluster Selection Hysteresis

引入双门槛：

```text
Entry：进入 Top-N 才新建仓
Exit：跌出 Top-(N + buffer) 才退出
```

支持 `minimum_holding_weeks / minimum_score_improvement / expected_benefit_over_cost`。顺序为强制退出检查、保留 buffer 内持仓、计算空 slot、选择 Entry 候选、检查改善与成本后决定换仓。

**为什么改：** 排名第3和第4通常没有实质差异，小波动不应立即变成交易。

**不改的后果：** 佣金、滑点和容量损失增加，策略依赖不现实低成本假设。

PIT 资格失效、产品终止、数据 INVALID 和风险硬门禁可以绕过最短持有期。

## 5. 代表 ETF 质量模型

Cluster 选择决定方向，ETF Quality 决定用什么产品表达，质量分数不得改变 Cluster Momentum。

第一阶段使用 `cluster_representativeness / ADV liquidity / listing_age`；PIT 数据具备后增加 tracking error、费率、规模、价差、折溢价波动和跟踪连续性。保存 representativeness、liquidity、cost、tracking、stability 分项原值与排名。

**为什么改：** 相关性高、成交额大的 ETF 仍可能费率高、跟踪差或规模不稳定。

**不改的后果：** 资产方向判断正确，但实际产品长期拖累收益且无法解释。

保留代表 lock。Hard Failure 立即更换；Quality Deterioration 只有连续多个评价期低于退出门槛才更换，防止小幅分数变化导致换品。

## 6. 组合权重

永久保留 Equal Weight。第一阶段只增加 Inverse Volatility，第二阶段再研究 Risk Parity；首版不引入均值—方差优化。

约束包括 `max_etf_weight / max_cluster_weight / max_asset_class_weight / minimum_cash_weight / maximum_turnover_per_rebalance`。权重只使用信号日前数据。

**为什么改：** 名义等权不等于风险等权，高波动资产可能主导组合回撤。

**不改的后果：** 相同 Top-N 在不同环境承担完全不同风险。

优化模型必须与等权进行相同 OOS fold 比较，并报告集中度和换手。

## 7. 独立 Portfolio Risk Layer

风险层只决定总敞口，不修改 Alpha 排名：

```text
selected_assets + raw_target_weights
→ volatility/regime policy
→ gross_exposure + scaled_weights + cash + reason_codes
```

Volatility Target 使用 `target_volatility / estimated_portfolio_volatility`，并限制在预先声明的最小和最大敞口；long-only 无杠杆时最大不超过1。

首版可交易状态只支持可解释的 `RISK_ON / NEUTRAL / RISK_OFF`，映射固定最大敞口。阈值在 Train/Validation 确定，禁止查看 OOS 后修改。

**为什么改：** 正动量决定方向，但不能保证高低波动时期承担稳定风险。

**不改的后果：** 高波动期风险被动放大，无法区分 Alpha 失效和仓位过高。

Regime 不得混入 momentum score，否则无法归因。

## 8. 统一决策流水线

```text
raw_signal_scores
→ coverage_filtered_scores
→ selected_clusters
→ selected_representatives
→ raw_portfolio_weights
→ risk_scaled_weights
→ execution_targets
```

每阶段保存 input、output、policy version 和 reason codes。配置拆为 signal、coverage、selection、representative quality、portfolio、risk 六块；任一变化生成新策略版本。

## 9. 失败与降级

- coverage 不足：簇信号不可用。
- 全部不可用：留现金并标记数据原因。
- 代表无候选：对应 slot 留现金。
- 质量字段缺失：按预先声明降级，不得默认为最好。
- 波动率无法估计：退回预先声明的基线或保守敞口。
- 风险状态不可用：输出 `RISK_STATE_UNAVAILABLE` 并采用保守敞口。
- 权重约束不可满足：决策 INVALID，不能静默突破约束。

## 10. 测试与验收

- 20个成员只有1个有效时 coverage gate 拒绝；未上市成员不进分母。
- 多周期排名可重复，零波动不产生无限分数。
- 持仓位于 Exit Buffer 内时不换仓；强制失效可绕过持有期。
- 分数改善不足或收益不覆盖成本时不交易。
- ETF Quality 不改变 Cluster Momentum，小幅质量变化不突破 lock。
- 等权、逆波动使用同一选中资产并满足全部约束。
- Volatility Target 只缩放敞口，Regime 不改写 Alpha score。
- 每项增强有独立消融 Variant，可单独关闭。
- 当前单窗口、固定等权策略始终可运行，作为永久基线。
