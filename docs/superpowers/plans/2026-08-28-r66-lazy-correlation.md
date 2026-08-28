# R66 Lazy Correlation ETF 轮动 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增与 R64 行为等价但按需计算相关性的 R66 ETF 轮动策略，并验证结果一致、计算量下降。

**Architecture:** R66 保留 R64 的信号、组合和生命周期逻辑，只将相关性计算抽成带缓存的 lazy lookup，并在 R66 因子路径中按 ETF 分组读取数据。公共 weekly returns 路径改为 branch-first，去掉无效的第一次复权计算；R64/R65 旧策略代码不改变。

**Tech Stack:** Python 3、pandas、NumPy、Pydantic、pytest、现有 fund-rotation registry/backtest contracts。

## Global Constraints

- 保持 `corr < 0.80`、最少 20 周观察、Top3 和 R57 权重不变。
- 保持 R64/R65 原实现不变。
- 所有新增策略中文名称以真实策略代号 `R66` 开头。
- 不引入新运行时依赖。
- 每项生产代码变更先有能失败的测试，再实现。

---

### Task 1: Lazy pairwise correlation component

**Files:**
- Modify: `agent/backtest/fund_rotation/strategies/ai_rotation_r66_lazy_correlation/strategy.py`
- Test: `agent/tests/fund_rotation/test_ai_rotation_r66_lazy_correlation.py`

**Interfaces:**
- Produces `PairwiseCorrelationLookup(returns, ranked_codes, min_pairwise_weeks)` callable as `lookup(left, right) -> tuple[float, int]`.
- Produces `select_lazy_direct_correlation_diversified(...)` with the same selected/diagnostics contract as R64.

- [ ] Step 1: 写测试，验证 lookup 计算 pair 时保持原 pandas 公式并缓存重复请求。
- [ ] Step 2: 运行定向测试，预期因 R66 模块/接口尚不存在而失败。
- [ ] Step 3: 实现 lookup 和严格复刻 R64 selector 控制流。
- [ ] Step 4: 运行定向测试，确认阈值边界、缺失观察和诊断优先级通过。

### Task 2: R66 behavior-equivalent strategy

**Files:**
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r66_lazy_correlation/__init__.py`
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r66_lazy_correlation/config.py`
- Modify: `agent/backtest/fund_rotation/strategies/ai_rotation_r66_lazy_correlation/strategy.py`
- Modify: `agent/backtest/fund_rotation/strategies/registry.py`
- Test: `agent/tests/fund_rotation/test_ai_rotation_r66_lazy_correlation.py`

**Interfaces:**
- Strategy id: `ai_rotation_r66_lazy_correlation`.
- Descriptor name: `R66 R64惰性相关性约束ETF轮动`.
- Config: frozen `top_n=3`, `correlation_lookback_weeks=52`, `min_pairwise_weeks=20`.

- [ ] Step 1: 写测试，验证注册、配置冻结、R64/R66 在固定上下文下目标权重及诊断一致。
- [ ] Step 2: 运行测试确认新策略注册和行为测试失败。
- [ ] Step 3: 以 R64 流程为基线实现 R66，替换为 lazy selector，保留 staged reentry、incumbent carry 和 artifact schema。
- [ ] Step 4: 运行 R66 定向测试并确认旧 R64 测试仍通过。

### Task 3: Remove duplicate weekly adjusted-close computation

**Files:**
- Modify: `agent/backtest/fund_rotation/causal_data.py`
- Test: `agent/tests/fund_rotation/test_causal_data.py` or the existing returns-focused test file.

**Interfaces:**
- `CausalDataView.returns("weekly", lookback)` keeps the existing output values and audit behavior.

- [ ] Step 1: 写测试，spy `compute_adjusted_close`，验证 weekly 路径只调用一次且结果与基线相同。
- [ ] Step 2: 运行测试确认当前实现因调用两次而失败。
- [ ] Step 3: 将 daily/monthly 的 adjusted-close 计算移入对应分支，weekly 直接调用 `compute_weekly_returns`。
- [ ] Step 4: 运行 returns 定向测试和 R66 端到端测试。

### Task 4: Group factor input by ETF for R66

**Files:**
- Modify: `agent/backtest/fund_rotation/strategies/ai_rotation_r66_lazy_correlation/strategy.py`
- Test: `agent/tests/fund_rotation/test_ai_rotation_r66_lazy_correlation.py`

**Interfaces:**
- R66 factor rows preserve all R64 factor values, statuses, observations and candidate ordering.

- [ ] Step 1: 写固定行情测试，比较分组实现与 R64 因子行输出。
- [ ] Step 2: 运行测试确认 R66 尚未使用分组实现时失败或暴露差异。
- [ ] Step 3: 预先建立 `ts_code -> DataFrame` 映射，用 `.get()` 读取每只 ETF，保持 causal ordering 和 adjustment validation。
- [ ] Step 4: 运行等价性测试。

### Task 5: Full verification

**Files:**
- Test: `agent/tests/fund_rotation/test_ai_rotation_r64_direct_corr_diversification.py`
- Test: `agent/tests/fund_rotation/test_ai_rotation_r65_r64_direct_corr_rank_buffer.py`
- Test: `agent/tests/fund_rotation/test_ai_rotation_r60_r64_session_invariants.py`
- Test: all `agent/tests/fund_rotation` tests.

- [ ] Step 1: 运行 R64/R65/R66 相关测试。
- [ ] Step 2: 运行完整 fund-rotation 测试集。
- [ ] Step 3: 检查 git diff，确认没有修改 R64/R65 策略语义和没有遗留调试代码。
- [ ] Step 4: 记录测试结果和 R66 使用方式。

