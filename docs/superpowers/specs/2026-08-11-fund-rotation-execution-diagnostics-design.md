# 基金轮动执行规则与诊断统计 v2 设计

**目标：** 建立唯一、可复算的执行事实模型，严格区分订单、尝试、成交和公司行为，并按 ETF 历史类型解析交易规则。

## 1. 当前问题与必要修正

当前 Runner 已在统计前排除 `CORPORATE_ACTION`，所以“公司行为污染当前主路径 fill rate”已经不是事实；但仍存在：

1. 遗留 `compute_execution_diagnostics(events)` 与 Runner 私有实现形成两套定义。
2. Runner 对每日 residual retry 的 `requested` 求和，得到 attempt-level fill rate，却使用模糊字段 `fill_rate`。
3. `blocked_order_count` 实际统计 blocked attempts。
4. 当前 `orders.csv` 按 attempt 展开并重复 parent 状态，不能直接按行汇总 parent order。
5. 基金轮动统一使用一个规则类；`ExecutionConfig.lot_size` 未传入规则实例，声明与行为不一致。

**通俗解释：** 一张1000股订单分两天各成交500股。按 parent order 看是100%完成；按每日尝试看是1000/1500=66.7%。两个数都有意义，但必须说明对象。

**不修改的后果：** 用户会误读成交能力，不同报表可能冲突，执行缺陷和策略缺陷无法分开，ETF 类型扩展后还会套用错误规则。

## 2. 执行事实模型

### 2.1 Parent Order

保存 `order_id / decision_id / ts_code / direction / created_date / original_requested_quantity / current_economic_quantity / cumulative_filled_quantity / remaining_quantity / status / completed_date / cancel_reason / reject_reason`。

状态限定为 `OPEN / PARTIALLY_FILLED / FILLED / CANCELED / EXPIRED / REJECTED`。`BLOCKED` 只描述某次 execution attempt，不能成为 parent order 状态；一次尝试被阻塞后，只要仍可重试，parent 保持 `OPEN` 或 `PARTIALLY_FILLED`。

`original_requested_quantity` 创建后不可变，是 parent fill rate 的固定分母。公司行为需要调整经济数量时只更新 `current_economic_quantity` 并保留独立 adjustment 记录。新决策改变目标时取消旧 parent，再创建新 parent，禁止原地改写原始请求量。

### 2.2 Execution Attempt

保存 `attempt_id / order_id / attempt_number / trade_date / requested_quantity / filled_quantity / unfilled_quantity / raw_price / executed_price / commission / explicit_fee / slippage_cost / participation_rate / status / reason_code`。

Attempt 状态限定为 `PENDING / FILLED / PARTIALLY_FILLED / BLOCKED / INVALID`。同一 parent order 可有多个 attempt；每个交易日最多一个 active attempt。

### 2.3 Executed Trade

只有 `filled_quantity > 0` 才形成 trade，保存 `trade_id / attempt_id / order_id / ts_code / direction / quantity / price / notional / commission / explicit_fee / slippage_cost / trade_date`。

### 2.4 Corporate Action

独立保存 `corporate_action_id / ts_code / action_type / effective_date / old_quantity / new_quantity / old_cost_basis / new_cost_basis / adjustment_factor`。它可以调整持仓，但不得进入订单数、成交数、fill rate 或 turnover。

## 3. Execution Ledger

```python
class ExecutionLedger:
    parent_orders: tuple[ParentOrderRecord, ...]
    attempts: tuple[ExecutionAttemptRecord, ...]
    trades: tuple[ExecutedTradeRecord, ...]
    corporate_actions: tuple[CorporateActionRecord, ...]
```

所有执行产物和指标都从 Ledger 生成，禁止直接对混合 `trade_events` 猜测实体类型。

### 3.1 真实资金记账契约

成交价 `executed_price` 已包含相对参考价格的滑点，因此现金账只按实际成交价、成交数量和显式费用更新：

```text
sell_cash_in = quantity × executed_price - commission - explicit_fee
buy_cash_out = quantity × executed_price + commission + explicit_fee
```

`slippage_cost` 是 `raw/reference_price` 与 `executed_price` 的机会成本诊断，不得再次从现金或真实 NAV 中扣减。Ledger 必须能分别复算现金、持仓市值和 NAV。

## 4. 指标定义

### 4.1 Parent-order 指标

输出 `order_count / fully_filled_order_count / partially_filled_order_count / open_order_count / canceled_order_count / expired_order_count / rejected_order_count`。不存在 `blocked_order_count`；阻塞只进入 attempt 指标。

```text
order_quantity_fill_rate =
    Σ 每个 order 的最终累计成交数量
    ÷ Σ 每个 order 的原始请求数量
```

计算前必须按 `order_id` 聚合，不能对 attempt 展开行求和。

### 4.2 Attempt 指标

输出 `attempt_count / filled_attempt_count / partial_attempt_count / blocked_attempt_count / attempt_quantity_fill_rate / blocked_attempt_rate`。

```text
attempt_quantity_fill_rate =
    Σ attempt.filled_quantity
    ÷ Σ attempt.requested_quantity
```

### 4.3 Trade 与成本指标

输出 `executed_trade_count / buy_trade_count / sell_trade_count / total_notional / buy_notional / sell_notional / commission / explicit_fee / slippage_opportunity_cost`，并给出各成本相对 average NAV 的比例。`explicit_cash_cost = commission + explicit_fee` 会进入真实现金账；`slippage_opportunity_cost = reference_price_account_pnl - actual_executed_account_pnl`，正数表示相对参考账户的拖累。废弃含义混合的裸 `total_execution_cost`；需要合计时必须明确是现金成本还是包含机会成本的执行拖累估计。

禁止输出未注明公式的裸 `turnover`。首版固定输出：

```text
gross_traded_notional_ratio =
    (buy_notional + sell_notional) ÷ average_portfolio_nav

one_way_turnover =
    0.5 × (buy_notional + sell_notional) ÷ average_portfolio_nav

annualized_one_way_turnover =
    one_way_turnover × annualization_factor_from_actual_evaluation_days
```

该契约明确选择 half-gross 定义，不使用另一种常见的 `min(buy_notional, sell_notional)` 定义。年化因子按真实评价天数计算，不能假设完整一年。

### 4.4 公司行为指标

只输出 `corporate_action_count / share_adjustment_count / adjusted_position_count`，仅用于审计。

## 5. 指标接口与契约版本

```python
compute_order_diagnostics(ledger)
compute_attempt_diagnostics(ledger)
compute_trade_diagnostics(ledger, nav)
compute_corporate_action_diagnostics(ledger)
compute_execution_diagnostics_v2(ledger, nav)
```

正式产物写入 `metric_contract_version = execution_diagnostics_v2`。遗留函数不再是正式入口；迁移期只允许生成 legacy 对照。

## 6. 市场硬规则与执行成本分离

### 6.1 MarketRuleResolver

```python
class MarketRuleResolver:
    def resolve(
        self,
        instrument: FundInstrumentVersion,
        trade_date: str,
        knowledge_cutoff: str,
    ) -> MarketRules:
        ...
```

初始类别为 `domestic_equity_etf / bond_etf / commodity_etf / cross_border_etf / money_market_etf / other`。每类明确 settlement、lot、tick、price limit 和 short 等不可绕过的市场约束；分类和规则都按 `trade_date + knowledge_cutoff` 从 PIT、带版本的规则数据解析。

规则优先级：交易所和法律硬约束高于 PIT 产品规则，高于研究运行配置。未知类型返回 `UNKNOWN_EXECUTION_RULE` 并拒绝执行，不能静默退回国内股票 ETF 默认值。

### 6.2 ExecutionCostModel

```python
class ExecutionCostModel:
    def estimate(
        self,
        order: ParentOrderRecord,
        market: MarketObservation,
        scenario: CostScenario,
    ) -> ExecutionCostEstimate:
        ...
```

成本模型只负责 commission、fees、spread、slippage 和 market impact。Participation rate 是容量/冲击模型的输入和约束，不是市场硬规则。规则版本、成本模型版本和成本情景分别进入运行身份，不能通过调整成本情景绕过市场硬约束。

## 7. 数据流

```text
目标权重决策
→ Parent Order Manager
→ PIT 市场硬规则解析
→ Execution Attempt
   ├─ 未完成：保留 residual
   └─ 已成交：Executed Trade → 更新现金和持仓

Corporate Action → 独立调整持仓 → 不进入交易统计
```

## 8. 失败与降级

- attempt 找不到 parent、attempt 重复、累计成交超限、FILLED 后继续尝试：运行失败。
- 公司行为进入 trade ledger：运行失败。
- ETF 类型或规则无法解析：该产品不执行并记录稳定原因码。
- 成本、价格或数量非有限：attempt INVALID，不能默认为零。
- 规则版本缺失：子运行 `EXECUTION_CONTRACT_INVALID`。
- 资金账再次扣除已经包含在成交价中的滑点：对账失败。

## 9. 兼容迁移

并行产出 `legacy_execution_diagnostics / execution_diagnostics_v2 / diagnostics_difference`。前端按“订单、尝试、成交、成本、公司行为”分组；废弃裸 `fill_rate` 和裸 `turnover`，将旧 `blocked_order_count` 映射为 `blocked_attempt_count`，旧 `trade_count` 映射为 `executed_trade_count`。历史产物保留契约版本，不得与 v2 直接排名。

## 10. 测试

- 一次完全成交：order 和 attempt fill rate 均100%。
- 两日完成1000股：order fill rate 100%，attempt fill rate 66.7%。
- 连续三日阻塞：一张保持 OPEN 的 parent、三次 blocked attempt；到期后 parent 才进入 EXPIRED。
- 部分成交后取消；新决策取消旧 residual。
- `original_requested_quantity` 不因 residual、公司行为或新决策而被覆盖；公司行为调整 `current_economic_quantity` 并保留调整记录。
- 公司行为不改变订单、成交和换手指标，但正确调整持仓。
- 多日成交产生多笔 trade，但只属于一个 parent。
- `lot_size` 等 PIT 市场规则按版本生效；研究配置只能施加更严格的约束，不能放宽交易所硬规则。
- 不同 ETF 类型解析不同规则，未知类型拒绝执行。
- 相同成交使用 executed price 更新现金时，slippage 只进入诊断，真实 NAV 不重复扣减。
- 市场规则和成本情景分别版本化；改变成本情景不会改变 lot、tick、settlement 或 price limit。
- legacy 与 v2 差异可稳定复现。

## 11. 验收

- 所有指标名称唯一对应一种实体，并可从 Ledger 复算。
- 公司行为不进入订单、尝试、成交或换手指标。
- parent 与 attempt 在 residual 场景下输出不同但正确的结果。
- Parent 不存在 BLOCKED 状态，所有阻塞均可追溯到具体 attempt。
- 换手指标均使用明确名称、固定公式和 average NAV 分母。
- `ExecutionConfig` 不存在声明可配置但实际无效的字段。
- 每笔成交可追溯到决策、parent、attempt 和规则版本。
- 只有 v2 契约可以进入正式研究报告。
