# Task 4 实施简报：冻结 R40 并启动 Shadow A

## 目标

严格执行计划 Task 4：从 U1 与 R39 control identities 冻结 `ai_rotation_r40_single_name_ceiling`（固定 50% ceiling），启动 Shadow A，并明确“shadow started”不等于“shadow qualified”。当前只有在真实 forward evidence 不足时记录 `INSUFFICIENT_FORWARD_EVIDENCE`，不得伪造 104 周资格结论。

## 允许修改范围

- 仅按计划检查并最小修改 `agent/src/stockpred/fund_rotation/forward_validation.py`
- 仅按计划检查并最小修改 `agent/src/stockpred/fund_rotation/production_adapters.py`
- 修改/新增计划列明的测试和 `experiments/fund_rotation_research_validity/start_r40_shadow.py`、`shadow_a_report.md`

不得修改 R39/R40 策略逻辑、公共 execution ledger、平台级架构或既有历史记录；复用现有 `FrozenStrategyVersion`、`ShadowDecisionService`、`ShadowExecutionService`、`ShadowRunScheduler` 和 `shadow_*` artifact contracts。

## 必须验证

- 冻结配置不可变、决策先于执行且各自密封；决策/执行使用不同 idempotency key。
- 执行延迟、连续账户状态、ideal/executable NAV 分离均保留现有语义。
- 冻结 strategy ID 为 `ai_rotation_r40_single_name_ceiling`，ceiling 固定 `0.5`。
- 启动首个 Shadow cycle 时生成冻结 manifest、qualification policy、初始 evidence/assessment、deployment manifest 和首批 append-only decision/account artifact（若环境无真实输入，诚实记录 unavailable）。
- 104 周证据尚未取得时状态必须为 `INSUFFICIENT_FORWARD_EVIDENCE`，不得宣称 qualified。

## 实施顺序

1. 先写 failing tests 并运行 focused RED。
2. 以最小 wiring 补齐 manifest/deployment/evidence 缺口，保留既有 forward-validation state machine。
3. 运行 focused forward-validation tests 和相关回归。
4. 冻结 R40、尝试启动 Shadow A；没有真实服务/数据时只生成 unavailable 证据。
5. 生成中文报告，说明 started/qualified 区分、输入 hash、测试证据和最小改动自评。
6. 等待独立 gpt-5.6-luna/high review；P0/P1 非零不得进入 Task 5。
