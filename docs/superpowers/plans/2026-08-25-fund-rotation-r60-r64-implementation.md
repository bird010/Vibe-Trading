# R60–R64 基金轮动正交策略实施计划

> **For agentic workers:** 按任务逐项执行；每个策略均先写失败测试，再写最小实现。

**Goal:** 在不改变既有策略、公共 Runner、PIT/执行合同和评价门禁的前提下，新增并注册 R60–R64 五个独立 challenger 策略。

**Architecture:** 每个策略使用独立目录和独立测试，优先复用既有 R59/R58 生命周期与因子计算边界；R64 使用独立配置和直接相关性选择。registry 只追加 import 和 whitelist entry，保留既有顺序与断言。

**Tech Stack:** Python 3、Pydantic、pandas/numpy、pytest、现有 fund-rotation contracts/Runner。

## Global Constraints

- 新策略 ID 唯一：`ai_rotation_r60_r59_medium_trend_gate` 至 `ai_rotation_r64_direct_corr_diversification`。
- 不修改 R39/R57/R58/R59 实现、公共 Runner、Execution/PIT 数据语义或既有 Champion gates。
- signal cutoff 保持 CLOSE；所有 lookback 截止 signal date；诊断必须 strict JSON、确定性重放。
- R60→R61→R62→R63→R64 顺序实现；正式研究结果出现后不复用同 ID 修改行为。
- 暂不执行回测晋级；实现阶段只完成单测、回归测试、注册和实现审查材料。

### Task 1: R60 中期趋势 gate

**Files:**
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r60_r59_medium_trend_gate/__init__.py`
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r60_r59_medium_trend_gate/strategy.py`
- Create: `agent/tests/fund_rotation/test_ai_rotation_r60_r59_medium_trend_gate.py`
- Modify: `agent/backtest/fund_rotation/strategies/registry.py`

**Implementation:** 从 R59 派生独立 session；复用 R57 composite 和 R59 positive-slope gate，只增加因果 126D medium return `close_t/close_{t-126}-1 > 0` 的后置 gate。保留短期 factor 排名，不把 medium return 放入 score。记录 `medium_return_126d`、数据不足与负趋势两类排除原因；requirements 保持公共 warmup 上界。

**Tests:** descriptor/registry/requirements；正中期趋势通过；负中期趋势排除；127 行数据不足与未来数据不影响；R59 生命周期保持。

### Task 2: R61 双时间尺度 score

**Files:**
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r61_r59_dual_horizon_score/__init__.py`
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r61_r59_dual_horizon_score/strategy.py`
- Create: `agent/tests/fund_rotation/test_ai_rotation_r61_r59_dual_horizon_score.py`
- Modify: registry

**Implementation:** 复用 R59 的 positive-slope gate，但不继承 R60 medium gate；对短期 composite 与 126D medium return 各自独立横截面标准化，ranking score 为 `0.5*short_z + 0.5*medium_z`。缺失/不足样本不得误当作零，diagnostics 分离两个 score。

**Tests:** 独立标准化；50/50 只影响 ranking；没有 R60 gate；因果截止、缺失、确定性和生命周期回归。

### Task 3: R62 真 inverse-volatility weighting

**Files:**
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r62_r59_true_invvol/__init__.py`
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r62_r59_true_invvol/strategy.py`
- Create: `agent/tests/fund_rotation/test_ai_rotation_r62_r59_true_invvol.py`
- Modify: registry

**Implementation:** selected codes 必须与 R59 相同；在 staged/carry 前使用 60D close return volatility，`raw=1/sigma`，归一化到 filled/top_n 的 base exposure，并按单 ETF 不超过 50% cap 后确定性再归一化。缺失 volatility 时整体 fallback equal slots。保留 50% staged re-entry 和 incumbent carry。

**Tests:** 1/sigma 而非 1/(1+sigma)；selected codes 不变；cap；缺失 fallback；base exposure；staging/carry 顺序；因果与 diagnostics。

### Task 4: R63 rank hysteresis / exit buffer

**Files:**
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r63_r59_rank_buffer/__init__.py`
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r63_r59_rank_buffer/strategy.py`
- Create: `agent/tests/fund_rotation/test_ai_rotation_r63_r59_rank_buffer.py`
- Modify: registry

**Implementation:** 在 R59 ranking 后使用 entry Top3 / exit Top4；上期 selected cluster 当前 rank=4 且 positive slope、factor complete 时保留，rank>=5 或 gate 失败立即退出。recluster 清空 state；同 epoch representative replacement 允许保持 cluster identity。只保存 cluster state，不改变 composite score。

**Tests:** rank 3→4 保留、3→5 退出、gate 失败退出、recluster reset、替换代表、最多三槽、按当前 rank 排序、staging/carry、deterministic replay。

### Task 5: R64 直接 pairwise correlation diversification

**Files:**
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r64_direct_corr_diversification/__init__.py`
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r64_direct_corr_diversification/config.py`
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r64_direct_corr_diversification/strategy.py`
- Create: `agent/tests/fund_rotation/test_ai_rotation_r64_direct_corr_diversification.py`
- Modify: registry

**Implementation:** 新 frozen config；对全部 PIT eligible ETF 计算 R57 因子和 positive slope gate，按 score greedy 选择 Top3，要求 pairwise weekly correlation `<0.80`、至少 20 周且 finite；未知相关性保守跳过。完全不运行 clustering/representative/gates，保留 staged re-entry/incumbent carry，diagnostics 只保存真实相关性信息。

**Tests:** threshold 边界、负相关、NaN/不足样本、现金空槽、无 cluster artifacts、R57 因果、生命周期、字符串 mapping key 和有限数值。

### Task 6: 回归和实现收尾

**Files:**
- Modify: only the five focused test files or exact registry catalog assertion if required
- Create: implementation reports/ledger entries only where the existing research layout already supports them

**Verification:** 先运行五个 focused suites，再运行 `agent/tests/fund_rotation` 全套；检查 registry IDs 唯一、旧 R59 文件无 diff、公共 contract tests 通过。只在所有测试完成后形成研究记录；不声称回测或 Champion 晋级已完成。
