# 基金轮动 V3 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变既有策略目标组合的前提下，把基金轮动证据链统一到真实持仓、decision identity 和通用 Strategy Score，并完成前端 Ranking、执行、时间线与 K 线证据升级。

**Architecture:** 后端先修复 Decision Bundle 的事实来源：Before 来自 positions history，Order/Fill 以 decision_id 关联，Strategy Score 由 ScoreModel 产生并只授予 representative。前端只消费通用 score contract，业务 wrapper 将周频真实点传入共享蜡烛图；旧 schema 保留读取兼容，不能确认 scope 的历史证据不猜测。

**Tech Stack:** Python、pytest、FastAPI/Pydantic、React、TypeScript、Vitest、Vite、Tailwind。

## Global Constraints

- 不修改 Momentum 数学定义、撮合算法、Cluster Gate 或历史数据重算。
- 不生成日频 fake score；周频 score 只保存真实观测点。
- `ranking_eligible=false` 时 `rank=null` 且 score 为 null。
- 任何新行为先写失败测试，再写最小生产代码。
- 保留 V2 artifact 的读取兼容。

---

### Task 1: Phase 1 事实语义

**Files:**
- Modify: `agent/src/stockpred/fund_rotation/decision_evidence.py`
- Test: `agent/tests/fund_rotation/test_decision_evidence.py`

- [ ] 为真实 positions history Before、decision_id execution join、target/actual turnover 分离和 Actual Before 状态写失败测试。
- [ ] 从 positions history 选择 signal 前最近账户状态；无历史状态时才使用空组合。
- [ ] 用 decision_id 关联 orders/fills，只有旧记录缺少 identity 时才按 signal week fallback。
- [ ] 在 bundle 中同时输出 `target_changed_positions`、`actual_changed_positions` 和真实 Before/Target/Execution turnover。
- [ ] 运行该测试文件并保留既有兼容测试。

### Task 2: Phase 1 代表性和 Ranking

**Files:**
- Modify: strategy trace producer under `agent/src/backtest/fund_rotation/`
- Modify: `frontend/src/components/stockpred/fund-rotation/rebalance/RankingLane.tsx`
- Modify: `frontend/src/components/stockpred/fund-rotation/types.ts`
- Test: backend representative strategy tests and `frontend/src/components/stockpred/fund-rotation/__tests__/DecisionPanels.test.tsx`

- [ ] 为代表与成员的 rank/score 资格写失败测试。
- [ ] 仅 representative 产出 rank/score；成员保留 `SAME_CLUSTER_EXCLUDED`。
- [ ] RankingLane 过滤、按 rank 排序、在 rank N 后插入 cutoff，并按 direction 对 score 做视觉归一化。
- [ ] 状态改用真实 `before` weight，不能使用上一期 target。

### Task 3: Strategy Score contract

**Files:**
- Create: `agent/src/backtest/fund_rotation/scoring/__init__.py`
- Create: `agent/src/backtest/fund_rotation/scoring/contracts.py`
- Create: `agent/src/backtest/fund_rotation/scoring/cluster_momentum.py`
- Modify: current correlation representative strategy producer
- Test: `agent/tests/fund_rotation/test_strategy_score.py`

- [ ] 定义 `StrategyScore`、`ScoreModel`、方向和资格校验。
- [ ] 包装现有 cluster momentum，不改变计算结果。
- [ ] 实现 deterministic tie break 的 `select_top_scores`。
- [ ] 用旧实现与新模型的 parity fixture 验证 target portfolio 完全一致。

### Task 4: Evidence schema v2 and frontend score contract

**Files:**
- Modify: `agent/src/stockpred/fund_rotation/decision_evidence.py`
- Modify: `agent/src/stockpred/fund_rotation/api_models.py`
- Modify: `frontend/src/components/stockpred/fund-rotation/types.ts`
- Modify: `frontend/src/components/stockpred/fund-rotation/TradeMarkersChart.tsx`
- Modify: `frontend/src/components/charts/CandlestickChart.tsx`
- Test: backend evidence tests and chart tests

- [ ] 输出 schema v2 的 generic score、scope、subject、model metadata 和 real weekly points。
- [ ] Momentum 作为 component，不作为前端主曲线概念。
- [ ] fund-rotation wrapper 改用 `strategyScore`，共享图表保留 generic overlay 能力。
- [ ] 周频真实点使用 `connectNulls=true`，不补每日数据，真实点显示 symbol。
- [ ] 非 representative instrument 不生成 score 曲线。

### Task 5: Why / execution / timeline / lazy load

**Files:**
- Modify: `frontend/src/components/stockpred/fund-rotation/rebalance/ExecutionSummary.tsx`
- Modify: `frontend/src/components/stockpred/fund-rotation/rebalance/PortfolioChangeChart.tsx`
- Modify: `frontend/src/components/stockpred/fund-rotation/rebalance/StrategyPipeline.tsx`
- Modify: `frontend/src/components/stockpred/fund-rotation/holdings/HoldingsWeightTimeline.tsx`
- Modify: `frontend/src/components/stockpred/fund-rotation/useBacktestDetail.ts`
- Test: corresponding frontend suites

- [ ] 展示 Before/Target/Order/Fill、target 与 execution turnover、commission 和阻断原因。
- [ ] dumbbell 使用 0%–100% 真实比例坐标。
- [ ] metadata、cluster threshold 和 score components 从后端传递，不在前端猜业务。
- [ ] timeline 按 zoom 过滤事件、折叠长尾并固定显示 Cash。
- [ ] K 线切换为 selected instrument 单标的懒加载并缓存。

### Task 6: Verification

- [ ] 运行相关 pytest、Vitest 和 Vite build。
- [ ] 运行 `git diff --check`。
- [ ] 记录已有基线失败，不将其误报为 V3 回归。

