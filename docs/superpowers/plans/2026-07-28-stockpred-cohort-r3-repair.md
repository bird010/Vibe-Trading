# StockPred Cohort R3 修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 Cohort 回测的执行价格、固定期限估值、清算估值、数据质量、统一入口和前端图表闭环。

**Architecture:** 执行层与复权标签层分离：真实成交统一使用原始人民币价格，复权价格仅提供收益率。引擎用不可变入场成本和退出前快照生成三套收益，再通过统一的版本化产物/API 提供给前端。

**Tech Stack:** Python 3.10、pandas、pytest、FastAPI、React、TypeScript、Vitest。

## Global Constraints

- 执行成交、涨跌停判断、ADV、费用和现金全部使用未复权人民币价格。
- 复权价格只用于 raw label、固定期限收益率和相对收益比较，不与真实股数直接相乘。
- Horizon 在任何退出前冻结，使用目标退出日复权开盘收益率。
- 残仓终值必须扣预计卖出费用和流动性折价。
- 复权覆盖不足、行情截断和未知 PIT 依赖必须 fail-closed。
- 数据失败收益为 null，不能伪造成 0 或从覆盖率分母消失。
- 必须遵循 RED → GREEN → REFACTOR；禁止先改生产代码再补测试。

---

### Task 1: 核心价格、估值与清算匹配基准

**Files:**
- Modify: `agent/backtest/stockpred/cohort/engine.py`
- Modify: `agent/backtest/stockpred/cohort/ledger.py`
- Modify: `agent/backtest/stockpred/cohort/metrics.py`
- Modify: `agent/backtest/stockpred/execution/policy.py`
- Modify: `agent/backtest/stockpred/execution/valuation.py`
- Test: `agent/tests/stockpred/test_cohort_engine.py`
- Test: `agent/tests/stockpred/test_cohort_benchmark.py`
- Test: `agent/tests/stockpred/test_execution_policy.py`
- Test: `agent/tests/stockpred/test_execution_valuation.py`

**Interfaces:**
- Consumes: `apply_qfq(prices, adjustment_factors)`, `ValuationPolicy.horizon_mark()`, `ValuationPolicy.terminal_value()`.
- Produces: 原始价格执行事件、不可变初始成本、目标日 horizon value、残仓 terminal value、按初始本金比例生成的 `ExitEvent`。

- [ ] **Step 1: 写入并运行 RED 测试**

增加独立测试，至少断言：

```python
def test_full_exit_benchmark_weight_uses_entry_cost():
    # 100 股以 10 元买入、20 元全额卖出、C=10000
    # ExitEvent.proportion 必须是 0.1，而不是 0.2
    ...

def test_execution_uses_raw_open_when_adj_open_exists():
    # open=10、adj_open=5、up_limit=11
    # 成交价必须为 10，数量和费用也以 10 为基准
    ...

def test_adjustment_factor_missing_does_not_fill_from_another_stock():
    # A 无因子、B 因子为 2；A 的 adj_open 必须为 NaN/质量失败
    ...

def test_horizon_uses_adjusted_open_return_on_original_notional():
    # 入场原始名义金额 1000；复权开盘从 5 到 6；horizon 仓位价值为 1200
    ...

def test_unliquidated_terminal_value_deducts_sell_cost_and_haircut():
    ...
```

运行：

```powershell
E:\anaconda3\envs\AlphaFin\python.exe -m pytest agent/tests/stockpred/test_cohort_engine.py agent/tests/stockpred/test_cohort_benchmark.py agent/tests/stockpred/test_execution_policy.py agent/tests/stockpred/test_execution_valuation.py -q
```

预期：新增测试因当前退出价回退、复权填充、执行使用 `adj_open`、horizon 使用 `adj_close`、残仓按成本估值而失败。

- [ ] **Step 2: 最小实现**

实现要求：

```python
# execution/policy.py
open_price = pd.to_numeric(row.get("open"), errors="coerce")

# engine.py
market = apply_qfq(raw, factors)
# 不 ffill/bfill，不静默覆盖缺失；保留 adj_factor_missing。

# 退出前冻结不可变成本
entry_price = ledger.initial_entry_price(code)
proportion = e.executed_quantity * entry_price / committed_capital
ledger.apply_exit(e)

# horizon
position_value = original_entry_notional * target_adj_open / entry_adj_open

# liquidation
terminal = valuation_policy.terminal_value(...)
```

为 ledger 增加生产需要的不可变初始成本读取接口，不增加仅供测试使用的方法。残仓 terminal value 显式传给 `compute_cohort_result()`；残仓初始本金比例在终止估值日补充 `ExitEvent(is_terminal=True)`。

- [ ] **Step 3: 运行 GREEN 与回归**

运行 Step 1 测试及：

```powershell
E:\anaconda3\envs\AlphaFin\python.exe -m pytest agent/tests/stockpred/test_cohort_metrics.py agent/tests/stockpred/test_cohort_ledger.py agent/tests/stockpred/test_cohort_integration.py -q
```

预期：全部通过。

- [ ] **Step 4: 自审并提交**

检查没有自定义复权填充、没有复权价进入 ExecutionPolicy、没有在 `apply_exit()` 后读取可删除成本。

```powershell
git add agent/backtest/stockpred agent/tests/stockpred
git commit -m "fix(cohort): correct execution and valuation semantics"
```

### Task 2: Eligibility、失败语义、事件不变量与统一编排

**Files:**
- Modify: `agent/backtest/stockpred/cohort/eligibility.py`
- Modify: `agent/backtest/stockpred/cohort/contracts.py`
- Modify: `agent/backtest/stockpred/cohort/engine.py`
- Modify: `agent/backtest/stockpred/cohort/ledger.py`
- Modify: `agent/backtest/stockpred/execution/adv.py`
- Modify: `agent/backtest/stockpred/execution/policy.py`
- Modify: `agent/src/stockpred/batch_screening.py`
- Modify: `agent/src/stockpred/strategy_execution.py`
- Modify: `agent/backtest/stockpred/cohort/pit_assurance.py`
- Test: `agent/tests/stockpred/test_cohort_engine.py`
- Test: `agent/tests/stockpred/test_cohort_ledger.py`
- Test: `agent/tests/stockpred/test_batch_screening.py`
- Test: `agent/tests/stockpred/test_pit_assurance.py`

**Interfaces:**
- Consumes: 信号日原始行情、复权质量、市场交易日历、策略数据依赖。
- Produces: fail-closed `EligibilityResult`、可空收益字段、完整 `ExecutionEvent`、process/in-process 一致的 Cohort 结果。

- [ ] **Step 1: 写入并运行 RED 测试**

至少覆盖：

```python
def test_suspended_candidate_is_not_frozen_into_signal_snapshot(): ...
def test_all_rejected_date_is_counted_as_auditable_cohort(): ...
def test_truncated_horizon_is_failed_data_with_null_returns(): ...
def test_fully_rejected_order_reduces_fill_rate(): ...
def test_cross_cohort_or_oversell_event_fails_execution(): ...
def test_in_process_cohort_request_uses_cohort_runner(): ...
def test_unknown_pit_dependency_is_snapshot_only(): ...
```

运行对应单测，确认均因当前缺失行为而失败。

- [ ] **Step 2: 最小实现**

- Eligibility 接收信号日行情和复权缺失标记，检查 `list_date`、`delist_date`、`list_status`、ST、停牌和 98% 覆盖。
- `CohortResult` 的失败收益字段允许 `None`；集中构造 `FAILED_DATA`，所有计划日进入 `total_cohort_count`。
- `is_truncated` 或目标/延长期行情不足直接 `FAILED_DATA`。
- `ExecutionEvent` 保存原始 requested quantity/value 和非空 cohort_id；账本对错 cohort、方向、重复及超卖失败为 `FAILED_EXECUTION`。
- ADV 以市场日历重建窗口：确认停牌补 0，未知缺失 fail-closed。
- 抽取 process/in-process 共用的单策略 Cohort helper。
- `req.json` 使用 `atomic_json`。
- 策略 Adapter 提供依赖列表；unknown 依赖返回 `snapshot_only`。

- [ ] **Step 3: 运行 GREEN、Ruff 与回归**

```powershell
E:\anaconda3\envs\AlphaFin\python.exe -m pytest agent/tests/stockpred/test_cohort_engine.py agent/tests/stockpred/test_cohort_ledger.py agent/tests/stockpred/test_batch_screening.py agent/tests/stockpred/test_pit_assurance.py -q
ruff check --no-cache agent/backtest/stockpred agent/src/stockpred agent/tests/stockpred
```

预期：新增测试通过；本任务触及文件没有 Ruff 错误。

- [ ] **Step 4: 提交**

```powershell
git add agent/backtest/stockpred agent/src/stockpred agent/tests/stockpred
git commit -m "fix(cohort): enforce data quality and unified routing"
```

### Task 3: Chart 产物、API 与前端报告闭环

**Files:**
- Modify: `agent/backtest/stockpred/cohort/engine.py`
- Modify: `agent/backtest/stockpred/cohort/chart_bundle.py`
- Modify: `agent/backtest/stockpred/cohort/artifacts.py`
- Modify: `agent/src/stockpred/artifact_resolver.py`
- Modify: `agent/src/api/stockpred_routes.py`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/components/stockpred/CohortStockPredReport.tsx`
- Modify: `frontend/src/pages/RunDetail.tsx`
- Test: `agent/tests/stockpred/test_chart_bundle.py`
- Test: `agent/tests/stockpred/test_artifact_resolver.py`
- Test: `frontend/src/components/stockpred/__tests__/CohortStockPredReport.test.tsx`
- Test: `frontend/src/pages/__tests__/RunDetail.test.tsx`

**Interfaces:**
- Produces: 全量股票 chart manifest、股票列表 API、受校验的 chart API、时期稳定性 API、个股 K 线/买卖点 Tab、Legacy 显式报告。

- [ ] **Step 1: 写入并运行 RED 测试**

至少断言：

```python
def test_chart_bundle_contains_all_signal_codes_and_extended_exit_dates(): ...
def test_chart_endpoint_rejects_path_outside_version_dir(): ...
def test_chart_endpoint_rejects_sha256_mismatch(): ...
def test_chart_json_sanitizes_non_finite_values(): ...
```

前端至少断言：

```typescript
it("loads symbol list and renders candlestick with cohort order markers", async () => { ... })
it("routes legacy schema through LegacyStockPredReport", async () => { ... })
it("renders year and quarter stability rows", async () => { ... })
```

先运行并确认因功能缺失而失败。

- [ ] **Step 2: 最小实现**

- `all_codes` 来自过滤后的全量信号，不限 Top-N。
- chart 范围使用 `data_start/data_end`，manifest 即使空仓也合法。
- Resolver 校验 version ID、schema、manifest SHA-256、文件相对路径、文件 SHA-256 和必需字段。
- 所有 Cohort JSON 返回统一递归清理 NaN/Inf。
- 增加 symbols 与 period breakdown API。
- Cohort 页面增加“个股”Tab，调用 symbol/chart API 并复用现有 CandlestickChart；订单按 cohort_id 可筛选。
- legacy schema 使用 `LegacyStockPredReport`；未知 schema 显示明确错误。

- [ ] **Step 3: 运行 GREEN 与回归**

```powershell
E:\anaconda3\envs\AlphaFin\python.exe -m pytest agent/tests/stockpred/test_chart_bundle.py agent/tests/stockpred/test_artifact_resolver.py agent/tests/stockpred/test_cohort_artifacts.py -q
Set-Location frontend
npx tsc --noEmit -p tsconfig.json --pretty false
npm run test:run -- --reporter=dot
```

预期：新增测试通过；若仍有分支基线失败，报告其是否与本任务有关。

- [ ] **Step 4: 提交**

```powershell
git add agent frontend
git commit -m "feat(cohort): complete chart and report delivery"
```

