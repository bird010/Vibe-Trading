# Phase 0：公平比较地基实施计划

> **执行要求：** 使用 `executing-plans` 技能按任务顺序实施。每个任务采用 TDD，并在提交前运行基金轮动全套测试。

**目标：** 在抽象策略插件之前，修正数据快照、52 周边界、评价日历、首期收益和初始建仓语义，使后续策略比较建立在一致且可重放的事实之上。

**架构：** 新增不可变数据快照和值对象形式的正式评价上下文；当前 pipeline 暂时继续存在，但真实执行、理想执行和基准执行共享同一日历与初始目标调度规则。指标函数显式接收初始净值锚点。

**技术栈：** Python、pandas、Lance、pytest。

---

## 0. 开始前记录

**读取：**

- `agent/backtest/fund_rotation/pipeline.py`
- `agent/backtest/fund_rotation/returns.py`
- `agent/backtest/fund_rotation/metrics.py`
- `agent/backtest/fund_rotation/ideal_executor.py`
- `agent/backtest/fund_rotation/benchmarks.py`
- `agent/src/stockpred/fund_rotation/service.py`

**成功标准：** 本阶段不改变策略选择逻辑；唯一允许的结果变化是本计划列出的 52 周边界、缺失收益、正式评价日历、初始建仓与首期指标修正。

## Task 1：保存修复前 golden 基线和差异许可清单

**文件：**

- 新建：`agent/tests/fund_rotation/fixtures/phase0/README.md`
- 新建：`agent/tests/fund_rotation/fixtures/phase0/pre_fix_golden.json`
- 新建：`agent/tests/fund_rotation/test_phase0_golden.py`

**步骤：**

1. 使用固定的 80 周合成数据，包含至少 3 只可投资 ETF 和独立的 `510300.SH` 基准；数据中加入一次复权事件和一个缺失收盘价。
2. 在改代码前执行当前 pipeline，把规范化后的调仓日、目标权重、订单、成交、阻塞、日终净值和指标保存为 golden；排除时间戳、绝对临时路径等非确定字段。
3. 在 `README.md` 固定允许差异：首个可用 52 周窗口、缺失值不前填、预评价目标首日执行、完整评价日历、首期收益/指标。除此之外不允许离散事件变化。
4. 写逐字段比较测试：日期、代码、动作、原因码、订单方向、状态、簇标签和整数份额完全一致；目标权重 `rtol=0, atol=1e-12`；成交价格、佣金、现金和权益金额 `rtol=1e-12, atol=1e-6`；归一化 NAV `rtol=1e-10, atol=1e-10`；收益风险指标 `rtol=1e-9, atol=1e-10`。
5. 运行测试，确认修改前基线可稳定重放。

**验证：**

```powershell
E:\anaconda3\envs\VibeTrading\python.exe -m pytest agent\tests\fund_rotation\test_phase0_golden.py -q
```

**提交：** `test(fund-rotation): capture phase0 comparison baseline`

## Task 2：在读取前固定不可变数据快照

**文件：**

- 新建：`agent/src/stockpred/fund_rotation/data_snapshot.py`
- 修改：`agent/src/stockpred/fund_rotation/service.py`
- 修改：`agent/backtest/fund_rotation/pipeline.py`
- 新建：`agent/tests/fund_rotation/test_data_snapshot.py`
- 修改：`agent/tests/fund_rotation/test_pipeline.py`

**契约：**

```python
@dataclass(frozen=True)
class PinnedFundDataSnapshot:
    fund_version: int
    fund_adj_version: int
    dim_version: int
    universe_codes: tuple[str, ...]
    trading_dates: tuple[str, ...]
    fingerprint: str
```

**步骤：**

1. 写失败测试：在快照创建后向数据集追加新版本，后续读取仍只能看到固定版本。
2. 写失败测试：ETF 池和交易日历改变任一项，`fingerprint` 必须改变；顺序差异在规范化后不得改变指纹。
3. 实现 `resolve_pinned_snapshot(...)`：先解析三个 Lance 版本，再用 `lance.dataset(path, version=...)` 读取和构造 ETF 池/日历，最后生成规范 JSON 的 SHA-256。
4. 修改 service：任务入队前只保存请求；后台任务启动后首先创建一次快照，然后所有数据读取显式传入版本，禁止重新打开 latest。
5. 将三个版本、ETF 池哈希、交易日历哈希和总指纹写入 manifest。
6. 运行目标测试和全套测试。

**验证：**

```powershell
E:\anaconda3\envs\VibeTrading\python.exe -m pytest agent\tests\fund_rotation\test_data_snapshot.py agent\tests\fund_rotation\test_pipeline.py -q
E:\anaconda3\envs\VibeTrading\python.exe -m pytest agent\tests\fund_rotation -q
```

**提交：** `fix(fund-rotation): pin immutable data snapshot before reads`

## Task 3：显式禁止收益率缺失值前填

**文件：**

- 修改：`agent/backtest/fund_rotation/returns.py`
- 修改：`agent/backtest/fund_rotation/metrics.py`
- 修改：`agent/backtest/fund_rotation/pipeline.py`
- 修改：`agent/tests/fund_rotation/test_returns.py`
- 修改：`agent/tests/fund_rotation/test_pipeline.py`
- 修改：`agent/tests/fund_rotation/test_review_contracts.py`

**步骤：**

1. 写行为测试：价格序列中间缺值时，缺值后的收益不得由隐式前填产生 0 或跨空档伪收益。
2. 写契约测试，用 spy 断言基金轮动路径调用 `pct_change(fill_method=None)`；这是为了防止 pandas 版本变化，不能只依赖当前 pandas 3.0.3 的默认值。
3. 在 `returns.py`、`metrics.py` 和 `pipeline.py` 的所有相关调用上显式传入 `fill_method=None`。
4. 用 `rg -n "pct_change" agent/backtest/fund_rotation` 人工核对无遗漏。
5. 运行目标测试和全套测试。

**提交：** `fix(fund-rotation): make missing-return policy explicit`

## Task 4：固定 52 周训练窗口边界

**文件：**

- 修改：`agent/backtest/fund_rotation/returns.py`
- 修改：`agent/backtest/fund_rotation/pipeline.py`
- 修改：`agent/tests/fund_rotation/test_returns.py`
- 修改：`agent/tests/fund_rotation/test_pipeline.py`

**步骤：**

1. 写直接单元测试：精确 53 个周末价格应形成 52 行有效周收益，且窗口不得包含信号时点之后的数据。
2. 写窗口 spy 测试：`correlation_lookback_weeks=52` 时送入聚类器的有效收益恰为 52 行。
3. 端到端测试改用 60–80 周数据，并保留信号周之后的下一交易日；不得误用“精确 53 周全 pipeline”证明可成交。
4. 最小修正窗口切片的边界，保持可配置 `correlation_lookback_weeks`（最小训练周数门禁仍用现有 `min_training_weeks`）。
5. 断言训练不足时返回已定义的非 ready 状态，不得静默缩短窗口。

**提交：** `fix(fund-rotation): correct configurable weekly training boundary`

## Task 5：建立正式评价日历和净值语义

**文件：**

- 新建：`agent/backtest/fund_rotation/evaluation.py`
- 修改：`agent/backtest/fund_rotation/pipeline.py`
- 修改：`agent/backtest/fund_rotation/metrics.py`
- 修改：`agent/tests/fund_rotation/test_pipeline.py`
- 新建：`agent/tests/fund_rotation/test_evaluation.py`

**契约：**

```python
@dataclass(frozen=True)
class EvaluationContext:
    trading_dates: tuple[pd.Timestamp, ...]
    initial_nav: float = 1.0

def validate_equity_index(equity: pd.Series, context: EvaluationContext) -> None: ...
```

**步骤：**

1. 写失败测试：正式日历只能包含 `start_date <= d <= end_date` 的可交易日，并且顺序唯一。
2. 写失败测试：少一天、多一天或日期重复的净值序列均拒绝；不能只检查首尾或取交集。
3. 写失败测试：首个日终净值为 0.98 时，首期收益必须是 `0.98 / 1.0 - 1`；输出序列不得把首日强行改写成 1.0，也不得在同一日期复制开盘前/收盘后两个点。
4. 先构造完整 `EvaluationContext`，再生成信号执行计划，避免使用未定义的 `eval_start`。
5. 修改指标入口，使总收益、年化收益、波动率、夏普和最大回撤都以同一 `initial_nav` 语义处理首期；需要时在内部把无日期的初始锚点作为前置收益基准，而不是伪造交易日。
6. 在 pipeline 结束前调用严格索引校验。

**提交：** `fix(fund-rotation): formalize evaluation calendar and initial nav`

## Task 6：统一预评价目标的首次执行

**文件：**

- 修改：`agent/backtest/fund_rotation/evaluation.py`
- 修改：`agent/backtest/fund_rotation/pipeline.py`
- 修改：`agent/backtest/fund_rotation/ideal_executor.py`
- 修改：`agent/backtest/fund_rotation/benchmarks.py`
- 修改：`agent/tests/fund_rotation/test_pipeline.py`
- 修改：`agent/tests/fund_rotation/test_ideal_executor.py`
- 修改：`agent/tests/fund_rotation/test_benchmarks.py`

**契约：**

```python
def schedule_targets(
    targets: Sequence[TargetSnapshot],
    evaluation_dates: Sequence[pd.Timestamp],
) -> Mapping[pd.Timestamp, TargetSnapshot]: ...
```

**步骤：**

1. 写长历史测试，使正式开始日前确实存在有效目标；断言它在正式评价首个交易日按开盘规则产生订单，并核对数量和成交时点。
2. 写首日 `HOLD_TARGETS` 且无历史目标的测试，断言全现金且无订单。
3. 让真实执行器、理想执行器和买入持有基准调用同一个 `schedule_targets`，删除各自的“下一个原始日期”推断。
4. 写三路径一致性测试：相同目标的首次有效执行日相同；成交价格/容量规则仍各按其既定模型。
5. 保留完整正式评价区间日终估值，即使没有后续调仓。

**提交：** `fix(fund-rotation): align initial target scheduling across executors`

## Task 7：覆盖空目标、全阻塞和完整区间

**文件：**

- 修改：`agent/backtest/fund_rotation/pipeline.py`
- 修改：`agent/tests/fund_rotation/test_pipeline.py`
- 修改：`agent/tests/fund_rotation/test_execution_review_fixes.py`

**步骤：**

1. 用有效的 `start_date <= end_date` 写无目标测试；也可直接调用执行循环，但不得构造 2099 开始、较早结束的无效配置。
2. 断言无目标时仍输出与正式评价日历等长的现金净值，指标为零收益，而非空序列。
3. 使用 60–80 周且能产生执行的 fixture 制造最低佣金/现金不足，断言增仓全部 `BLOCKED`、原因为 `insufficient_cash_after_commission`，现金净值仍覆盖全区间。
4. 断言部分策略失败不得改变其他策略的日历语义（为 Phase 4 保留契约）。

**提交：** `fix(fund-rotation): preserve full evaluation interval without fills`

## Task 8：核对 golden 差异并关闭阶段

**文件：**

- 修改：`agent/tests/fund_rotation/test_phase0_golden.py`
- 新建：`agent/tests/fund_rotation/fixtures/phase0/approved_delta.json`
- 修改：`docs/superpowers/plans/2026-08-02-fund-rotation-phase0-comparison-foundation.md`（仅填写执行记录，不改验收口径）

**步骤：**

1. 生成修复后规范化结果，并把每项变化分类到许可清单。
2. 离散事件只允许由首次 52 周信号、初始目标执行和评价日历修正引起；其他变化必须先查明根因。
3. 按设计 §35.1 分类验收：目标权重 `rtol=0, atol=1e-12`；成交价格、佣金、现金和权益金额 `rtol=1e-12, atol=1e-6`；NAV `rtol=1e-10, atol=1e-10`；指标 `rtol=1e-9, atol=1e-10`。离散字段和整数份额必须完全一致，不得以浮点容差比较。
4. 运行全部基金轮动测试，并记录测试数和耗时。
5. `rg` 核对所有 Lance 打开点均使用固定版本，所有相关 `pct_change` 均显式禁用填充。

**最终验证：**

```powershell
E:\anaconda3\envs\VibeTrading\python.exe -m pytest agent\tests\fund_rotation -q
rg -n "lance\.dataset|pct_change" agent\src\stockpred\fund_rotation agent\backtest\fund_rotation
git diff --check
```

**提交：** `test(fund-rotation): verify phase0 comparison foundation`

## Phase 0 出口门禁

- Task 1–8 各自提交且全套测试均通过。
- manifest 能证明所有读取使用同一组固定版本。
- 端到端结果覆盖完整正式评价日历。
- golden 未出现许可清单之外的行为变化。
- 评审者可从订单和净值产物复算首日交易及首期收益。

## Phase 0 执行记录（Task 8 填写，不改验收口径）

- **提交链**（分支 `data-layer-improve`）：
  - Task 1 `7d3c0c3` 捕获修复前 golden 基线与许可清单
  - Task 2 `a5da87c` 读取前固定不可变数据快照
  - Task 3 `7fcbd35` 显式缺失值不前填（6 处 pct_change fill_method=None）
  - Task 4 `ba6344d` 52 周训练窗口边界修正（窗口恰 52 行）
  - Task 5 `175f0eb`（+修复 `b35d294..175f0eb`）评价日历与 initial_nav 净值语义
  - Task 6 `ba8bddf`（+修复 `ba8bddf..6c207dd`）统一预评价目标首次执行（schedule_targets 三路径）
  - Task 7 `92ab83e` 空目标/全阻塞/完整评价区间现金净值
  - Task 8 本记录 + approved_delta 收尾
- **测试**：`agent/tests/fund_rotation` 全套 **298 passed**（约 19.5s）。每个 Task 均 TDD（RED→GREEN）并经 CodeReview 子代理逐条对照设计文档审阅通过。
- **golden 差异核对**：修复后相对 pre_fix_golden 的差异**仅限两个已批准类别**，无离散事件（调仓周、订单、成交、簇标签、整数份额）变化：
  1. `strategy_metrics.*` / `benchmark_metrics.*`：Task 5 initial_nav 无日期锚点使首期收益相对 1.0 计量、num_periods +1（设计 §32.1）。
  2. `positions_summary: length differs`：Task 7 日终权益/持仓扩展至完整评价日历，执行日前补现金行（设计 §24/§32.1）；executed_equity 经公共区间重索引保持不变且仍被逐日验证。
- **rg 核对**：fund_rotation 范围内所有 `pct_change` 均 `fill_method=None`；所有业务 `lance.dataset` 读取均 `version=` 固定（data_snapshot.py 中仅为解析版本号而短暂打开 latest，随即以固定版本重开，符合设计）。
- **已知限制（移交后续）**：run_signal_pipeline 的 benchmark 段在“无成交/全阻塞”时 `_first_actual_fill_date` 会报错（Task 7 测试按步骤直接调用执行循环规避）；设计 §27 “比较日期严格等于完整 evaluation_calendar”属多策略比较服务（Phase 1+）范围。二者均在 Phase 0 范围外，未使现状恶化。
