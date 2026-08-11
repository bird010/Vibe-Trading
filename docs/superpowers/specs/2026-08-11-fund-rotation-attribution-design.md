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

真实账户只按实际成交价记账，并声明 `accounting_contract_version = daily_accounting_v1`。每日必须先复算资产负债表：

```text
Ending Cash
= Beginning Cash
+ Cash Income
+ Sell Quantity × Sell Executed Price
- Buy Quantity × Buy Executed Price
- Commission
- Other Explicit Fee

Ending NAV
= Ending Cash
+ Σ Ending Position Quantity × Ending Valuation Price
```

损益桥接再满足：

```text
Ending NAV - Beginning NAV
= Position And Trading P&L
+ Cash Income
- Commission
- Other Explicit Fee
```

`executed_price` 已包含相对参考价格的滑点，真实现金和 NAV 不再单独扣 `Slippage Cost`。公司行为调整份额和成本基础，但调整前后经济价值必须连续，份额调整本身不创造收益。

每只 ETF、每个交易日保存 begin quantity、begin/end price、market P&L、weight 和 P&L contribution。现金收益即使假设为零也显式记录。

### 3.1 DailyAccountingEventOrder

所有历史回测、Walk-forward 和 Shadow 账户使用同一日级事件顺序：

```text
1. 载入昨日收盘后的 Beginning Account State
2. 应用当日开盘前生效的 Corporate Action
3. 将昨日收盘价转换为当日份额单位下的 comparable prior close
4. 读取已封存目标，创建订单或因份额单位变化创建 replacement parent
5. 执行卖出 attempts/fills，并立即更新现金和显式费用
6. 执行买入 attempts/fills，并立即更新现金和显式费用
7. 使用当日收盘价或预声明的 stale-price policy 估值
8. 保存 Ending Account State
9. 计算 P&L components 和 reconciliation residual
```

费用随成交立即进入现金账，不得在收盘估值后再次结算。公司行为、成交和估值缺少可靠时间或价格时，按预声明 policy 降级或拒绝归因，不能由实现者自行调整顺序。

### 3.2 日级 P&L 公式

完成公司行为单位转换后，定义：

```text
qA：公司行为后、交易前数量
P0：转换到当日份额单位的昨日可比收盘价
Po：当日开盘价
Pc：当日收盘估值价，或按 stale-price policy 取得的估值价
Δqk：第 k 笔 signed fill，买入为正、卖出为负
Ek：第 k 笔实际成交价
Income：现金分红、利息等真正的现金收益
Fees：当日佣金和其他显式费用
```

```text
overnight_pnl = qA × (Po - P0)
holding_intraday_pnl = qA × (Pc - Po)
trade_day_pnl = Σ [Δqk × (Pc - Ek)]

daily_nav_change
= corporate_action_economic_effect
+ overnight_pnl
+ holding_intraday_pnl
+ trade_day_pnl
+ Income
- Fees
```

拆分、合并和份额折算等纯单位转换的 `corporate_action_economic_effect` 必须为零。`cash_in_lieu` 是零碎份额转换为现金的资产转换，不属于普通 `Income`；现金分红才进入 `Income`。若行情已经使用包含同一公司行为的复权口径，禁止再次确认同一收益。上述组件与真实 NAV 的差额进入 reconciliation residual，不得塞入 holding 或 other P&L。

当 `Po` 不可用但 `P0/Pc` 可验证时，不得伪造开盘价；只发布 `holding_close_to_close_pnl = qA × (Pc - P0)` 并将 overnight/intraday 拆分标记为 `DEGRADED_OPEN_PRICE_UNAVAILABLE`。`Pc` 也不可验证时，该资产当日归因 INVALID。

## 4. 执行拖累

真实账本直接核算：

```text
commission_drag
other_fee_drag
```

滑点按参考执行与实际执行之间的反事实机会成本核算：

```text
slippage_effect_return = actual_executed_account_return - reference_price_account_return
slippage_drag_return = reference_price_account_return - actual_executed_account_return
```

`effect` 保留带符号贡献，`drag` 正数表示拖累。它们解释实际成交价相对 reference/raw price 带来的差异，但不得再次进入真实现金扣减。

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

## 6. 两条固定消融链

### 6.1 Strategy Component Chain

```text
S0：Cash / 无 Alpha 基线
S1：Direct ETF Momentum
S2：S1 + Correlation Clustering
S3：S2 + Representative ETF
S4：S3 + ETF Quality Selection
S5：S4 + Portfolio Weighting
S6：S5 + Portfolio Risk Layer
```

S0→S6 共享相同的 execution contract/version。正式组件效果以可执行账户为主要口径，理论账户只作为机制解释辅助口径：

```text
executable_component_effect_n =
    Return(executable_equity(Sn)) - Return(executable_equity(Sn-1))

ideal_component_effect_n =
    Return(ideal_target_equity(Sn)) - Return(ideal_target_equity(Sn-1))

executable_weighting_effect =
    Return(executable_equity(S5)) - Return(executable_equity(S4))
executable_risk_effect =
    Return(executable_equity(S6)) - Return(executable_equity(S5))

ideal_weighting_effect =
    Return(ideal_target_equity(S5)) - Return(ideal_target_equity(S4))
ideal_risk_effect =
    Return(ideal_target_equity(S6)) - Return(ideal_target_equity(S5))
```

产物字段必须带 `ideal_` 或 `executable_` 前缀，禁止输出账户口径不明的 `Return(Sn)`。所有差分共享起止日期、初始 NAV 和复利口径。

### 6.2 Execution Ladder

对固定策略目标单独启用执行现实：

```text
X0：Reference-price ideal target
X1：+ Eligibility / price-limit / settlement hard rules
X2：+ Lot-size / tick-size rounding
X3：+ Capacity / participation / residual retry
X4：+ Spread / slippage / market impact
X5：+ Commission / taxes / explicit fees
```

```text
cumulative_execution_effect_n = Return(Xn) - Return(X0)
incremental_execution_effect_n = Return(Xn) - Return(Xn-1)
incremental_execution_drag_n = Return(Xn-1) - Return(Xn)
```

累计 effect 回答从理想到该层的总差异；增量 effect/drag 回答当前层新增的影响。市场规则和成本模型采用不同版本字段，不能把调整成本情景等同于放宽硬规则。

**为什么使用固定链：** 组件顺序会影响边际贡献，固定链能保证每次采用同一解释规则。

**限制：** 这些差值只是在指定消融顺序和共同契约下的边际效果，不是永恒不变的因果真相。

## 7. 反事实公平性

所有消融 Variant 必须共享 PIT Universe、快照、日历、信号日、规则、成本、初始资金、OOS fold 和随机种子。计算 clustering effect 时，S1 和 S2 只能在是否聚类上不同；S4 与 S5 只能改变 weighting policy；S5 与 S6 只能改变 risk policy。同时改变动量、权重或费用时拒绝归因。计算 X ladder 时策略目标必须固定，且 Xn 只能新增该层声明的执行现实。

## 8. 理论与可执行双净值

每个 Variant 保留：

```text
ideal_target_equity
executable_equity
```

理论账户假设按规定参考价格无摩擦实现目标；可执行账户经过交易规则、容量、滑点、费用和 residual retry。

```text
execution_effect_return = executable_return - ideal_target_return
execution_drag_return = ideal_target_return - executable_return
```

`effect` 是带符号贡献；`drag` 采用“正数表示拖累”的口径。两者不得混名，也不得把 drag 强制截断为非负，因为延迟成交偶尔可能改善结果。

拆分 execution drag 时严格按 X1→X5 顺序启用 hard rules、lot/tick rounding、capacity/residual、spread/slippage/impact 和 explicit fees。

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

如果事后状态、OOS 失败案例或分段表现被用于设计后续策略版本，对应区间必须追加 `CONSUMED_AS_RESEARCH_INPUT / consumed_at / derived_experiment_ids`。这不会改写原版本已经产生的证据，但后续版本不得继续把该区间声称为 untouched OOS。

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
- 日级事件严格按 `DailyAccountingEventOrder` 执行；费用随成交入账，不在收盘后重复结算。
- overnight、holding intraday、trade-day、Income、Fees 和 corporate-action effect 按固定公式复算并共同还原 NAV。
- 佣金和显式费用只从真实现金扣一次；滑点已包含在成交价中，只作为相对参考账户的机会成本归因。
- 阻塞产生 unintentional cash，动量门槛产生 intentional cash。
- S1 与 S2 只有聚类组件不同；S4/S5 只改变 weighting，S5/S6 只改变 risk；X ladder 固定策略目标，不公平反事实被拒绝。
- 每个策略组件同时输出明确区分的 ideal effect 和 executable effect，禁止使用账户口径不明的结果字段。
- theoretical 与 executable 差异可复算。
- `execution_effect_return` 与 `execution_drag_return` 符号相反，延迟成交有利时不会被强制改写为零。
- 回撤区间和恢复日期正确。
- 事后 regime 不能进入交易接口。
- 事后分析用于后续设计时生成 OOS 消费记录。
- OOS fold 归因不混入 Train/Validation。
- 残差超容差时拒绝发布。
- 正式报告能回答为什么赚、为什么亏、哪里无法执行。
