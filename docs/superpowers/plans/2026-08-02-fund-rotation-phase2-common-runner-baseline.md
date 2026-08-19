# Phase 2：公共 Runner 与基准策略迁移实施计划

> **执行要求：** 使用 `executing-plans` 技能实施。先提取无行为变化的公共能力，再让 baseline 策略驱动它；公开 API 仍不切换。

**目标：** 从现有大 pipeline 中分离数据访问、策略决策、目标调度、执行、估值、指标和产物发布，使公共 Runner 不知道具体策略算法。

**架构：** `FundRotationBacktestRunner` 驱动 `FundRotationStrategySession`；`CausalDataView` 是策略唯一数据入口；现有 ETF 执行规则和组合目标权重执行器被提取为公共执行模块。

**技术栈：** Python、pandas、pytest。

---

## Task 1：实现受控因果数据视图

**文件：**

- 新建：`agent/backtest/fund_rotation/causal_data.py`
- 新建：`agent/tests/fund_rotation/test_causal_data.py`

**接口：**

```python
class CausalDataView:
    def daily_bars(self, fields: Sequence[str], lookback: int | None = None) -> pd.DataFrame: ...
    def adjusted_closes(self, lookback: int | None = None) -> pd.DataFrame: ...
    def returns(self, frequency: Literal["daily", "weekly", "monthly"], lookback: int) -> pd.DataFrame: ...
    def causal_adv(self, lookback_days: int = 20) -> pd.Series: ...
    def fund_adjustments(self, lookback: int | None = None) -> pd.DataFrame: ...
    def eligible_universe(self) -> tuple[FundInstrument, ...]: ...
    def trading_calendar(self, lookback: int | None = None) -> tuple[pd.Timestamp, ...]: ...
    @property
    def signal_date(self) -> pd.Timestamp: ...
```

**步骤：**

1. 写失败测试：请求未声明数据集/字段、超过声明回看长度、`signal_date` 之后日期、非快照 ETF 均抛 `UNDECLARED_STRATEGY_DATA_ACCESS`。
2. 用“只向策略暴露声明列的只读副本/视图”实现强制机制，不依赖约定或仅记录日志。
3. 禁止返回底层可变 DataFrame 引用；修改返回值不能污染其他子运行。
4. 增加访问审计，记录字段、日期范围和行数，不记录整表。
5. 测试两个 session 读取/修改互不影响。

**提交：** `feat(fund-rotation): enforce causal strategy data access`

## Task 2：提取公共执行与估值模块

**文件：**

- 新建：`agent/backtest/fund_rotation/execution.py`
- 修改：`agent/backtest/fund_rotation/pipeline.py`
- 修改：`agent/tests/fund_rotation/test_executor.py`
- 新建：`agent/tests/fund_rotation/test_execution_module.py`

**迁移范围：** 从 `pipeline.py` 提取执行上下文、先卖后买、共同缩放、现金/佣金不可行处理、ADV20 容量、ETF 100 份单位、估值和全区间净值生成。`_execute_with_capacity`、`_mark_to_market` 等执行器代码必须进入公共模块，不得放进策略包。

**步骤：**

1. 先为现有私有函数写 characterization tests，冻结订单排序、数量、费用、阻塞原因和估值。
2. 提取数据类和纯函数，pipeline 暂通过兼容导入调用新位置。
3. 保持同 bar 规则：先完成减仓，再对增仓共同求解可行缩放；无可行增仓时保留现金并将订单标为 `BLOCKED/insufficient_cash_after_commission`。
4. ADV20 只能使用执行日前已完成交易日的成交额，不得使用执行日全天成交额。
5. 运行 engine parity、migration 和 execution review tests。

**提交：** `refactor(fund-rotation): extract shared execution and valuation`

## Task 3：实现策略无关 Runner

**文件：**

- 新建：`agent/backtest/fund_rotation/runner.py`
- 新建：`agent/tests/fund_rotation/test_runner.py`

**接口：**

```python
class FundRotationBacktestRunner:
    def run(
        self,
        strategy: FundRotationStrategy,
        config: BaseModel,
        snapshot: PinnedFundDataSnapshot,
        evaluation: EvaluationContext,
        execution: ExecutionConfig,
        cancellation: CancellationToken,
    ) -> FundRotationRunResult: ...
```

**步骤：**

1. 写最小假策略测试，覆盖 SET、HOLD、INVALID、取消和异常。
2. Runner 先解析需求和建立 data view，再创建独立 session，按 scheduled dates 驱动 evaluate。
3. 首个 HOLD 无目标时维持现金且不创建目标事件；HOLD 不调用订单管理器创建、替换、取消或重算订单，已有残余订单继续沿用原父订单和公共执行规则。纯预热期不调用 `evaluate()`；满足预热后 INVALID 在任何被调度日期都终止子运行。
4. 使用 Phase 0 的 `schedule_targets`、公共执行模块和正式评价上下文。
5. Runner 只识别公共决策类型，不允许 `strategy_id` 分支。
6. finalize 失败应使子运行失败但保留此前事件，不能伪装成功。

**提交：** `feat(fund-rotation): add strategy-neutral runner`

## Task 4：让 baseline 策略完整生成现有目标

**文件：**

- 修改：`agent/backtest/fund_rotation/strategies/correlation_all_members/strategy.py`
- 新建：`agent/backtest/fund_rotation/strategies/correlation_all_members/signals.py`
- 修改：`agent/tests/fund_rotation/test_correlation_all_members_strategy.py`
- 新建：`agent/tests/fund_rotation/test_baseline_runner_parity.py`

**步骤：**

1. 从现有 pipeline 搬迁数据准备、相关矩阵、聚类、动量和目标生成调用到 baseline session；暂不改变算法。
2. 每次 evaluate 只读取 `signal_date` 及以前可见的数据，并把调试信息积累在 session 私有状态。
3. 用 Phase 0 golden fixture 同时运行旧 pipeline 和新 Runner。
4. 对照每个调仓日的目标、订单、成交、阻塞、净值和指标；仅接受 Phase 0 的逐项 `golden_delta`。离散字段与整数份额完全一致；目标权重 `rtol=0, atol=1e-12`；金额字段 `rtol=1e-12, atol=1e-6`；NAV `rtol=1e-10, atol=1e-10`；指标 `rtol=1e-9, atol=1e-10`。
5. 若出现差异，先证明根因，禁止用兼容分支复制错误行为。

**提交：** `feat(fund-rotation): run baseline strategy through common runner`

## Task 5：实现通用产物发布

**文件：**

- 新建：`agent/src/stockpred/fund_rotation/artifact_publisher.py`
- 修改：`agent/src/stockpred/fund_rotation/artifacts.py`
- 新建：`agent/tests/fund_rotation/test_artifact_publisher.py`

**步骤：**

1. 定义公共 roles：manifest、evaluation_calendar、targets、orders、fills、equity、metrics、events。
2. 策略产物通过 `StrategyArtifact(role, media_type, payload)` 声明；publisher 负责安全文件名、JSON/CSV 序列化和 manifest 索引。
3. 未知 role 可作为 namespaced strategy role 保存，但不能覆盖公共 role。
4. 验证路径穿越、重复 role、不可序列化 payload 会失败且不留下“成功”manifest。
5. baseline 的聚类诊断先作为策略专属产物发布，为 Phase 3 保留格式兼容。

**提交：** `feat(fund-rotation): publish common and strategy artifacts`

## Task 6：收缩旧 pipeline 并完成内部验收

**文件：**

- 修改：`agent/backtest/fund_rotation/pipeline.py`
- 修改：`agent/tests/fund_rotation/test_pipeline.py`
- 修改：`agent/tests/fund_rotation/test_review_contracts.py`

**步骤：**

1. 将旧 pipeline 收缩成参数转换和对新 Runner 的兼容调用；暂时保留其公开函数名。
2. 用架构测试禁止 pipeline 重新包含订单撮合、估值或策略算法实现。
3. 运行 Phase 0 golden、新旧 parity 和全套基金轮动测试。
4. 检查现有 API 仍能通过兼容入口创建旧形态回测。

**验证：**

```powershell
E:\anaconda3\envs\VibeTrading\python.exe -m pytest agent\tests\fund_rotation\test_runner.py agent\tests\fund_rotation\test_baseline_runner_parity.py -q
E:\anaconda3\envs\VibeTrading\python.exe -m pytest agent\tests\fund_rotation -q
git diff --check
```

**提交：** `refactor(fund-rotation): reduce legacy pipeline to runner adapter`

## Phase 2 出口门禁

- Runner 对策略算法零感知。
- CausalDataView 运行时阻断未声明和未来数据访问。
- baseline 经新 Runner 的结果满足 golden/parity 规则。
- 执行器、估值、指标和公共产物只有一个实现来源。
