# 基金轮动收益归因与市场状态分析设计

**目标：** 在可对账前提下回答策略为什么赚、为什么亏、哪一层贡献收益、执行拖累多少，以及收益集中在哪些时间和市场环境。

## 1. 为什么修改

当前已经保存净值、交易、持仓、簇历史、代表 ETF、排除记录和执行诊断，但尚未把收益拆成各决策环节贡献。

**通俗解释：** 公司赚了100万元，不等于知道是产品卖得好、成本降低还是一次性运气。没有拆账，就不知道下一年该保留什么。

**不修改的后果：** 无法证明聚类价值、无法区分风险退出与被迫空仓、无法定位回撤来源，策略升级只能比较最终净值，OOS 失败时也不知道应该改信号、组合还是执行。

## 2. 两套互补归因

### 2.1 真实账本归因

回答实际账户的钱如何变化，基于真实持仓、现金、成交和公司行为进行损益核算，属于会计事实。

### 2.2 策略组件归因

回答只增加或移除某组件后结果如何变化，通过相同数据和执行契约下的反事实 Variant 估计，属于受实验设计约束的边际效果。

两者不得混用或共同塞进一个含义模糊的 `alpha` 字段。

## 3. 真实账本恒等式

每日必须满足：

```text
Ending NAV
= Beginning NAV
+ Position P&L
+ Cash Income
- Commission
- Slippage Cost
- Other Explicit Cost
```

公司行为调整份额和成本基础，但调整前后经济价值必须连续，份额调整本身不创造收益。

每只 ETF、每个交易日保存 begin quantity、begin/end price、market P&L、weight 和 P&L contribution。现金收益即使假设为零也显式记录。

## 4. 执行拖累

直接核算：

```text
commission_drag
slippage_drag
other_fee_drag
```

通过反事实执行计算：

```text
capacity_delay_effect
blocked_attempt_effect
lot_rounding_effect
```

容量和阻塞不是直接费用，不能全部称为滑点。

## 5. 对账门禁

```text
attribution_residual =
    actual_nav_change - sum(attributed_components)
```

每日和全区间均计算残差。超过数值容差时输出 `ATTRIBUTION_RECONCILIATION_FAILED`，正式归因报告拒绝发布；禁止把残差强制塞入“其他”。

## 6. 固定组件消融链

```text
A：Direct ETF Momentum
B：A + Correlation Clustering
C：B + Representative ETF
D：C + ETF Quality Selection
E：D + Portfolio Weighting
F：E + Portfolio Risk Layer
G：F + Execution Constraints
```

边际效果定义为相邻版本收益差，例如 `clustering_effect = Return(B) - Return(A)`，依次得到 representative、quality、weighting、risk 和 execution effect。

**为什么使用固定链：** 组件顺序会影响边际贡献，固定链能保证每次采用同一解释规则。

**限制：** 这些差值只是在指定消融顺序和共同契约下的边际效果，不是永恒不变的因果真相。

## 7. 反事实公平性

所有消融 Variant 必须共享 PIT Universe、快照、日历、信号日、规则、成本、初始资金、OOS fold 和随机种子。计算 clustering effect 时，A 和 B 只能在是否聚类上不同；同时改变动量、权重或费用时拒绝归因。

## 8. 理论与可执行双净值

每个 Variant 保留：

```text
ideal_target_equity
executable_equity
```

理论账户假设按规定参考价格无摩擦实现目标；可执行账户经过交易规则、容量、滑点、费用和 residual retry。

```text
total_execution_drag = executable_return - ideal_target_return
```

拆分 execution drag 时逐层启用费用、容量、阻塞和零股约束。

**为什么改：** 信号可能正确，但产品流动性或规则使其无法实现。只看最终净值会误把执行问题当成 Alpha 失效。

## 9. 现金归因

区分：

```text
intentional_cash：动量门槛、风险状态、vol target、主动配置
unintentional_cash：代表缺失、数据门禁、订单阻塞、容量、零股、执行失败
```

主动降风险和被迫买不进去不是同一种策略行为；不区分会把执行缺陷误报成风控效果。

## 10. 回撤归因

对主要回撤输出 peak、trough、recovery、max drawdown，并拆分 asset selection、cluster selection、weighting、risk exposure、execution drag 和 cash effect；同时列出亏损贡献最大的 ETF、Cluster、日期、交易和阻塞原因。

全年收益为正也可能包含无法承受的集中回撤，因此年度归因不能替代回撤区间分析。

## 11. 市场状态

### 11.1 事后诊断状态

`Bull / Bear / Sideways / High Vol / Low Vol` 可使用完整区间稳定分类，但必须标记 `POST_HOC_ANALYTICS_ONLY`，不得驱动交易。

### 11.2 可交易状态

进入风险层的状态必须只使用信号日可知数据，标记 `CAUSAL_TRADING_REGIME`。两者使用不同字段和产物。

每种状态报告天数、收益、波动率、Sharpe、最大回撤、换手、成本、hit rate 和组件贡献。

**不修改的后果：** 全区间 Sharpe 会隐藏策略只在牛市有效、在震荡或熊市持续失效的结构。

## 12. 时间集中度

输出年度、季度、月度归因及 best year、best 5 periods、worst 5 periods 的贡献占比。大部分收益集中于极少数时期则输出 `RETURN_CONCENTRATION_WARNING`；阈值必须预先声明。

## 13. 数据模型与产物

```python
class AttributionResult:
    reconciliation: ReconciliationResult
    pnl_components: tuple[PnLComponent, ...]
    strategy_components: tuple[ComponentEffect, ...]
    execution_components: tuple[ExecutionEffect, ...]
    drawdown_episodes: tuple[DrawdownAttribution, ...]
    regime_results: tuple[RegimeAttribution, ...]
    concentration_metrics: dict[str, float]
    quality_status: str
```

产物：

```text
attribution_summary.json
daily_pnl_attribution.csv
component_attribution.csv
execution_attribution.csv
cash_attribution.csv
drawdown_attribution.csv
regime_attribution.csv
period_attribution.csv
attribution_reconciliation.json
attribution_manifest.json
```

报告首页同时展示实际总收益、可解释收益合计、归因残差和质量状态，不能只展示正贡献。

## 14. 失败与降级

- 账本无法对账：归因失败。
- 反事实使用不同快照或日历：组件归因失败。
- 消融基线失败：依赖它的后续贡献不发布。
- 公司行为价值不连续：账本归因失败。
- 理论与执行账户契约不一致：execution drag 不发布。
- Regime 样本过少：标记不可比较。
- 事后状态用于交易：研究结果 INVALID。

## 15. 测试与验收

- 每日损益归因还原 NAV；公司行为前后经济价值连续。
- 佣金和滑点只进入执行拖累。
- 阻塞产生 unintentional cash，动量门槛产生 intentional cash。
- A 与 B 只有聚类组件不同，不公平反事实被拒绝。
- theoretical 与 executable 差异可复算。
- 回撤区间和恢复日期正确。
- 事后 regime 不能进入交易接口。
- OOS fold 归因不混入 Train/Validation。
- 残差超容差时拒绝发布。
- 正式报告能回答为什么赚、为什么亏、哪里无法执行。
